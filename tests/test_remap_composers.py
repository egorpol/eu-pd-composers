import argparse
import json

import pandas as pd
import pytest
import requests

import common
import remap_composers


def entity(qid, death, *, precision=9, film=False):
    occupations = ["Q36834", "Q1415090"] if film else ["Q36834"]
    return {
        "id": qid,
        "claims": {
            "P31": [{"mainsnak": {"snaktype": "value", "datavalue": {
                "value": {"id": "Q5"},
            }}}],
            "P106": [{"mainsnak": {"snaktype": "value", "datavalue": {
                "value": {"id": occupation},
            }}} for occupation in occupations],
            "P569": [{"rank": "normal", "mainsnak": {
                "snaktype": "value", "datavalue": {"value": {
                    "time": "+1900-01-01T00:00:00Z", "precision": 9,
                }},
            }}],
            "P570": [{"rank": "preferred", "mainsnak": {
                "snaktype": "value", "datavalue": {"value": {
                    "time": f"+{death:04d}-00-00T00:00:00Z", "precision": precision,
                }},
            }}],
        },
    }


def composer(qid, name, death, *, scope="classical_core", occupations="composer"):
    pd_year = death + 71
    return {
        "composer_id": qid,
        "name_display": name,
        "name_aliases": "NA|None",
        "birth_year": "1900",
        "death_year": f"{death}.0",
        "date_precision": "year",
        "occupations": occupations,
        "scope_class": scope,
        "scope_class_src": "occupations",
        "eu_pd_year": str(pd_year),
        "eu_pd_status": "pd" if pd_year <= 2026 else "not_pd",
        "years_until_eu_pd": str(max(0, pd_year - 2026)),
        "citizenship_qids": "Q30|Q145",
        "style_tags": "crossover_popular|serialism",
        "style_tags_src": "llm",
        "imslp_works_count": "123.0",
        "works_count_by_category": '{"chamber": 2}',
        "schema_version": "3",
        "dump_date": "r008",
    }


@pytest.fixture(autouse=True)
def no_network_or_real_cache(monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail("Tests must not access the network or real cache")

    monkeypatch.setattr(requests.Session, "request", fail)
    monkeypatch.setattr(common, "cache_get", fail)
    monkeypatch.setattr(common, "cache_set", fail)


@pytest.fixture
def source(monkeypatch):
    composers = pd.DataFrame([
        composer("Q2", "Second", 1986, scope="film_media", occupations="composer|film_composer"),
        composer("Q1", "First", 1954),
        composer("Qmissing", "Missing", 1953, scope="film_media", occupations="film_composer"),
    ])
    entities = {
        "Q2": entity("Q2", 1987, film=True),
        "Q1": entity("Q1", 1955, precision=8),
    }
    calls = []

    def cache_get(namespace, qid):
        calls.append((namespace, qid))
        return entities.get(qid)

    monkeypatch.setattr(common, "cache_get", cache_get)
    return composers, calls


def test_remap_preserves_rows_and_unrelated_fields(source):
    composers, calls = source
    original = composers.copy(deep=True)
    out, report = remap_composers.remap_composers(composers, 2026)
    assert calls == [("wikidata_entity", qid) for qid in composers["composer_id"]]
    assert out["composer_id"].tolist() == ["Q2", "Q1", "Qmissing"]
    pd.testing.assert_frame_equal(composers, original)
    unchanged = [col for col in composers if col not in remap_composers.REMAP_FIELDS]
    pd.testing.assert_frame_equal(out[unchanged], composers[unchanged])
    insert_at = list(out.columns).index("scope_class_src") + 1
    assert list(out.columns)[insert_at:insert_at + 2] == ["is_film_composer", "qa_flags"]
    assert out["death_year"].tolist() == ["1987", "1959", "1953"]
    assert out["is_film_composer"].tolist() == ["true", "false", "true"]
    assert out["qa_flags"].tolist() == ["", "death_imprecise", ""]
    assert out["eu_pd_status"].tolist() == ["not_pd", "not_pd", "pd"]
    assert out["eu_pd_year"].tolist() == ["2058", "2030", "2024"]
    assert report["field_change_counts"] == {
        "birth_year": 0, "death_year": 2, "date_precision": 1,
        "scope_class": 1, "scope_class_src": 0, "is_film_composer": 3,
        "qa_flags": 1, "eu_pd_year": 2, "eu_pd_status": 1, "years_until_eu_pd": 2,
    }
    assert report["qa_flags_counts"]["death_imprecise"] == 1
    assert report["qa_flags_counts"]["death_before_birth"] == 0
    assert report["missing_entities"] == 1
    assert [row["name"] for row in report["changed_rows"]] == ["Second", "First"]
    assert report["film_media_remaining"] == 1
    assert report["film_media_examples"] == ["Missing"]


def test_existing_new_columns_and_missing_entity_are_preserved(source):
    composers, _ = source
    composers["is_film_composer"] = ["false", "false", "true"]
    composers["qa_flags"] = ["", "", "not_human"]
    out, report = remap_composers.remap_composers(composers, 2026)
    assert list(out.columns) == list(composers.columns)
    for field in composers:
        expected = "1953" if field == "death_year" else composers.at[2, field]
        assert out.at[2, field] == expected
    assert report["qa_flags_counts"]["not_human"] == 1


def test_preserves_qa_flags_from_later_pipeline_stages(source):
    composers, _ = source
    composers["qa_flags"] = ["death_rank_conflict|manual_override|imslp_dates_conflict",
                              "imslp_p839_wrong", "manual_override"]
    out, _ = remap_composers.remap_composers(composers, 2026)
    assert out.qa_flags.tolist() == ["manual_override|imslp_dates_conflict",
                                    "death_imprecise|imslp_p839_wrong", "manual_override"]


@pytest.fixture
def dump_files(tmp_path, monkeypatch, source):
    composers, calls = source
    monkeypatch.setattr(common, "DATA_DIR", tmp_path)
    composers.to_csv(tmp_path / "composers_r008.tsv", sep="\t", index=False)
    works = pd.DataFrame([{
        "composer_id": "Q2", "title": "NA", "has_files": "false",
        "dump_date": "r008", "fetched_at": "None", "imslp_pageid": "123.0",
    }])
    works.to_csv(tmp_path / "works_r008.tsv", sep="\t", index=False)
    (tmp_path / "dump_meta_r008.json").write_text(json.dumps({
        "created_at_utc": "2020-01-01T00:00:00+00:00",
    }))
    return tmp_path, works, calls


def args(*, dry_run=False):
    return argparse.Namespace(from_dump="r008", to="r999", dry_run=dry_run)


def test_dry_run_reports_all_changes_without_writing(dump_files, capsys):
    directory, _, _ = dump_files
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    remap_composers.run(args(dry_run=True))
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == before
    output = capsys.readouterr().out
    assert "PD reference year 2020" in output
    assert "Second (Q2): death_year: 1986 → 1987" in output
    assert "First (Q1): death_year: 1954 → 1959; eu_pd_status: pd → not_pd" in output
    assert "death_imprecise: 1 — First" in output
    assert "Missing cached entities: 1" in output


@pytest.mark.parametrize("has_dump_date", [True, False])
def test_writer_preserves_works_and_records_meta(dump_files, has_dump_date):
    directory, works, _ = dump_files
    if not has_dump_date:
        works = works.drop(columns=["dump_date"])
        works.to_csv(directory / "works_r008.tsv", sep="\t", index=False)
    source_bytes = (directory / "composers_r008.tsv").read_bytes()
    remap_composers.run(args())
    assert (directory / "composers_r008.tsv").read_bytes() == source_bytes
    written = pd.read_csv(
        directory / "works_r999.tsv", sep="\t", dtype=str, keep_default_na=False,
    )
    expected = works.copy()
    if has_dump_date:
        expected["dump_date"] = "r999"
    pd.testing.assert_frame_equal(written, expected)
    out = pd.read_csv(
        directory / "composers_r999.tsv", sep="\t", dtype=str, keep_default_na=False,
    )
    assert out["death_year"].tolist() == ["1987", "1959", "1953"]
    assert out["dump_date"].tolist() == ["r999"] * 3
    assert out["name_aliases"].tolist() == ["NA|None"] * 3
    assert out["imslp_works_count"].tolist() == ["123.0"] * 3
    assert out["years_until_eu_pd"].tolist() == ["38", "10", "0"]
    meta = json.loads((directory / "dump_meta_r999.json").read_text())
    assert meta["derived_from_dump_id"] == "r008"
    assert meta["enrichment"] == "remap_composers_wikidata"
    assert meta["row_counts"] == {"composers": 3, "works": 1}
    assert meta["field_change_counts"]["death_year"] == 2
    assert meta["qa_flags_counts"]["death_imprecise"] == 1
    assert meta["missing_entities"] == 1
    assert meta["pd_reference_year"] == 2020


@pytest.mark.parametrize("filename", [
    "composers_r999.tsv", "works_r999.tsv", "dump_meta_r999.json",
])
def test_writer_refuses_overwrite_before_any_output(dump_files, filename):
    directory, _, _ = dump_files
    (directory / filename).write_text("sentinel")
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        remap_composers.run(args())
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == before


def test_qa_examples_are_limited_to_fifteen(monkeypatch):
    composers = pd.DataFrame([composer(f"Q{i}", f"Name {i}", 1954) for i in range(20)])
    monkeypatch.setattr(common, "cache_get", lambda _, qid: entity(qid, 1955, precision=8))
    _, report = remap_composers.remap_composers(composers, 2026)
    assert report["qa_flags_counts"]["death_imprecise"] == 20
    assert report["qa_flags_examples"]["death_imprecise"] == [f"Name {i}" for i in range(15)]
