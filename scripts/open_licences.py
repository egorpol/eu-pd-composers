"""Shared open-licence column names and work-page derivation helpers.

Work cells come from cached IMSLP wikitext (`imslp_licences` block parsing).
Composer cells come from the hand-curated `data/overrides/open_catalogues.tsv`
registry (applied by `remap_open_licences.py`).
"""

from __future__ import annotations

import re
from typing import Any

from common import pipe_join
from imslp_licences import (
    _FILE_NAME_RE,
    file_formats,
    is_open_licence,
    iter_blocks,
    licence_id,
)

# Works dump columns (after imslp_librettists / other imslp_* evidence).
OPEN_WORK_COLUMNS = (
    "imslp_open_licences",
    "imslp_open_scope",
    "imslp_open_formats",
)

# Composers dump columns (rights axis, after years_until_eu_pd).
OPEN_COMPOSER_COLUMNS = (
    "open_catalogue_url",
    "open_catalogue_licence",
)

EMPTY_OPEN_WORK = {col: "" for col in OPEN_WORK_COLUMNS}
EMPTY_OPEN_COMPOSER = {col: "" for col in OPEN_COMPOSER_COLUMNS}

_NEW_COMPOSITION = re.compile(r"^new\s+composition$", re.I)


def _norm_ws(raw: str) -> str:
    return re.sub(r"\s+", " ", (raw or "").strip())


def _is_composer_arranger(raw: str) -> bool:
    """True when Arranger is blank or literally 'Composer' (IMSLP's own-arrangement marker)."""
    s = _norm_ws(raw)
    return not s or s.casefold() == "composer"


def is_composition_scope_block(fields: dict[str, str], *, eu_pd_status: str) -> bool:
    """Whether one open score block may mark the work's music (not just the edition) as open.

    Needs Image Type "New Composition", a non-`pd` composer, and no third-party Arranger.
    """
    if (eu_pd_status or "").strip() == "pd":
        return False
    if not _NEW_COMPOSITION.match(_norm_ws(fields.get("Image Type", ""))):
        return False
    return _is_composer_arranger(fields.get("Arranger", ""))


def open_work_cells(wikitext: str, *, eu_pd_status: str = "") -> dict[str, str]:
    """Derive the three open-licence work cells from page wikitext.

    Only score blocks (`{{#fte:imslpfile}}`) whose licence `is_open_licence` count.
    Audio blocks are ignored. Scope is `composition` when at least one open score block
    has Image Type "New Composition", the work's composer is not EU-`pd`, and Arranger is
    empty or "Composer"; otherwise `edition` when open score blocks exist. Uploaders often
    misuse "New Composition" for typesets/arrangements of public-domain works.
    """
    licences: set[str] = set()
    formats: set[str] = set()
    composition_scope = False
    n_open = 0

    for block in iter_blocks(wikitext or ""):
        if block["kind"] != "score":
            continue
        fields = block["fields"]
        names = [v for k, v in fields.items() if _FILE_NAME_RE.match(k) and v]
        lid = licence_id(fields.get("Copyright", ""))
        if not is_open_licence(lid):
            continue
        n_open += 1
        licences.add(lid)
        formats.update(file_formats(names))
        if is_composition_scope_block(fields, eu_pd_status=eu_pd_status):
            composition_scope = True

    if not n_open:
        return dict(EMPTY_OPEN_WORK)

    scope = "composition" if composition_scope else "edition"
    return {
        "imslp_open_licences": pipe_join(sorted(licences)),
        "imslp_open_scope": scope,
        "imslp_open_formats": pipe_join(sorted(formats)),
    }


def open_score_block_summary(wikitext: str, *, eu_pd_status: str = "") -> dict[str, Any]:
    """Debug/test helper: open score blocks with licence, image type and formats."""
    rows = []
    for block in iter_blocks(wikitext or ""):
        if block["kind"] != "score":
            continue
        fields = block["fields"]
        names = [v for k, v in fields.items() if _FILE_NAME_RE.match(k) and v]
        lid = licence_id(fields.get("Copyright", ""))
        if not is_open_licence(lid):
            continue
        rows.append(
            {
                "licence": lid,
                "image_type": fields.get("Image Type", ""),
                "arranger": fields.get("Arranger", ""),
                "formats": file_formats(names),
            }
        )
    return {"blocks": rows, "cells": open_work_cells(wikitext, eu_pd_status=eu_pd_status)}
