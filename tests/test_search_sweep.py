import argparse

import pandas as pd

import search_sweep as ss


def test_parse_output_keeps_batch_ids_and_real_urls_only():
    text = "\n".join(
        [
            "# tokens_used=123",
            "Q1\t1\thttps://a.example/scores.html\tScores",
            "Q1\t2\tftp://b.example/x\tnot http",
            "Q2\t0\t\t",
            "Q9\t1\thttps://c.example/\tnot in batch",
            "Here are the results:",
        ]
    )
    rows = ss.parse_output(text, {"Q1", "Q2"})
    assert [(r["composer_id"], r["rank"], r["url"]) for r in rows] == [
        ("Q1", "1", "https://a.example/scores.html"),
        ("Q2", "0", ""),
    ]


def test_select_composers_skips_pd_and_keeps_included_ids():
    composers = pd.DataFrame(
        {
            "composer_id": ["Q1", "Q2", "Q3", "Q4", "Q5"],
            "name_display": list("ABCDE"),
            "eu_pd_status": ["pd", "not_pd", "unknown_death", "not_pd", "not_pd"],
        }
    )
    args = argparse.Namespace(sample=2, include=["Q5"], seed=1, limit=0)
    chosen = ss.select_composers(composers, args)
    assert len(chosen) == 2 and "Q5" in set(chosen["composer_id"]) and "Q1" not in set(chosen["composer_id"])
    assert ss.query_for("Kyle Gann") == '"Kyle Gann" score'
