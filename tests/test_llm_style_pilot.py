"""Determinism checks for the stratified pilot sample."""

from __future__ import annotations

import pandas as pd

import pilot_sample as ps


def _fake_composers(n_wd=50, n_pd=50, n_other=40) -> pd.DataFrame:
    rows = []
    for i in range(n_wd):
        rows.append(
            {
                "composer_id": f"QW{i:03d}",
                "style_tags": "serialism",
                "style_tags_src": "wikidata",
                "eu_pd_status": "not_pd",
                "imslp_works_count": 0,
                "wikipedia_url": f"https://en.wikipedia.org/wiki/W{i}",
                "qa_flags": "",
            }
        )
    for i in range(n_pd):
        rows.append(
            {
                "composer_id": f"QP{i:03d}",
                "style_tags": "",
                "style_tags_src": "",
                "eu_pd_status": "pd",
                "imslp_works_count": 3,
                "wikipedia_url": f"https://en.wikipedia.org/wiki/P{i}",
                "qa_flags": "",
            }
        )
    for i in range(n_other):
        rows.append(
            {
                "composer_id": f"QO{i:03d}",
                "style_tags": "",
                "style_tags_src": "",
                "eu_pd_status": "not_pd",
                "imslp_works_count": 0,
                "wikipedia_url": f"https://en.wikipedia.org/wiki/O{i}",
                "qa_flags": "",
            }
        )
    # Excluded rows must never appear
    rows.append(
        {
            "composer_id": "QBAD1",
            "style_tags": "serialism",
            "style_tags_src": "wikidata",
            "eu_pd_status": "pd",
            "imslp_works_count": 5,
            "wikipedia_url": "https://en.wikipedia.org/wiki/Bad",
            "qa_flags": "not_human",
        }
    )
    rows.append(
        {
            "composer_id": "QBAD2",
            "style_tags": "",
            "style_tags_src": "",
            "eu_pd_status": "pd",
            "imslp_works_count": 5,
            "wikipedia_url": "https://en.wikipedia.org/wiki/Bad2",
            "qa_flags": "no_composer_occupation",
        }
    )
    return pd.DataFrame(rows)


def test_pilot_sample_deterministic_and_stratified():
    df = _fake_composers()
    a = ps.build_sample(df, seed=42)
    b = ps.build_sample(df, seed=42)
    c = ps.build_sample(df, seed=43)
    assert a == b
    assert a != c
    assert len(a) == 100
    assert len(set(a)) == 100
    assert "QBAD1" not in a and "QBAD2" not in a
    wd = [x for x in a if x.startswith("QW")]
    pd_ids = [x for x in a if x.startswith("QP")]
    other = [x for x in a if x.startswith("QO")]
    assert len(wd) == 40
    assert len(pd_ids) == 40
    assert len(other) == 20
