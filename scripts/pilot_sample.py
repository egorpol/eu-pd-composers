#!/usr/bin/env python3
"""Deterministic stratified pilot sample for the LLM style pass (~100 composers).

Writes data/llm_ledger/pilot_ids.txt:
  - 40 composers with Wikidata styles
  - 40 `pd` composers with IMSLP works and no style tags
  - 20 others with a Wikipedia URL

Excludes rows whose qa_flags contain not_human or no_composer_occupation.
Seed is recorded in a header comment.
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path
from typing import Any

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import DATA_DIR, dump_tsv_path  # noqa: E402

SEED = 20261009
OUT_PATH = DATA_DIR / "llm_ledger" / "pilot_ids.txt"
EXCLUDE_FLAGS = ("not_human", "no_composer_occupation")


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


def has_excluded_qa(flags: Any) -> bool:
    text = _as_str(flags)
    if not text:
        return False
    parts = set(text.split("|"))
    return any(f in parts for f in EXCLUDE_FLAGS)


def sample_ids(pool: list[str], n: int, rng: random.Random) -> list[str]:
    ids = sorted(pool)
    rng.shuffle(ids)
    if len(ids) < n:
        raise RuntimeError(f"Need {n} ids but pool only has {len(ids)}")
    return ids[:n]


def build_sample(composers: pd.DataFrame, *, seed: int = SEED) -> list[str]:
    rng = random.Random(seed)
    df = composers.copy()
    df = df[~df["qa_flags"].map(has_excluded_qa)].copy()
    df["composer_id"] = df["composer_id"].astype(str)
    df["_styles"] = df["style_tags"].map(_as_str)
    df["_src"] = df["style_tags_src"].map(_as_str)
    df["_wiki"] = df["wikipedia_url"].map(_as_str)
    df["_pd"] = df["eu_pd_status"].map(_as_str)
    works_count = pd.to_numeric(df.get("imslp_works_count"), errors="coerce").fillna(0)

    wd_pool = df.loc[df["_src"] == "wikidata", "composer_id"].tolist()
    pd_pool = df.loc[
        (df["_pd"] == "pd") & (works_count > 0) & (df["_styles"] == ""),
        "composer_id",
    ].tolist()

    wd_ids = sample_ids(wd_pool, 40, rng)
    # Fresh RNG stream position is fine; exclude already chosen from later pools.
    chosen = set(wd_ids)
    pd_ids = sample_ids([c for c in pd_pool if c not in chosen], 40, rng)
    chosen.update(pd_ids)

    other_pool = df.loc[
        (df["_wiki"] != "") & (~df["composer_id"].isin(chosen)),
        "composer_id",
    ].tolist()
    # Prefer composers outside the wd/pd eligibility pools for the "others" stratum.
    wd_set = set(wd_pool)
    pd_set = set(pd_pool)
    other_pref = [c for c in other_pool if c not in wd_set and c not in pd_set]
    other_fallback = [c for c in other_pool if c not in set(other_pref)]
    if len(other_pref) >= 20:
        other_ids = sample_ids(other_pref, 20, rng)
    else:
        other_ids = sample_ids(other_pref, len(other_pref), rng) if other_pref else []
        need = 20 - len(other_ids)
        other_ids.extend(sample_ids(other_fallback, need, rng))

    # Stable output order: wd, pd, other (each stratum sorted for readability)
    return sorted(wd_ids) + sorted(pd_ids) + sorted(other_ids)


def write_ids(path: Path, ids: list[str], *, seed: int, dump: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# pilot sample for LLM style pass",
        f"# dump={dump} seed={seed} n={len(ids)}",
        f"# strata: 40 wikidata styles, 40 pd+imslp works no style_tags, 20 others with wikipedia_url",
        f"# excluded qa_flags: {', '.join(EXCLUDE_FLAGS)}",
        *ids,
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def run(args: argparse.Namespace) -> int:
    composers = pd.read_csv(dump_tsv_path("composers", args.dump), sep="\t", low_memory=False)
    seed = int(args.seed)
    ids = build_sample(composers, seed=seed)
    out = Path(args.out) if args.out else OUT_PATH
    write_ids(out, ids, seed=seed, dump=args.dump)
    print(f"Wrote {len(ids)} ids → {out} (seed={seed})")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dump", required=True)
    p.add_argument("--seed", type=int, default=SEED)
    p.add_argument("--out", default=None, help="Output path (default: data/llm_ledger/pilot_ids.txt)")
    raise SystemExit(run(p.parse_args()))


if __name__ == "__main__":
    main()
