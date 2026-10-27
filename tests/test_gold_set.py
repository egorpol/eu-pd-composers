"""Gold set sampler and scorer (offline, synthetic dumps)."""

import argparse
import json
import re

import pandas as pd
import pytest

import common
import gold_set as gs
from force_family import GENRE_FORMS, map_genre_form


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
        "gnd_id": [f"1000{i:05d}X" if i % 3 else "" for i in range(n)],
        "viaf_id": [str(1000 + i) for i in range(n)],
        "imslp_match_status": ["matched" if i % 2 else "not_found" for i in range(n)],
        "imslp_match_method": ["wikidata_p839" if i % 2 else "exact_name" for i in range(n)],
        "qa_flags": ["birth_from_list" if i % 15 == 0 else "" for i in range(n)],
        "eu_pd_status": [statuses[i % 3] for i in range(n)],
        "death_year": ["1950" if i % 3 == 0 else ("1990" if i % 3 == 1 else "") for i in range(n)],
        "style_tags": ["impressionism" if i % 7 == 0 else "" for i in range(n)],
        "style_tags_src": ["wikidata" if i % 7 == 0 else "" for i in range(n)],
        "llm_style_tags": ["" if i % 4 == 0 else ["romantic|national_folk", "modern"][i % 2] for i in range(n)],
        "llm_style_period": ["" if i % 4 == 0 else ["romantic", "modern"][i % 2] for i in range(n)],
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
        "imslp_style": [["Romantic", "Modern", ""][i % 3] for i in range(n)],
    })


def test_build_sample_is_deterministic_and_blind():
    kwargs = dict(seed=7, n_composers=30, n_works=40, minimum=3, recheck_share=0.1, version=1)
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
    assert list(works_sheet.columns) == ["item", *gs.SHOWN["works"], *gs.GOLD_FIELDS["works"]]
    assert "force_family" in works_sheet and (works_sheet["force_family"] == "").all()
    assert "genre_form" in works_sheet and (works_sheet["genre_form"] == "").all()
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


def test_genre_form_vocabulary_and_precedence():
    assert gs.VOCAB["genre_form"] == (set(GENRE_FORMS) - {"arrangement"}) | {"none", "unsure"}
    assert gs.GOLD_FIELDS["works"] == ("force_family", "genre_form", "has_files", "rights_basis", "notes")
    cats = {
        "opera": "Operas", "ballet": "Ballets", "concerto": "Concertos", "symphony": "Symphonies",
        "sonata": "Sonatas", "suite": "Suites", "variations": "Variations", "etude": "Etudes",
        "prelude_fugue": "Preludes", "string_quartet": "Quartets|For 2 violins, viola, cello", "dance": "Waltzes",
        "mass_requiem": "Masses", "oratorio_cantata": "Cantatas", "overture_tone_poem": "Overtures",
        "song": "Songs",
    }
    forms = [f for f in gs.GOLD_GENRE_FORMS if f not in {"song_cycle", "none"}]
    for a, b in zip(forms, forms[1:]):
        aliased = gs.alias_dump_genre_form(map_genre_form(f"{cats[a]}|{cats[b]}"))
        assert aliased == a, (a, b, aliased)


def test_genre_form_metrics():
    gold = [
        {"item": "W1", "work_id": "A", "genre_form": "song"},
        {"item": "W2", "work_id": "B", "genre_form": "none"},
        {"item": "W3", "work_id": "C", "genre_form": "sonata"},
        {"item": "W4", "work_id": "D", "genre_form": "suite"},
        {"item": "W5", "work_id": "E", "genre_form": "none"},
        {"item": "W6", "work_id": "F", "genre_form": "unsure"},
        {"item": "W7", "work_id": "G", "genre_form": "arrangement"},
    ]
    dump = [
        {"work_id": "A", "genre_form": "other", "force_family": "solo_voice", "has_files": "true",
         "imslp_copyright_flags": ""},
        {"work_id": "B", "genre_form": "", "force_family": "piano_solo", "has_files": "true",
         "imslp_copyright_flags": ""},
        {"work_id": "C", "genre_form": "arrangement", "force_family": "piano_solo", "has_files": "true",
         "imslp_copyright_flags": ""},
        {"work_id": "D", "genre_form": "", "force_family": "orchestral", "has_files": "true",
         "imslp_copyright_flags": ""},
        {"work_id": "E", "genre_form": "sonata", "force_family": "piano_solo", "has_files": "true",
         "imslp_copyright_flags": ""},
        {"work_id": "F", "genre_form": "sonata", "force_family": "piano_solo", "has_files": "true",
         "imslp_copyright_flags": ""},
        {"work_id": "G", "genre_form": "sonata", "force_family": "piano_solo", "has_files": "true",
         "imslp_copyright_flags": ""},
    ]
    result = _scored(gold, dump, "works")
    m = result["metrics"]
    assert result["invalid_cells"] == ["W7 genre_form='arrangement'"]
    # W1 song/other→song ok; W2 none/empty→none ok; W3 sonata/arrangement wrong+arrangement;
    # W4 suite/empty wrong+missed; W5 none/sonata wrong+spurious; W6 unsure ineligible; W7 invalid.
    assert (m["genre_form_wrong"]["eligible"], m["genre_form_wrong"]["errors"]) == (5, 3)
    assert (m["genre_form_missed"]["eligible"], m["genre_form_missed"]["errors"]) == (2, 1)
    assert (m["genre_form_spurious"]["eligible"], m["genre_form_spurious"]["errors"]) == (2, 1)
    assert (m["genre_form_arrangement"]["eligible"], m["genre_form_arrangement"]["errors"]) == (1, 1)


def test_recheck_agreement():
    first = pd.DataFrame({"item": ["W1", "W2", "W3", "W4"], "force_family": ["choral", "organ", "choral", ""],
                          "genre_form": ["song", "none", "sonata", ""],
                          "has_files": ["yes"] * 4, "rights_basis": ["none"] * 4})
    second = first.assign(force_family=["choral", "choral", "choral", "organ"])
    result = gs.agreement("works", first, second)
    assert result["force_family"]["pairs"] == 3
    assert result["force_family"]["agree"] == pytest.approx(2 / 3)
    assert result["genre_form"] == {"pairs": 3, "agree": 1.0, "kappa": 1.0}
    assert result["has_files"] == {"pairs": 4, "agree": 1.0, "kappa": 1.0}


def test_sample_refuses_to_overwrite(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "DATA_DIR", tmp_path)
    monkeypatch.setattr(gs, "DATA_DIR", tmp_path)
    _composers().to_csv(tmp_path / "composers_r900.tsv", sep="\t", index=False)
    _works().to_csv(tmp_path / "works_r900.tsv", sep="\t", index=False)
    args = argparse.Namespace(dump="r900", seed=1, composers=20, works=20, min_per_stratum=2,
                              recheck_share=0.1, out_dir=None, gold_version=1, packages=1)
    gs.run_sample(args)
    sheet = tmp_path / "gold" / "r900" / "composers_sheet.tsv"
    sheet.write_text(sheet.read_text() + "annotated\n")
    before = sheet.read_bytes()
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        gs.run_sample(args)
    assert sheet.read_bytes() == before


def test_form_page_carries_shown_columns_only(tmp_path):
    result = gs.build_sample(_composers(), _works(), seed=7, n_composers=30, n_works=40, minimum=3,
                             recheck_share=0.1, version=1)
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
    assert "must not leak" not in html and gs.FORM_PLACEHOLDER not in html and gs.FORM_DUMP_PLACEHOLDER not in html
    assert "<title>eu-pd-composers Gold Set r900</title>" in html
    assert [i["item"] for i in data["practice"]["works"]] == ["P-W1", "P-W2"]
    assert local.read_text(encoding="utf-8").startswith("<!doctype html>")


def test_form_template_matches_scorer_vocabulary():
    template = gs.FORM_TEMPLATE.read_text(encoding="utf-8")
    keys = lambda a, b: re.findall(r'^\s*\["([a-z_]+)", "', template[template.index(a):template.index(b)], re.M)
    assert keys("const FAMILIES", "const GENRES") == [f for f in gs.FORCE_FAMILIES if f != "unclassified"]
    assert keys("const GENRES", "const BASES") == list(gs.GOLD_GENRE_FORMS)
    assert keys("const BASES", "const TABS") == list(gs.BASIS_LABELS)
    columns = lambda a, b: {k: re.findall(r'"([a-z_]+)"', v) for k, v in re.findall(
        r'^\s+([cw]): \[(.*)\],$', template[template.index(a):template.index(b)], re.M)}
    assert columns("const FIELDS", "const SHEET_COLS") == {"c": list(gs.GOLD_FIELDS["composers"]),
                                                          "w": list(gs.GOLD_FIELDS["works"])}
    assert columns("const SHEET_COLS", "const FAMILIES") == {"c": ["item", *gs.SHOWN["composers"]],
                                                            "w": ["item", *gs.SHOWN["works"]]}
    # Gold v2 sheets put the style fields before notes, and the form splices them in the same order.
    assert 'FIELDS.c.splice(FIELDS.c.indexOf("notes"), 0, "' + '", "'.join(gs.STYLE_FIELDS) + '")' in template
    # The one-click source fills the form the import keeps unchanged; the GND link is the canonical URI.
    assert 'const gnd = "GND " + it.gnd_id;' in template and '"https://d-nb.info/gnd/" + enc(it.gnd_id)' in template
    assert gs._cell("death_source", "GND 118590219") == "GND 118590219"


def test_import_form_answers(tmp_path):
    result = gs.build_sample(_composers(), _works(), seed=7, n_composers=30, n_works=40, minimum=3,
                             recheck_share=0.1, version=1)
    for kind in gs.SETS:
        for part in ("sheet", "recheck"):
            result[kind][part].to_csv(tmp_path / f"{kind}_{part}.tsv", sep="\t", index=False)
    sheet_path = tmp_path / "works_sheet.tsv"
    sheet = pd.read_csv(sheet_path, sep="\t", dtype=str, keep_default_na=False)
    sheet.loc[sheet["item"] == "W002", "notes"] = "typed in a spreadsheet"
    sheet.loc[sheet["item"] == "W003", "notes"] = "cleared in the form"
    sheet.to_csv(sheet_path, sep="\t", index=False)
    recheck_item = result["composers"]["recheck"]["item"].iloc[0]
    store = tmp_path / "store"
    docs = {
        "answers/W001.json": {"item": "W001", "force_family": "concerto", "has_files": "yes",
                              "rights_basis": "none", "notes": "harp\tand\nstrings", "updatedAt": "x"},
        "answers/W003.json": {"item": "W003", "force_family": "harp_only"},
        "answers/probe.json": {"item": "probe"},
        f"recheck/{recheck_item}.json": {"item": recheck_item, "wikidata_same_person": "yes",
                                         "death_source": " 118776312 "},
    }
    for name, doc in docs.items():
        (store / name).parent.mkdir(parents=True, exist_ok=True)
        (store / name).write_text(json.dumps(doc), encoding="utf-8")
    composers_before = (tmp_path / "composers_sheet.tsv").read_bytes()
    summary = gs.import_answers(tmp_path, store)
    works = pd.read_csv(sheet_path, sep="\t", dtype=str, keep_default_na=False).set_index("item")
    assert works.loc["W001", list(gs.GOLD_FIELDS["works"])].tolist() == [
        "concerto", "", "yes", "none", "harp and strings"]
    assert works.loc["W002", "notes"] == "typed in a spreadsheet"
    assert works.loc["W003", "notes"] == ""
    assert summary["works_sheet.tsv"] == {"imported": 2, "problems": ["W003 force_family='harp_only'"]}
    assert summary["answers_unknown"] == ["probe"]
    assert summary["composers_recheck.tsv"]["imported"] == 1
    recheck = pd.read_csv(tmp_path / "composers_recheck.tsv", sep="\t", dtype=str, keep_default_na=False)
    assert recheck.loc[recheck["item"] == recheck_item, "death_source"].item() == "GND 118776312"
    assert gs._cell("death_source", "LoC n92085993") == "LoC n92085993" and gs._cell("notes", "118776312") == "118776312"
    assert (tmp_path / "composers_sheet.tsv").read_bytes() == composers_before
    assert list(works.reset_index().columns) == ["item", *gs.SHOWN["works"], *gs.GOLD_FIELDS["works"]]


def test_missing_genre_form_column_is_empty(tmp_path):
    """r016-style sheets without genre_form still import and score."""
    result = gs.build_sample(_composers(), _works(), seed=7, n_composers=30, n_works=40, minimum=3,
                             recheck_share=0.1, version=1)
    for kind in gs.SETS:
        for part in ("sheet", "recheck", "design"):
            frame = result[kind][part]
            if part != "design" and kind == "works":
                frame = frame.drop(columns=["genre_form"])
            frame.to_csv(tmp_path / f"{kind}_{part}.tsv", sep="\t", index=False)
    store = tmp_path / "store"
    (store / "answers").mkdir(parents=True)
    (store / "answers" / "W001.json").write_text(
        json.dumps({"item": "W001", "force_family": "concerto", "genre_form": "suite",
                    "has_files": "yes", "rights_basis": "none"}), encoding="utf-8")
    summary = gs.import_answers(tmp_path, store)
    works = pd.read_csv(tmp_path / "works_sheet.tsv", sep="\t", dtype=str, keep_default_na=False)
    assert "genre_form" in works.columns
    assert works.loc[works["item"] == "W001", "genre_form"].item() == "suite"
    assert summary["works_sheet.tsv"]["imported"] == 1
    dump = _works(40)
    dump["genre_form"] = ""
    sheet = gs.ensure_gold_columns("works", works)
    design = pd.read_csv(tmp_path / "works_design.tsv", sep="\t", dtype=str, keep_default_na=False)
    design["stratum_size"] = design["stratum_size"].astype(int)
    scored = gs.score_set("works", sheet, design, dump, gs.work_metrics())
    assert scored["annotated"] >= 1
    assert "genre_form_wrong" in scored["metrics"]


def _write_parent(directory, result):
    directory.mkdir(parents=True, exist_ok=True)
    for kind in gs.SETS:
        for part in ("sheet", "recheck", "design"):
            result[kind][part].to_csv(directory / f"{kind}_{part}.tsv", sep="\t", index=False)


def test_subset_is_blind_renumbered_and_keeps_population_sizes(tmp_path):
    parent = gs.build_sample(_composers(), _works(), seed=7, n_composers=30, n_works=40, minimum=3,
                             recheck_share=0.1, version=1)
    parent["composers"]["sheet"].loc[0, "notes"] = "an answer that must not leak"
    sheets = {kind: parent[kind]["sheet"] for kind in gs.SETS}
    designs = {kind: parent[kind]["design"].astype(str) for kind in gs.SETS}
    kwargs = dict(seed=3, sizes={"composers": 8, "works": 10}, minimum=1)
    first, second = gs.build_subset(sheets, designs, **kwargs), gs.build_subset(sheets, designs, **kwargs)
    for kind in gs.SETS:
        for part in ("sheet", "recheck", "design"):
            pd.testing.assert_frame_equal(first[kind][part], second[kind][part])
    sheet, design = first["composers"]["sheet"], first["composers"]["design"]
    assert sheet["item"].tolist() == [f"C{i:03d}" for i in range(1, 9)]
    assert list(sheet.columns) == ["item", *gs.SHOWN["composers"], *gs.GOLD_FIELDS["composers"]]
    assert (sheet[list(gs.GOLD_FIELDS["composers"])] == "").all().all()
    assert len(first["composers"]["recheck"]) == 0 and list(first["composers"]["recheck"].columns) == list(sheet.columns)
    # Every parent stratum keeps at least one item and its population size; ids map back to the parent.
    parent_design = parent["composers"]["design"].set_index("item")
    assert set(design["stratum"]) == set(parent_design["stratum"])
    assert (design["stratum_size"] == design["source_item"].map(parent_design["stratum_size"])).all()
    assert (design["composer_id"] == design["source_item"].map(parent_design["composer_id"])).all()
    assert (sheet["composer_id"] == design["composer_id"]).all()
    assert design.groupby("stratum")["stratum_sample"].first().sum() == 8


def test_subset_form_and_inter_annotator_score(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "DATA_DIR", tmp_path)
    monkeypatch.setattr(gs, "DATA_DIR", tmp_path)
    (tmp_path / "gold").mkdir()
    (tmp_path / "gold" / "practice.json").write_text(gs.FORM_TEMPLATE.parent.parent.joinpath(
        "data", "gold", "practice.json").read_text(encoding="utf-8"), encoding="utf-8")
    composers, works = _composers(), _works()
    works["genre_form"] = ""
    composers.to_csv(tmp_path / "composers_r900.tsv", sep="\t", index=False)
    works.to_csv(tmp_path / "works_r900.tsv", sep="\t", index=False)
    parent = gs.build_sample(composers, works, seed=7, n_composers=30, n_works=40, minimum=3, recheck_share=0.1, version=1)
    _write_parent(tmp_path / "gold" / "r900", parent)
    gs.run_subset(argparse.Namespace(dump="r900", seed=1, composers=8, works=10, min_per_stratum=1,
                                     gold_dir=None, out_dir=None))
    overlap = tmp_path / "gold" / "r900_overlap"
    meta = json.loads((overlap / "meta.json").read_text(encoding="utf-8"))
    assert meta["recheck_share"] == 0 and sum(v["stratum_sample"] for v in meta["strata"]["works"].values()) == 10
    with pytest.raises(FileExistsError):
        gs.run_subset(argparse.Namespace(dump="r900", seed=1, composers=8, works=10, min_per_stratum=1,
                                         gold_dir=None, out_dir=None))
    page, _ = gs.run_form(argparse.Namespace(dump="r900_overlap", gold_dir=None, out_dir=str(tmp_path / "out")))
    html = page.read_text(encoding="utf-8")
    start = html.index('<script id="gold-data" type="application/json">')
    block = html[start:html.index("</script>", start)]
    data = json.loads(block[block.index(">") + 1:])
    assert [len(data[k]) for k in ("composers", "works", "composers_recheck", "works_recheck")] == [8, 10, 0, 0]

    # Annotator S01 fills the overlap; the parent annotator agrees on force_family for all but one work.
    read = dict(sep="\t", dtype=str, keep_default_na=False)
    mine = overlap / "annotators" / "S01"
    mine.mkdir(parents=True)
    for kind in gs.SETS:
        pd.read_csv(overlap / f"{kind}_sheet.tsv", **read).to_csv(mine / f"{kind}_sheet.tsv", sep="\t", index=False)
    student = pd.read_csv(mine / "works_sheet.tsv", **read)
    student["force_family"], student["has_files"] = "choral", "yes"
    student.to_csv(mine / "works_sheet.tsv", sep="\t", index=False)
    parent_works = pd.read_csv(tmp_path / "gold" / "r900" / "works_sheet.tsv", **read)
    shared = parent_works["work_id"].isin(student["work_id"])
    parent_works.loc[shared, ["force_family", "has_files"]] = ["choral", "yes"]
    parent_works.loc[parent_works.index[shared][0], "force_family"] = "organ"
    parent_works.to_csv(tmp_path / "gold" / "r900" / "works_sheet.tsv", sep="\t", index=False)
    report = gs.run_score(argparse.Namespace(dump="r900", gold_dir=str(overlap), out=str(mine / "report.md"),
                                             json=None, annotator="S01", compare=str(tmp_path / "gold" / "r900"),
                                             pool=False))
    inter = report["works"]["inter_annotator"]
    assert inter["force_family"]["pairs"] == 10 and inter["force_family"]["agree"] == pytest.approx(0.9)
    assert inter["has_files"] == {"pairs": 10, "agree": 1.0, "kappa": 1.0}
    assert report["composers"]["inter_annotator"]["death_status"]["pairs"] == 0
    assert "recheck" not in report["works"] and report["works"]["annotated"] == 10
    markdown = (mine / "report.md").read_text(encoding="utf-8")
    assert "annotator S01" in markdown and "Inter-annotator agreement with" in markdown


def test_kit_holds_guide_rules_form_and_blank_sheets_only(tmp_path):
    parent = gs.build_sample(_composers(), _works(), seed=7, n_composers=30, n_works=40, minimum=3, recheck_share=0.1, version=1)
    subset = gs.build_subset({k: parent[k]["sheet"] for k in gs.SETS},
                             {k: parent[k]["design"].astype(str) for k in gs.SETS},
                             seed=3, sizes={"composers": 8, "works": 10}, minimum=1)
    _write_parent(tmp_path / "r900_overlap", subset)
    out = gs.run_kit(argparse.Namespace(dump="r900_overlap", gold_dir=str(tmp_path / "r900_overlap"),
                                        out_dir=str(tmp_path / "kit"), packages=None, annotator=None))
    files = sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file())
    assert files == ["README.md", "RULES.md", "data/gold/r900_overlap/composers_sheet.tsv",
                     "data/gold/r900_overlap/works_sheet.tsv", "gold_form.html"]
    guide = (out / "README.md").read_text(encoding="utf-8")
    assert guide.startswith("# Contributor guide") and "__" not in guide
    assert "**8 composers** and **10 works**" in guide and "`data/gold/r900_overlap/composers_sheet.tsv`" in guide
    rules = (out / "RULES.md").read_text(encoding="utf-8")
    assert "## Composers" in rules and "### Rights basis" in rules and "What the scores mean" not in rules
    assert (out / "gold_form.html").read_text(encoding="utf-8").startswith("<!doctype html>")
    sheet = tmp_path / "r900_overlap" / "works_sheet.tsv"
    filled = pd.read_csv(sheet, sep="\t", dtype=str, keep_default_na=False).assign(notes="answered", has_files="yes")
    filled.to_csv(sheet, sep="\t", index=False)
    out = gs.run_kit(argparse.Namespace(dump="r900_overlap", gold_dir=str(tmp_path / "r900_overlap"),
                                        out_dir=str(tmp_path / "kit2"), packages=None, annotator=None))
    kit = pd.read_csv(out / "data" / "gold" / "r900_overlap" / "works_sheet.tsv", sep="\t", dtype=str, keep_default_na=False)
    assert len(kit) == len(filled) and kit[list(gs.GOLD_FIELDS["works"])].eq("").all().all()


# --- Gold v2: style, packages, contributors -------------------------------------------------------

V2 = dict(seed=7, n_composers=30, n_works=45, minimum=2, recheck_share=0.1, version=2, packages=3)


def test_v2_composer_strata():
    base = {"qa_flags": "", "imslp_match_status": "matched", "eu_pd_status": "pd", "style_tags_src": "",
            "llm_style_tags": "romantic"}
    assert gs.composer_stratum_v2(pd.Series(base)) == "consensus/pd"
    assert gs.composer_stratum_v2(pd.Series({**base, "style_tags_src": "wikidata"})) == "wikidata/pd"
    assert gs.composer_stratum_v2(pd.Series({**base, "llm_style_tags": "", "eu_pd_status": "not_pd"})) == \
        "no_consensus/not_pd"
    assert gs.composer_stratum_v2(pd.Series({**base, "imslp_match_status": "unverified_heuristic"})) == \
        "qa_or_unstable"
    assert gs.composer_stratum_v2(pd.Series({**base, "qa_flags": "birth_from_list"})) == "qa_or_unstable"


def test_v2_sample_deals_strata_across_packages_in_item_order():
    first, second = gs.build_sample(_composers(), _works(), **V2), gs.build_sample(_composers(), _works(), **V2)
    for kind in gs.SETS:
        for part in ("sheet", "recheck", "design"):
            pd.testing.assert_frame_equal(first[kind][part], second[kind][part])
        design = first[kind]["design"]
        assert design["package"].is_monotonic_increasing  # item ids run in package order
        sizes = design["package"].value_counts()
        assert sorted(sizes.index) == [1, 2, 3] and sizes.max() - sizes.min() <= 1
        spread = design.groupby("stratum")["package"].value_counts().unstack(fill_value=0)
        assert (spread.max(axis=1) - spread.min(axis=1) <= 1).all()
        recheck = set(first[kind]["recheck"]["item"])
        assert recheck and recheck <= set(design.loc[design["package"] == 1, "item"])
    sheet = first["composers"]["sheet"]
    assert list(sheet.columns) == ["item", *gs.SHOWN["composers"], *gs.GOLD_FIELDS["composers"][:-1],
                                   *gs.STYLE_FIELDS, "notes"]
    assert not {"llm_style_tags", "style_tags", "stratum", "package"} & set(sheet.columns)
    assert list(first["works"]["sheet"].columns) == ["item", *gs.SHOWN["works"], *gs.GOLD_FIELDS["works"]]
    assert {s.split("/")[0] for s in first["composers"]["design"]["stratum"]} >= {"wikidata", "consensus",
                                                                                     "no_consensus"}


def test_validate_and_import_styles(tmp_path):
    sheet = pd.DataFrame({"item": ["C1", "C2", "C3", "C4", "C5"], "imslp_url": [""] * 5,
                          "styles": ["romantic|national_folk", "unknown", "baroque|baroque", "rock", "a|b|c|d"],
                          "primary_period": ["romantic", "unknown", "jazz", "", ""], "style_source": [""] * 5})
    sheet = gs.ensure_gold_columns("composers", sheet)
    problems = gs.validate("composers", sheet)
    assert [p.split(" ")[0] for p in problems] == ["C3", "C3", "C4", "C5"]
    assert gs._cell("styles", ["romantic", " national_folk "]) == "romantic|national_folk"
    assert gs._cell("styles", "romantic | modern") == "romantic|modern"
    result = gs.build_sample(_composers(), _works(), **V2)
    for kind in gs.SETS:
        for part in ("sheet", "recheck"):
            result[kind][part].to_csv(tmp_path / f"{kind}_{part}.tsv", sep="\t", index=False)
    (tmp_path / "store" / "answers").mkdir(parents=True)
    (tmp_path / "store" / "answers" / "C001.json").write_text(json.dumps(
        {"item": "C001", "styles": ["late_romantic", "impressionism"], "primary_period": "late_romantic",
         "style_source": "Grove"}), encoding="utf-8")
    summary = gs.import_answers(tmp_path, tmp_path / "store")
    assert summary["composers_sheet.tsv"] == {"imported": 1, "problems": []}
    composers = pd.read_csv(tmp_path / "composers_sheet.tsv", sep="\t", dtype=str, keep_default_na=False)
    assert composers.loc[0, ["styles", "primary_period", "style_source"]].tolist() == [
        "late_romantic|impressionism", "late_romantic", "Grove"]


def test_masi_distance_and_krippendorff_alpha():
    from style_agreement import krippendorff_alpha, masi_distance
    assert masi_distance({"a"}, {"a"}) == 0 and masi_distance(set(), set()) == 0
    assert masi_distance({"a"}, {"b"}) == 1
    assert masi_distance({"a"}, {"a", "b"}) == pytest.approx(1 - 0.5 * 2 / 3)
    assert masi_distance({"a", "c"}, {"a", "b"}) == pytest.approx(1 - (1 / 3) * (1 / 3))
    # Krippendorff (2011), "Computing Krippendorff's alpha-reliability": 4 coders, 12 units, nominal α = 0.743.
    units = [[1, 1, 1], [2, 2, 3, 2], [3, 3, 3, 3], [3, 3, 3, 3], [2, 2, 2, 2], [1, 2, 3, 4], [4, 4, 4, 4],
             [1, 1, 2, 1], [2, 2, 2, 2], [5, 5, 5], [1, 1], [3]]
    assert krippendorff_alpha(units) == pytest.approx(0.743, abs=5e-4)
    assert krippendorff_alpha([[1, 1], [2, 2]]) == 1.0 and krippendorff_alpha([[1]]) is None


def test_style_sources_precision_recall_and_period():
    rows = pd.DataFrame({
        "composer_id": ["Q1", "Q2", "Q3", "Q4"], "stratum": ["s"] * 4, "stratum_size": [40] * 4,
        "styles": ["romantic|national_folk", "modern", "unknown", "serialism"],
        "primary_period": ["romantic", "modern", "unknown", "modern"],
        "dump_llm_style_tags": ["romantic", "modern|avant_garde", "late_romantic", ""],
        "dump_llm_style_period": ["romantic", "early_20th_century", "late_romantic", ""],
        "dump_style_tags": ["national_folk", "", "", "serialism"], "dump_style_tags_src": ["wikidata", "", "", "wikidata"],
    })
    labeller = {"Q1": {"styles": ["romantic", "national_folk"], "primary_period": "romantic"},
                "Q4": {"styles": [], "primary_period": "unknown"}}
    out = gs.style_sources(rows, {"m · grounded · v2": labeller}, {"Q1": {"romantic"}, "Q2": {"modern", "romantic"}})
    assert (out["labelled"], out["unknown"]) == (3, 1)
    llm = out["LLM consensus (dump)"]
    # Consensus tags: romantic ✓, modern ✓, avant_garde ✗ → precision 2/3; annotator tags: romantic,
    # national_folk, modern, serialism → recall 2/4. Q4 has no consensus and counts as no tags.
    assert (llm["precision_error"]["eligible"], llm["precision_error"]["errors"]) == (3, 1)
    assert (llm["recall_error"]["eligible"], llm["recall_error"]["errors"]) == (4, 2)
    assert (llm["no_shared_tag"]["eligible"], llm["no_shared_tag"]["errors"]) == (2, 0)
    assert (llm["period_error"]["eligible"], llm["period_error"]["errors"]) == (2, 1)
    wd = out["Wikidata movements (dump)"]
    # Wikidata recall counts movement tags only: national_folk (Q1) and serialism (Q4), both given.
    assert wd["composers"] == 2 and (wd["recall_error"]["eligible"], wd["recall_error"]["errors"]) == (2, 0)
    model = out["m · grounded · v2"]
    assert model["composers"] == 2 and model["recall_error"]["errors"] == 1 and model["mean_jaccard"] == 0.5
    imslp = out["IMSLP work periods (dump)"]
    assert "precision_error" not in imslp and imslp["period_error"]["eligible"] == 1  # Q2's tie is skipped


def test_agreement_table_with_no_style_pairs():
    empty = gs.agreement("composers", pd.DataFrame({"item": ["C001"], "styles": ["romantic"]}),
                         pd.DataFrame({"item": ["C001"], "styles": [""]}))
    assert empty["styles"]["pairs"] == 0
    assert "| styles | 0 | — | — |" in gs._agreement_table("Re-check:", empty)


def test_broad_romantic_remap_reads_the_entity_cache(monkeypatch):
    def entity(*qids):
        return {"claims": {"P135": [{"mainsnak": {"snaktype": "value", "datavalue": {"value": {"id": q}}}}
                                    for q in qids]}}
    cache = {"W1": entity("Q207591"), "W2": entity("Q1125039"), "W3": entity("Q37068", "Q136774417", "Q1196170")}
    monkeypatch.setattr(gs, "cache_get_readonly", lambda ns, key: cache.get(key))
    composers = pd.DataFrame({
        "composer_id": ["Q1", "Q2", "Q3", "Q4"], "wikidata_qid": ["W1", "W2", "W3", "W4"],
        "style_tags": ["late_romantic", "late_romantic", "late_romantic|national_folk", "serialism"],
        "style_tags_src": ["wikidata"] * 4,
    })
    # Broad only → romantic; narrow only → unchanged; both → both tags. Q4 has no late_romantic, no lookup.
    assert gs.broad_romantic_remap(composers) == {"Q1": {"romantic"},
                                                  "Q3": {"late_romantic", "romantic", "national_folk"}}
    del cache["W2"]
    assert gs.broad_romantic_remap(composers) is None  # a missing entry drops the remapped row

    rows = pd.DataFrame({
        "composer_id": ["Q1", "Q2"], "stratum": ["s"] * 2, "stratum_size": [40] * 2,
        "styles": ["romantic", "late_romantic"], "primary_period": ["romantic", "late_romantic"],
        "dump_llm_style_tags": ["", ""], "dump_llm_style_period": ["", ""],
        "dump_style_tags": ["late_romantic", "late_romantic"], "dump_style_tags_src": ["wikidata", "wikidata"],
    })
    out = gs.style_sources(rows, {}, {}, {"Q1": {"romantic"}})
    published, remapped = out["Wikidata movements (dump)"], out["Wikidata, broad Romantic as romantic"]
    # late_romantic is in Wikidata's vocabulary: Q2's tag counts for recall; Q1's is a precision error.
    assert (published["precision_error"]["errors"], published["recall_error"]["eligible"]) == (1, 1)
    assert (remapped["precision_error"]["errors"], remapped["recall_error"]["eligible"]) == (0, 2)
    assert "Wikidata, broad Romantic as romantic" not in gs.style_sources(rows, {}, {})


def _v2_gold(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "DATA_DIR", tmp_path)
    monkeypatch.setattr(gs, "DATA_DIR", tmp_path)
    (tmp_path / "gold").mkdir()
    (tmp_path / "gold" / "practice.json").write_text(gs.FORM_TEMPLATE.parent.parent.joinpath(
        "data", "gold", "practice.json").read_text(encoding="utf-8"), encoding="utf-8")
    for name in ("README.md", "annotator_guide.md", "style_definitions.tsv"):
        (tmp_path / "gold" / name).write_text(gs.FORM_TEMPLATE.parent.parent.joinpath(
            "data", "gold", name).read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(gs, "GUIDE", tmp_path / "gold" / "annotator_guide.md")
    monkeypatch.setattr(gs, "STYLE_DEFINITIONS", tmp_path / "gold" / "style_definitions.tsv")
    composers, works = _composers(), _works()
    works["genre_form"] = ""
    composers.to_csv(tmp_path / "composers_r900.tsv", sep="\t", index=False)
    works.to_csv(tmp_path / "works_r900.tsv", sep="\t", index=False)
    _write_parent(tmp_path / "gold" / "r900", gs.build_sample(composers, works, **V2))
    return tmp_path / "gold" / "r900"


def test_v2_kit_holds_only_the_packages_and_the_style_rules(tmp_path, monkeypatch):
    gold = _v2_gold(tmp_path, monkeypatch)
    out = gs.run_kit(argparse.Namespace(dump="r900", gold_dir=str(gold), out_dir=str(tmp_path / "kit"),
                                        packages="1", annotator="S02"))
    read = dict(sep="\t", dtype=str, keep_default_na=False)
    design = pd.read_csv(gold / "composers_design.tsv", **read)
    core = set(design.loc[design["package"] == "1", "item"])
    sheet = pd.read_csv(out / "data" / "gold" / "r900" / "composers_sheet.tsv", **read)
    assert set(sheet["item"]) == core and "styles" in sheet.columns
    rules = (out / "RULES.md").read_text(encoding="utf-8")
    assert "### Style (gold v2)" in rules and "## Style definitions" in rules and "`serialism`" in rules
    guide = (out / "README.md").read_text(encoding="utf-8")
    assert f"**{len(core)} composers**" in guide and "__" not in guide
    html = (out / "gold_form.html").read_text(encoding="utf-8")
    start = html.index('<script id="gold-data" type="application/json">')
    block = html[start:html.index("</script>", start)]
    data = json.loads(block[block.index(">") + 1:])
    assert data["style"] is True and [d["slug"] for d in data["styles"]] == [s for s, _ in gs.STYLE_VOCAB]
    assert data["eras"] == list(gs.ERA_SLUGS) and data["composers_recheck"] == []
    assert {i["item"] for i in data["composers"]} == core
    # Once the maintainer's core answers are in the gold dir, later kits still come out blank.
    main = pd.read_csv(gold / "composers_sheet.tsv", **read)
    main.loc[main["item"].isin(core), ["wikidata_same_person", "styles"]] = ["yes", "romantic"]
    main.to_csv(gold / "composers_sheet.tsv", sep="\t", index=False)
    out = gs.run_kit(argparse.Namespace(dump="r900", gold_dir=str(gold), out_dir=str(tmp_path / "kit3"),
                                        packages="1,2", annotator="S03"))
    sheet = pd.read_csv(out / "data" / "gold" / "r900" / "composers_sheet.tsv", **read)
    assert set(sheet["item"]) == set(design.loc[design["package"].isin(["1", "2"]), "item"])
    assert sheet[list(gs.answer_fields("composers", sheet.columns))].eq("").all().all()


def test_pool_prefers_a_complete_answer_over_an_earlier_partial_one():
    cols = ["item", "composer_id", *gs.answer_fields("composers", ["styles"])]
    blank = dict.fromkeys(cols, "")
    full = {**blank, "wikidata_same_person": "yes", "imslp_check": "none", "death_status": "dead",
            "death_source": "GND 118", "styles": "romantic", "primary_period": "romantic", "style_source": "Grove"}
    main = pd.DataFrame([{**blank, "item": "C001", "composer_id": "c1", "wikidata_same_person": "yes"},
                         {**full, "item": "C002", "composer_id": "c2"}])
    s02 = pd.DataFrame([{**full, "item": i, "composer_id": c, "styles": "modern"} for i, c in (("C001", "c1"), ("C002", "c2"))])
    pooled, counts = gs.pool("composers", {"main": main, "S02": s02})
    assert counts == {"main": 1, "S02": 1}
    assert pooled.set_index("item")["styles"].to_dict() == {"C001": "modern", "C002": "romantic"}
    unknown = pd.DataFrame([{**full, "styles": "unknown", "style_source": "", "primary_period": p} for p in ("romantic", "unknown")])
    assert gs._complete("composers", unknown).tolist() == [False, True]


def test_imslp_composer_periods_split_multi_period_cells():
    works = pd.DataFrame({"composer_id": ["c1", "c2"], "imslp_style": ["Romantic|Early 20th century", ""]})
    assert gs.imslp_composer_periods(works) == {"c1": {"romantic", "early_20th_century"}, "c2": set()}


def test_pooled_score_merges_contributors_and_reports_alpha(tmp_path, monkeypatch):
    gold = _v2_gold(tmp_path, monkeypatch)
    read = dict(sep="\t", dtype=str, keep_default_na=False)
    main = pd.read_csv(gold / "composers_sheet.tsv", **read)
    design = pd.read_csv(gold / "composers_design.tsv", **read)
    core = design.loc[design["package"] == "1", "item"].tolist()
    main.loc[main["item"].isin(core), ["wikidata_same_person", "styles", "primary_period", "style_source"]] = [
        "yes", "romantic|national_folk", "romantic", "Grove"]
    main.to_csv(gold / "composers_sheet.tsv", sep="\t", index=False)
    s02 = gold / "annotators" / "S02"
    s02.mkdir(parents=True)
    batch = main[main["item"].isin(core) | (main["item"] == design.loc[design["package"] == "2", "item"].iloc[0])].copy()
    batch.loc[batch.index[0], "styles"] = "romantic"
    batch.loc[batch.index[-1], ["wikidata_same_person", "styles", "primary_period", "style_source"]] = [
        "no", "modern", "modern", "MGG"]
    batch.to_csv(s02 / "composers_sheet.tsv", sep="\t", index=False)
    pd.read_csv(gold / "works_sheet.tsv", **read).head(0).to_csv(s02 / "works_sheet.tsv", sep="\t", index=False)
    report = gs.run_score(argparse.Namespace(dump="r900", gold_dir=None, out=str(tmp_path / "report.md"), json=None,
                                             annotator=None, compare=None, pool=True))
    assert report["pooled_from"]["composers"] == {"main": len(core), "S02": 1}
    assert report["composers"]["annotated"] == len(core) + 1
    contributors = report["composers"]["contributors"]
    assert contributors["styles"]["items"] == len(core)
    assert contributors["styles"]["agree"] == pytest.approx((len(core) - 1) / len(core))
    assert contributors["wikidata_same_person"]["agree"] == 1.0
    sources = report["composers"]["style_sources"]
    assert sources["labelled"] == len(core) + 1 and "LLM consensus (dump)" in sources
    markdown = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "pooled: composers main" in markdown and "; works none" in markdown
    assert "Style sources against the annotator" in markdown
    assert "Agreement across contributors" in markdown
    solo = gs.run_score(argparse.Namespace(dump="r900", gold_dir=None, out=str(tmp_path / "s02.md"), json=None,
                                           annotator="S02", compare=None, pool=False))
    assert solo["composers"]["annotated"] == len(core) + 1 and solo["works"]["annotated"] == 0
