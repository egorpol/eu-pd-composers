"""Agreement metrics on a tiny fixture with known values."""

from __future__ import annotations

import pandas as pd

import style_agreement as sa


def test_jaccard_and_kappa_basics():
    assert sa.jaccard(["a", "b"], ["b", "c"]) == 1 / 3
    assert sa.jaccard([], []) == 1.0
    assert sa.jaccard(["a"], []) == 0.0
    # Perfect agreement
    assert sa.cohens_kappa([("x", "x"), ("y", "y"), ("x", "x")]) == 1.0
    # Chance-level-ish: always predict majority
    pairs = [("a", "a"), ("a", "b"), ("a", "a"), ("a", "b")]
    k = sa.cohens_kappa(pairs)
    assert k is not None
    assert -1.0 <= k <= 1.0


def test_micro_pr_known():
    preds = [{"romantic"}, {"modern", "serialism"}]
    refs = [{"romantic", "national_folk"}, {"modern"}]
    # tp: romantic + modern = 2; fp: serialism = 1; fn: national_folk = 1
    prec, rec, f1 = sa.micro_pr(preds, refs)
    assert prec == 2 / 3
    assert rec == 2 / 3
    assert abs(f1 - 2 / 3) < 1e-9


def test_dominant_imslp_period_ties():
    assert sa.dominant_imslp_period(["Romantic", "Romantic", "Modern"]) == {"romantic"}
    assert sa.dominant_imslp_period(["Romantic", "Modern"]) == {"romantic", "modern"}
    assert sa.dominant_imslp_period(["Romántico"]) == {"romantic"}
    assert sa.dominant_imslp_period([]) == set()


def test_build_report_fixture():
    composers = pd.DataFrame(
        [
            {
                "composer_id": "Q1",
                "style_tags": "serialism|avant_garde",
                "style_tags_src": "wikidata",
            },
            {
                "composer_id": "Q2",
                "style_tags": "",
                "style_tags_src": "",
            },
            {
                "composer_id": "Q3",
                "style_tags": "minimalism",
                "style_tags_src": "wikidata",
            },
        ]
    )
    works = pd.DataFrame(
        [
            {"composer_id": "Q1", "imslp_style": "Modern"},
            {"composer_id": "Q1", "imslp_style": "Modern"},
            {"composer_id": "Q1", "imslp_style": "Early 20th century"},
            {"composer_id": "Q2", "imslp_style": "Romantic"},
            {"composer_id": "Q3", "imslp_style": "Modern"},
            {"composer_id": "Q3", "imslp_style": "Romantic"},  # tie
        ]
    )
    ledger = [
        {
            "composer_id": "Q1",
            "model": "mA",
            "condition": "closed",
            "prompt_version": "style-v1",
            "styles": ["serialism"],
            "primary_period": "modern",
            "confidence": "high",
        },
        {
            "composer_id": "Q2",
            "model": "mA",
            "condition": "closed",
            "prompt_version": "style-v1",
            "styles": [],
            "primary_period": "unknown",
            "confidence": "low",
        },
        {
            "composer_id": "Q3",
            "model": "mA",
            "condition": "closed",
            "prompt_version": "style-v1",
            "styles": ["minimalism"],
            "primary_period": "modern",
            "confidence": "medium",
        },
        {
            "composer_id": "Q1",
            "model": "mA",
            "condition": "grounded",
            "prompt_version": "style-v2",
            "styles": ["serialism", "avant_garde"],
            "primary_period": "modern",
            "confidence": "high",
        },
        {
            "composer_id": "Q1",
            "model": "mB",
            "condition": "closed",
            "prompt_version": "style-v1",
            "styles": ["avant_garde"],
            "primary_period": "early_20th_century",
            "confidence": "medium",
        },
    ]
    report = sa.build_report(ledger, composers, works)
    assert "ground truth" in report["disclaimer"].lower() or "Not ground" in report["disclaimer"] or "no source is ground truth" in report["disclaimer"].lower()

    closed_a = next(
        r for r in report["per_model_condition"] if r["model"] == "mA" and r["condition"] == "closed@style-v1"
    )
    assert closed_a["coverage_n"] == 3
    assert closed_a["abstain_n"] == 1
    # Wikidata refs: Q1 and Q3
    assert closed_a["wikidata"]["n"] == 2
    # Q1: {serialism} vs {serialism, avant_garde} → 1/2; Q3: perfect → 1
    assert abs(closed_a["wikidata"]["mean_jaccard"] - 0.75) < 1e-9
    # IMSLP: Q1 modern in {modern} → hit; Q2 abstain period skipped; Q3 modern in {modern,romantic} → hit
    assert closed_a["imslp_primary_period"]["n"] == 2
    assert closed_a["imslp_primary_period"]["n_ties"] == 1
    assert closed_a["imslp_primary_period"]["accuracy"] == 1.0

    # inter-model on closed for Q1
    im = next(r for r in report["inter_model"] if r["condition"] == "closed@style-v1")
    assert im["n"] == 1
    assert im["mean_jaccard_styles"] == 0.0  # serialism vs avant_garde

    cvg = next(r for r in report["closed_vs_grounded"] if r["model"] == "mA")
    assert cvg["n"] == 1
    assert (cvg["closed"], cvg["grounded"]) == ("closed@style-v1", "grounded@style-v2")
    assert cvg["mean_jaccard_styles"] == 0.5  # {serialism} vs {serialism, avant_garde}

    md = sa.render_markdown(report)
    assert "Wikidata" in md
    assert "IMSLP" in md
    assert "not ground truth" in md.lower() or "Not ground truth" in md


def test_pair_agreement_counts_both_abstain_separately():
    a = {
        "Q1": {"styles": [], "primary_period": "unknown"},
        "Q2": {"styles": ["romantic"], "primary_period": "romantic"},
        "Q3": {"styles": ["baroque"], "primary_period": "baroque"},
    }
    b = {
        "Q1": {"styles": [], "primary_period": "unknown"},
        "Q2": {"styles": ["romantic"], "primary_period": "romantic"},
        "Q3": {"styles": [], "primary_period": "unknown"},
    }
    out = sa.pair_agreement(a, b, ["Q1", "Q2", "Q3"])
    assert out["n"] == 3
    assert out["n_both_abstain_styles"] == 1
    assert out["n_styles_labelled"] == 2
    assert out["mean_jaccard_styles"] == 0.5  # Q2 = 1.0, Q3 = 0.0; Q1 not scored
    assert out["n_period_known"] == 1
