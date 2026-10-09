"""Offline regression tests for identity evidence, ownership and revision audits."""

import argparse
import json

import pandas as pd
import pytest
import requests

import common
import imslp
import imslp_identity as identity
import remap_imslp_matches as remap
from check_release import check_data_quality
from remap_force import rollup_composers


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail("Identity tests must not access the network or shared cache")

    monkeypatch.setattr(requests.Session, "request", fail)
    monkeypatch.setattr(common, "cache_get", fail)
    monkeypatch.setattr(common, "cache_set", fail)


def page(birth="1900", death="1943", *, title="Test Person", kind="person", **extra):
    template = f"#fte:{kind}" if kind != "imslpcomposer" else "#imslpcomposer:"
    return {
        "wikitext": "{{" + template + f"\n|Born Year={birth}|Born Month=5|Born Day=14\n"
        f"|Died Year={death}|Biography Link=[[wikipedia:{title}|Wikipedia]]\n" + "}}",
        "missing": False, "redirect": False, **extra,
    }


def composer(qid, name, birth, death, category, *, status="unverified_heuristic", title=None, **extra):
    return {
        "composer_id": qid, "name_display": name, "name_aliases": "NA|None",
        "birth_year": birth, "death_year": death,
        "wikipedia_url": "https://en.wikipedia.org/wiki/" + (title or name).replace(" ", "_"),
        "imslp_category": category, "imslp_url": "https://imslp.org/wiki/" + category if category else "",
        "imslp_match_status": status,
        "imslp_match_method": "wikidata_p839" if status == "matched" else "exact_name",
        "qa_flags": "", "eu_pd_status": "pd" if death and int(float(death)) < 1956 else "not_pd",
        "dump_date": "r010", **extra,
    }


def test_preserves_collision_winner_audit_when_loser_is_already_rejected():
    composers = pd.DataFrame([
        composer("Q1", "Test Person", "1900", "1943", "Category:Test", status="matched",
                 imslp_match_evidence="dates_agree|wikilink_agree|collision_won"),
        composer("Q2", "Different Person", "1980", "", "Category:Test", status="rejected_heuristic",
                 imslp_match_evidence="dates_conflict_strong|collision_lost"),
    ])
    out, _ = identity.resolve_matches(composers, {"Category:Test": page()})
    assert out.imslp_match_evidence.tolist() == composers.imslp_match_evidence.tolist()


@pytest.mark.parametrize("kind", ["person", "composer", "imslpcomposer", "performer"])
def test_parse_all_templates(kind):
    text = page(kind=kind)["wikitext"].replace("|Biography Link", "|Alternate Names=Leopold Smit; Leo Smit, L. Smit\n|Biography Link")
    assert identity.parse_person_page(text) == {
        "template_kind": kind, "born_year": 1900, "died_year": 1943,
        "wikipedia_title": "Test Person", "alternate_names": ["Leopold Smit", "Leo Smit", "L. Smit"],
    }


@pytest.mark.parametrize("kind", ["person", "composer", "imslpcomposer", "performer"])
def test_blank_and_missing_fields(kind):
    text = page("", "", kind=kind, title="")["wikitext"]
    parsed = identity.parse_person_page(text)
    assert parsed["born_year"] is None and parsed["died_year"] is None
    assert parsed["wikipedia_title"] == "" and parsed["alternate_names"] == []
    template = f"#fte:{kind}" if kind != "imslpcomposer" else "#imslpcomposer:"
    assert identity.parse_person_page("{{" + template + "}}") == parsed


def test_nested_pipes_template_scope_comments_and_confusion_links():
    parsed = identity.parse_person_page("""
<!-- {{#fte:person|Born Year=1111}} -->
{{#fte:person
|Born Year = 1892 |Born Month=4|Born Day=21
|Died Year = 1958
|Biography Link=[[wikipedia:Jaroslav Kvapil (composer)|Wikipedia]]; {{Plain|https://example.invalid/|Biography}}
|Alternate Names={{AltNames|{{FN|Jaroslav|Kvapil}}|{{FN|J.|Kvapil}}}}
|Extra Information=*Frequently confused with [[wikipedia:Jaroslav Kvapil|the writer]] (1868–1950).
{{Something|Born Year=1868|Died Year=1950}}
}}
|Born Year=1868|Died Year=1950
""")
    assert parsed == {
        "template_kind": "person", "born_year": 1892, "died_year": 1958,
        "wikipedia_title": "Jaroslav Kvapil (composer)", "alternate_names": ["Jaroslav Kvapil", "J. Kvapil"],
    }


@pytest.mark.parametrize("value", ["c.1850", "1850/51", "unknown", "{{Year|1850}}", "1850?", "1850–1855"])
def test_ambiguous_year_is_unknown(value):
    parsed = identity.parse_person_page(page(value, "")["wikitext"])
    assert parsed["born_year"] is None and parsed["died_year"] is None


def test_no_person_template_and_external_biography_link():
    assert identity.parse_person_page("{{Other|Born Year=1900}}") == {
        "template_kind": "none", "born_year": None, "died_year": None,
        "wikipedia_title": "", "alternate_names": [],
    }
    parsed = identity.parse_person_page("{{#fte:composer|Biography Link=[https://en.wikipedia.org/wiki/Leo_Smit Wikipedia]}}")
    assert parsed["wikipedia_title"] == "Leo_Smit"


HAIEFF_WIKITEXT = """{{#fte:person
|IsLocked=Yes
|Nationality=Russian
|Nationality2=American
|Born Year=1919|Born Month=8|Born Day=25
|Died Year=1994|Died Month=3|Died Day=1
|Sex=male
|Time Period=Modern
|Biography Link={{wp|Alexei Haieff}}
}}"""


def test_parse_real_haieff_wp_biography():
    assert identity.parse_person_page(HAIEFF_WIKITEXT) == {
        "template_kind": "person", "born_year": 1919, "died_year": 1994,
        "wikipedia_title": "Alexei Haieff", "alternate_names": [],
    }


@pytest.mark.parametrize("biography,title", [
    ("{{wp|Frederic_Hymen_Cowen}}", "Frederic_Hymen_Cowen"),
    ("{{wp|Alexei Haieff|Biography}}", "Alexei Haieff"),
    ("{{ WP | en:Alexei Haieff | Biography }}", "Alexei Haieff"),
    ("{{wp|EN:Alexei Haieff}}", "Alexei Haieff"),
    ("{{wp|fr:Alexei Haieff}}", ""),
    ("{{wp|pt-br:Alexei Haieff|Biography}}", ""),
    ("{{wp||Biography}}", ""),
])
def test_parse_wp_title_label_case_and_language(biography, title):
    parsed = identity.parse_person_page("{{#fte:person|Biography Link=" + biography + "}}")
    assert parsed["wikipedia_title"] == title


def test_haieff_weak_conflict_with_wp_link_promotes_match():
    cat = "Category:Haieff,_Alexei"
    rows = pd.DataFrame([composer("Q1428724", "Alexei Haieff", "1914", "1994", cat)])
    works = pd.DataFrame([{"composer_id": "Q1428724", "force_family": "chamber"}])
    out, kept, report = remap.remap_matches(rows, works, "r999", {cat: {
        "wikitext": HAIEFF_WIKITEXT, "missing": False,
    }})
    assert out.at[0, "imslp_match_status"] == "matched"
    assert out.at[0, "imslp_match_method"] == "exact_name+wikilink"
    assert out.at[0, "qa_flags"] == "imslp_dates_conflict"
    assert out.at[0, identity.EVIDENCE_COLUMN] == "dates_conflict_weak|wikilink_agree"
    assert report["weak_conflicts_kept"][0]["wikilink"] == "agree"
    assert report["works_dropped"] == 0
    pd.testing.assert_frame_equal(kept, works)


@pytest.mark.parametrize("years, expected", [
    (("1900.0", "1943", 1900, 1943), "agree"),
    (("1899", "1944", 1900, 1943), "agree"),
    (("1902", "1943", 1900, 1943), "conflict_weak"),
    (("1900", "1945", 1900, 1943), "conflict_weak"),
    (("", "1943", None, 1943), "agree"),
    (("1900", "", 1900, None), "agree"),
    (("", "1945", None, 1943), "conflict_weak"),
    (("1900", "1943", 1910, 1953), "conflict_weak"),
    (("1900", "1943", 1911, 1954), "conflict_strong"),
    (("1900", "1943", 1902, 1954), "conflict_strong"),
    (("1900", "1943", 1901, 1954), "conflict_weak"),
    (("1900", "1943", 1950, 1943), "conflict_weak"),
    (("", "1943", None, 1953), "conflict_weak"),
    (("", "1943", None, 1954), "conflict_strong"),
    (("1900", "", 1911, None), "conflict_strong"),
    (("1900", "", None, 1943), "unknown"),
    (("", "", 1900, 1943), "unknown"),
    ((None, None, None, None), "unknown"),
])
def test_compare_dates(years, expected):
    assert identity.compare_dates(*years) == expected


@pytest.mark.parametrize("url, title, expected", [
    ("https://en.wikipedia.org/wiki/Leo_Smit_(Dutch_composer)", "Leo Smit (Dutch composer)", "agree"),
    ("https://en.wikipedia.org/wiki/Jaroslav_Kvapil", "Jaroslav Kvapil (composer)", "differs"),
    ("https://en.wikipedia.org/wiki/Émile_Bernard", "%C3%A9mile_Bernard", "agree"),
    ("https://en.wikipedia.org/wiki/leo_Smit#Works", "Leo Smit", "agree"),
    ("https://en.wikipedia.org/wiki/Leo_smit", "Leo Smit", "differs"),
    ("https://en.wikipedia.org/wiki/Leo%20Smit?oldid=1", "Leo_Smit", "agree"),
    ("", "Leo Smit", "unknown"), ("https://en.wikipedia.org/wiki/Leo_Smit", "", "unknown"),
])
def test_wikipedia_comparison(url, title, expected):
    assert identity.compare_wikipedia_link(url, title) == expected


@pytest.fixture
def source():
    composers = pd.DataFrame([
        composer("Q643984", "Leo Smit", "1900", "1943", "Category:Smit,_Leo", status="matched", title="Leo Smit (Dutch composer)"),
        composer("Q2341356", "Leo Smit", "1921", "1999", "Category:Smit,_Leo", title="Leo Smit (American composer)"),
        composer("Q8017433", "William Reed", "1910", "2002", "Category:Reed,_William"),
        composer("Q373961", "William Reed", "1859", "1945", "Category:Reed,_William", title="William Reed (composer)"),
        composer("Q956296", "Jaroslav Kvapil", "1868", "1950", "Category:Kvapil,_Jaroslav"),
        composer("Qtrusted", "Trusted", "1900", "1943", "Category:Trusted", status="matched", qa_flags="birth_rank_conflict"),
        composer("Qunknown", "Unknown", "1900", "1943", "Category:Unknown"),
        composer("Qmissing", "Missing", "1900", "1943", "Category:Missing"),
        composer("Qperformer", "Vladimir Horowitz", "1903", "1989", "Category:Horowitz,_Vladimir"),
        composer("Qnone", "No category", "1900", "1943", "", status="not_found"),
    ], index=[10, 20, 30, 40, 50, 60, 70, 80, 90, 100])
    pages = {
        "Category:Smit,_Leo": page(title="Leo Smit (Dutch composer)"),
        "Category:Reed,_William": page("1859", "1945", title="William Reed (musician)"),
        "Category:Kvapil,_Jaroslav": page("1892", "1958", title="Jaroslav Kvapil (composer)", redirect=True, resolved_title="Category:Kvapil, Jaroslav"),
        "Category:Trusted": page("1898", "1943", title="Trusted"),
        "Category:Unknown": page("", "", title="Unknown"),
        "Category:Missing": page(missing=True),
        "Category:Horowitz,_Vladimir": page("1903", "1989", title="Vladimir Horowitz", kind="performer"),
    }
    works = pd.DataFrame([
        {"composer_id": qid, "work_id": f"work{i}", "force_family": family,
         "force_family_src": "llm", "title": "NA", "fetched_at": "None", "imslp_pageid": f"{i}.0"}
        for i, (qid, family) in enumerate([
            ("Q643984", "chamber"), ("Q2341356", "chamber"), ("Q2341356", "piano_solo"),
            ("Q373961", "organ"), ("Q8017433", "organ"), ("Q956296", "solo_voice"),
            ("Qtrusted", "chamber"), ("Qunknown", "piano_solo"),
            ("Qmissing", "solo_instrument"), ("Qperformer", "piano_solo"),
        ])
    ])
    composers = rollup_composers(composers, works)
    composers["imslp_works_count"] = composers["works_count_total"].astype(str)
    composers["works_count_total"] = composers["works_count_total"].astype(str)
    return composers, works, pages


def test_known_collisions_kvapil_p839_and_performer(source):
    composers, _, pages = source
    before = composers.copy(deep=True)
    out, decisions = identity.resolve_matches(composers, pages)
    pd.testing.assert_frame_equal(composers, before)
    keyed = out.set_index("composer_id")
    for qid in ["Q643984", "Q373961", "Qperformer", "Qtrusted"]:
        assert keyed.at[qid, "imslp_match_status"] == "matched"
        assert keyed.at[qid, "imslp_url"]
    for qid in ["Q2341356", "Q8017433", "Q956296"]:
        assert keyed.at[qid, "imslp_match_status"] == "rejected_heuristic"
        assert keyed.at[qid, "imslp_url"] == ""
        assert keyed.at[qid, "imslp_category"]
        assert keyed.at[qid, "imslp_match_method"] == "exact_name"
    assert keyed.at["Q373961", "imslp_match_method"] == "exact_name+life_dates"
    assert "wikilink_differs" in keyed.at["Q373961", identity.EVIDENCE_COLUMN]
    assert "performer_page" in keyed.at["Qperformer", identity.EVIDENCE_COLUMN]
    assert "collision_won" in keyed.at["Q643984", identity.EVIDENCE_COLUMN]
    assert "collision_lost" in keyed.at["Q2341356", identity.EVIDENCE_COLUMN]
    assert keyed.at["Qtrusted", "qa_flags"] == "birth_rank_conflict|imslp_dates_conflict"
    assert keyed.at["Qnone", identity.EVIDENCE_COLUMN] == ""
    assert keyed.at["Qmissing", "imslp_match_status"] == "unverified_heuristic"
    assert "page_missing" in keyed.at["Qmissing", identity.EVIDENCE_COLUMN]
    columns = list(out.columns)
    assert columns[columns.index("imslp_match_method") + 1] == identity.EVIDENCE_COLUMN
    untouched = [col for col in composers if col not in {"imslp_match_status", "imslp_match_method", "imslp_url", "qa_flags"}]
    pd.testing.assert_frame_equal(out[untouched], composers[untouched])
    assert next(d for d in decisions if d["qid"] == "Q956296")["reason"] == "dates_conflict_strong"
    repeated, _ = identity.resolve_matches(out, pages)
    assert repeated.set_index("composer_id").at["Qtrusted", "qa_flags"] == keyed.at["Qtrusted", "qa_flags"]


@pytest.mark.parametrize("scenario, winner", [
    ("wikilink_tie", "Q1"), ("both_agree", None), ("both_unknown", None),
    ("both_conflict", None), ("missing", None), ("p839_weak_conflict", "Q2"),
    ("p839_strong_conflict", "Q1"),
])
def test_collision_priority_and_conservative_ambiguity(scenario, winner):
    cat = "Category:Shared"
    rows = pd.DataFrame([
        composer("Q1", "First", "1900", "1943", cat),
        composer("Q2", "Second", "1900", "1943", cat, status="matched" if scenario.startswith("p839_") else "unverified_heuristic"),
    ])
    if scenario.startswith("p839_"):
        rows.loc[1, "birth_year"] = "1800"
    if scenario == "p839_strong_conflict":
        rows.loc[1, "death_year"] = "1843"
    entry = page(
        "" if scenario == "both_unknown" else "1950" if scenario == "both_conflict" else "1900",
        "" if scenario == "both_unknown" else "1943",
        title="First" if scenario == "wikilink_tie" or scenario.startswith("p839_") else "Another",
        missing=scenario == "missing",
    )
    out, decisions = identity.resolve_matches(rows, {cat: entry})
    active_ids = out.loc[out.imslp_match_status.isin(identity.ACTIVE_STATUSES), "composer_id"].tolist()
    assert active_ids == ([winner] if winner else [])
    assert all(d["collision"]["winner"] == winner for d in decisions)
    if scenario == "p839_weak_conflict":
        assert out.at[1, "qa_flags"] == "imslp_dates_conflict"
    if scenario == "p839_strong_conflict":
        assert out.at[1, "qa_flags"] == "imslp_p839_wrong"
        assert out.at[1, "imslp_match_status"] == "rejected_p839"
        assert "collision_lost" in out.at[1, identity.EVIDENCE_COLUMN]
    if scenario == "wikilink_tie":
        assert out.at[1, "imslp_match_method"] == "exact_name"


def test_multiple_p839_holders_fail_explicitly():
    cat = "Category:Shared"
    rows = pd.DataFrame([composer(f"Q{i}", f"Name {i}", "1900", "1943", cat, status="matched") for i in range(2)])
    with pytest.raises(ValueError, match="Multiple P839 holders for Category:Shared: Q0, Q1"):
        identity.resolve_matches(rows, {cat: page()})


@pytest.mark.parametrize("name,birth,death,page_birth,page_death,link,expected_status", [
    ("Alan Bush", "1900", "1995", "1900", "1992", "agree", "matched"),
    ("Jaime Teixidor", "1884", "1957", "1886", "1957", "agree", "matched"),
    ("C. A. Bracco", "1859", "1903", "", "1905", "agree", "matched"),
    ("Alexei Haieff", "1914", "1994", "1919", "1994", "unknown", "unverified_heuristic"),
    ("John Palmer", "1959", "", "1961", "", "unknown", "unverified_heuristic"),
    ("John Palmer", "1959", "", "1961", "", "differs", "unverified_heuristic"),
])
def test_weak_heuristic_rules_keep_works(name, birth, death, page_birth, page_death, link, expected_status):
    cat = "Category:Test"
    rows = pd.DataFrame([composer("Qtest", name, birth, death, cat, qa_flags="birth_from_list")])
    title = name if link == "agree" else "Different Person" if link == "differs" else ""
    pages = {cat: page(page_birth, page_death, title=title)}
    works = pd.DataFrame([{"composer_id": "Qtest", "force_family": "chamber"}])
    out, kept, report = remap.remap_matches(rows, works, "r999", pages)
    assert out.at[0, "imslp_match_status"] == expected_status
    assert out.at[0, "imslp_match_method"] == ("exact_name+wikilink" if link == "agree" else "exact_name")
    assert out.at[0, "qa_flags"] == "birth_from_list|imslp_dates_conflict"
    assert out.at[0, "imslp_url"] == rows.at[0, "imslp_url"]
    assert "dates_conflict_weak" in out.at[0, identity.EVIDENCE_COLUMN]
    assert len(kept) == 1 and report["works_dropped"] == 0
    assert report["weak_conflicts_kept"][0]["wikilink"] == link
    assert len(report["unverified_heuristic"]) == (0 if expected_status == "matched" else 1)
    repeated, _ = identity.resolve_matches(out, pages)
    assert repeated.at[0, "imslp_match_status"] == expected_status
    assert repeated.at[0, "imslp_match_method"] == out.at[0, "imslp_match_method"]
    assert repeated.at[0, "qa_flags"] == out.at[0, "qa_flags"]


@pytest.mark.parametrize("name,birth,death,page_birth,page_death", [
    ("Leo Ornstein", "1893", "2002", "1895", "2002"),
    ("Oskar Rieding", "1846", "1916", "1840", "1918"),
    ("Stéphan Elmas", "1864", "1937", "1862", "1937"),
    ("Vladimir Shcherbachov", "1887", "1952", "1889", "1952"),
    ("Hugh Blair", "1864", "1932", "1862", "1932"),
    ("Caro Roma", "1866", "1937", "1869", "1937"),
    ("Oliveria Prescott", "1842", "1917", "1842", "1919"),
    ("María Luisa Sepúlveda", "1883", "1958", "1898", "1958"),
    ("Lucia Contini Anselmi", "1876", "1913", "1876", "1935"),
])
def test_named_p839_weak_conflicts_are_flagged_and_keep_works(name, birth, death, page_birth, page_death):
    cat = "Category:Test"
    rows = pd.DataFrame([composer("Qtest", name, birth, death, cat, status="matched")])
    works = pd.DataFrame([{"composer_id": "Qtest", "force_family": "chamber"}])
    out, kept, report = remap.remap_matches(rows, works, "r999", {cat: page(page_birth, page_death, title="")})
    assert out.at[0, "imslp_match_status"] == "matched"
    assert out.at[0, "imslp_match_method"] == "wikidata_p839"
    assert out.at[0, "qa_flags"] == "imslp_dates_conflict"
    assert out.at[0, identity.EVIDENCE_COLUMN] == "dates_conflict_weak"
    assert out.at[0, "imslp_url"] == rows.at[0, "imslp_url"]
    assert len(kept) == 1 and report["works_dropped"] == 0
    assert len(report["p839_dates_conflicts"]) == len(report["weak_conflicts_kept"]) == 1
    assert report["rejected_p839"] == []


@pytest.mark.parametrize("name,birth,death,page_birth,page_death", [
    ("John White", "1936", "2024", "1855", "1902"),
    ("Jindřich Feld", "1925", "2007", "1883", "1953"),
    ("John Lambert", "1926", "1995", "", "1892"),
    ("Karl Höller", "1907", "1987", "1838", "1901"),
])
def test_named_p839_strong_conflicts_drop_works_and_keep_audit(name, birth, death, page_birth, page_death):
    cat = "Category:Höller,_Georg" if name == "Karl Höller" else "Category:Test"
    rows = pd.DataFrame([composer("Qtest", name, birth, death, cat, status="matched", qa_flags="birth_rank_conflict")])
    # Even an agreeing Wikipedia link cannot rescue a strong date conflict.
    pages = {cat: page(page_birth, page_death, title=name)}
    works = pd.DataFrame([{"composer_id": "Qtest", "force_family": "chamber"}])
    out, kept, report = remap.remap_matches(rows, works, "r999", pages)
    assert out.at[0, "imslp_match_status"] == "rejected_p839"
    assert out.at[0, "imslp_match_method"] == "wikidata_p839"
    assert out.at[0, "imslp_category"] == cat and out.at[0, "imslp_url"] == ""
    assert out.at[0, "qa_flags"] == "birth_rank_conflict|imslp_p839_wrong"
    assert out.at[0, identity.EVIDENCE_COLUMN] == "dates_conflict_strong|wikilink_agree"
    assert len(kept) == 0 and out.at[0, "works_count_total"] == 0
    assert out.at[0, "work_categories_present"] == out.at[0, "works_count_by_category"] == ""
    assert report["status_transitions"] == {"matched → rejected_p839": 1}
    assert report["works_dropped"] == 1 and report["pd_works_dropped"] == 0
    assert len(report["rejected_p839"]) == len(report["p839_dates_conflicts"]) == 1
    assert report["rejected_p839"][0]["works_dropped"] == 1
    assert report["weak_conflicts_kept"] == []
    assert check_data_quality(out, kept, {})[0] == []
    assert any("without matched/unverified_heuristic status: 1" in e for e in check_data_quality(out, works, {})[0])
    repeated, _ = identity.resolve_matches(out, pages)
    assert repeated.at[0, "qa_flags"] == out.at[0, "qa_flags"]


@pytest.mark.parametrize("dates", ["agree", "unknown"])
def test_p839_agree_and_unknown_preserve_match(dates):
    cat = "Category:Test"
    rows = pd.DataFrame([composer("Qtest", "Test", "1900", "1943", cat, status="matched", qa_flags="birth_from_list")])
    entry = page("1900", "1943", title="Different") if dates == "agree" else page("", "", title="Different")
    out, _ = identity.resolve_matches(rows, {cat: entry})
    pd.testing.assert_frame_equal(out[rows.columns], rows)
    assert out.at[0, identity.EVIDENCE_COLUMN] == f"dates_{dates}|wikilink_differs"


def test_strong_heuristic_conflict_overrides_agreeing_link():
    cat = "Category:Marx,_Karl"
    rows = pd.DataFrame([composer("Q882538", "Karl Marx", "1897", "1985", cat)])
    out, _ = identity.resolve_matches(rows, {cat: page("1818", "1883", title="Karl Marx")})
    assert out.at[0, "imslp_match_status"] == "rejected_heuristic"
    assert out.at[0, "imslp_url"] == "" and out.at[0, "imslp_category"] == cat
    assert out.at[0, identity.EVIDENCE_COLUMN] == "dates_conflict_strong|wikilink_agree"


@pytest.mark.parametrize("has_valid_p839", [True, False])
def test_multiple_p839_claimants_exclude_strong_conflicts(has_valid_p839):
    cat = "Category:Shared"
    rows = pd.DataFrame([
        composer("Qwrong", "Wrong", "1800", "1843", cat, status="matched"),
        composer("Qother", "Other", "1900" if has_valid_p839 else "1750", "1943" if has_valid_p839 else "1800", cat, status="matched"),
    ])
    out, decisions = identity.resolve_matches(rows, {cat: page()})
    assert out.at[0, "imslp_match_status"] == "rejected_p839"
    assert out.at[1, "imslp_match_status"] == ("matched" if has_valid_p839 else "rejected_p839")
    assert all(d["collision"]["winner"] == ("Qother" if has_valid_p839 else None) for d in decisions)


def test_uncached_page_stays_unverified():
    rows = pd.DataFrame([composer("Q1", "First", "1900", "1943", "Category:First")])
    out, decisions = identity.resolve_matches(rows, {})
    assert out.at[0, "imslp_match_status"] == "unverified_heuristic"
    assert out.at[0, identity.EVIDENCE_COLUMN] == "dates_unknown"
    assert decisions[0]["reason"] == "page_not_cached"


@pytest.mark.parametrize("has_dump_date", [False, True])
def test_drop_works_rollups_and_release_gates(source, has_dump_date):
    composers, works, pages = source
    if has_dump_date:
        works["dump_date"] = "r010"
    original_works = works.copy(deep=True)
    old_errors, _ = check_data_quality(composers, works, {})
    assert any("categories held by multiple active composers: 2" in e for e in old_errors)
    out, kept, report = remap.remap_matches(composers, works, "r999", pages)
    assert report["status_transitions"] == {
        "unverified_heuristic → matched": 2, "unverified_heuristic → rejected_heuristic": 3,
    }
    assert report["works_dropped"] == 4 and report["pd_works_dropped"] == 1
    assert len(report["collisions"]) == 2 and len(report["p839_dates_conflicts"]) == 1
    assert len(report["unverified_heuristic"]) == 2
    keyed = out.set_index("composer_id")
    for qid in ["Q2341356", "Q8017433", "Q956296"]:
        assert keyed.at[qid, "works_count_total"] == 0
        assert keyed.at[qid, "imslp_works_count"] == 0
        assert keyed.at[qid, "work_categories_present"] == ""
        assert keyed.at[qid, "works_count_by_category"] == ""
    assert keyed.at["Q643984", "works_count_by_category"] == '{"chamber": 1}'
    assert keyed.at["Qtrusted", "works_count_total"] == 1
    assert out.dump_date.eq("r999").all()
    assert list(kept.columns) == list(works.columns)
    if has_dump_date:
        assert kept.dump_date.eq("r999").all()
    else:
        assert "dump_date" not in kept.columns
    pd.testing.assert_frame_equal(works, original_works)
    expected = works[~works.composer_id.isin({"Q2341356", "Q8017433", "Q956296"})].copy()
    if has_dump_date:
        expected["dump_date"] = "r999"
    pd.testing.assert_frame_equal(kept, expected)
    errors, _ = check_data_quality(out, kept, {})
    assert errors == []
    errors, _ = check_data_quality(out, works, {})
    assert any("without matched/unverified_heuristic status: 4" in e for e in errors)


@pytest.mark.parametrize("status", ["not_found", "not_checked", "rejected_heuristic", "rejected_p839", ""])
def test_release_gate_for_inactive_owners(status):
    composers = pd.DataFrame([{"composer_id": "Q1", "imslp_match_status": status, "imslp_category": "Category:One"}])
    errors, _ = check_data_quality(composers, pd.DataFrame([{"composer_id": "Q1"}]), {})
    assert len(errors) == 1 and "without matched/unverified_heuristic status: 1" in errors[0]


def test_release_gate_ignores_empty_categories_and_inactive_claimants():
    composers = pd.DataFrame([
        {"composer_id": "Q1", "imslp_match_status": "matched", "imslp_category": "Category:Shared"},
        {"composer_id": "Q2", "imslp_match_status": "rejected_heuristic", "imslp_category": "Category:Shared"},
        {"composer_id": "Q3", "imslp_match_status": "unverified_heuristic", "imslp_category": ""},
        {"composer_id": "Q4", "imslp_match_status": "matched", "imslp_category": ""},
    ])
    assert check_data_quality(composers, pd.DataFrame(columns=["composer_id"]), {})[0] == []


@pytest.fixture
def dump_files(source, tmp_path, monkeypatch):
    composers, works, pages = source
    monkeypatch.setattr(common, "DATA_DIR", tmp_path)
    calls = []

    def cache_get(namespace, key):
        calls.append((namespace, key))
        return pages[key]

    monkeypatch.setattr(common, "cache_get", cache_get)
    composers.to_csv(tmp_path / "composers_r010.tsv", sep="\t", index=False)
    works.to_csv(tmp_path / "works_r010.tsv", sep="\t", index=False)
    return tmp_path, composers, works, pages, calls


def args(dry_run=False):
    return argparse.Namespace(from_dump="r010", to="r999", dry_run=dry_run)


def test_dry_run_report_and_no_writes(dump_files, capsys):
    directory, _, _, _, calls = dump_files
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    remap.run(args(dry_run=True))
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == before
    output = capsys.readouterr().out
    for expected in [
        "IMSLP identity remap r010 → r999 (offline)",
        "unverified_heuristic → matched: 2", "unverified_heuristic → rejected_heuristic: 3",
        "Jaroslav Kvapil (Q956296): dump 1868–1950; IMSLP 1892–1958", "reason: dates_conflict_strong",
        "Leo Smit (Q2341356): dump 1921–1999; IMSLP 1900–1943", "works dropped 2",
        "winner: Leo Smit (Q643984)", "winner: William Reed (Q373961)", "Collisions: 2",
        "P839 date conflicts: 1", "Trusted (Qtrusted): dump 1900–1943; IMSLP 1898–1943",
        "Rejected P839 rows: 0", "Weak conflicts kept (flagged imslp_dates_conflict): 1", "wikilink_agree; status: matched; works kept",
        "Heuristics left unverified: 2", "reason: no_comparable_years", "reason: page_missing",
        "Works dropped: 4 (EU PD composers: 1)", "Evidence counts:", "performer_page: 1", "collision_lost: 2",
    ]:
        assert expected in output
    assert len(calls) == 7
    assert all(namespace == imslp.CAT_PAGE_NAMESPACE for namespace, _ in calls)
    assert (imslp.CAT_PAGE_NAMESPACE, "Category:Smit,_Leo") in calls


def test_dry_run_reports_wrong_p839_and_weak_heuristic_link(dump_files, capsys):
    directory, _, _, pages, _ = dump_files
    pages["Category:Trusted"] = page("1800", "1843", title="Trusted")
    pages["Category:Unknown"] = page("1898", "1943", title="Unknown")
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    remap.run(args(dry_run=True))
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == before
    output = capsys.readouterr().out
    for expected in [
        "matched → rejected_p839: 1", "Rejected P839 rows: 1",
        "Trusted (Qtrusted): dump 1900–1943; IMSLP 1800–1843", "imslp_p839_wrong",
        "Weak conflicts kept (flagged imslp_dates_conflict): 1",
        "Unknown (Qunknown): dump 1900–1943; IMSLP 1898–1943",
        "wikilink_agree; status: matched; works kept",
        "Works dropped: 5 (EU PD composers: 2)",
        "dates_conflict_strong: 4", "dates_conflict_weak: 1",
    ]:
        assert expected in output


@pytest.mark.parametrize("with_new_conflicts", [False, True])
@pytest.mark.parametrize("has_dump_date", [False, True])
def test_writer_preserves_cells_and_records_complete_report(dump_files, with_new_conflicts, has_dump_date):
    directory, composers, works, pages, _ = dump_files
    if has_dump_date:
        works["dump_date"] = "r010"
        works.to_csv(directory / "works_r010.tsv", sep="\t", index=False)
    if with_new_conflicts:
        pages["Category:Trusted"] = page("1800", "1843", title="Trusted")
        pages["Category:Unknown"] = page("1898", "1943", title="Unknown")
    before = (directory / "composers_r010.tsv").read_bytes()
    remap.run(args())
    assert (directory / "composers_r010.tsv").read_bytes() == before
    written = pd.read_csv(directory / "composers_r999.tsv", sep="\t", dtype=str, keep_default_na=False)
    untouched = [col for col in composers if col not in {
        "imslp_match_status", "imslp_match_method", "imslp_url", "qa_flags", "imslp_works_count",
        "works_count_total", "works_count_by_category", "work_categories_present", "dump_date",
    }]
    pd.testing.assert_frame_equal(written[untouched], composers[untouched].reset_index(drop=True))
    kept = pd.read_csv(directory / "works_r999.tsv", sep="\t", dtype=str, keep_default_na=False)
    assert kept["title"].eq("NA").all() and kept["fetched_at"].eq("None").all()
    assert kept["imslp_pageid"].str.endswith(".0").all()
    assert list(kept.columns) == list(works.columns)
    if has_dump_date:
        assert kept["dump_date"].eq("r999").all()
    else:
        assert "dump_date" not in kept.columns
    meta = json.loads((directory / "dump_meta_r999.json").read_text())
    assert meta["row_counts"] == {"composers": 10, "works": 5 if with_new_conflicts else 6}
    assert meta["derived_from_dump_id"] == "r010"
    expected_report = remap.remap_matches(composers, works, "r999", pages)[2]
    for key, value in expected_report.items():
        assert meta[key] == value
    if with_new_conflicts:
        assert meta["rejected_p839"][0]["qid"] == "Qtrusted"
        assert meta["rejected_p839"][0]["works_dropped"] == 1
        assert meta["weak_conflicts_kept"][0]["qid"] == "Qunknown"
        assert meta["weak_conflicts_kept"][0]["wikilink"] == "agree"


@pytest.mark.parametrize("filename", ["composers_r999.tsv", "works_r999.tsv", "dump_meta_r999.json"])
def test_writer_refuses_overwrite_before_output(dump_files, filename):
    directory, *_ = dump_files
    (directory / filename).write_text("sentinel")
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        remap.run(args())
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == before
