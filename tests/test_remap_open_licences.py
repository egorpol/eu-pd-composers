"""Tests for open-licence work/composer remap (offline, no network)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

import common
import open_licences as ol
import remap_open_licences as remap
from open_licences import OPEN_COMPOSER_COLUMNS, OPEN_WORK_COLUMNS

NEW_COMPOSITION = """===Scores===
{{#fte:imslpfile
|File Name 1=PMLP1-score.pdf
|File Name 2=PMLP1-score.mxl
|Image Type=New Composition
|Copyright=Creative Commons Attribution-ShareAlike 4.0
}}
"""

TYPESET = """===Scores===
{{#fte:imslpfile
|File Name 1=PMLP2-score.pdf
|Image Type=Typeset
|Copyright=Creative Commons Attribution 4.0
}}
"""

NEW_ARRANGEMENT = """===Scores===
{{#fte:imslpfile
|File Name 1=PMLP3-arr.pdf
|File Name 2=PMLP3-arr.mid
|Image Type=New Arrangement
|Copyright=CC0 1.0
}}
"""

PERFORMANCE_RESTRICTED = """===Scores===
{{#fte:imslpfile
|File Name 1=PMLP4-score.pdf
|Image Type=Typeset
|Copyright=Performance Restricted Attribution-NonCommercial-NoDerivs 3.0
}}
"""

AUDIO_ONLY_OPEN = """===Recordings===
{{#fte:imslpaudio
|File Name 1=PMLP5-a.mp3
|Copyright=Creative Commons Attribution 3.0
}}
===Scores===
{{#fte:imslpfile
|File Name 1=PMLP5-score.pdf
|Image Type=Normal Scan
|Copyright=Public Domain
}}
"""

MIXED = """===Scores===
{{#fte:imslpfile
|File Name 1=PMLP6-a.pdf
|Image Type=Typeset
|Copyright=Creative Commons Attribution 4.0
}}
{{#fte:imslpfile
|File Name 1=PMLP6-b.pdf
|Image Type=  New Composition
|Copyright=Creative Commons Attribution-NonCommercial 4.0
}}
"""

NEW_COMPOSITION_THIRD_PARTY_ARRANGER = """===Scores===
{{#fte:imslpfile
|File Name 1=PMLP7-score.pdf
|Image Type=New Composition
|Arranger={{LinkArr|Hee Cheol|Kim}}
|Copyright=Creative Commons Attribution-ShareAlike 4.0
}}
"""

NEW_COMPOSITION_ARRANGER_COMPOSER = """===Scores===
{{#fte:imslpfile
|File Name 1=PMLP8-score.pdf
|Image Type=New Composition
|Arranger=Composer
|Copyright=Creative Commons Attribution 4.0
}}
"""


def test_scope_new_composition():
    cells = ol.open_work_cells(NEW_COMPOSITION, eu_pd_status="not_pd")
    assert cells["imslp_open_scope"] == "composition"
    assert cells["imslp_open_licences"] == "CC-BY-SA-4.0"
    assert cells["imslp_open_formats"] == "musicxml|pdf"


def test_scope_new_composition_on_pd_composer_is_edition():
    cells = ol.open_work_cells(NEW_COMPOSITION, eu_pd_status="pd")
    assert cells["imslp_open_scope"] == "edition"
    assert cells["imslp_open_licences"] == "CC-BY-SA-4.0"


def test_scope_new_composition_third_party_arranger_is_edition():
    cells = ol.open_work_cells(
        NEW_COMPOSITION_THIRD_PARTY_ARRANGER, eu_pd_status="not_pd",
    )
    assert cells["imslp_open_scope"] == "edition"


def test_scope_new_composition_arranger_composer_is_composition():
    cells = ol.open_work_cells(
        NEW_COMPOSITION_ARRANGER_COMPOSER, eu_pd_status="not_pd",
    )
    assert cells["imslp_open_scope"] == "composition"


def test_scope_typeset_is_edition():
    cells = ol.open_work_cells(TYPESET, eu_pd_status="not_pd")
    assert cells["imslp_open_scope"] == "edition"
    assert cells["imslp_open_licences"] == "CC-BY-4.0"
    assert cells["imslp_open_formats"] == "pdf"


def test_scope_new_arrangement_is_edition():
    cells = ol.open_work_cells(NEW_ARRANGEMENT, eu_pd_status="not_pd")
    assert cells["imslp_open_scope"] == "edition"
    assert cells["imslp_open_licences"] == "CC0-1.0"
    assert cells["imslp_open_formats"] == "midi|pdf"


def test_performance_restricted_cc_ignored():
    assert ol.open_work_cells(PERFORMANCE_RESTRICTED) == ol.EMPTY_OPEN_WORK


def test_audio_open_licence_ignored():
    assert ol.open_work_cells(AUDIO_ONLY_OPEN) == ol.EMPTY_OPEN_WORK


def test_mixed_new_composition_wins_scope():
    cells = ol.open_work_cells(MIXED, eu_pd_status="unknown_death")
    assert cells["imslp_open_scope"] == "composition"
    assert cells["imslp_open_licences"] == "CC-BY-4.0|CC-BY-NC-4.0"


def _registry_tsv(path: Path, rows: list[str] | None = None) -> Path:
    header = "\t".join(remap.REGISTRY_COLUMNS)
    body = "\n".join(rows or [])
    path.write_text(header + ("\n" + body + "\n" if body else "\n"), encoding="utf-8")
    return path


def _seed_row(
    composer_id="Q1795042",
    name="Kyle Gann",
    url="http://www.kylegann.com/Gannmusic.html",
    licence="CC-BY-NC-3.0",
) -> str:
    return "\t".join(
        [
            composer_id,
            name,
            url,
            licence,
            "composer",
            "176",
            url,
            "This work is licensed under a Creative Commons Attribution-Noncommercial 3.0 License.",
            "2026-10-11",
            "seed",
        ]
    )


def test_registry_validation_errors(tmp_path):
    path = _registry_tsv(
        tmp_path / "bad.tsv",
        [
            _seed_row(),
            _seed_row(),  # duplicate
            _seed_row("Q9", "X", "ftp://example.com/x", "AllRightsReserved"),
        ],
    )
    with pytest.raises(ValueError, match="duplicate composer_id|not an open licence|must be http"):
        remap.load_registry(path)


def test_registry_not_in_dump_is_warning_not_error(tmp_path, monkeypatch):
    registry = remap.load_registry(
        _registry_tsv(tmp_path / "reg.tsv", [_seed_row("Q999", "Ghost")])
    )
    composers = pd.DataFrame(
        [{"composer_id": "Q1", "name_display": "A", "years_until_eu_pd": "0", "dump_date": "r001"}]
    )
    works = pd.DataFrame(
        [
            {
                "work_id": "W1",
                "composer_id": "Q1",
                "imslp_pageid": "1",
                "title": "T",
                "composition_year": "",
                "imslp_librettists": "",
            }
        ]
    )
    monkeypatch.setattr(remap, "cache_get", lambda *a, **k: None)
    c_out, w_out, report = remap.remap_open_licences(composers, works, registry)
    assert report["registry_not_in_dump"] == ["Q999"]
    assert report["composers_with_open_catalogue"] == 0
    assert c_out.loc[0, "open_catalogue_url"] == ""


def test_uncached_keeps_prior(tmp_path, monkeypatch):
    registry = remap.load_registry(_registry_tsv(tmp_path / "reg.tsv"))
    composers = pd.DataFrame([{"composer_id": "Q1", "name_display": "A", "years_until_eu_pd": ""}])
    works = pd.DataFrame(
        [
            {
                "work_id": "W1",
                "composer_id": "Q1",
                "imslp_pageid": "99",
                "title": "Prior",
                "composition_year": "",
                "imslp_librettists": "",
                "imslp_open_licences": "CC-BY-4.0",
                "imslp_open_scope": "edition",
                "imslp_open_formats": "pdf",
            },
            {
                "work_id": "W2",
                "composer_id": "Q1",
                "imslp_pageid": "1",
                "title": "Redirect",
                "composition_year": "",
                "imslp_librettists": "",
                "imslp_open_licences": "CC0-1.0",
                "imslp_open_scope": "composition",
                "imslp_open_formats": "pdf",
            },
        ]
    )

    def cache_get(namespace, key):
        if key == "1":
            return {"pageid": 1, "redirect": True, "wikitext": ""}
        return None

    monkeypatch.setattr(remap, "cache_get", cache_get)
    monkeypatch.setattr(common, "cache_get", cache_get)
    _, out, report = remap.remap_open_licences(composers, works, registry)
    assert out.loc[0, "imslp_open_licences"] == "CC-BY-4.0"
    assert out.loc[0, "imslp_open_scope"] == "edition"
    assert out.loc[1, "imslp_open_licences"] == ""
    assert out.loc[1, "imslp_open_scope"] == ""
    assert report["kept_prior"] == 1
    assert report["missing_cache"] == 1
    assert report["redirect_or_missing"] == 1


def test_parsed_page_and_composer_catalogue(tmp_path, monkeypatch):
    registry = remap.load_registry(
        _registry_tsv(tmp_path / "reg.tsv", [_seed_row("Q1", "Composer")])
    )
    composers = pd.DataFrame(
        [
            {
                "composer_id": "Q1",
                "name_display": "Composer",
                "years_until_eu_pd": "0",
                "eu_pd_status": "not_pd",
            }
        ]
    )
    works = pd.DataFrame(
        [
            {
                "work_id": "W1",
                "composer_id": "Q1",
                "imslp_pageid": "10",
                "title": "Open",
                "composition_year": "2000",
                "imslp_librettists": "",
            }
        ]
    )

    def cache_get(namespace, key):
        assert namespace == "imslp_page_wikitext"
        return {"pageid": 10, "wikitext": NEW_COMPOSITION}

    monkeypatch.setattr(remap, "cache_get", cache_get)
    c_out, w_out, report = remap.remap_open_licences(composers, works, registry)
    assert w_out.loc[0, "imslp_open_scope"] == "composition"
    assert list(w_out.columns)[
        list(w_out.columns).index("imslp_librettists") + 1 :
    ][:3] == list(OPEN_WORK_COLUMNS)
    assert c_out.loc[0, "open_catalogue_url"].startswith("http")
    assert c_out.loc[0, "open_catalogue_licence"] == "CC-BY-NC-3.0"
    assert list(c_out.columns)[
        list(c_out.columns).index("years_until_eu_pd") + 1 :
    ][:2] == list(OPEN_COMPOSER_COLUMNS)
    assert report["works_with_open_licences"] == 1
    assert report["composers_with_open_catalogue"] == 1
    assert report["parsed"] == 1


def test_preserve_schema_strips_new_columns(tmp_path, monkeypatch):
    registry = remap.load_registry(_registry_tsv(tmp_path / "reg.tsv"))
    composers = pd.DataFrame([{"composer_id": "Q1", "name_display": "A"}])
    works = pd.DataFrame(
        [{"work_id": "W1", "composer_id": "Q1", "imslp_pageid": "10", "title": "T"}]
    )
    monkeypatch.setattr(
        remap, "cache_get", lambda *a, **k: {"pageid": 10, "wikitext": TYPESET}
    )
    c_out, w_out, _ = remap.remap_open_licences(composers, works, registry)
    assert set(OPEN_WORK_COLUMNS) <= set(w_out.columns)
    assert set(OPEN_COMPOSER_COLUMNS) <= set(c_out.columns)
    # Simulate --preserve-schema reindex
    w_stripped = w_out.reindex(columns=works.columns)
    c_stripped = c_out.reindex(columns=composers.columns)
    assert "imslp_open_licences" not in w_stripped.columns
    assert "open_catalogue_url" not in c_stripped.columns


def test_column_tuples_shared_with_pipeline():
    import pipeline

    assert OPEN_WORK_COLUMNS == pipeline.OPEN_WORK_COLUMNS
    assert OPEN_COMPOSER_COLUMNS == pipeline.OPEN_COMPOSER_COLUMNS
