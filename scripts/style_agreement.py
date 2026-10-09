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


def build_report(
    ledger: list[dict[str, Any]],
    composers: pd.DataFrame,
    works: pd.DataFrame,
) -> dict[str, Any]:
    composers = composers.copy()
    composers["composer_id"] = composers["composer_id"].astype(str)
    by_cid = composers.set_index("composer_id", drop=False)

    # Dominant IMSLP period per composer
    imslp_dom: dict[str, set[str]] = {}
    for cid, grp in works.groupby(works["composer_id"].astype(str)):
        periods: list[str] = []
        for val in grp["imslp_style"].tolist():
            periods.extend(pipe_split(val))
        imslp_dom[str(cid)] = dominant_imslp_period(periods)

    # Group ledger by (model, condition); keep latest line per composer if duplicates
    groups: dict[tuple[str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in ledger:
        key = (str(row.get("model", "")), str(row.get("condition", "")))
        cid = str(row.get("composer_id", ""))
        groups[key][cid] = row

    per_mc: list[dict[str, Any]] = []
    for (model, condition), by_composer in sorted(groups.items()):
        n = len(by_composer)
        abstain_n = sum(1 for r in by_composer.values() if is_abstain(r))
        # Wikidata agreement (Wikidata = reference)
        jacs: list[float] = []
        pred_sets: list[set[str]] = []
        ref_sets: list[set[str]] = []
        for cid, row in by_composer.items():
            if cid not in by_cid.index:
                continue
            ref = wikidata_styles(by_cid.loc[cid])
            if not ref:
                continue
            pred = set(row.get("styles") or [])
            jacs.append(jaccard(pred, ref))
            pred_sets.append(pred)
            ref_sets.append(ref)
        prec, rec, f1 = micro_pr(pred_sets, ref_sets) if pred_sets else (None, None, None)

        # IMSLP primary_period agreement (IMSLP dominant = reference for accuracy)
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

        acc = correct / compared if compared else None
        kappa = cohens_kappa(period_pairs)

        # Confusion: predicted period × IMSLP dominant (singletons only)
        confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        labels_axis = list(ERA_SLUGS) + ["jazz", "traditional_folk", "non_western_classical", "unknown"]
        for pred, ref in period_pairs:
            confusion[pred][ref] += 1

        per_mc.append(
            {
                "model": model,
                "condition": condition,
                "coverage_n": n,
                "abstain_n": abstain_n,
                "abstain_rate": abstain_n / n if n else None,
                "wikidata": {
                    "reference": "wikidata style_tags (style_tags_src=wikidata)",
                    "n": len(jacs),
                    "mean_jaccard": sum(jacs) / len(jacs) if jacs else None,
                    "micro_precision": prec,
                    "micro_recall": rec,
                    "micro_f1": f1,
                },
                "imslp_primary_period": {
                    "reference": "dominant mapped IMSLP work imslp_style (ties → set; accuracy = membership)",
                    "n": compared,
                    "n_ties": ties,
                    "accuracy": acc,
                    "cohens_kappa_singletons": kappa,
                    "kappa_n": len(period_pairs),
                    "confusion": {p: dict(confusion[p]) for p in confusion},
                    "confusion_axis_note": labels_axis,
                },
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
                jacs = [
                    jaccard(models[m1][c].get("styles") or [], models[m2][c].get("styles") or [])
                    for c in common
                ]
                pairs = [
                    (
                        str(models[m1][c].get("primary_period", "")),
                        str(models[m2][c].get("primary_period", "")),
                    )
                    for c in common
                ]
                inter_model.append(
                    {
                        "condition": condition,
                        "model_a": m1,
                        "model_b": m2,
                        "n": len(common),
                        "mean_jaccard_styles": sum(jacs) / len(jacs) if jacs else None,
                        "cohens_kappa_primary_period": cohens_kappa(pairs),
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
        if "closed" not in conds or "grounded" not in conds:
            continue
        common = sorted(set(conds["closed"]) & set(conds["grounded"]))
        if not common:
            continue
        jacs = [
            jaccard(
                conds["closed"][c].get("styles") or [],
                conds["grounded"][c].get("styles") or [],
            )
            for c in common
        ]
        pairs = [
            (
                str(conds["closed"][c].get("primary_period", "")),
                str(conds["grounded"][c].get("primary_period", "")),
            )
            for c in common
        ]
        closed_vs_grounded.append(
            {
                "model": model,
                "n": len(common),
                "mean_jaccard_styles": sum(jacs) / len(jacs) if jacs else None,
                "cohens_kappa_primary_period": cohens_kappa(pairs),
                "note": "closed vs grounded; neither condition is ground truth",
            }
        )

    return {
        "disclaimer": (
            "No source is ground truth. Wikidata and IMSLP are independent references "
            "for measuring agreement/disagreement with LLM labels; disagreement is the finding."
        ),
        "ledger_rows": len(ledger),
        "per_model_condition": per_mc,
        "inter_model": inter_model,
        "closed_vs_grounded": closed_vs_grounded,
        "imslp_period_map": dict(IMSLP_PERIOD_TO_SLUG),
    }


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
        "Same condition, different models. κ on `primary_period`, mean Jaccard on styles. "
        "Neither model is ground truth."
    )
    lines.append("")
    if report["inter_model"]:
        lines.append("| condition | model A | model B | n | mean Jaccard | κ period |")
        lines.append("|---|---|---|---:|---:|---:|")
        for row in report["inter_model"]:
            lines.append(
                f"| {row['condition']} | {row['model_a']} | {row['model_b']} | {row['n']} | "
                f"{fmt_num(row['mean_jaccard_styles'])} | "
                f"{fmt_num(row['cohens_kappa_primary_period'])} |"
            )
    else:
        lines.append("_No overlapping model pairs in the ledger._")
    lines.append("")
    lines.append("## Closed vs grounded agreement")
    lines.append("")
    lines.append(
        "Same model, closed vs grounded. κ on `primary_period`, mean Jaccard on styles. "
        "Neither condition is ground truth."
    )
    lines.append("")
    if report["closed_vs_grounded"]:
        lines.append("| model | n | mean Jaccard | κ period |")
        lines.append("|---|---:|---:|---:|")
        for row in report["closed_vs_grounded"]:
            lines.append(
                f"| {row['model']} | {row['n']} | {fmt_num(row['mean_jaccard_styles'])} | "
                f"{fmt_num(row['cohens_kappa_primary_period'])} |"
            )
    else:
        lines.append("_No model has both closed and grounded labels in the ledger._")
    lines.append("")
    return "\n".join(lines)


def run(args: argparse.Namespace) -> int:
    ledger = load_ledger(Path(args.ledger) if args.ledger else LEDGER_PATH)
    composers = pd.read_csv(dump_tsv_path("composers", args.dump), sep="\t", low_memory=False)
    works = pd.read_csv(dump_tsv_path("works", args.dump), sep="\t", low_memory=False)
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
    raise SystemExit(run(p.parse_args()))


if __name__ == "__main__":
    main()
