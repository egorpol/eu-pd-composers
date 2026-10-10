"""Gold set sampler and scorer (offline, synthetic dumps)."""

import argparse
import json
import re

import pandas as pd
import pytest

import common
import gold_set as gs


def test_allocate_sqrt_floor_cap_and_total():
    alloc = gs.allocate({"big": 10_000, "mid": 400, "tiny": 5}, 100, 8)
    assert sum(alloc.values()) == 100
    assert alloc["tiny"] == 5  # capped at stratum size
    assert alloc["big"] > alloc["mid"] >= 8
    assert gs.allocate({"a": 3, "b": 4}, 50, 8) == {"a": 3, "b": 4}


def test_strata_rules():
    base = {"qa_flags": "", "imslp_match_status": "matched", "imslp_match_method": "wikidata_p839",
            "eu_pd_status": "pd"}
    assert gs.composer_stratum(pd.Series(base)) == "pd/p839"
    assert gs.composer_stratum(pd.Series({**base, "imslp_match_method": "exact_name+life_dates"})) == "pd/name"
    assert gs.composer_stratum(pd.Series({**base, "imslp_match_status": "not_found"})) == "pd/not_found"
    assert gs.composer_stratum(pd.Series({**base, "imslp_match_status": "rejected_p839"})) == \
        "imslp_rejected_or_unverified"
    assert gs.composer_stratum(pd.Series({**base, "qa_flags": "birth_from_list"})) == "qa_flagged"
    work = {"force_family_src": "imslp_tags", "imslp_copyright_flags": "nonpd_eu"}
    assert gs.work_stratum(pd.Series(work)) == "imslp/eu_warning"
    assert gs.work_stratum(pd.Series({**work, "imslp_copyright_flags": "wima"})) == "imslp/other_rights"
    assert gs.work_stratum(pd.Series({**work, "imslp_copyright_flags": "nonpd_us"})) == "imslp/none"
    assert gs.work_stratum(pd.Series({**work, "force_family_src": "llm_grok"})) == "llm"
    assert gs.work_stratum(pd.Series({**work, "force_family_src": "arr_only"})) == "unlabelled"


def _composers(n=60):
    statuses = ["pd", "not_pd", "unknown_death"]
    return pd.DataFrame({
        "composer_id": [f"Q{i}" for i in range(n)],
        "name_display": [f"Composer {i}" for i in range(n)],
        "wikipedia_url": [f"https://en.wikipedia.org/wiki/C{i}" for i in range(n)],
        "wikidata_url": [f"https://www.wikidata.org/wiki/Q{i}" for i in range(n)],
        "imslp_url": [f"https://imslp.org/wiki/Category:C{i}" if i % 2 else "" for i in range(n)],
        "imslp_match_status": ["matched" if i % 2 else "not_found" for i in range(n)],
        "imslp_match_method": ["wikidata_p839" if i % 2 else "exact_name" for i in range(n)],
        "qa_flags": ["birth_from_list" if i % 15 == 0 else "" for i in range(n)],
        "eu_pd_status": [statuses[i % 3] for i in range(n)],
        "death_year": ["1950" if i % 3 == 0 else ("1990" if i % 3 == 1 else "") for i in range(n)],
    })


def _works(n=90):
    return pd.DataFrame({
        "work_id": [f"Work {i}" for i in range(n)],
        "composer_id": [f"Q{i % 60}" for i in range(n)],
        "title": [f"Work {i}" for i in range(n)],
        "imslp_work_url": [f"https://imslp.org/wiki/Work_{i}" for i in range(n)],
        "force_family": [["piano_solo", "choral", "orchestral"][i % 3] for i in range(n)],
        "force_family_src": ["llm" if i % 10 == 0 else "imslp_tags" for i in range(n)],
        "imslp_copyright_flags": ["nonpd_eu" if i % 4 == 0 else "" for i in range(n)],
        "has_files": ["true"] * n,
    })


def test_build_sample_is_deterministic_and_blind():
    kwargs = dict(seed=7, n_composers=30, n_works=40, minimum=3, recheck_share=0.1)
    first = gs.build_sample(_composers(), _works(), **kwargs)
    second = gs.build_sample(_composers(), _works(), **kwargs)
    for kind in gs.SETS:
        for part in ("sheet", "recheck", "design"):
            pd.testing.assert_frame_equal(first[kind][part], second[kind][part])
    sheet, design, recheck = (first["composers"][p] for p in ("sheet", "design", "recheck"))
    assert len(sheet) == 30 and len(recheck) == 3
    assert list(sheet.columns) == ["item", *gs.SHOWN["composers"], *gs.GOLD_FIELDS["composers"]]
    assert not {"eu_pd_status", "stratum", "weight"} & set(sheet.columns)
    assert (sheet[list(gs.GOLD_FIELDS["composers"])] == "").all().all()
    assert set(recheck["item"]) == set(design.loc[design["recheck"] == "true", "item"])
    assert sheet["item"].tolist() == [f"C{i:03d}" for i in range(1, 31)]
    works_sheet = first["works"]["sheet"]
    assert "force_family" in works_sheet and (works_sheet["force_family"] == "").all()
    assert gs.build_sample(_composers(), _works(), **{**kwargs, "seed": 8})["composers"]["design"][
        "composer_id"].tolist() != design["composer_id"].tolist()


def test_wilson_reference_values():
    low, high = gs.wilson(0.5, 100)
    assert round(low, 3) == 0.404 and round(high, 3) == 0.596
    assert gs.wilson(0.0, 50)[0] == 0.0 and round(gs.wilson(0.0, 50)[1], 3) == 0.071


def test_stratified_rate_weights_strata():
    rows = pd.DataFrame({
        "stratum": ["a"] * 4 + ["b"] * 4,
        "stratum_size": [100] * 4 + [900] * 4,
        "eligible": [1] * 8,
        "error": [1, 1, 0, 0, 0, 0, 0, 0],
    })
    result = gs.stratified_rate(rows)
    assert result["rate"] == 0.25
    assert result["weighted_rate"] == pytest.approx(0.05)  # (100·0.5 + 900·0) / 1000
    assert result["weighted_ci"][0] < 0.05 < result["weighted_ci"][1]
    none = gs.stratified_rate(rows.assign(error=0))
    assert none["weighted_rate"] == 0 and none["weighted_ci"] == gs.wilson(0, 8)
    assert gs.stratified_rate(rows.assign(eligible=0, error=0))["rate"] is None


def test_validate_flags_bad_cells():
    sheet = pd.DataFrame([
        {"item": "C001", "imslp_url": "https://imslp.org/x", "wikidata_same_person": "maybe", "imslp_check": "found",
         "imslp_found_url": "", "death_status": "dead", "death_year": "19x0", "death_source": "", "notes": ""},
        {"item": "C002", "imslp_url": "", "wikidata_same_person": "yes", "imslp_check": "none",
         "imslp_found_url": "", "death_status": "", "death_year": "", "death_source": "", "notes": ""},
    ])
    problems = gs.validate("composers", sheet)
    assert problems == ["C001 wikidata_same_person='maybe'", "C001 imslp_check='found' (want different/same/unsure)",
                        "C001 death_year='19x0' (want YYYY)"]


def _scored(gold_rows, dump_rows, kind="composers"):
    sheet = pd.DataFrame(gold_rows)
    for col in gs.GOLD_FIELDS[kind]:
        sheet[col] = sheet.get(col, "")
    sheet = sheet.fillna("")
    design = pd.DataFrame({"item": sheet["item"], "stratum": "s", "stratum_size": 100})
    metrics = gs.composer_metrics(2026) if kind == "composers" else gs.work_metrics()
    return gs.score_set(kind, sheet, design, pd.DataFrame(dump_rows), metrics)


def test_composer_metrics_end_to_end():
    gold = [
        {"item": "C1", "composer_id": "Q1", "imslp_url": "u", "wikidata_same_person": "yes", "imslp_check": "same",
         "death_status": "dead", "death_year": "1960"},
        {"item": "C2", "composer_id": "Q2", "imslp_url": "", "wikidata_same_person": "no", "imslp_check": "found",
         "death_status": "dead", "death_year": "1950"},
        {"item": "C3", "composer_id": "Q3", "imslp_url": "", "wikidata_same_person": "yes", "imslp_check": "none",
         "death_status": "living"},
        {"item": "C4", "composer_id": "Q4", "imslp_url": "u", "notes": "only a note"},
    ]
    dump = [
        {"composer_id": "Q1", "death_year": "1955", "eu_pd_status": "pd"},
        {"composer_id": "Q2", "death_year": "", "eu_pd_status": "unknown_death"},
        {"composer_id": "Q3", "death_year": "", "eu_pd_status": "unknown_death"},
        {"composer_id": "Q4", "death_year": "1900", "eu_pd_status": "pd"},
    ]
    result = _scored(gold, dump)
    m = result["metrics"]
    assert result["annotated"] == 3  # a note alone is not an annotation
    assert (m["identity_wrong"]["eligible"], m["identity_wrong"]["errors"]) == (3, 1)
    assert (m["imslp_link_wrong"]["eligible"], m["imslp_link_wrong"]["errors"]) == (1, 0)
    assert (m["imslp_link_missed"]["eligible"], m["imslp_link_missed"]["errors"]) == (2, 1)
    assert (m["death_year_wrong"]["eligible"], m["death_year_wrong"]["errors"]) == (1, 1)
    assert m["death_year_off_by_2plus"]["errors"] == 1
    assert (m["death_missing"]["eligible"], m["death_missing"]["errors"]) == (2, 1)
    assert (m["false_pd"]["eligible"], m["false_pd"]["errors"]) == (1, 1)  # died 1960 > 1955
    assert (m["missed_pd"]["eligible"], m["missed_pd"]["errors"]) == (2, 1)  # died 1950, dump unknown


def test_work_metrics_end_to_end():
    gold = [
        {"item": "W1", "work_id": "A", "force_family": "choral", "has_files": "yes", "rights_basis": "eu_warning"},
        {"item": "W2", "work_id": "B", "force_family": "unsure", "has_files": "no", "rights_basis": "none"},
        {"item": "W3", "work_id": "C", "force_family": "bogus", "has_files": "yes", "rights_basis": "unsure"},
    ]
    dump = [
        {"work_id": "A", "force_family": "choral", "has_files": "true", "imslp_copyright_flags": "nonpd_eu"},
        {"work_id": "B", "force_family": "organ", "has_files": "true", "imslp_copyright_flags": "pd_us_only"},
        {"work_id": "C", "force_family": "organ", "has_files": "", "imslp_copyright_flags": ""},
    ]
    result = _scored(gold, dump, "works")
    m = result["metrics"]
    assert result["invalid_cells"] == ["W3 force_family='bogus'"]
    assert (m["force_family_wrong"]["eligible"], m["force_family_wrong"]["errors"]) == (1, 0)
    assert (m["has_files_wrong"]["eligible"], m["has_files_wrong"]["errors"]) == (2, 1)
    assert (m["rights_basis_wrong"]["eligible"], m["rights_basis_wrong"]["errors"]) == (2, 1)


def test_recheck_agreement():
    first = pd.DataFrame({"item": ["W1", "W2", "W3", "W4"], "force_family": ["choral", "organ", "choral", ""],
                          "has_files": ["yes"] * 4, "rights_basis": ["none"] * 4})
    second = first.assign(force_family=["choral", "choral", "choral", "organ"])
    result = gs.agreement("works", first, second)
    assert result["force_family"]["pairs"] == 3
    assert result["force_family"]["agree"] == pytest.approx(2 / 3)
    assert result["has_files"] == {"pairs": 4, "agree": 1.0, "kappa": 1.0}


def test_sample_refuses_to_overwrite(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "DATA_DIR", tmp_path)
    monkeypatch.setattr(gs, "DATA_DIR", tmp_path)
    _composers().to_csv(tmp_path / "composers_r900.tsv", sep="\t", index=False)
    _works().to_csv(tmp_path / "works_r900.tsv", sep="\t", index=False)
    args = argparse.Namespace(dump="r900", seed=1, composers=20, works=20, min_per_stratum=2,
                              recheck_share=0.1, out_dir=None)
    gs.run_sample(args)
    sheet = tmp_path / "gold" / "r900" / "composers_sheet.tsv"
    sheet.write_text(sheet.read_text() + "annotated\n")
    before = sheet.read_bytes()
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        gs.run_sample(args)
    assert sheet.read_bytes() == before


def test_form_page_carries_shown_columns_only(tmp_path):
    result = gs.build_sample(_composers(), _works(), seed=7, n_composers=30, n_works=40, minimum=3,
                             recheck_share=0.1)
    for kind in gs.SETS:
        for part in ("sheet", "recheck"):
            frame = result[kind][part].copy()
            if part == "sheet":
                frame.loc[0, "notes"] = "an answer that must not leak"
                if kind == "works":
                    frame.loc[1, "title"] = "Lied </script><b>"
            frame.to_csv(tmp_path / f"{kind}_{part}.tsv", sep="\t", index=False)
    page, local = gs.run_form(argparse.Namespace(dump="r900", gold_dir=str(tmp_path), out_dir=str(tmp_path / "out")))
    html = page.read_text(encoding="utf-8")
    start = html.index('<script id="gold-data" type="application/json">')
    block = html[start:html.index("</script>", start)]
    data = json.loads(block[block.index(">") + 1:])
    assert data["dump"] == "r900"
    assert [len(data[k]) for k in ("composers", "works", "composers_recheck", "works_recheck")] == [30, 40, 3, 4]
    assert set(data["composers"][0]) == {"item", *gs.SHOWN["composers"]}
    assert data["works"][1]["title"] == "Lied </script><b>"
    assert "must not leak" not in html and gs.FORM_PLACEHOLDER not in html
    assert [i["item"] for i in data["practice"]["works"]] == ["P-W1", "P-W2"]
    assert local.read_text(encoding="utf-8").startswith("<!doctype html>")


def test_form_template_matches_scorer_vocabulary():
    template = gs.FORM_TEMPLATE.read_text(encoding="utf-8")
    keys = lambda a, b: re.findall(r'^\s*\["([a-z_]+)", "', template[template.index(a):template.index(b)], re.M)
    assert keys("const FAMILIES", "const BASES") == [f for f in gs.FORCE_FAMILIES if f != "unclassified"]
    assert keys("const BASES", "const TABS") == list(gs.BASIS_LABELS)
    columns = lambda a, b: {k: re.findall(r'"([a-z_]+)"', v) for k, v in re.findall(
        r'^\s+([cw]): \[(.*)\],$', template[template.index(a):template.index(b)], re.M)}
    assert columns("const FIELDS", "const SHEET_COLS") == {"c": list(gs.GOLD_FIELDS["composers"]),
                                                          "w": list(gs.GOLD_FIELDS["works"])}
    assert columns("const SHEET_COLS", "const FAMILIES") == {"c": ["item", *gs.SHOWN["composers"]],
                                                            "w": ["item", *gs.SHOWN["works"]]}
