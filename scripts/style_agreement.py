#!/usr/bin/env python3
"""Agreement report: LLM style ledger vs Wikidata / IMSLP (no source is ground truth).

Reads data/llm_ledger/style_labels.jsonl + a composers/works dump and writes a
markdown report (optional --json). Comparisons clearly label which side is the
reference for precision/recall or accuracy; disagreement between sources is the
research finding.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Optional

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import DATA_DIR, dump_tsv_path, pipe_split  # noqa: E402
from style_vocab import (  # noqa: E402
    ERA_SLUGS,
    IMSLP_PERIOD_TO_SLUG,
    map_imslp_period,
    map_wikidata_tag,
)

LEDGER_PATH = DATA_DIR / "llm_ledger" / "style_labels.jsonl"

# Three-way comparison: the full-pass labellers and prompt versions (llm_style_pass.PROMPT_VERSIONS).
THREE_WAY_LABELLERS = ("claude-haiku-5-5@low", "gpt-6.1-sol@low", "grok-4.7-low")
THREE_WAY_CONDITIONS = ("closed@style-v1", "grounded@style-v2")
# apply_llm_styles.py: the viewer shows the tags both of these gave in the grounded condition.
VIEWER_CONSENSUS = ("gpt-6.1-sol@low", "grok-4.7-low")

DISCLAIMER = (
    "No source is ground truth. Wikidata and IMSLP are independent references "
    "for measuring agreement/disagreement with LLM labels; disagreement is the finding."
)


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if text.lower() in {"nan", "none"}:
        return ""
    return text


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def cohens_kappa(pairs: list[tuple[str, str]]) -> float | None:
    """Cohen's κ for two categorical label sequences (same length)."""
    if not pairs:
        return None
    n = len(pairs)
    labels = sorted({x for p in pairs for x in p})
    if len(labels) < 2 and all(a == b for a, b in pairs):
        return 1.0
    po = sum(1 for a, b in pairs if a == b) / n
    ca = Counter(a for a, _ in pairs)
    cb = Counter(b for _, b in pairs)
    pe = sum((ca[l] / n) * (cb[l] / n) for l in labels)
    if abs(1.0 - pe) < 1e-12:
        return 1.0 if abs(po - 1.0) < 1e-12 else 0.0
    return (po - pe) / (1.0 - pe)


def masi_distance(a: Iterable[str], b: Iterable[str]) -> float:
    """MASI distance for set-valued labels (Passonneau 2006): 1 − Jaccard × monotonicity.

    Monotonicity is 1 for equal sets, 2/3 when one contains the other, 1/3 when they only
    overlap, 0 when disjoint. Two empty sets (both abstain) are identical.
    """
    sa, sb = set(a), set(b)
    if sa == sb:
        return 0.0
    if not sa & sb:
        return 1.0
    monotonicity = 2 / 3 if sa <= sb or sb <= sa else 1 / 3
    return 1.0 - jaccard(sa, sb) * monotonicity


def krippendorff_alpha(units: list[list[Any]], distance: Any = None) -> float | None:
    """Krippendorff's α for any number of coders, missing values allowed.

    `units` holds the values coders gave each unit; units with fewer than two values are not
    pairable and drop out. `distance(a, b)` defaults to nominal (0 if equal, else 1).
    """
    delta = distance or (lambda a, b: 0.0 if a == b else 1.0)
    pairable = [u for u in units if len(u) >= 2]
    # Values must be hashable: expected disagreement runs over distinct values, not all n² pairs.
    counts = Counter(v for u in pairable for v in u)
    n = sum(counts.values())
    if n < 2:
        return None
    observed = sum(
        sum(delta(a, b) for i, a in enumerate(u) for j, b in enumerate(u) if i != j) / (len(u) - 1)
        for u in pairable
    ) / n
    expected = sum(
        ca * (cb - (a == b)) * delta(a, b) for a, ca in counts.items() for b, cb in counts.items()
    ) / (n * (n - 1))
    if expected == 0:
        return 1.0 if observed == 0 else None
    return 1.0 - observed / expected


def micro_pr(
    preds: list[set[str]], refs: list[set[str]]
) -> tuple[float | None, float | None, float | None]:
    """Micro precision / recall / F1 treating refs as reference multi-labels."""
    tp = fp = fn = 0
    for pred, ref in zip(preds, refs):
        tp += len(pred & ref)
        fp += len(pred - ref)
        fn += len(ref - pred)
    prec = tp / (tp + fp) if (tp + fp) else None
    rec = tp / (tp + fn) if (tp + fn) else None
    if prec is None or rec is None or (prec + rec) == 0:
        f1 = None if prec is None or rec is None else 0.0
    else:
        f1 = 2 * prec * rec / (prec + rec)
    return prec, rec, f1


def load_ledger(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def is_abstain(row: dict[str, Any]) -> bool:
    styles = row.get("styles") or []
    return (not styles) and str(row.get("primary_period", "")) == "unknown"


def pair_agreement(
    a: dict[str, dict[str, Any]], b: dict[str, dict[str, Any]], common: list[str]
) -> dict[str, Any]:
    """Agreement of two label sets on shared composers.

    Both-abstain pairs are counted separately: scoring them as Jaccard 1.0 would let
    shared silence pass for agreement on labels.
    """
    sa = {c: a[c].get("styles") or [] for c in common}
    sb = {c: b[c].get("styles") or [] for c in common}
    labelled = [c for c in common if sa[c] or sb[c]]
    jacs = [jaccard(sa[c], sb[c]) for c in labelled]
    pairs = [
        (str(a[c].get("primary_period", "")), str(b[c].get("primary_period", "")))
        for c in common
    ]
    known = [(x, y) for x, y in pairs if x != "unknown" and y != "unknown"]
    return {
        "n": len(common),
        "n_both_abstain_styles": len(common) - len(labelled),
        "n_styles_labelled": len(labelled),
        "mean_jaccard_styles": sum(jacs) / len(jacs) if jacs else None,
        "n_period_known": len(known),
        "cohens_kappa_primary_period": cohens_kappa(known),
    }


def dominant_imslp_period(periods: list[str]) -> set[str]:
    """Most frequent mapped IMSLP period slug among works; ties → set."""
    counts: Counter[str] = Counter()
    for p in periods:
        slug = map_imslp_period(p)
        if slug:
            counts[slug] += 1
    if not counts:
        return set()
    top = max(counts.values())
    return {s for s, n in counts.items() if n == top}


def wikidata_styles(row: pd.Series) -> set[str]:
    if _as_str(row.get("style_tags_src")) != "wikidata":
        return set()
    out: set[str] = set()
    for tag in pipe_split(row.get("style_tags")):
        mapped = map_wikidata_tag(tag)
        if mapped:
            out.add(mapped)
    return out


def group_ledger(ledger: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, dict[str, Any]]]:
    """(labeller, condition@prompt_version) → composer_id → row; the last line per composer wins."""
    groups: dict[tuple[str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in ledger:
        cond = f"{row.get('condition', '')}@{row.get('prompt_version', '')}"
        # Codex efforts share one model id, so the effort is part of the labeller's name.
        model = str(row.get("model", ""))
        if row.get("effort"):
            model = f"{model}@{row['effort']}"
        groups[(model, cond)][str(row.get("composer_id", ""))] = row
    return groups


def composer_refs(
    composers: pd.DataFrame, works: pd.DataFrame
) -> tuple[pd.DataFrame, dict[str, set[str]]]:
    """Composers indexed by id, and the dominant IMSLP period per composer."""
    composers = composers.copy()
    composers["composer_id"] = composers["composer_id"].astype(str)
    by_cid = composers.set_index("composer_id", drop=False)
    imslp_dom: dict[str, set[str]] = {}
    for cid, grp in works.groupby(works["composer_id"].astype(str)):
        periods: list[str] = []
        for val in grp["imslp_style"].tolist():
            periods.extend(pipe_split(val))
        imslp_dom[str(cid)] = dominant_imslp_period(periods)
    return by_cid, imslp_dom


def wikidata_scores(preds: dict[str, set[str]], by_cid: pd.DataFrame) -> dict[str, Any]:
    """Style tags vs Wikidata (Wikidata = reference) on composers that have Wikidata styles."""
    jacs: list[float] = []
    pred_sets: list[set[str]] = []
    ref_sets: list[set[str]] = []
    for cid, pred in preds.items():
        if cid not in by_cid.index:
            continue
        ref = wikidata_styles(by_cid.loc[cid])
        if not ref:
            continue
        jacs.append(jaccard(pred, ref))
        pred_sets.append(pred)
        ref_sets.append(ref)
    prec, rec, f1 = micro_pr(pred_sets, ref_sets) if pred_sets else (None, None, None)
    return {
        "n": len(jacs),
        "mean_jaccard": sum(jacs) / len(jacs) if jacs else None,
        "micro_precision": prec,
        "micro_recall": rec,
        "micro_f1": f1,
    }


def imslp_period_scores(
    by_composer: dict[str, dict[str, Any]], imslp_dom: dict[str, set[str]]
) -> dict[str, Any]:
    """primary_period vs the dominant IMSLP period (IMSLP = reference for accuracy)."""
    period_pairs: list[tuple[str, str]] = []
    correct = 0
    compared = 0
    ties = 0
    for cid, row in by_composer.items():
        dom = imslp_dom.get(cid) or set()
        # Only compare when IMSLP has a mappable dominant period and model did not abstain period
        pred = str(row.get("primary_period", ""))
        if not dom or pred == "unknown":
            continue
        compared += 1
        if len(dom) > 1:
            ties += 1
        if pred in dom:
            correct += 1
        # κ uses a single reference label: singleton only
        if len(dom) == 1:
            period_pairs.append((pred, next(iter(dom))))

    # Confusion: predicted period × IMSLP dominant (singletons only)
    confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    labels_axis = list(ERA_SLUGS) + ["jazz", "traditional_folk", "non_western_classical", "unknown"]
    for pred, ref in period_pairs:
        confusion[pred][ref] += 1
    return {
        "reference": "dominant mapped IMSLP work imslp_style (ties → set; accuracy = membership)",
        "n": compared,
        "n_ties": ties,
        "accuracy": correct / compared if compared else None,
        "cohens_kappa_singletons": cohens_kappa(period_pairs),
        "kappa_n": len(period_pairs),
        "confusion": {p: dict(confusion[p]) for p in confusion},
        "confusion_axis_note": labels_axis,
    }


def build_report(
    ledger: list[dict[str, Any]],
    composers: pd.DataFrame,
    works: pd.DataFrame,
) -> dict[str, Any]:
    by_cid, imslp_dom = composer_refs(composers, works)

    groups = group_ledger(ledger)

    per_mc: list[dict[str, Any]] = []
    for (model, condition), by_composer in sorted(groups.items()):
        n = len(by_composer)
        abstain_n = sum(1 for r in by_composer.values() if is_abstain(r))
        styles = {cid: set(row.get("styles") or []) for cid, row in by_composer.items()}
        per_mc.append(
            {
                "model": model,
                "condition": condition,
                "coverage_n": n,
                "abstain_n": abstain_n,
                "abstain_rate": abstain_n / n if n else None,
                "wikidata": {
                    "reference": "wikidata style_tags (style_tags_src=wikidata)",
                    **wikidata_scores(styles, by_cid),
                },
                "imslp_primary_period": imslp_period_scores(by_composer, imslp_dom),
            }
        )

    # Inter-model agreement (same condition)
    inter_model: list[dict[str, Any]] = []
    by_cond: dict[str, dict[str, dict[str, dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    for (model, condition), by_composer in groups.items():
        by_cond[condition][model] = by_composer
    for condition, models in sorted(by_cond.items()):
        mnames = sorted(models)
        for i, m1 in enumerate(mnames):
            for m2 in mnames[i + 1 :]:
                common = sorted(set(models[m1]) & set(models[m2]))
                if not common:
                    continue
                inter_model.append(
                    {
                        "condition": condition,
                        "model_a": m1,
                        "model_b": m2,
                        **pair_agreement(models[m1], models[m2], common),
                        "note": "pairwise model agreement; neither side is ground truth",
                    }
                )

    # Closed vs grounded (same model)
    closed_vs_grounded: list[dict[str, Any]] = []
    by_model: dict[str, dict[str, dict[str, dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    for (model, condition), by_composer in groups.items():
        by_model[model][condition] = by_composer
    for model, conds in sorted(by_model.items()):
        for closed in sorted(c for c in conds if c.startswith("closed@")):
            for grounded in sorted(c for c in conds if c.startswith("grounded@")):
                common = sorted(set(conds[closed]) & set(conds[grounded]))
                if not common:
                    continue
                closed_vs_grounded.append(
                    {
                        "model": model,
                        "closed": closed,
                        "grounded": grounded,
                        **pair_agreement(conds[closed], conds[grounded], common),
                        "note": "closed vs grounded; neither condition is ground truth",
                    }
                )

    return {
        "disclaimer": DISCLAIMER,
        "ledger_rows": len(ledger),
        "per_model_condition": per_mc,
        "inter_model": inter_model,
        "closed_vs_grounded": closed_vs_grounded,
        "imslp_period_map": dict(IMSLP_PERIOD_TO_SLUG),
    }


def vote(rows: list[dict[str, Any]], k: int) -> set[str]:
    """Style tags that at least k of the given labels carry."""
    counts = Counter(s for row in rows for s in set(row.get("styles") or []))
    return {s for s, c in counts.items() if c >= k}


def build_three_way(
    ledger: list[dict[str, Any]],
    composers: pd.DataFrame,
    works: pd.DataFrame,
    labellers: tuple[str, ...] = THREE_WAY_LABELLERS,
    conditions: tuple[str, ...] = THREE_WAY_CONDITIONS,
) -> dict[str, Any]:
    """The labellers on equal terms: only composers whose input hash all of them share.

    Aggregates only; never per-composer labels (some composers are blind gold-set items).
    """
    by_cid, imslp_dom = composer_refs(composers, works)
    groups = group_ledger(ledger)
    out: list[dict[str, Any]] = []
    for condition in conditions:
        label_sets = [groups.get((lab, condition), {}) for lab in labellers]
        seen = set().union(*label_sets)
        shared = sorted(
            cid
            for cid in set.intersection(*(set(s) for s in label_sets))
            if len({s[cid].get("input_hash") for s in label_sets}) == 1
        )
        rows = {lab: {cid: s[cid] for cid in shared} for lab, s in zip(labellers, label_sets)}

        per_labeller: list[dict[str, Any]] = []
        for lab in labellers:
            abstain_n = sum(1 for r in rows[lab].values() if is_abstain(r))
            period = imslp_period_scores(rows[lab], imslp_dom)
            per_labeller.append(
                {
                    "model": lab,
                    "abstain_n": abstain_n,
                    "abstain_rate": abstain_n / len(shared) if shared else None,
                    "wikidata": wikidata_scores(
                        {cid: set(r.get("styles") or []) for cid, r in rows[lab].items()}, by_cid
                    ),
                    "imslp_primary_period": {
                        k: v for k, v in period.items() if k not in {"confusion", "confusion_axis_note"}
                    },
                }
            )

        pairs = [
            {"model_a": a, "model_b": b, **pair_agreement(rows[a], rows[b], shared)}
            for i, a in enumerate(labellers)
            for b in labellers[i + 1 :]
        ]

        # An abstain is a missing value, as in gold_set.py: a composer counts once two models answered.
        period_units = [
            [str(rows[lab][cid].get("primary_period", "")) for lab in labellers
             if rows[lab][cid].get("primary_period") != "unknown"]
            for cid in shared
        ]
        style_units = [
            [frozenset(rows[lab][cid]["styles"]) for lab in labellers if rows[lab][cid].get("styles")]
            for cid in shared
        ]

        rules = [((a, b), 2) for i, a in enumerate(labellers) for b in labellers[i + 1 :]]
        rules += [(labellers, 2), (labellers, len(labellers))]
        votes: list[dict[str, Any]] = []
        for members, k in rules:
            tags = {cid: vote([rows[m][cid] for m in members], k) for cid in shared}
            covered = sum(1 for t in tags.values() if t)
            votes.append(
                {
                    "rule": f"{k} of {len(members)}",
                    "members": list(members),
                    "viewer_policy": condition.startswith("grounded@")
                    and k == len(members)
                    and set(members) == set(VIEWER_CONSENSUS),
                    "covered_n": covered,
                    "coverage": covered / len(shared) if shared else None,
                    "wikidata": wikidata_scores(tags, by_cid),
                }
            )

        out.append(
            {
                "condition": condition,
                "n_labelled_by_any": len(seen),
                "n_shared": len(shared),
                "n_left_out": len(seen) - len(shared),
                "per_labeller": per_labeller,
                "pairs": pairs,
                "alpha_primary_period": {
                    "units": sum(1 for u in period_units if len(u) >= 2),
                    "alpha": krippendorff_alpha(period_units),
                },
                "alpha_styles": {
                    "units": sum(1 for u in style_units if len(u) >= 2),
                    "alpha": krippendorff_alpha(style_units, masi_distance),
                },
                "votes": votes,
            }
        )
    return {"disclaimer": DISCLAIMER, "labellers": list(labellers), "conditions": out}


def fmt_pct(x: Optional[float]) -> str:
    if x is None:
        return "n/a"
    return f"{100.0 * x:.1f}%"


def fmt_num(x: Optional[float]) -> str:
    if x is None:
        return "n/a"
    return f"{x:.3f}"


def render_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# LLM style agreement report")
    lines.append("")
    lines.append(report["disclaimer"])
    lines.append("")
    lines.append(f"Ledger rows: {report['ledger_rows']}")
    lines.append("")
    lines.append("## Coverage and abstain rate (per model × condition)")
    lines.append("")
    lines.append("| model | condition | n | abstain | abstain rate |")
    lines.append("|---|---|---:|---:|---:|")
    for row in report["per_model_condition"]:
        lines.append(
            f"| {row['model']} | {row['condition']} | {row['coverage_n']} | "
            f"{row['abstain_n']} | {fmt_pct(row['abstain_rate'])} |"
        )
    lines.append("")
    lines.append("## Agreement with Wikidata styles")
    lines.append("")
    lines.append(
        "Reference: Wikidata `style_tags` where `style_tags_src == wikidata`. "
        "Metrics: per-composer Jaccard (mean) and micro precision/recall/F1 "
        "(Wikidata = reference). Not ground truth."
    )
    lines.append("")
    lines.append("| model | condition | n | mean Jaccard | micro P | micro R | micro F1 |")
    lines.append("|---|---|---:|---:|---:|---:|---:|")
    for row in report["per_model_condition"]:
        wd = row["wikidata"]
        lines.append(
            f"| {row['model']} | {row['condition']} | {wd['n']} | "
            f"{fmt_num(wd['mean_jaccard'])} | {fmt_num(wd['micro_precision'])} | "
            f"{fmt_num(wd['micro_recall'])} | {fmt_num(wd['micro_f1'])} |"
        )
    lines.append("")
    lines.append("## Agreement of primary_period with dominant IMSLP period")
    lines.append("")
    lines.append(
        "Reference: most frequent mapped `imslp_style` among the composer's works "
        "(ties → set; accuracy counts a hit if the prediction is in the set). "
        "Cohen's κ is computed only on singleton (non-tie) dominants. Not ground truth."
    )
    lines.append("")
    lines.append("| model | condition | n | ties | accuracy | κ (singletons) | κ n |")
    lines.append("|---|---|---:|---:|---:|---:|---:|")
    for row in report["per_model_condition"]:
        im = row["imslp_primary_period"]
        lines.append(
            f"| {row['model']} | {row['condition']} | {im['n']} | {im['n_ties']} | "
            f"{fmt_num(im['accuracy'])} | {fmt_num(im['cohens_kappa_singletons'])} | "
            f"{im['kappa_n']} |"
        )
    lines.append("")
    lines.append("### Confusion tables (predicted period × IMSLP dominant, singletons)")
    lines.append("")
    for row in report["per_model_condition"]:
        conf = row["imslp_primary_period"]["confusion"]
        if not conf:
            continue
        lines.append(f"#### {row['model']} / {row['condition']}")
        lines.append("")
        refs = sorted({r for m in conf.values() for r in m})
        preds = sorted(conf)
        header = "| pred \\ imslp | " + " | ".join(refs) + " |"
        sep = "|---|" + "|".join(["---:" for _ in refs]) + "|"
        lines.append(header)
        lines.append(sep)
        for p in preds:
            cells = [str(conf.get(p, {}).get(r, 0)) for r in refs]
            lines.append(f"| {p} | " + " | ".join(cells) + " |")
        lines.append("")

    lines.append("## Inter-model agreement")
    lines.append("")
    lines.append(
        "Same condition, different models. Mean Jaccard on styles over pairs where at least "
        "one model gave styles; κ on `primary_period` over pairs where both gave a period. "
        "Neither model is ground truth."
    )
    lines.append("")
    if report["inter_model"]:
        lines.append(
            "| condition | model A | model B | n | both abstain | Jaccard n | mean Jaccard "
            "| κ n | κ period |"
        )
        lines.append("|---|---|---|---:|---:|---:|---:|---:|---:|")
        for row in report["inter_model"]:
            lines.append(
                f"| {row['condition']} | {row['model_a']} | {row['model_b']} | {row['n']} | "
                f"{row['n_both_abstain_styles']} | {row['n_styles_labelled']} | "
                f"{fmt_num(row['mean_jaccard_styles'])} | {row['n_period_known']} | "
                f"{fmt_num(row['cohens_kappa_primary_period'])} |"
            )
    else:
        lines.append("_No overlapping model pairs in the ledger._")
    lines.append("")
    lines.append("## Closed vs grounded agreement")
    lines.append("")
    lines.append(
        "Same model, closed vs grounded. Same pair rules as above. "
        "Neither condition is ground truth."
    )
    lines.append("")
    if report["closed_vs_grounded"]:
        lines.append(
            "| model | closed | grounded | n | both abstain | Jaccard n | mean Jaccard "
            "| κ n | κ period |"
        )
        lines.append("|---|---|---|---:|---:|---:|---:|---:|---:|")
        for row in report["closed_vs_grounded"]:
            lines.append(
                f"| {row['model']} | {row['closed']} | {row['grounded']} | {row['n']} | "
                f"{row['n_both_abstain_styles']} | {row['n_styles_labelled']} | "
                f"{fmt_num(row['mean_jaccard_styles'])} | {row['n_period_known']} | "
                f"{fmt_num(row['cohens_kappa_primary_period'])} |"
            )
    else:
        lines.append("_No model has both closed and grounded labels in the ledger._")
    lines.append("")
    return "\n".join(lines)


def render_three_way_markdown(report: dict[str, Any]) -> str:
    conds = report["conditions"]
    lines: list[str] = []
    lines.append("# LLM labellers on equal terms")
    lines.append("")
    lines.append(report["disclaimer"])
    lines.append("")
    lines.append(
        "Labellers: " + ", ".join(f"`{m}`" for m in report["labellers"]) + ". Only composers whose "
        "input hash (prompt version + input record) all of them share are compared."
    )
    lines.append("")
    lines.append("| condition | labelled by any | shared input | left out |")
    lines.append("|---|---:|---:|---:|")
    for c in conds:
        lines.append(f"| {c['condition']} | {c['n_labelled_by_any']} | {c['n_shared']} | {c['n_left_out']} |")
    lines.append("")
    lines.append("## Each model")
    lines.append("")
    lines.append(
        "Wikidata: micro precision/recall/F1 of style tags on composers with Wikidata styles "
        "(Wikidata = reference). IMSLP: `primary_period` vs the dominant IMSLP period (accuracy on "
        "composers where the model gave a period; κ on non-tied dominants). Not ground truth."
    )
    lines.append("")
    lines.append(
        "| condition | model | abstain | Wikidata n | P | R | F1 | IMSLP n | accuracy | κ |"
    )
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for c in conds:
        for m in c["per_labeller"]:
            wd, im = m["wikidata"], m["imslp_primary_period"]
            lines.append(
                f"| {c['condition']} | {m['model']} | {fmt_pct(m['abstain_rate'])} | {wd['n']} | "
                f"{fmt_num(wd['micro_precision'])} | {fmt_num(wd['micro_recall'])} | "
                f"{fmt_num(wd['micro_f1'])} | {im['n']} | {fmt_num(im['accuracy'])} | "
                f"{fmt_num(im['cohens_kappa_singletons'])} |"
            )
    lines.append("")
    lines.append("## Each pair")
    lines.append("")
    lines.append(
        "Mean Jaccard on styles where at least one model gave styles; κ on `primary_period` where "
        "both gave a period."
    )
    lines.append("")
    lines.append("| condition | model A | model B | both abstain | Jaccard n | mean Jaccard | κ n | κ period |")
    lines.append("|---|---|---|---:|---:|---:|---:|---:|")
    for c in conds:
        for p in c["pairs"]:
            lines.append(
                f"| {c['condition']} | {p['model_a']} | {p['model_b']} | {p['n_both_abstain_styles']} | "
                f"{p['n_styles_labelled']} | {fmt_num(p['mean_jaccard_styles'])} | "
                f"{p['n_period_known']} | {fmt_num(p['cohens_kappa_primary_period'])} |"
            )
    lines.append("")
    lines.append("## All together")
    lines.append("")
    lines.append(
        "Krippendorff's α (nominal for `primary_period`, MASI distance for style sets). An abstain is "
        "a missing value; a composer counts once at least two models answered."
    )
    lines.append("")
    lines.append("| condition | α period | units | α styles | units |")
    lines.append("|---|---:|---:|---:|---:|")
    for c in conds:
        ap, ast_ = c["alpha_primary_period"], c["alpha_styles"]
        lines.append(
            f"| {c['condition']} | {fmt_num(ap['alpha'])} | {ap['units']} | "
            f"{fmt_num(ast_['alpha'])} | {ast_['units']} |"
        )
    lines.append("")
    lines.append("## Voting rules")
    lines.append("")
    lines.append(
        "A tag is kept when at least k of the listed models gave it. Coverage: share of shared "
        "composers left with at least one tag. Wikidata scores as above (an empty vote counts as "
        "missing every reference tag). (viewer) marks the rule the viewer uses today."
    )
    lines.append("")
    lines.append("| condition | rule | models | covered | coverage | P | R | F1 |")
    lines.append("|---|---|---|---:|---:|---:|---:|---:|")
    for c in conds:
        for v in c["votes"]:
            wd = v["wikidata"]
            rule = v["rule"] + (" (viewer)" if v["viewer_policy"] else "")
            lines.append(
                f"| {c['condition']} | {rule} | {' + '.join(v['members'])} | {v['covered_n']} | "
                f"{fmt_pct(v['coverage'])} | {fmt_num(wd['micro_precision'])} | "
                f"{fmt_num(wd['micro_recall'])} | {fmt_num(wd['micro_f1'])} |"
            )
    lines.append("")
    return "\n".join(lines)


def run(args: argparse.Namespace) -> int:
    ledger = load_ledger(Path(args.ledger) if args.ledger else LEDGER_PATH)
    composers = pd.read_csv(dump_tsv_path("composers", args.dump), sep="\t", low_memory=False)
    works = pd.read_csv(dump_tsv_path("works", args.dump), sep="\t", low_memory=False)
    if args.three_way:
        report = build_three_way(ledger, composers, works)
        md = render_three_way_markdown(report)
    else:
        report = build_report(ledger, composers, works)
        md = render_markdown(report)
    out = Path(args.out) if args.out else None
    if out:
        out.write_text(md, encoding="utf-8")
        print(f"Wrote {out}")
    else:
        print(md)
    if args.json:
        jpath = Path(args.json)
        jpath.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Wrote {jpath}")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dump", required=True)
    p.add_argument("--ledger", default=None, help="Path to style_labels.jsonl")
    p.add_argument("--out", default=None, help="Write markdown report to this path")
    p.add_argument("--json", default=None, help="Also write JSON report to this path")
    p.add_argument(
        "--three-way",
        action="store_true",
        help="Only the full-pass labellers, on composers whose input all of them share",
    )
    raise SystemExit(run(p.parse_args()))


if __name__ == "__main__":
    main()
