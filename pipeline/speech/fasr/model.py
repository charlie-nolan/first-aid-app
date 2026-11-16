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

    def forward(self, feats):
        x = ((feats - self.mu) / self.sd).transpose(1, 2)
        x = F.relu(self.bn0(self.stem(x)))
        x = self.blocks(x)
        return self.head(x).transpose(1, 2).log_softmax(-1)

    @staticmethod
    def out_len(n_frames):
        return (n_frames - 2) // 2 + 1  # stem: k=5, stride 2, left pad 3

    def n_params(self):
        return sum(p.numel() for p in self.parameters())
