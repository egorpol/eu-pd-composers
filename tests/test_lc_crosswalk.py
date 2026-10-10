"""The committed LC crosswalk covers every force_family / genre_form value."""

import lc_crosswalk


def test_committed_crosswalk_is_complete():
    rows = lc_crosswalk.load_crosswalk()
    assert lc_crosswalk.crosswalk_errors(rows) == []


def test_crosswalk_errors():
    good = {"facet": "genre_form", "value": "sonata", "scheme": "lcgft", "relation": "exactMatch",
            "label": "Sonatas", "uri": "http://id.loc.gov/authorities/genreForms/gf2014027099", "note": ""}
    rows = [
        good,
        {**good, "value": "symphony", "uri": "http://id.loc.gov/authorities/performanceMediums/mp2013015550"},
        {**good, "value": "suite", "relation": "sameAs", "uri": "http://id.loc.gov/authorities/genreForms/gf2014027116"},
        {**good, "value": "polka"},
        {**good, "value": "other", "scheme": "", "uri": ""},
    ]
    errors = lc_crosswalk.crosswalk_errors(rows)
    assert any("symphony" in e and "not a lcgft uri" in e for e in errors)
    assert any("suite" in e and "SKOS" in e for e in errors)
    assert any("polka" in e and "unknown value" in e for e in errors)
    assert any("other" in e and "must be empty" in e for e in errors)
    assert any(e.startswith("force_family values missing") for e in errors)
