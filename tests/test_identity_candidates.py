"""Offline parts of identity_candidates.py: suspect selection and year agreement."""

import pandas as pd
import pytest

import common
import identity_candidates as ic


def person(qid, birth, death, occupations=("Q36834",)):
    def year(y):
        return [{"rank": "normal", "mainsnak": {"snaktype": "value", "datavalue": {
            "value": {"time": f"+{y}-01-01T00:00:00Z", "precision": 9}}}}]
    return {"id": qid, "claims": {
        "P31": [{"mainsnak": {"snaktype": "value", "datavalue": {"value": {"id": "Q5"}}}}],
        "P106": [{"mainsnak": {"snaktype": "value", "datavalue": {"value": {"id": o}}}} for o in occupations],
        "P569": year(birth), "P570": year(death)}}


@pytest.mark.parametrize("args, agree", [
    ((1920, 1989, 1920, 1989), True), ((1920, 1989, 1921, 1988), True), ((1920, 1989, 1870, 1942), False),
    ((1920, None, 1920, 1989), True), ((None, None, 1920, 1989), False),
])
def test_years_agree(args, agree):
    assert ic.years_agree(*args) is agree


def test_suspects_flag_year_gaps_and_occupations(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "CACHE_DIR", tmp_path)
    common.cache_set("wikidata_entity", "Q134085", person("Q134085", 1870, 1942, occupations=("Q169470",)))
    common.cache_set("wikidata_entity", "Q2", person("Q2", 1900, 1950))
    common.cache_set("wikidata_entity", "Q3", person("Q3", 1900, 1953))
    composers = pd.DataFrame([
        {"composer_id": "Q134085", "name_display": "Jean Perrin", "wikipedia_url": "https://en.wikipedia.org/wiki/Jean_Perrin"},
        {"composer_id": "Q2", "name_display": "Fine", "wikipedia_url": "https://en.wikipedia.org/wiki/Fine"},
        {"composer_id": "Q3", "name_display": "Small gap", "wikipedia_url": "https://en.wikipedia.org/wiki/Small_gap"},
    ])
    list_rows = [
        {"URL": "https://en.wikipedia.org/wiki/Jean_Perrin", "Year of birth": "1920", "Year of death": "1989",
         "Nationality": "Swiss"},
        {"URL": "https://en.wikipedia.org/wiki/Fine", "Year of birth": "1900", "Year of death": "1950"},
        {"URL": "https://en.wikipedia.org/wiki/Small_gap", "Year of birth": "1900", "Year of death": "1950"},
    ]
    found = ic.suspects(composers, list_rows, min_gap=5)
    assert [(s["composer_id"], s["signals"], s["list_years"], s["wikidata_years"]) for s in found] == [
        ("Q134085", "list_years_gap_50|no_composer_occupation", "1920–1989", "1870–1942")]
    assert [s["composer_id"] for s in ic.suspects(composers, list_rows, min_gap=3)] == ["Q134085", "Q3"]
