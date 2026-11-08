"""Source manual manifest: download, ingest, hash-pin and check distribution statements.

Every manual the library quotes must be listed in sources.yaml with its edition and
where it came from. A PDF is only usable once its sha256 is pinned in the manifest,
so a later build can't silently pick up a different printing.
"""
from __future__ import annotations

import hashlib
import re
import shutil
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import yaml

PUBLIC_RELEASE = "approved for public release"
# US Government works with no distribution statement printed (older or civilian titles) are
# public domain under 17 USC 105; they are allowed once someone has checked the cover.
US_GOV_WORK = "17 usc 105"


@dataclass
class Manual:
    id: str
    title: str
    edition: str
    file: str
    urls: list[str] = field(default_factory=list)
    sha256: str | None = None
    distribution: str = "unverified"
    raw: dict = field(default_factory=dict)

    def path(self, root: Path) -> Path:
        return root / self.file


PERSONAL_MANIFEST = "sources.personal.yaml"   # gitignored: personal-use copies, never shipped
PERSONAL_EXTRACT = "extract_personal"          # build/<this>/<id>, kept apart from build/extract


def load_manifest(path: Path, personal: bool = False) -> list[Manual]:
    """sources.yaml, plus sources.personal.yaml next to it when `personal` is set. Personal
    entries are copyrighted books Charlie keeps for his own device only: they are read only
    for a --personal build and must be marked `personal: true`."""
    data = yaml.safe_load(path.read_text())
    entries = list(data["manuals"])
    ppath = path.parent / PERSONAL_MANIFEST
    if personal and ppath.exists():
        for m in (yaml.safe_load(ppath.read_text()) or {}).get("manuals") or []:
            if m.get("personal") is not True:
                raise SystemExit(f"{ppath}: {m.get('id')} must be marked personal: true")
            entries.append(m)
    out = []
    for m in entries:
        out.append(Manual(id=m["id"], title=m["title"], edition=str(m["edition"]),
                          file=m["file"], urls=m.get("urls") or [], sha256=m.get("sha256"),
                          distribution=str(m.get("distribution", "unverified")), raw=m))
    return out


def save_manifest(path: Path, manuals: list[Manual]) -> None:
    data = yaml.safe_load(path.read_text())
    by_id = {m.id: m for m in manuals}
    for m in data["manuals"]:
        mm = by_id[m["id"]]
        m["sha256"] = mm.sha256
        m["distribution"] = mm.distribution
    path.write_text(yaml.safe_dump(data, sort_keys=False, width=100))


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def detect_distribution(pdf: Path, pages: int = 4) -> str | None:
    """Return the distribution sentence from the first few pages, if one is printed."""
    import pdfplumber

    pat = re.compile(r"(approved for public release[^.]*\.(?:\s*distribution is unlimited\.)?)", re.I)
    with pdfplumber.open(pdf) as doc:
        for page in doc.pages[:pages]:
            m = pat.search(" ".join((page.extract_text() or "").split()))
            if m:
                return m.group(1)
    return None


def ingest(root: Path, manifest: Path, manual_id: str, src: Path) -> Manual:
    """Copy a PDF the user supplied into sources/, pin its hash and read its distribution line."""
    manuals = load_manifest(manifest)
    m = next((x for x in manuals if x.id == manual_id), None)
    if m is None:
        raise SystemExit(f"{manual_id} is not in {manifest}")
    dst = m.path(root)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.resolve() != dst.resolve():
        shutil.copyfile(src, dst)
    digest = sha256_file(dst)
    if m.sha256 and m.sha256 != digest:
        raise SystemExit(f"{manual_id}: sha256 {digest} does not match pinned {m.sha256}")
    m.sha256 = digest
    dist = detect_distribution(dst)
    m.distribution = dist or "NOT FOUND - check the cover by hand"
    save_manifest(manifest, manuals)
    return m


def fetch(root: Path, manifest: Path, manual_id: str | None = None, timeout: int = 60) -> list[str]:
    """Try each URL in order; first success is ingested. Returns a log of what happened."""
    log = []
    for m in load_manifest(manifest):
        if manual_id and m.id != manual_id:
            continue
        if m.path(root).exists() and m.sha256:
            log.append(f"{m.id}: already present")
            continue
        for url in m.urls:
            if not url.lower().endswith(".pdf"):
                continue
            tmp = m.path(root).with_suffix(".part")
            tmp.parent.mkdir(parents=True, exist_ok=True)
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "plb-builder/0.1"})
                with urllib.request.urlopen(req, timeout=timeout) as r, tmp.open("wb") as f:
                    shutil.copyfileobj(r, f)
                if tmp.read_bytes()[:5] != b"%PDF-":
                    raise ValueError("not a PDF")
                ingest(root, manifest, m.id, tmp)
                tmp.unlink()
                log.append(f"{m.id}: downloaded from {url}")
                break
            except Exception as e:  # noqa: BLE001 - report and try the next mirror
                log.append(f"{m.id}: {url} failed ({e})")
                tmp.unlink(missing_ok=True)
    return log


def verify(root: Path, manifest: Path, personal: bool = False) -> list[str]:
    """Problems that block a release build (missing files, unpinned or changed hashes)."""
    problems = []
    for m in load_manifest(manifest, personal):
        p = m.path(root)
        if not p.exists():
            continue  # manuals are optional until a tree cites them
        for pg in m.raw.get("pages") or []:          # web manuals: every saved page is pinned
            pp = root / pg["file"]
            if not pp.exists():
                problems.append(f"{m.id}: page {pg['file']} missing")
            elif sha256_file(pp) != pg.get("sha256"):
                problems.append(f"{m.id}: page {pg['file']} changed since it was pinned")
        if not m.sha256:
            problems.append(f"{m.id}: hash not pinned (run plb ingest)")
        elif sha256_file(p) != m.sha256:
            problems.append(f"{m.id}: file changed since it was pinned")
        dist = m.distribution.lower()
        if PUBLIC_RELEASE not in dist and "unlimited" not in dist and "no releasability" not in dist \
                and US_GOV_WORK not in dist:
            problems.append(f"{m.id}: distribution not confirmed public release ({m.distribution})")
    return problems
