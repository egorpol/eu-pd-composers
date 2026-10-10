"""Schema-aware revision comparisons and timestamp exclusions."""

import json
import sys

import pandas as pd
import pytest

import common
import diff_dumps as diff


def dumps():
    return {
        "composers": pd.DataFrame([
            {"composer_id": "Q1", "name_display": "One", "birth_year": "1900", "death_year": "1955",
             "eu_pd_year": "2026", "eu_pd_status": "pd", "imslp_match_status": "matched", "qa_flags": "old|keep",
             "style_tags_src": "llm", "dump_date": "r001", "pageviews_enwiki": "001"},
            {"composer_id": "Q2", "name_display": "Two", "birth_year": "", "death_year": "",
             "eu_pd_year": "", "eu_pd_status": "unknown_death", "imslp_match_status": "not_found", "qa_flags": "",
             "style_tags_src": "", "dump_date": "r001", "pageviews_enwiki": ""},
        ]),
        "works": pd.DataFrame([
            {"composer_id": "Q1", "imslp_pageid": "1", "title": "NA", "force_family": "other",
             "force_family_src": "llm", "fetched_at": "old"},
            {"composer_id": "Q1", "imslp_pageid": "2", "title": "002", "force_family": "chamber",
             "force_family_src": "imslp_tags", "fetched_at": "old"},
        ]),
    }


def test_dates_views_meta_and_order_do_not_change_data():
    prev = dumps()
    nxt = {stem: df.iloc[::-1, ::-1].copy() for stem, df in prev.items()}
    nxt["composers"]["dump_date"] = "different"
    nxt["composers"]["pageviews_enwiki"] = "999"
    nxt["composers"]["pageviews_window"] = "added ignored column"
    nxt["works"]["fetched_at"] = "new"
    _, summary = diff.compare_dumps(prev, nxt)
    assert not summary["has_data_changes"]
    assert summary["schema_changes"] == [{"file": "composers", "added": ["pageviews_window"], "removed": []}]


def test_all_report_sections_and_schema_changes():
    prev = dumps()
    nxt = {stem: df.copy() for stem, df in prev.items()}
    nxt["composers"].loc[0, ["birth_year", "death_year", "eu_pd_status", "imslp_match_status", "qa_flags"]] = [
        "1901", "1956", "not_pd", "rejected_p839", "keep|new"]
    nxt["composers"].loc[1, "composer_id"] = "Q3"
    nxt["works"].loc[0, ["force_family", "force_family_src"]] = ["guitar", "imslp_tags"]
    nxt["works"].loc[1, "imslp_pageid"] = "3"
    nxt["works"] = nxt["works"].drop(columns="title")
    nxt["works"]["new_col"] = "value"
    report, summary = diff.compare_dumps(prev, nxt, "r001", "r002")
    assert summary == {"has_data_changes": True, "composers": {"prev": 2, "next": 2},
                       "works": {"prev": 2, "next": 2}, "pd_flips": 1,
                       "schema_changes": [{"file": "works", "added": ["new_col"], "removed": ["title"]}]}
    for expected in ("Q2", "Q3", "1955 → 1956", "1900 → 1901", "matched → rejected_p839",
                     "other → guitar: 1", "llm → imslp_tags: 1", "50.00%", "new: +1, −0", "old: +0, −1",
                     "Added: **1**", "Removed: **1**"):
        assert expected in report


def test_cell_spelling_matters_and_duplicate_keys_fail():
    prev = dumps()
    nxt = dumps()
    nxt["works"].loc[1, "title"] = "2"
    assert diff.compare_dumps(prev, nxt)[1]["has_data_changes"]
    nxt["works"] = pd.concat([nxt["works"], nxt["works"].iloc[:1]])
    with pytest.raises(ValueError, match="Duplicate"):
        diff.compare_dumps(prev, nxt)


def test_llm_style_share_counts_consensus_only():
    previous, following = dumps(), dumps()
    following["composers"]["llm_style_tags"] = ["modern", ""]
    following["composers"]["llm_style_src"] = "llm_consensus_grounded_v2"
    report, _ = diff.compare_dumps(previous, following, "r014", "r015")
    assert "r014 composer styles: 0/2" in report
    assert "r015 composer styles: 1/2" in report


def test_cli_staged_dump_and_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "DATA_DIR", tmp_path)
    staged = tmp_path / "staged"
    staged.mkdir()
    for directory, dump_id in ((tmp_path, "r001"), (staged, "r002")):
        for stem, df in dumps().items():
            df.to_csv(directory / f"{stem}_{dump_id}.tsv", sep="\t", index=False)
    output, summary = tmp_path / "diff.md", tmp_path / "summary.json"
    monkeypatch.setattr(sys, "argv", ["diff_dumps.py", "r001", "r002", "--next-dir", str(staged),
                                     "--out", str(output), "--summary-json", str(summary)])
    diff.main()
    assert "Data changes: **no**" in output.read_text()
    assert json.loads(summary.read_text())["has_data_changes"] is False
