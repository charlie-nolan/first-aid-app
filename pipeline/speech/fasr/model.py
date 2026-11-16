"""The on-device acoustic model: a small causal 1-D conv net with CTC over 41 tokens.

Only ops ESP-DL runs well (Conv as 2-D with height 1, depthwise conv, ReLU, Add), no
recurrence and no attention, so it exports cleanly to int8 and streams: every layer is
causal (left padding only), so the device can run it on audio as it arrives.

Default size ("s"): 12 blocks x 192 channels, ~0.55 M weights -> ~0.55 MB int8,
~0.5 M multiply-adds per 20 ms frame (~25 M MAC/s), receptive field ~3.4 s.
"""
from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F

from .features import NMEL
from .phones import N_TOKENS

SIZES = {  # name: (channels, blocks, kernel)
    "xs": (128, 8, 15),
    "s": (192, 12, 15),
    "m": (256, 14, 15),
}


class CausalConv(nn.Module):
    def __init__(self, cin, cout, k, stride=1, groups=1):
        super().__init__()
        self.pad = k - stride if k > stride else 0
        self.conv = nn.Conv1d(cin, cout, k, stride=stride, groups=groups, bias=False)

    def forward(self, x):
        return self.conv(F.pad(x, (self.pad, 0)))


class Block(nn.Module):
    """depthwise (time) -> BN -> ReLU -> pointwise -> BN -> +residual -> ReLU"""

    def __init__(self, c, k, drop):
        super().__init__()
        self.dw = CausalConv(c, c, k, groups=c)
        self.bn1 = nn.BatchNorm1d(c)
        self.pw = nn.Conv1d(c, c, 1, bias=False)
        self.bn2 = nn.BatchNorm1d(c)
        self.drop = nn.Dropout(drop)

    def forward(self, x):
        y = F.relu(self.bn1(self.dw(x)))
        y = self.drop(self.bn2(self.pw(y)))
        return F.relu(x + y)


class AcousticModel(nn.Module):
    """(B, T, 40) log-mel -> (B, T/2, 41) log-probs. 10 ms in, 20 ms out."""

    def __init__(self, size: str = "s", drop: float = 0.1):
        super().__init__()
        c, n, k = SIZES[size]
        self.size = size
        self.register_buffer("mu", torch.zeros(NMEL))
        self.register_buffer("sd", torch.ones(NMEL))
        self.stem = CausalConv(NMEL, c, 5, stride=2)
        self.bn0 = nn.BatchNorm1d(c)
        self.blocks = nn.Sequential(*[Block(c, k, drop) for _ in range(n)])
        self.head = nn.Conv1d(c, N_TOKENS, 1)
        # Per-user adaptation slot ("speaker profile"), identity for the base model:
        #   film[k] = (scale, shift) per channel after the stem (k=0) and after block k.
        # Base training never touches it. On-device enrollment (fasr adapt) fits it, plus the
        # user's own feature mean/std and a head bias, from 2-3 minutes of prompted speech.
        self.register_buffer("film", torch.zeros(n + 1, 2, c))
        self.register_buffer("head_bias", torch.zeros(N_TOKENS))

    def features(self, feats, user_mu=None, user_sd=None):
        mu = self.mu if user_mu is None else user_mu
        sd = self.sd if user_sd is None else user_sd
        x = ((feats - mu) / sd).transpose(1, 2)
        x = F.relu(self.bn0(self.stem(x)))
        x = self._film(x, 0)
        for k, b in enumerate(self.blocks, 1):
            x = self._film(b(x), k)
        return x

    def _film(self, x, k):
        f = self.film[k]
        return x * (1 + f[0])[None, :, None] + f[1][None, :, None]

    def forward(self, feats, user_mu=None, user_sd=None):
        x = self.features(feats, user_mu, user_sd)
        y = self.head(x) + self.head_bias[None, :, None]
        return y.transpose(1, 2).log_softmax(-1)

    def load_compat(self, state: dict):
        """Load a checkpoint made before the adaptation slot existed."""
        missing, unexpected = self.load_state_dict(state, strict=False)
        bad = [k for k in missing if k not in ("film", "head_bias")]
        if bad or unexpected:
            raise RuntimeError(f"checkpoint mismatch: missing {bad}, unexpected {unexpected}")

    @staticmethod
    def out_len(n_frames):
        return (n_frames - 2) // 2 + 1  # stem: k=5, stride 2, left pad 3

    def n_params(self):
        return sum(p.numel() for p in self.parameters())
