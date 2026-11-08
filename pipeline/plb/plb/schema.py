"""Pydantic schemas for the hand-written content files, so a malformed file fails early.

These check *shape*: required keys, types, and spelling of every key (a typo such as
`cites:` or `choises:` used to be silently ignored, leaving a node with no citation or
no branches). The content rules that need the extracted manuals (verbatim citations,
figures, tag vocabulary, label length, the "nothing" fallback, mushrooms) stay in
trees.py / check.py, because they need the manuals, which CI does not have.

    plb validate          check content/ without building (no PDFs or extracts needed)

Files covered:
    content/trees/*.yaml          TreeFile
    content/config.yaml           ConfigFile
    content/contexts.yaml         ContextsFile
    content/strings.yaml          StringsFile
    content/phrases/expect.yaml   ExpectFile
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal, Union

import yaml
from pydantic import (BaseModel, ConfigDict, Field, RootModel, ValidationError,
                      field_validator, model_validator)

from .trees import Issue

# A citation reference: MANUAL_ID/PARA, e.g. ATP4-02.11/4-37, FEMA-CERT/114.2, ATP3-50.21/2.h64
REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")
FIG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/(fig|img)/[A-Za-z0-9._-]+$")
KEY = re.compile(r"^[a-z0-9][a-z0-9_-]*$")      # tree ids and node keys
NodeKey = str
Kind = Literal["kit", "improvised", "nothing", "next"]
Shelf = Literal["first-aid", "survival", "bushcraft", "plants", "fire", "traps", "weather"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


def _check_ref(v: str) -> str:
    if not REF.match(v):
        raise ValueError(f"citation {v!r} should look like MANUAL/PARA, e.g. ATP4-02.11/4-37")
    return v


class Excerpt(_Strict):
    ref: str
    from_: str | None = Field(None, alias="from")
    to: str | None = None

    @field_validator("ref")
    @classmethod
    def _ref(cls, v):
        return _check_ref(v)

    @field_validator("from_", "to")
    @classmethod
    def _not_blank(cls, v):
        if v is not None and not v.strip():
            raise ValueError("excerpt markers can't be blank; leave the key out instead")
        return v


class Cite(RootModel[Union[str, Excerpt]]):
    @field_validator("root")
    @classmethod
    def _ref(cls, v):
        return _check_ref(v) if isinstance(v, str) else v


class SayAlways(_Strict):
    say: str = Field(min_length=1)
    always: bool = False


Phrase = Union[str, SayAlways]


def _tags(v: Any):
    if v is None:
        return v
    if not isinstance(v, dict):
        raise ValueError("tags must be a map like {region: [wa, or]}")
    for dim, vals in v.items():
        if dim not in ("region", "season", "activity"):
            raise ValueError(f"unknown tag dimension {dim!r} (region, season or activity)")
        vals = [vals] if isinstance(vals, str) else vals
        if not isinstance(vals, list) or not all(isinstance(x, str) for x in vals):
            raise ValueError(f"tags.{dim} must be a list of names")
    return v


class Choice(_Strict):
    label: str = Field(min_length=1)
    to: NodeKey
    kind: Kind = "next"


class Node(_Strict):
    label: str = Field(min_length=1)
    cite: list[Cite] = []
    figure: str | None = None
    phrases: list[Phrase] = []
    tags: dict[str, Any] | None = None
    emergency: bool = False
    choices: list[Choice] = []
    lookalikes: list[Cite] = []
    species: bool = False
    no_match: bool = False

    @field_validator("tags", mode="before")
    @classmethod
    def _tag_map(cls, v):
        return _tags(v)

    @field_validator("figure")
    @classmethod
    def _fig(cls, v):
        if v is not None and not FIG.match(v):
            raise ValueError(f"figure {v!r} should look like MANUAL/fig/N-N or MANUAL/img/pN-N")
        return v

    @model_validator(mode="after")
    def _body(self):
        if not self.cite and not self.no_match:
            raise ValueError("no citation: every node needs `cite:` (or `no_match: true`)")
        if self.species and not self.lookalikes:
            raise ValueError("`species: true` needs `lookalikes:` citations")
        if self.species and self.no_match:
            raise ValueError("a leaf is either `species` or `no_match`, not both")
        return self


class TreeFile(_Strict):
    id: str
    title: str = Field(min_length=1)
    shelf: Shelf
    root: NodeKey
    emergency: bool = False
    tags: dict[str, Any] | None = None
    phrases: list[Phrase] = []
    nodes: dict[NodeKey, Node] = Field(min_length=1)

    @field_validator("tags", mode="before")
    @classmethod
    def _tag_map(cls, v):
        return _tags(v)

    @field_validator("id")
    @classmethod
    def _id(cls, v):
        if not KEY.match(v):
            raise ValueError(f"tree id {v!r} must be lowercase letters, digits, - or _")
        return v

    @field_validator("nodes")
    @classmethod
    def _keys(cls, v):
        bad = [k for k in v if not KEY.match(str(k))]
        if bad:
            raise ValueError(f"node keys must be lowercase letters, digits, - or _: {bad}")
        return v

    @model_validator(mode="after")
    def _links(self):
        if self.root not in self.nodes:
            raise ValueError(f"root {self.root!r} is not a node")
        for k, n in self.nodes.items():
            for c in n.choices:
                if c.to not in self.nodes:
                    raise ValueError(f"node {k!r}: choice goes to unknown node {c.to!r}")
        return self


class ConfigFile(_Strict):
    home_tree: str | None = None
    sleep_picture: str | None = None
    reference_shelf: bool = False
    library_groups: dict[str, list[str]] | None = None


class _Dim(RootModel[list[Union[str, dict[str, Any]]]]):
    @field_validator("root")
    @classmethod
    def _uniq(cls, v):
        ids = [x["id"] if isinstance(x, dict) else x for x in v]
        if any(not isinstance(i, str) for i in ids):
            raise ValueError("each value is a name, or a map with an `id`")
        dup = sorted({i for i in ids if ids.count(i) > 1})
        if dup:
            raise ValueError(f"duplicate values {dup}")
        return v


class ContextsFile(_Strict):
    region: _Dim | None = None
    season: _Dim | None = None
    activity: _Dim | None = None
    groups: dict[str, list[str]] = {}


class StringsFile(RootModel[dict[str, str]]):
    @field_validator("root")
    @classmethod
    def _ascii(cls, v):
        for k, s in v.items():
            if not re.match(r"^[A-Z][A-Z0-9_]*$", k):
                raise ValueError(f"string id {k!r} must be UPPER_SNAKE_CASE")
            if any(not (32 <= ord(c) < 127) for c in s):
                raise ValueError(f"{k}: device strings must be plain ASCII")
        return v


class ExpectFile(RootModel[dict[str, str]]):
    @field_validator("root")
    @classmethod
    def _targets(cls, v):
        for spoken, want in v.items():
            if not re.match(r"^[a-z0-9_-]+/[a-z0-9_-]+$", want):
                raise ValueError(f"{spoken!r}: target {want!r} should be tree/node")
        return v


def _issues(err: ValidationError, where: str) -> list[Issue]:
    out = []
    for e in err.errors():
        loc = ".".join(str(x) for x in e["loc"] if x not in ("root",) and not str(x).startswith(
            ("str", "Excerpt", "SayAlways", "function-")))
        msg = e["msg"].removeprefix("Value error, ")
        if e["type"] == "extra_forbidden":
            msg = "unknown key (typo?)"
        out.append(Issue("error", f"{where}{(' ' + loc) if loc else ''}", msg))
    return out


def validate_tree(data: Any, where: str) -> list[Issue]:
    try:
        TreeFile.model_validate(data)
    except ValidationError as e:
        return _issues(e, where)
    return []


FILES = {"config.yaml": ConfigFile, "contexts.yaml": ContextsFile,
         "strings.yaml": StringsFile, "phrases/expect.yaml": ExpectFile}


def validate_content(content: Path, trees: bool = True) -> list[Issue]:
    """Schema-check every content file. Needs no PDFs or extracts."""
    issues: list[Issue] = []
    for rel, model in FILES.items():
        p = content / rel
        if not p.exists():
            continue
        try:
            data = yaml.safe_load(p.read_text())
        except yaml.YAMLError as e:
            issues.append(Issue("error", rel, f"not valid YAML: {e}"))
            continue
        if data is None:
            continue
        try:
            model.model_validate(data)
        except ValidationError as e:
            issues += _issues(e, rel)
    files = sorted((content / "trees").glob("*.yaml")) if trees else []
    ids: dict[str, str] = {}
    for p in files:
        where = f"trees/{p.name}"
        try:
            data = yaml.safe_load(p.read_text())
        except yaml.YAMLError as e:
            issues.append(Issue("error", where, f"not valid YAML: {e}"))
            continue
        if data is None:
            continue
        issues += validate_tree(data, where)
        tid = data.get("id") if isinstance(data, dict) else None
        if tid in ids:
            issues.append(Issue("error", where, f"tree id {tid!r} is also used by {ids[tid]}"))
        elif tid:
            ids[tid] = where
    return issues
