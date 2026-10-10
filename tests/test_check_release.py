"""Regression gate thresholds and schema drift."""

import sys

import pandas as pd
import pytest

import check_release
import common


def frames(composers=100, works=100):
    return (pd.DataFrame({"composer_id": [f"Q{i}" for i in range(composers)], "eu_pd_status": ["pd"] * composers}),
            pd.DataFrame({"composer_id": ["Q0"] * works, "imslp_pageid": [str(i) for i in range(works)]}))


@pytest.mark.parametrize("composer_n,work_n,failed", [(103, 95, False), (97, 95, False),
                                                       (104, 100, True), (96, 100, True), (100, 94, True), (100, 200, False)])
def test_thresholds(composer_n, work_n, failed):
    c, w = frames(composer_n, work_n)
    pc, pw = frames()
    errors, warnings = check_release.check_regressions(c, w, pc, pw)
    assert bool(errors) == failed
    assert "composers added:" in warnings[1]


def test_schema_and_pd_warnings():
    pc, pw = frames()
    pc["old_column"] = ""
    pw["work_column"] = ""
    c, w = frames()
    c.loc[0, "eu_pd_status"] = "not_pd"
    errors, warnings = check_release.check_regressions(c, w, pc, pw)
    assert len(errors) == 2
    assert "old_column" in errors[0] and "work_column" in errors[1]
    assert warnings[0] == "EU PD status flips: 1"


def test_empty_pageids_do_not_count_as_a_shared_imslp_page():
    c, w = frames(2, 2)
    w["composer_id"] = ["Q0", "Q1"]
    w["imslp_pageid"] = ""
    _, warnings = check_release.check_data_quality(c, w, {})
    assert not any("more than one composer" in warning for warning in warnings)


def test_llm_style_sanity_and_legacy_revision_compatibility():
    c, w = frames(2, 2)
    c["style_tags_src"] = ["llm_luna_xhigh", "wikidata"]
    assert not check_release.check_data_quality(c, w, {})[0]
    c["llm_style_tags"] = ["modern|invented", "impressionism"]
    c["llm_style_period"] = ["unknown", "romantic"]
    errors, _ = check_release.check_data_quality(c, w, {})
    assert len(errors) == 3
    assert any("invented" in error for error in errors)
    assert any("unknown" in error for error in errors)
    assert any("legacy LLM" in error for error in errors)
    c["style_tags_src"] = ["", "wikidata"]
    c["llm_style_tags"] = ["modern", "impressionism"]
    c["llm_style_period"] = ["", "romantic"]
    assert not check_release.check_data_quality(c, w, {})[0]


def test_work_evidence_vocabularies():
    c, w = frames(1, 4)
    w["imslp_copyright_flags"] = ["nonpd_eu|wima", "pd_us_only", "", "made_up"]
    w["has_files"] = ["true", "false", "", "maybe"]
    w["imslp_file_hosts"] = ["ca|us", "asia", "", "eu"]
    errors, warnings = check_release.check_data_quality(c, w, {})
    assert errors == ["unknown imslp_copyright_flags tokens: made_up", "invalid has_files values: maybe",
                      "works with file hosts but has_files != true: 2"]
    assert any("new IMSLP file hosts" in warning and "eu" in warning for warning in warnings)


@pytest.mark.parametrize("filled_after,failed", [(95, False), (94, True)])
def test_has_files_coverage_cannot_collapse(filled_after, failed):
    c, w = frames()
    pc, pw = frames()
    pw["has_files"] = "true"
    w["has_files"] = ["true"] * filled_after + [""] * (100 - filled_after)
    errors, _ = check_release.check_regressions(c, w, pc, pw)
    assert bool(errors) == failed


def test_cli_against_loads_baseline(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(common, "DATA_DIR", tmp_path)
    monkeypatch.setattr(common, "CACHE_DIR", tmp_path / "cache")
    for dump_id, n in (("r001", 100), ("r002", 104)):
        c, w = frames(n, 100)
        w["work_id"] = w.imslp_pageid
        common.write_tsv_dump(c, "composers", dump_id)
        common.write_tsv_dump(w, "works", dump_id)
        common.write_dump_meta(dump_id, {"row_counts": {"composers": len(c), "works": len(w)}})
    monkeypatch.setattr(sys, "argv", ["check_release.py", "--dump", "r002", "--against", "r001"])
    with pytest.raises(SystemExit) as exc:
        check_release.main()
    assert exc.value.code == 1
    assert "composers changed by more than 3%" in capsys.readouterr().out
    errors, _ = check_release.check("r002", None, "missing")
    assert any("missing baseline" in err for err in errors)


@pytest.mark.parametrize("stem,column", [("composers", "composer_id"), ("works", "composer_id"),
                                         ("works", "force_family"), ("works", "work_id")])
def test_schema_drift_is_reported_even_for_required_columns(tmp_path, monkeypatch, stem, column):
    monkeypatch.setattr(common, "DATA_DIR", tmp_path)
    monkeypatch.setattr(common, "CACHE_DIR", tmp_path / "cache")
    c, w = frames()
    w["work_id"] = w.imslp_pageid
    w["force_family"] = "piano_solo"
    w["force_family_src"] = "imslp_tags"
    w["imslp_genre_categories"] = "For piano"
    for dump_id in ("r001", "r002"):
        for name, df in (("composers", c), ("works", w)):
            frame = df.drop(columns=column) if dump_id == "r002" and name == stem else df
            common.write_tsv_dump(frame, name, dump_id)
        common.write_dump_meta(dump_id, {"row_counts": {"composers": len(c), "works": len(w)}})
    errors, _ = check_release.check("r002", None, "r001")
    assert any("schema drift" in err and column in err for err in errors)
