import pytest

from wikidata_enrich import apply_year_fallbacks, enrich_from_entity, infer_scope_class


def time_claim(year, *, rank="normal", precision=9, snaktype="value"):
    return {
        "rank": rank,
        "mainsnak": {
            "snaktype": snaktype,
            "datavalue": {
                "value": {
                    "time": f"+{year:04d}-01-01T00:00:00Z",
                    "precision": precision,
                }
            },
        },
    }


def id_claim(qid):
    return {"mainsnak": {"snaktype": "value", "datavalue": {"value": {"id": qid}}}}


def entity(*, births=(), deaths=(), instances=("Q5",), occupations=("Q36834",)):
    return {
        "id": "Q123",
        "claims": {
            "P569": list(births),
            "P570": list(deaths),
            "P31": [id_claim(qid) for qid in instances],
            "P106": [id_claim(qid) for qid in occupations],
        },
    }


@pytest.mark.parametrize("pid, field", [("P569", "birth_year"), ("P570", "death_year")])
def test_deprecated_first_is_never_used(pid, field):
    ent = entity()
    ent["claims"][pid] = [
        time_claim(1986, rank="deprecated"),
        time_claim(1987, rank="preferred"),
    ]
    result = enrich_from_entity(ent)
    assert result[field] == 1987
    assert "rank_conflict" not in result["qa_flags"]


def test_preferred_not_first_wins_and_ignores_normal_conflict():
    result = enrich_from_entity(entity(deaths=[
        time_claim(1918), time_claim(1919), time_claim(1916, rank="preferred"),
    ]))
    assert result["death_year"] == 1916
    assert result["qa_flags"] == ""


@pytest.mark.parametrize("rank", ["normal", "preferred"])
def test_best_rank_conflicts_keep_first_value(rank):
    result = enrich_from_entity(entity(
        births=[time_claim(1900, rank=rank), time_claim(1901, rank=rank)],
        deaths=[time_claim(1970, rank=rank), time_claim(1971, rank=rank)],
    ))
    assert result["birth_year"] == 1900
    assert result["death_year"] == 1970
    assert result["qa_flags"] == "birth_rank_conflict|death_rank_conflict"


def test_same_year_dates_do_not_conflict():
    claims = [time_claim(1970, precision=11), time_claim(1970, precision=11)]
    claims[1]["mainsnak"]["datavalue"]["value"]["time"] = "+1970-12-31T00:00:00Z"
    result = enrich_from_entity(entity(deaths=claims))
    assert result["death_date"] == "1970-01-01"
    assert result["qa_flags"] == ""


@pytest.mark.parametrize("snaktype", ["novalue", "somevalue"])
def test_nonvalue_snaks_are_ignored_even_with_datavalue(snaktype):
    result = enrich_from_entity(entity(deaths=[
        time_claim(1986, rank="preferred", snaktype=snaktype), time_claim(1987),
    ]))
    assert result["death_year"] == 1987
    assert result["qa_flags"] == ""


def test_no_usable_time_values():
    result = enrich_from_entity(entity(deaths=[
        time_claim(1986, rank="deprecated"),
        {"rank": "preferred", "mainsnak": {"snaktype": "novalue"}},
        {"rank": "preferred", "mainsnak": {"snaktype": "somevalue"}},
        {"rank": "normal", "mainsnak": {"snaktype": "value"}},
    ]))
    assert result["death_year"] is None
    assert result["death_date"] is None
    assert result["date_precision"] == "unknown"
    assert result["qa_flags"] == ""


def test_malformed_preferred_value_falls_back_to_normal():
    malformed = time_claim(1900, rank="preferred")
    malformed["mainsnak"]["datavalue"]["value"]["time"] = "bad time"
    result = enrich_from_entity(entity(deaths=[malformed, time_claim(1970)]))
    assert result["death_year"] == 1970


@pytest.mark.parametrize("year, precision, latest, label", [
    (1850, 8, 1859, "decade"),
    (1855, 8, 1859, "decade"),
    (1859, 8, 1859, "decade"),
    (1801, 7, 1900, "century"),
    (1900, 7, 1900, "century"),
    (1901, 7, 2000, "century"),
    (1950, 7, 2000, "century"),
    (2000, 7, 2000, "century"),
    (1001, 6, 2000, "millennium"),
    (2000, 6, 2000, "millennium"),
    (2000, 0, 1_000_000_000, "billion_years"),
])
def test_coarse_time_uses_wikidata_interval_end(year, precision, latest, label):
    result = enrich_from_entity(entity(deaths=[time_claim(year, precision=precision)]))
    assert result["death_year"] == latest
    assert result["death_date"] == str(latest)
    assert result["date_precision"] == label
    assert result["qa_flags"] == "death_imprecise"


def test_birth_imprecision_and_death_precision_priority():
    result = enrich_from_entity(entity(
        births=[time_claim(1801, precision=7)], deaths=[time_claim(1962, precision=11)],
    ))
    assert result["birth_year"] == 1900
    assert result["date_precision"] == "day"
    assert result["qa_flags"] == "birth_imprecise"


@pytest.mark.parametrize("instances, expected", [
    (("Q4167410",), "not_human"),
    (("Q16334295",), "not_human"),
    ((), "not_human"),
    (("Q4167410", "Q5"), ""),
])
def test_instance_of_requires_human(instances, expected):
    assert enrich_from_entity(entity(instances=instances))["qa_flags"] == expected


@pytest.mark.parametrize("lifespan, expected", [
    (14, "implausible_lifespan"), (15, ""), (110, ""),
    (111, "implausible_lifespan"), (161, "implausible_lifespan"),
    (-1, "implausible_lifespan|death_before_birth"),
])
def test_lifespan_flags_are_informational(lifespan, expected):
    result = enrich_from_entity(entity(
        births=[time_claim(1801)], deaths=[time_claim(1801 + lifespan)],
    ))
    assert result["birth_year"] == 1801
    assert result["death_year"] == 1801 + lifespan
    assert result["qa_flags"] == expected


@pytest.mark.parametrize("occupation_qids, scope, film", [
    (("Q36834", "Q1415090"), "classical_core", "true"),
    (("Q21680663", "Q1415090"), "classical_core", "true"),
    (("Q1415090",), "film_media", "true"),
    (("Q753110",), "popular", "false"),
    (("Q15981151",), "popular", "false"),
    (("Q158852",), "crossover", "false"),
    ((), "classical_core", "false"),
])
def test_scope_and_film_flag(occupation_qids, scope, film):
    result = enrich_from_entity(entity(occupations=occupation_qids))
    assert result["scope_class"] == scope
    assert result["is_film_composer"] == film


def test_crossover_tag_keeps_precedence_over_composer():
    assert infer_scope_class(["composer"], ["crossover_popular"]) == (
        "crossover", "style_tags",
    )
    assert infer_scope_class([], ["crossover_popular"]) == ("crossover", "style_tags")
    assert infer_scope_class(["conductor"], ["crossover_popular"]) == (
        "crossover", "style_tags",
    )


def test_politician_item_is_flagged_as_no_composer():
    # Michael Howard (composer) resolved to the politician's QID in r008.
    result = enrich_from_entity(entity(occupations=("Q82955",)))
    assert "no_composer_occupation" in result["qa_flags"]


def test_list_fallback_fills_missing_wikidata_year():
    # Józef Koffler: P570 is "unknown value"; the Wikipedia list gives 1944.
    ent = entity(
        births=[time_claim(1896, precision=11)],
        deaths=[time_claim(1944, snaktype="somevalue")],
    )
    years = apply_year_fallbacks(
        enrich_from_entity(ent), ent["claims"], fallback_birth=1896, fallback_death=1944
    )
    assert years["death_year"] == 1944
    assert years["date_precision"] == "year"
    assert years["qa_flags"] == "death_from_list"


def test_fallback_equal_to_deprecated_year_is_rejected():
    # Tania León: only a deprecated 1996 death claim; she is alive.
    ent = entity(
        births=[time_claim(1943, precision=11)],
        deaths=[time_claim(1996, rank="deprecated")],
    )
    years = apply_year_fallbacks(
        enrich_from_entity(ent), ent["claims"], fallback_birth=1943, fallback_death=1996
    )
    assert years["death_year"] is None
    assert years["qa_flags"] == ""


def test_lifespan_flags_use_final_years():
    ent = entity(births=[time_claim(1801)])
    years = apply_year_fallbacks(
        enrich_from_entity(ent), ent["claims"], fallback_birth=None, fallback_death=1962
    )
    assert years["qa_flags"] == "implausible_lifespan|death_from_list"
