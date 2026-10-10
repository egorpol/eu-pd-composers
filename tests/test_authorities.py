"""Tests for authorities.py, refetch_authorities.py and remap_authorities.py (no network)."""

from __future__ import annotations

import json

import pandas as pd
import pytest

import authorities
import common
import remap_authorities
from build_dump import list_years_flags


def string_claim(value, rank="normal"):
    return {"rank": rank, "mainsnak": {"snaktype": "value", "datavalue": {"value": value}}}


def entity(qid, *, gnd=(), viaf=(), label="Name"):
    claims = {}
    if gnd:
        claims["P227"] = [string_claim(*g) if isinstance(g, tuple) else string_claim(g) for g in gnd]
    if viaf:
        claims["P214"] = [string_claim(v) for v in viaf]
    return {"id": qid, "labels": {"en": {"value": label}}, "claims": claims}


def record(gnd_id, born, died=None, professions=()):
    return {
        "gndIdentifier": gnd_id, "preferredName": "X", "type": ["DifferentiatedPerson"],
        "dateOfBirth": [born] if born else [], "dateOfDeath": [died] if died else [],
        "professions": list(professions), "profession_labels": [],
    }


COMPOSER = "https://d-nb.info/gnd/4032009-1"


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "CACHE_DIR", tmp_path / "cache")
    return tmp_path / "cache"


def test_wikidata_ids_prefer_preferred_rank_and_skip_deprecated_or_malformed():
    ent = entity("Q1", gnd=[("118500775", "deprecated"), ("not-a-gnd",), ("11850077X",), ("118551027", "preferred")],
                 viaf=["0123", "30939465"])
    assert authorities.wikidata_authority_ids(ent) == {"gnd_id": "118551027", "viaf_id": "30939465"}
    assert authorities.wikidata_authority_ids({"claims": {}}) == {"gnd_id": "", "viaf_id": ""}


@pytest.mark.parametrize("value, year", [
    (["1942"], 1942), (["1865-05-22"], 1865), ("1901", 1901), ([], None), (None, None), (["-0500"], None),
])
def test_year_of(value, year):
    assert authorities.year_of(value) == year


def test_match_needs_one_candidate_with_agreeing_dates():
    morera = record("124446809", "1865-05-22", "1942", [COMPOSER])
    namesake = record("124446841", "1959")
    assert authorities.match_candidates([namesake, morera], 1865, 1942) == morera
    # Death years must agree when both are known.
    assert authorities.match_candidates([morera], 1865, 1943) is None
    # No birth year in the dump: no name match at all.
    assert authorities.match_candidates([morera], None, 1942) is None
    # A candidate without a death year needs a composer profession.
    living = record("1012345678", "1950")
    assert authorities.match_candidates([living], 1950, 2020) is None
    assert authorities.match_candidates([record("1012345678", "1950", professions=[COMPOSER])], 1950, 2020)
    # Two agreeing candidates are ambiguous.
    twin = record("124446817", "1865", "1942")
    assert authorities.match_candidates([morera, twin], 1865, 1942) is None


class FakeSession:
    def __init__(self, members):
        self.members = members
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(params)
        members = self.members(params)

        class Response:
            status_code = 200
            headers = {}

            def raise_for_status(self):
                pass

            def json(self):
                return {"member": members}

        return Response()


def test_fetch_records_batches_and_caches_missing_ids(cache):
    full = {"gndIdentifier": "118551027", "preferredName": "Hilger", "dateOfDeath": ["1996-12-25"],
            "professionOrOccupation": [{"id": "https://d-nb.info/gnd/4130076-2", "label": "Juristin"}]}
    session = FakeSession(lambda params: [full])
    stats = authorities.fetch_records(["118551027", "118500775", "118551027"], session, batch_size=50)
    assert stats == {"requested": 2, "found": 1, "missing": 1}
    assert len(session.calls) == 1 and "OR" in session.calls[0]["q"]
    assert common.cache_get(authorities.RECORD_NAMESPACE, "118551027")["professions"] == [
        "https://d-nb.info/gnd/4130076-2"]
    assert common.cache_get(authorities.RECORD_NAMESPACE, "118500775") == {
        "gndIdentifier": "118500775", "missing": True}
    # Cached ids are not requested again.
    assert authorities.fetch_records(["118551027", "118500775"], session)["requested"] == 0
    assert len(session.calls) == 1


def test_search_people_reuses_cache_for_the_same_name(cache):
    session = FakeSession(lambda params: [{"gndIdentifier": "124446809", "dateOfBirth": ["1865"]}])
    first = authorities.search_people("Q1", "Enric Morera", session)
    again = authorities.search_people("Q1", "Enric Morera", session)
    assert first == again and len(session.calls) == 1
    assert session.calls[0]["filter"] == "type:DifferentiatedPerson"
    authorities.search_people("Q1", "Enric Morera i Viura", session)
    assert len(session.calls) == 2


def composer_row(qid, name, birth, death, *, flags="", status="pd", **extra):
    return {"composer_id": qid, "name_display": name, "wikidata_qid": qid, "birth_year": str(birth),
            "death_year": str(death), "qa_flags": flags, "eu_pd_status": status, "dump_date": "r001", **extra}


def test_remap_authorities_fills_ids_fallback_and_conflicts(cache):
    common.cache_set("wikidata_entity", "Q1", entity("Q1", gnd=["118551027"], viaf=["30939465"]))
    common.cache_set("wikidata_entity", "Q2", entity("Q2", viaf=["12345"]))
    common.cache_set("wikidata_entity", "Q3", entity("Q3"))
    common.cache_set("wikidata_entity", "Q5", entity("Q5", gnd=["118500775"]))
    common.cache_set(authorities.RECORD_NAMESPACE, "118551027", record("118551027", "1900", "1951"))
    common.cache_set(authorities.RECORD_NAMESPACE, "118500775", {"gndIdentifier": "118500775", "missing": True})
    common.cache_set(authorities.SEARCH_NAMESPACE, "Q2", {
        "name": "Bob", "candidates": [record("124446809", "1865-05-22", "1942", [COMPOSER])]})
    common.cache_set(authorities.SEARCH_NAMESPACE, "Q3", {"name": "Carol", "candidates": []})
    composers = pd.DataFrame([
        composer_row("Q1", "Alice", 1900, 1950, flags="birth_imprecise|gnd_from_lobid"),
        composer_row("Q2", "Bob", 1865, 1942),
        composer_row("Q3", "Carol", 1901, 1960, flags="gnd_death_conflict"),
        composer_row("Q4", "Dan", 1900, 1950),
        composer_row("Q5", "Eve", 1910, 1970),
    ])
    out, report = remap_authorities.remap_authorities(composers)
    assert list(out.columns[:5]) == ["composer_id", "name_display", "wikidata_qid", "gnd_id", "viaf_id"]
    rows = out.set_index("composer_id")
    assert rows.loc["Q1", ["gnd_id", "viaf_id", "qa_flags"]].tolist() == [
        "118551027", "30939465", "birth_imprecise|gnd_death_conflict"]
    assert rows.loc["Q2", ["gnd_id", "viaf_id", "qa_flags"]].tolist() == ["124446809", "12345", "gnd_from_lobid"]
    assert rows.loc["Q3", ["gnd_id", "viaf_id", "qa_flags"]].tolist() == ["", "", ""]
    # No cached entity: the row is left as it was.
    assert rows.loc["Q4", ["gnd_id", "viaf_id", "qa_flags"]].tolist() == ["", "", ""]
    assert rows.loc["Q5", "gnd_id"] == "118500775"
    counts = report["counts"]
    assert (counts["gnd_wikidata"], counts["gnd_lobid"], counts["missing_entity"], counts["no_gnd_or_viaf"]) == (2, 1, 1, 1)
    assert report["gnd_death_conflicts"] == [{
        "name": "Alice", "qid": "Q1", "gnd_id": "118551027", "death_year": 1950, "gnd_death_year": 1951,
        "eu_pd_status": "pd"}]
    assert [r["qid"] for r in report["gnd_not_in_lobid"]] == ["Q5"]


def test_remap_authorities_keeps_prior_cells_when_caches_are_missing(cache):
    common.cache_set("wikidata_entity", "Q1", entity("Q1", gnd=["118551027"]))
    common.cache_set("wikidata_entity", "Q2", entity("Q2"))
    composers = pd.DataFrame([
        composer_row("Q1", "Alice", 1900, 1950, flags="gnd_death_conflict", gnd_id="118551027", viaf_id=""),
        composer_row("Q2", "Bob", 1865, 1942, flags="gnd_from_lobid", gnd_id="124446809", viaf_id=""),
    ])
    out, report = remap_authorities.remap_authorities(composers)
    rows = out.set_index("composer_id")
    assert rows.loc["Q1", ["gnd_id", "qa_flags"]].tolist() == ["118551027", "gnd_death_conflict"]
    assert rows.loc["Q2", ["gnd_id", "qa_flags"]].tolist() == ["124446809", "gnd_from_lobid"]
    assert (report["counts"]["missing_record"], report["counts"]["kept_prior"]) == (1, 1)


def test_stage_writes_revision_and_preserve_schema_skips(cache, tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(common, "DATA_DIR", data)
    common.cache_set("wikidata_entity", "Q1", entity("Q1", gnd=["118551027"]))
    pd.DataFrame([composer_row("Q1", "Alice", 1900, 1950)]).to_csv(data / "composers_r001.tsv", sep="\t", index=False)
    pd.DataFrame([{"work_id": "W1", "composer_id": "Q1"}]).to_csv(data / "works_r001.tsv", sep="\t", index=False)
    args = ["--from-dump", "r001", "--to", "r002", "--preserve-schema"]
    monkeypatch.setattr("sys.argv", ["remap_authorities.py", *args])
    remap_authorities.main()
    kept = pd.read_csv(data / "composers_r002.tsv", sep="\t", dtype=str, keep_default_na=False)
    assert "gnd_id" not in kept.columns and kept["dump_date"].tolist() == ["r002"]
    monkeypatch.setattr("sys.argv", ["remap_authorities.py", "--from-dump", "r001", "--to", "r003"])
    remap_authorities.main()
    added = pd.read_csv(data / "composers_r003.tsv", sep="\t", dtype=str, keep_default_na=False)
    assert added["gnd_id"].tolist() == ["118551027"]
    meta = json.loads((data / "dump_meta_r003.json").read_text(encoding="utf-8"))
    assert meta["enrichment"] == "remap_authorities" and meta["authorities"]["counts"]["gnd_id"] == 1


@pytest.mark.parametrize("wd, birth, death, flags", [
    ({"birth_year": 1870, "death_year": 1942}, 1920, None, ["list_years_conflict"]),
    ({"birth_year": 1870, "death_year": 1942}, 1870, 1943, ["list_years_conflict"]),
    ({"birth_year": 1870, "death_year": 1942}, 1870, 1942, []),
    ({"birth_year": None, "death_year": 1942}, 1871, None, []),
])
def test_list_years_flags(wd, birth, death, flags):
    assert list_years_flags(wd, birth, death) == flags
