"""Tests for scripts/export_viewer_json.py (synthetic dumps only)."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

import export_viewer_json as exporter


def _write_dump(data_dir: Path, dump_id: str, composers: pd.DataFrame, works: pd.DataFrame):
    data_dir.mkdir(parents=True, exist_ok=True)
    composers.to_csv(data_dir / f"composers_{dump_id}.tsv", sep="\t", index=False)
    works.to_csv(data_dir / f"works_{dump_id}.tsv", sep="\t", index=False)


def _base_composer(**overrides):
    row = {
        "composer_id": "Q1",
        "name_display": "Alice",
        "name_sort": "Alice",
        "name_aliases": "",
        "birth_year": "1900",
        "death_year": "1950",
        "wikipedia_url": "",
        "wikidata_url": "",
        "citizenship_iso": "DE",
        "scope_class": "classical_core",
        "is_film_composer": "false",
        "eu_pd_year": "2021",
        "eu_pd_status": "pd",
        "style_tags": "serialism",
        "style_tags_src": "wikidata",
        "imslp_url": "",
        "imslp_match_status": "matched",
        "work_categories_present": "piano_solo",
        "works_count_total": "1",
        "pageviews_enwiki": "100",
        "pageviews_window": "2025-01..2025-12",
    }
    row.update(overrides)
    return row


def _base_work(**overrides):
    row = {
        "work_id": "W1",
        "composer_id": "Q1",
        "title": "Sonata",
        "imslp_work_url": "https://imslp.org/wiki/Sonata",
        "force_family": "piano_solo",
        "force_family_src": "imslp_tags",
    }
    row.update(overrides)
    return row


@pytest.fixture
def patched_paths(tmp_path, monkeypatch):
    data = tmp_path / "data"
    out = tmp_path / "viewer" / "data"
    monkeypatch.setattr(exporter, "DATA", data)
    monkeypatch.setattr(exporter, "OUT", out)

    def fake_meta(dump_id: str):
        return data / f"dump_meta_{dump_id}.json"

    monkeypatch.setattr(exporter, "dump_meta_path", fake_meta)
    return data, out


def test_export_evidence_keys_and_film_and_null_views(patched_paths):
    data, out = patched_paths
    composers = pd.DataFrame(
        [
            _base_composer(
                composer_id="Q1",
                is_film_composer="true",
                pageviews_enwiki="",
                works_count_total="2",
                work_categories_present="piano_solo|orchestral",
            ),
            _base_composer(
                composer_id="Q2",
                name_display="Bob",
                name_sort="Bob",
                is_film_composer="false",
                pageviews_enwiki="0",
                eu_pd_status="not_pd",
                eu_pd_year="2050",
                work_categories_present="chamber",
                works_count_total="1",
            ),
            _base_composer(
                composer_id="Q3",
                name_display="Carol",
                name_sort="Carol",
                is_film_composer="TRUE",
                pageviews_enwiki="42",
                work_categories_present="",
                works_count_total="0",
            ),
        ]
    )
    works = pd.DataFrame(
        [
            _base_work(
                work_id="W1",
                composer_id="Q1",
                imslp_style="Romantic|Early 20th century",
                imslp_first_published="1926",
                imslp_copyright_flags="nonpd_eu|nonpd_us",
                imslp_librettists="Someone",
                has_files="true",
                imslp_file_hosts="ca|us",
            ),
            _base_work(
                work_id="W2",
                composer_id="Q1",
                title="Empty evidence",
                force_family="orchestral",
                force_family_src="llm",
                imslp_style="",
                imslp_first_published="",
                imslp_copyright_flags="",
                imslp_librettists="",
                has_files="false",
                imslp_file_hosts="",
            ),
            _base_work(
                work_id="W3",
                composer_id="Q2",
                title="Quartet",
                force_family="chamber",
                force_family_src="imslp_geninfo",
                imslp_style="Baroque",
                imslp_first_published="1900",
                imslp_copyright_flags="pd_eu_rost",
                has_files="true",
                imslp_file_hosts="ca",
            ),
        ]
    )
    _write_dump(data, "r900", composers, works)

    exporter.export("r900", out)

    c_rows = json.loads((out / "composers.json").read_text(encoding="utf-8"))
    w_map = json.loads((out / "works_by_composer.json").read_text(encoding="utf-8"))
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))

    by_id = {c["id"]: c for c in c_rows}
    assert by_id["Q1"]["film"] is True
    assert by_id["Q1"]["views"] is None
    assert "film" not in by_id["Q2"]
    assert by_id["Q2"]["views"] == 0
    assert by_id["Q3"]["film"] is True
    assert by_id["Q3"]["views"] == 42

    w1 = next(w for w in w_map["Q1"] if w["t"] == "Sonata")
    assert w1["st"] == ["Romantic", "Early 20th century"]
    assert w1["fp"] == 1926
    assert w1["cf"] == ["nonpd_eu", "nonpd_us"]
    assert "librettists" not in w1
    assert w1["fh"] == ["ca", "us"]
    assert "hf" not in w1
    assert "imslp_librettists" not in w1

    w2 = next(w for w in w_map["Q1"] if w["t"] == "Empty evidence")
    assert "st" not in w2
    assert "fp" not in w2
    assert "cf" not in w2
    assert w2["hf"] is False
    assert "fh" not in w2

    w3 = w_map["Q2"][0]
    assert "hf" not in w3
    assert "fh" not in w3

    assert manifest["pageviews_window_label"] == "2025"
    assert list(manifest["facets"]["imslp_style"].items()) == [
        ("Baroque", 1),
        ("Early 20th century", 1),
        ("Romantic", 1),
    ]
    assert manifest["facets"]["force_family_src"] == [
        "imslp_geninfo",
        "imslp_tags",
        "llm",
    ]


def test_export_without_evidence_columns(patched_paths):
    data, out = patched_paths
    composers = pd.DataFrame([_base_composer()])
    works = pd.DataFrame([_base_work()])
    _write_dump(data, "r012like", composers, works)

    exporter.export("r012like", out)

    w_map = json.loads((out / "works_by_composer.json").read_text(encoding="utf-8"))
    work = w_map["Q1"][0]
    assert set(work) == {"t", "u", "f", "s"}
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["facets"]["imslp_style"] == {}
    assert manifest["facets"]["force_family_src"] == ["imslp_tags"]
    assert manifest["pageviews_window_label"] == "2025"


def test_pageviews_window_label_helper():
    assert exporter._pageviews_window_label(["2025-01..2025-12"] * 3) == "2025"
    assert exporter._pageviews_window_label([]) == ""
    assert exporter._pageviews_window_label(["2024-07..2025-06"]) == "2024-07..2025-06"


def test_imslp_style_alias_merge_and_style_tags_coverage(patched_paths):
    data, out = patched_paths
    composers = pd.DataFrame(
        [
            _base_composer(
                composer_id="Q1",
                style_tags="impressionism",
                style_tags_src="wikidata",
                works_count_total="2",
            ),
            _base_composer(
                composer_id="Q2",
                name_display="Bob",
                name_sort="Bob",
                style_tags="minimalism",
                style_tags_src="llm_luna_xhigh",
                llm_style_tags="minimalism|romantic",
                llm_style_period="romantic",
                llm_style_votes="gpt-6.1-sol@low=minimalism|romantic;grok-4.7-low=romantic|minimalism",
                works_count_total="1",
            ),
            _base_composer(
                composer_id="Q3",
                name_display="Carol",
                name_sort="Carol",
                style_tags="",
                style_tags_src="",
                works_count_total="0",
            ),
        ]
    )
    works = pd.DataFrame(
        [
            _base_work(
                work_id="W1",
                composer_id="Q1",
                imslp_style="Romántico",
            ),
            _base_work(
                work_id="W2",
                composer_id="Q1",
                title="Folk",
                imslp_style="Traditional|Traditional (folk)",
            ),
            _base_work(
                work_id="W3",
                composer_id="Q2",
                title="Other",
                imslp_style="Romantic",
            ),
        ]
    )
    _write_dump(data, "r901", composers, works)

    exporter.export("r901", out)

    w_map = json.loads((out / "works_by_composer.json").read_text(encoding="utf-8"))
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))

    romantico = next(w for w in w_map["Q1"] if w["t"] == "Sonata")
    assert romantico["st"] == ["Romantic"]

    folk = next(w for w in w_map["Q1"] if w["t"] == "Folk")
    assert folk["st"] == ["Traditional (folk)"]

    assert manifest["facets"]["imslp_style"] == {
        "Romantic": 2,
        "Traditional (folk)": 1,
    }
    assert "Romántico" not in manifest["facets"]["imslp_style"]
    assert "Traditional" not in manifest["facets"]["imslp_style"]

    assert manifest["style_tags_coverage"] == {
        "tagged": 2,
        "total": 3,
        "wikidata": 1,
        "llm": 1,
    }
    c_rows = json.loads((out / "composers.json").read_text())
    assert c_rows[1]["styles"] == [] and c_rows[1]["style_src"] == ""
    assert c_rows[1]["ls"] == ["minimalism", "Romantic"]
    assert c_rows[1]["lp"] == "Romantic"
    assert c_rows[1]["lv"] == [["minimalism", "Romantic"], ["Romantic", "minimalism"]]
    assert all(field not in c_rows[2] for field in ("ls", "lp", "lv"))
    assert manifest["llm_style_policy"]["condition"] == "grounded"
    assert [m["name"] for m in manifest["llm_style_policy"]["labellers"]] == ["GPT-6.1 Sol", "Grok 4.7"]


def test_votes_keep_abstention_missing_models_and_consensus_coverage(patched_paths):
    data, out = patched_paths
    composers = pd.DataFrame([
        _base_composer(composer_id="Q1", llm_style_tags="impressionism", llm_style_period="",
                       llm_style_votes="gpt-6.1-sol@low=impressionism;grok-4.7-low=impressionism"),
        _base_composer(composer_id="Q2", style_tags="", style_tags_src="", llm_style_tags="",
                       llm_style_votes="gpt-6.1-sol@low=romantic;grok-4.7-low="),
        _base_composer(composer_id="Q3", style_tags="", style_tags_src="", llm_style_tags="",
                       llm_style_votes="grok-4.7-low="),
    ])
    _write_dump(data, "r902", composers, pd.DataFrame([_base_work()]))
    exporter.export("r902", out)
    rows = json.loads((out / "composers.json").read_text())
    assert rows[1]["lv"] == [["Romantic"], []]
    assert rows[2]["lv"] == [None, []]
    assert "ls" not in rows[1] and "lp" not in rows[0]
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["style_tags_coverage"] == {"tagged": 1, "total": 3, "wikidata": 1, "llm": 1}


def test_llm_period_vocabulary_uses_canonical_imslp_spellings():
    assert exporter._llm_facet_value("romantic") == "Romantic"
    assert exporter._llm_facet_value("early_20th_century") == "Early 20th century"
    assert exporter._llm_facet_value("traditional_folk") == "Traditional (folk)"
    assert exporter._llm_facet_value("modern") == "Modern"
    assert exporter._llm_facet_value("late_romantic") == "late_romantic"


def test_normalize_imslp_style_helper():
    assert exporter._normalize_imslp_style("Romántico") == "Romantic"
    assert exporter._normalize_imslp_style("Traditional") == "Traditional (folk)"
    assert exporter._normalize_imslp_style("Romantic") == "Romantic"
    assert exporter._normalize_imslp_style("Baroque") == "Baroque"


def test_exporter_follows_data_dir_env(tmp_path):
    # The pipeline exports from a relocated data dir; a hard-coded repo path broke that.
    import os
    import subprocess
    import sys
    from pathlib import Path

    scripts = Path(__file__).resolve().parents[1] / "scripts"
    env = {**os.environ, "EU_PD_DATA_DIR": str(tmp_path)}
    out = subprocess.run(
        [sys.executable, "-c", "import export_viewer_json as e; print(e.DATA)"],
        cwd=scripts, env=env, capture_output=True, text=True, check=True,
    )
    assert out.stdout.strip() == str(tmp_path)
