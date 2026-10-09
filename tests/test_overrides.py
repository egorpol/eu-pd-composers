"""Tests for hand-reviewed composer overrides (synthetic DataFrames only)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
import pytest

import apply_overrides
import common
import overrides


META = {
    "reason": "hand check",
    "source": "https://example.test/note",
    "reviewer": "tester",
    "date": "2026-10-09",
}


def override_row(composer_id, field, value, **extra):
    row = {
        "composer_id": composer_id,
        "field": field,
        "value": value,
        **META,
    }
    row.update(extra)
    return row


def composers_frame():
    return pd.DataFrame(
        [
            {
                "composer_id": "Q1",
                "name_display": "Alice",
                "name_sort": "Alice",
                "name_aliases": "Old Alias",
                "birth_year": "1900",
                "death_year": "1950",
                "date_precision": "year",
                "wikipedia_url": "https://en.wikipedia.org/wiki/Alice",
                "wikidata_qid": "Q1",
                "wikidata_url": "https://www.wikidata.org/wiki/Q1",
                "citizenship_qids": "Q30",
                "citizenship_iso": "US",
                "occupations": "politician",
                "scope_class": "classical_core",
                "scope_class_src": "default",
                "is_film_composer": "false",
                "qa_flags": "no_composer_occupation",
                "eu_pd_year": "2021",
                "eu_pd_status": "pd",
                "years_until_eu_pd": "0",
                "style_tags": "serialism",
                "style_tags_src": "llm",
                "notable_works_qids": "",
                "imslp_category": "Category:Alice",
                "imslp_match_status": "unverified_heuristic",
                "imslp_match_method": "exact_name",
                "work_categories_present": "keyboard_other",
                "works_count_by_category": '{"keyboard_other": 1}',
                "works_count_total": "1",
                "imslp_works_count": "1",
                "pageviews_enwiki": "99",
                "dump_date": "r010",
                "untouched_col": "keep-me",
            },
            {
                "composer_id": "Q2",
                "name_display": "Bob",
                "name_sort": "Bob",
                "name_aliases": "",
                "birth_year": "1900",
                "death_year": "1980",
                "date_precision": "year",
                "wikipedia_url": "https://en.wikipedia.org/wiki/Bob",
                "wikidata_qid": "Q2",
                "wikidata_url": "https://www.wikidata.org/wiki/Q2",
                "citizenship_qids": "",
                "citizenship_iso": "",
                "occupations": "composer",
                "scope_class": "classical_core",
                "scope_class_src": "occupations",
                "is_film_composer": "false",
                "qa_flags": "",
                "eu_pd_year": "2051",
                "eu_pd_status": "not_pd",
                "years_until_eu_pd": "25",
                "style_tags": "",
                "style_tags_src": "",
                "notable_works_qids": "",
                "imslp_category": "Category:Bob",
                "imslp_match_status": "matched",
                "imslp_match_method": "wikidata_p839",
                "work_categories_present": "chamber",
                "works_count_by_category": '{"chamber": 2}',
                "works_count_total": "2",
                "imslp_works_count": "2",
                "pageviews_enwiki": "10",
                "dump_date": "r010",
                "untouched_col": "also-keep",
            },
            {
                "composer_id": "Q3",
                "name_display": "Carol",
                "name_sort": "Carol",
                "name_aliases": "",
                "birth_year": "1900",
                "death_year": "1940",
                "date_precision": "year",
                "wikipedia_url": "https://en.wikipedia.org/wiki/Carol",
                "wikidata_qid": "Q3",
                "wikidata_url": "https://www.wikidata.org/wiki/Q3",
                "citizenship_qids": "",
                "citizenship_iso": "",
                "occupations": "",
                "scope_class": "classical_core",
                "scope_class_src": "default",
                "is_film_composer": "false",
                "qa_flags": "not_human|no_composer_occupation",
                "eu_pd_year": "2011",
                "eu_pd_status": "pd",
                "years_until_eu_pd": "0",
                "style_tags": "",
                "style_tags_src": "",
                "notable_works_qids": "",
                "imslp_category": "",
                "imslp_match_status": "not_found",
                "imslp_match_method": "exact_name",
                "work_categories_present": "",
                "works_count_by_category": "",
                "works_count_total": "0",
                "imslp_works_count": "0",
                "pageviews_enwiki": "1",
                "dump_date": "r010",
                "untouched_col": "carol-keep",
            },
        ]
    )


def works_frame():
    return pd.DataFrame(
        [
            {
                "work_id": "W1",
                "composer_id": "Q1",
                "title": "Sonata",
                "force_family": "keyboard_other",
                "dump_date": "r010",
            },
            {
                "work_id": "W2",
                "composer_id": "Q2",
                "title": "Quartet",
                "force_family": "chamber",
                "dump_date": "r010",
            },
            {
                "work_id": "W3",
                "composer_id": "Q2",
                "title": "Trio",
                "force_family": "chamber",
                "dump_date": "r010",
            },
            {
                "work_id": "W4",
                "composer_id": "Q3",
                "title": "Noise",
                "force_family": "other",
                "dump_date": "r010",
            },
        ]
    )


def overrides_df(rows):
    return pd.DataFrame(rows, columns=list(overrides.OVERRIDE_COLUMNS))


def write_overrides_tsv(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    overrides_df(rows).to_csv(path, sep="\t", index=False)


def entity(
    qid,
    *,
    birth=1922,
    death=2002,
    occupations=("Q36834",),
    citizenship=(),
    p839=None,
    aliases=(),
    instance=("Q5",),
):
    claims = {
        "P31": [
            {
                "mainsnak": {
                    "snaktype": "value",
                    "datavalue": {"value": {"id": iid}},
                }
            }
            for iid in instance
        ],
        "P106": [
            {
                "mainsnak": {
                    "snaktype": "value",
                    "datavalue": {"value": {"id": oid}},
                }
            }
            for oid in occupations
        ],
        "P569": [
            {
                "rank": "normal",
                "mainsnak": {
                    "snaktype": "value",
                    "datavalue": {
                        "value": {
                            "time": f"+{birth:04d}-01-01T00:00:00Z",
                            "precision": 9,
                        }
                    },
                },
            }
        ],
        "P570": [
            {
                "rank": "normal",
                "mainsnak": {
                    "snaktype": "value",
                    "datavalue": {
                        "value": {
                            "time": f"+{death:04d}-01-01T00:00:00Z",
                            "precision": 9,
                        }
                    },
                },
            }
        ],
    }
    if citizenship:
        claims["P27"] = [
            {
                "mainsnak": {
                    "snaktype": "value",
                    "datavalue": {"value": {"id": cid}},
                }
            }
            for cid in citizenship
        ]
    if p839 is not None:
        claims["P839"] = [
            {
                "mainsnak": {
                    "snaktype": "value",
                    "datavalue": {"value": p839},
                }
            }
        ]
    return {
        "id": qid,
        "labels": {"en": {"value": qid}},
        "aliases": {
            "en": [{"value": a} for a in aliases],
        },
        "claims": claims,
    }


def country_entity(qid, iso):
    return {
        "id": qid,
        "claims": {
            "P297": [
                {
                    "mainsnak": {
                        "snaktype": "value",
                        "datavalue": {"value": iso},
                    }
                }
            ]
        },
    }


def cache_entity(qid, payload):
    common.cache_set("wikidata_entity", qid, payload)


# --- load_overrides validation ---


def test_load_overrides_empty_header_only(tmp_path):
    path = tmp_path / "composers.tsv"
    path.write_text("\t".join(overrides.OVERRIDE_COLUMNS) + "\n", encoding="utf-8")
    df = overrides.load_overrides(path)
    assert len(df) == 0
    assert list(df.columns) == list(overrides.OVERRIDE_COLUMNS)


def test_load_overrides_missing_metadata(tmp_path):
    path = tmp_path / "bad.tsv"
    write_overrides_tsv(
        path,
        [override_row("Q1", "death_year", "1955", reason="")],
    )
    with pytest.raises(ValueError, match="missing required metadata `reason`"):
        overrides.load_overrides(path)


def test_load_overrides_duplicate_field(tmp_path):
    path = tmp_path / "dup.tsv"
    write_overrides_tsv(
        path,
        [
            override_row("Q1", "death_year", "1955"),
            override_row("Q1", "death_year", "1956"),
        ],
    )
    with pytest.raises(ValueError, match="duplicate override"):
        overrides.load_overrides(path)


def test_load_overrides_drop_combined(tmp_path):
    path = tmp_path / "drop.tsv"
    write_overrides_tsv(
        path,
        [
            override_row("Q1", "_drop", "true"),
            override_row("Q1", "death_year", "1955"),
        ],
    )
    with pytest.raises(ValueError, match="`_drop` cannot be combined"):
        overrides.load_overrides(path)


# --- apply validation ---


def test_apply_unknown_field():
    ov = overrides_df([override_row("Q1", "not_a_column", "x")])
    with pytest.raises(ValueError, match="unknown field"):
        overrides.apply_composer_overrides(
            composers_frame(), works_frame(), ov, pd_year=2026,
        )


def test_apply_stale_composer_id():
    ov = overrides_df([override_row("Q999", "death_year", "1955")])
    with pytest.raises(ValueError, match="not present in dump"):
        overrides.apply_composer_overrides(
            composers_frame(), works_frame(), ov, pd_year=2026,
        )


def test_apply_rekey_target_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "CACHE_DIR", tmp_path)
    cache_entity("Q2", entity("Q2"))
    ov = overrides_df([override_row("Q1", "composer_id", "Q2")])
    with pytest.raises(ValueError, match="already exists"):
        overrides.apply_composer_overrides(
            composers_frame(), works_frame(), ov, pd_year=2026,
        )


def test_apply_lists_every_problem(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "CACHE_DIR", tmp_path)
    cache_entity("Q2", entity("Q2"))
    ov = overrides_df(
        [
            override_row("Q999", "nope", "x"),
            override_row("Q1", "composer_id", "Q2"),
        ]
    )
    with pytest.raises(ValueError) as exc:
        overrides.apply_composer_overrides(
            composers_frame(), works_frame(), ov, pd_year=2026,
        )
    msg = str(exc.value)
    assert "unknown field" in msg
    assert "not present in dump" in msg
    assert "already exists" in msg


def test_missing_entity_error(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "CACHE_DIR", tmp_path)
    ov = overrides_df([override_row("Q1", "composer_id", "Q99999999")])
    with pytest.raises(ValueError, match="Q99999999"):
        overrides.apply_composer_overrides(
            composers_frame(), works_frame(), ov, pd_year=2026,
        )


@pytest.mark.parametrize("drop_value", ["false", ""])
def test_apply_rejects_non_true_drop(drop_value):
    ov = overrides_df([override_row("Q3", "_drop", drop_value)])
    with pytest.raises(ValueError, match=r"`_drop` value must be `true`"):
        overrides.apply_composer_overrides(
            composers_frame(), works_frame(), ov, pd_year=2026,
        )


def test_apply_rejects_blank_composer_id():
    ov = overrides_df([override_row("", "death_year", "1955")])
    with pytest.raises(ValueError, match="missing composer_id"):
        overrides.apply_composer_overrides(
            composers_frame(), works_frame(), ov, pd_year=2026,
        )


def test_apply_rejects_blank_field():
    ov = overrides_df([override_row("Q1", "", "x")])
    with pytest.raises(ValueError, match="missing field"):
        overrides.apply_composer_overrides(
            composers_frame(), works_frame(), ov, pd_year=2026,
        )


def test_apply_rejects_missing_metadata():
    ov = overrides_df([override_row("Q1", "death_year", "1955", reason="")])
    with pytest.raises(ValueError, match="missing required metadata `reason`"):
        overrides.apply_composer_overrides(
            composers_frame(), works_frame(), ov, pd_year=2026,
        )


def test_apply_rejects_duplicate_field():
    ov = overrides_df(
        [
            override_row("Q1", "death_year", "1955"),
            override_row("Q1", "death_year", "1956"),
        ]
    )
    with pytest.raises(ValueError, match="duplicate override"):
        overrides.apply_composer_overrides(
            composers_frame(), works_frame(), ov, pd_year=2026,
        )


def test_apply_rejects_drop_combined_with_other_fields():
    ov = overrides_df(
        [
            override_row("Q1", "_drop", "true"),
            override_row("Q1", "death_year", "1955"),
        ]
    )
    with pytest.raises(ValueError, match="`_drop` cannot be combined"):
        overrides.apply_composer_overrides(
            composers_frame(), works_frame(), ov, pd_year=2026,
        )


# --- behaviour ---


def test_value_override():
    ov = overrides_df([override_row("Q1", "name_display", "Alicia")])
    composers, works, report = overrides.apply_composer_overrides(
        composers_frame(), works_frame(), ov, pd_year=2026,
    )
    assert composers.loc[composers["composer_id"] == "Q1", "name_display"].iloc[0] == "Alicia"
    assert "manual_override" in composers.loc[
        composers["composer_id"] == "Q1", "qa_flags"
    ].iloc[0]
    assert report["value_changes"][0]["old"] == "Alice"
    assert report["value_changes"][0]["new"] == "Alicia"
    assert len(works) == 4


def test_rekey_moves_works_and_rederives(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "CACHE_DIR", tmp_path)
    cache_entity(
        "Q99999999",
        entity(
            "Q99999999",
            birth=1922,
            death=1999,
            occupations=("Q36834", "Q765778"),
            citizenship=("Q30",),
            p839="Category:Alice",
            aliases=("New Alias",),
        ),
    )
    cache_entity("Q30", country_entity("Q30", "US"))
    ov = overrides_df(
        [
            override_row("Q1", "death_year", "1949"),
            override_row("Q1", "composer_id", "Q99999999"),
        ]
    )
    composers, works, report = overrides.apply_composer_overrides(
        composers_frame(), works_frame(), ov, pd_year=2026,
    )
    assert "Q1" not in set(composers["composer_id"])
    row = composers.loc[composers["composer_id"] == "Q99999999"].iloc[0]
    assert row["wikidata_qid"] == "Q99999999"
    assert row["wikidata_url"] == "https://www.wikidata.org/wiki/Q99999999"
    # Explicit override wins over re-derived death_year 1999.
    assert row["death_year"] == "1949"
    assert row["birth_year"] == "1922"
    assert row["name_aliases"] == "New Alias"
    assert "politician" not in row["occupations"]
    assert "composer" in row["occupations"]
    assert row["style_tags_src"] != "llm" or row["style_tags"] == ""
    assert row["imslp_match_status"] == "matched"
    assert row["imslp_match_method"] == "wikidata_p839"
    assert list(works.loc[works["work_id"] == "W1", "composer_id"]) == ["Q99999999"]
    assert report["works_rekeyed"] == 1
    # name_display left alone unless overridden
    assert row["name_display"] == "Alice"


def test_explicit_override_beats_rederived(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "CACHE_DIR", tmp_path)
    cache_entity(
        "Q88",
        entity("Q88", birth=1910, death=1990, occupations=("Q36834",), aliases=("FromWD",)),
    )
    ov = overrides_df(
        [
            override_row("Q1", "composer_id", "Q88"),
            override_row("Q1", "name_aliases", "FromOverride"),
        ]
    )
    composers, _, _ = overrides.apply_composer_overrides(
        composers_frame(), works_frame(), ov, pd_year=2026,
    )
    row = composers.loc[composers["composer_id"] == "Q88"].iloc[0]
    assert row["name_aliases"] == "FromOverride"
    assert row["death_year"] == "1990"


def test_p839_equal_sets_matched(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "CACHE_DIR", tmp_path)
    cache_entity(
        "Q88",
        entity("Q88", p839="Category:Alice", occupations=("Q36834",)),
    )
    ov = overrides_df([override_row("Q1", "composer_id", "Q88")])
    composers, works, report = overrides.apply_composer_overrides(
        composers_frame(), works_frame(), ov, pd_year=2026,
    )
    row = composers.loc[composers["composer_id"] == "Q88"].iloc[0]
    assert row["imslp_match_status"] == "matched"
    assert row["imslp_match_method"] == "wikidata_p839"
    assert len(works[works["composer_id"] == "Q88"]) == 1
    assert report["rekeys"][0]["imslp"]["action"] == "matched_p839"


def test_p839_different_flags_recheck(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "CACHE_DIR", tmp_path)
    cache_entity(
        "Q88",
        entity("Q88", p839="Category:Other_Person", occupations=("Q36834",)),
    )
    ov = overrides_df([override_row("Q1", "composer_id", "Q88")])
    composers, works, report = overrides.apply_composer_overrides(
        composers_frame(), works_frame(), ov, pd_year=2026,
    )
    row = composers.loc[composers["composer_id"] == "Q88"].iloc[0]
    assert row["imslp_category"] == "Category:Alice"
    assert row["imslp_match_status"] == "unverified_heuristic"
    assert "imslp_needs_recheck" in row["qa_flags"]
    assert len(works[works["composer_id"] == "Q88"]) == 1
    assert report["rekeys"][0]["imslp"]["action"] == "imslp_needs_recheck"


def test_p839_none_leaves_imslp(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "CACHE_DIR", tmp_path)
    cache_entity("Q88", entity("Q88", p839=None, occupations=("Q36834",)))
    ov = overrides_df([override_row("Q1", "composer_id", "Q88")])
    composers, _, report = overrides.apply_composer_overrides(
        composers_frame(), works_frame(), ov, pd_year=2026,
    )
    row = composers.loc[composers["composer_id"] == "Q88"].iloc[0]
    assert row["imslp_category"] == "Category:Alice"
    assert row["imslp_match_status"] == "unverified_heuristic"
    assert "imslp_needs_recheck" not in row["qa_flags"]
    assert report["rekeys"][0]["imslp"]["action"] == "p839_none"


def test_citizenship_iso_from_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "CACHE_DIR", tmp_path)
    cache_entity(
        "Q88",
        entity(
            "Q88",
            occupations=("Q36834",),
            citizenship=("Q30", "Q999"),
        ),
    )
    cache_entity("Q30", country_entity("Q30", "us"))
    # Q999 missing from cache → skipped
    ov = overrides_df([override_row("Q1", "composer_id", "Q88")])
    composers, _, report = overrides.apply_composer_overrides(
        composers_frame(), works_frame(), ov, pd_year=2026,
    )
    row = composers.loc[composers["composer_id"] == "Q88"].iloc[0]
    assert row["citizenship_iso"] == "US"
    assert "Q999" in report["citizenship_iso_skipped"]


def test_non_qid_rekey(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "CACHE_DIR", tmp_path)
    ov = overrides_df([override_row("Q3", "composer_id", "wiki:John_Mitchell")])
    composers, works, _ = overrides.apply_composer_overrides(
        composers_frame(), works_frame(), ov, pd_year=2026,
    )
    assert "Q3" not in set(composers["composer_id"])
    row = composers.loc[composers["composer_id"] == "wiki:John_Mitchell"].iloc[0]
    assert row["wikidata_qid"] == ""
    assert row["wikidata_url"] == ""
    assert "not_human" not in row["qa_flags"]
    assert "no_composer_occupation" not in row["qa_flags"]
    assert "manual_override" in row["qa_flags"]
    assert row["name_display"] == "Carol"
    assert list(works.loc[works["work_id"] == "W4", "composer_id"]) == [
        "wiki:John_Mitchell"
    ]


def test_drop_removes_composer_and_works():
    ov = overrides_df([override_row("Q3", "_drop", "true")])
    composers, works, report = overrides.apply_composer_overrides(
        composers_frame(), works_frame(), ov, pd_year=2026,
    )
    assert "Q3" not in set(composers["composer_id"])
    assert "Q3" not in set(works["composer_id"])
    assert len(composers) == 2
    assert len(works) == 3
    assert report["drops"][0]["composer_id"] == "Q3"
    assert report["works_dropped"] == 1


def test_pd_recompute_after_death_year_override():
    ov = overrides_df([override_row("Q2", "death_year", "1950")])
    composers, _, _ = overrides.apply_composer_overrides(
        composers_frame(), works_frame(), ov, pd_year=2026,
    )
    row = composers.loc[composers["composer_id"] == "Q2"].iloc[0]
    assert row["eu_pd_year"] == "2021"
    assert row["eu_pd_status"] == "pd"
    assert row["years_until_eu_pd"] == "0"


def test_pd_recompute_clears_on_empty_death_year():
    ov = overrides_df([override_row("Q1", "death_year", "")])
    composers, _, _ = overrides.apply_composer_overrides(
        composers_frame(), works_frame(), ov, pd_year=2026,
    )
    row = composers.loc[composers["composer_id"] == "Q1"].iloc[0]
    assert row["eu_pd_year"] == ""
    assert row["eu_pd_status"] == "unknown_death"
    assert row["years_until_eu_pd"] == ""


def test_qa_flags_append_keeps_existing():
    ov = overrides_df([override_row("Q1", "name_display", "A")])
    composers, _, _ = overrides.apply_composer_overrides(
        composers_frame(), works_frame(), ov, pd_year=2026,
    )
    flags = composers.loc[composers["composer_id"] == "Q1", "qa_flags"].iloc[0]
    assert flags == "no_composer_occupation|manual_override"


def test_qa_flags_explicit_replace_then_manual():
    ov = overrides_df([override_row("Q1", "qa_flags", "birth_imprecise")])
    composers, _, _ = overrides.apply_composer_overrides(
        composers_frame(), works_frame(), ov, pd_year=2026,
    )
    flags = composers.loc[composers["composer_id"] == "Q1", "qa_flags"].iloc[0]
    assert flags == "birth_imprecise|manual_override"
    assert "no_composer_occupation" not in flags


def test_rollups_after_drop():
    ov = overrides_df([override_row("Q2", "_drop", "true")])
    composers, works, _ = overrides.apply_composer_overrides(
        composers_frame(), works_frame(), ov, pd_year=2026,
    )
    assert len(works) == 2
    assert "Q2" not in set(composers["composer_id"])
    q1 = composers.loc[composers["composer_id"] == "Q1"].iloc[0]
    assert q1["works_count_total"] == "1"
    assert q1["work_categories_present"] == "keyboard_other"


def test_untouched_row_byte_stability(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "CACHE_DIR", tmp_path)
    cache_entity("Q88", entity("Q88", occupations=("Q36834",)))
    source = composers_frame()
    ov = overrides_df([override_row("Q1", "composer_id", "Q88")])
    composers, _, _ = overrides.apply_composer_overrides(
        source, works_frame(), ov, pd_year=2026,
    )
    for col in source.columns:
        assert (
            composers.loc[composers["composer_id"] == "Q2", col].iloc[0]
            == source.loc[source["composer_id"] == "Q2", col].iloc[0]
        )


def test_empty_overrides_noop():
    composers_in = composers_frame()
    works_in = works_frame()
    composers, works, report = overrides.apply_composer_overrides(
        composers_in, works_in, overrides_df([]), pd_year=2026,
    )
    pd.testing.assert_frame_equal(composers, composers_in)
    pd.testing.assert_frame_equal(works, works_in)
    assert report["overrides_applied"] == 0
    assert report["value_changes"] == []


# --- CLI ---


@pytest.fixture
def dump_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "DATA_DIR", tmp_path)
    monkeypatch.setattr(common, "CACHE_DIR", tmp_path / "cache")
    composers_frame().to_csv(tmp_path / "composers_r010.tsv", sep="\t", index=False)
    works_frame().to_csv(tmp_path / "works_r010.tsv", sep="\t", index=False)
    (tmp_path / "dump_meta_r010.json").write_text(
        json.dumps({"created_at_utc": "2026-10-09T00:00:00+00:00"}),
        encoding="utf-8",
    )
    overrides_dir = tmp_path / "overrides"
    overrides_dir.mkdir()
    header = "\t".join(overrides.OVERRIDE_COLUMNS) + "\n"
    (overrides_dir / "composers.tsv").write_text(header, encoding="utf-8")
    return tmp_path


def test_cli_empty_overrides_noop_dry_run(dump_dir, capsys):
    before = {p.name: p.read_bytes() for p in dump_dir.rglob("*") if p.is_file()}
    apply_overrides.run(
        argparse.Namespace(
            from_dump="r010", to="r999", overrides=None, dry_run=True,
        )
    )
    after = {p.name: p.read_bytes() for p in dump_dir.rglob("*") if p.is_file()}
    assert after == before
    out = capsys.readouterr().out
    assert "Overrides applied: 0" in out


def test_cli_byte_for_byte_untouched_columns(dump_dir):
    ov_path = dump_dir / "tmp_overrides.tsv"
    write_overrides_tsv(
        ov_path,
        [override_row("Q1", "name_display", "Alicia")],
    )
    apply_overrides.run(
        argparse.Namespace(
            from_dump="r010",
            to="r999",
            overrides=str(ov_path),
            dry_run=False,
        )
    )
    written = pd.read_csv(
        dump_dir / "composers_r999.tsv", sep="\t", dtype=str, keep_default_na=False,
    )
    source = pd.read_csv(
        dump_dir / "composers_r010.tsv", sep="\t", dtype=str, keep_default_na=False,
    )
    for col in source.columns:
        if col == "dump_date":
            continue
        assert (
            written.loc[written["composer_id"] == "Q2", col].iloc[0]
            == source.loc[source["composer_id"] == "Q2", col].iloc[0]
        )
    assert (
        written.loc[written["composer_id"] == "Q1", "untouched_col"].iloc[0]
        == "keep-me"
    )
    assert written.loc[written["composer_id"] == "Q1", "name_display"].iloc[0] == "Alicia"
    assert written["dump_date"].tolist() == ["r999"] * 3
    works_out = pd.read_csv(
        dump_dir / "works_r999.tsv", sep="\t", dtype=str, keep_default_na=False,
    )
    assert works_out["dump_date"].tolist() == ["r999"] * 4
    meta = json.loads((dump_dir / "dump_meta_r999.json").read_text(encoding="utf-8"))
    assert meta["overrides"]["rows_applied"] == 1
    assert meta["overrides"]["sha256"]
    assert meta["enrichment"] == "apply_composer_overrides"


def test_cli_empty_file_writes_dump_date_only(dump_dir):
    apply_overrides.run(
        argparse.Namespace(
            from_dump="r010", to="r998", overrides=None, dry_run=False,
        )
    )
    written = pd.read_csv(
        dump_dir / "composers_r998.tsv", sep="\t", dtype=str, keep_default_na=False,
    )
    source = pd.read_csv(
        dump_dir / "composers_r010.tsv", sep="\t", dtype=str, keep_default_na=False,
    )
    for col in source.columns:
        if col == "dump_date":
            assert set(written[col]) == {"r998"}
        else:
            assert written[col].tolist() == source[col].tolist()
    meta = json.loads((dump_dir / "dump_meta_r998.json").read_text(encoding="utf-8"))
    assert meta["overrides"]["rows_applied"] == 0
