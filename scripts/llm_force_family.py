#!/usr/bin/env python3
"""LLM-assist force_family for works still unclassified after rules.

Uses Codex CLI + gpt-6-luna (batched). Writes a new dated dump; never overwrites.
Caches per-batch JSON under data/cache/llm_force_family/ for resume.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import CACHE_DIR, DATA_DIR, SCHEMA_VERSION, TOOL_VERSION, next_revision_id, pipe_join  # noqa: E402
from force_family import FORCE_FAMILIES, map_genre_form  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("llm_force_family")

ALLOWED = set(FORCE_FAMILIES)
SCHEMA_PATH = Path(__file__).resolve().parent / "llm_force_family_schema.json"
DEFAULT_CODEX = Path.home() / ".codex/plugins/.plugin-appserver/codex"

WORK_COLUMNS = [
    "work_id",
    "composer_id",
    "imslp_pageid",
    "imslp_work_url",
    "title",
    "imslp_genre_categories",
    "force_family",
    "force_family_src",
    "genre_form",
    "has_files",
    "fetched_at",
]

NEW_COMPOSER_COLS = [
    "work_categories_present",
    "works_count_by_category",
    "works_count_total",
]


def find_codex(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    env = os.environ.get("CODEX_BIN")
    if env:
        return Path(env)
    which = shutil.which("codex")
    if which:
        return Path(which)
    if DEFAULT_CODEX.exists():
        return DEFAULT_CODEX
    raise FileNotFoundError("codex binary not found; pass --codex or set CODEX_BIN")


def write_dump(df: pd.DataFrame, stem: str, dump_date: date) -> Path:
    path = DATA_DIR / f"{stem}_{dump_date.isoformat()}.tsv"
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite existing dump: {path}")
    df.to_csv(path, sep="\t", index=False)
    return path


def build_prompt(batch: list[dict]) -> str:
    payload = json.dumps(batch, ensure_ascii=False)
    families = "|".join(FORCE_FAMILIES)
    return f"""You classify classical sheet-music works into exactly one force_family.

Allowed values only: {families}

Rules:
- Prefer performing forces (piano / orchestra / chorus / chamber / etc.), not era or style.
- If the title is only a tempo or generic character piece with no force cue, use other (not unclassified) when a solo keyboard piece is the usual default for 19th/20th-c salon repertoire; use unclassified only when truly impossible to guess.
- pedagogical for methods/études/schools; stage_opera only for actual stage works.
- Return JSON matching the schema: {{"items":[{{"id":"...","force_family":"..."}}]}} with one entry per input id.

Works JSON:
{payload}
"""


def parse_model_json(text: str) -> dict[str, str]:
    text = text.strip()
    # Strip accidental fences
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    data = json.loads(text)
    items = data["items"] if isinstance(data, dict) and "items" in data else data
    out: dict[str, str] = {}
    for item in items:
        iid = str(item.get("id", "")).strip()
        fam = str(item.get("force_family", "")).strip()
        if iid and fam in ALLOWED:
            out[iid] = fam
    return out


def classify_batch(
    batch: list[dict],
    *,
    codex: Path,
    model: str,
    reasoning: str,
    cache_dir: Path,
    batch_idx: int,
) -> dict[str, str]:
    cache_file = cache_dir / f"batch_{batch_idx:05d}.json"
    if cache_file.exists():
        cached = json.loads(cache_file.read_text(encoding="utf-8"))
        return {k: v for k, v in cached.get("mapping", {}).items() if v in ALLOWED}

    prompt = build_prompt(batch)
    out_msg = cache_dir / f"batch_{batch_idx:05d}.last.txt"
    err_path = cache_dir / f"batch_{batch_idx:05d}.err"

    cmd = [
        str(codex),
        "exec",
        "-m",
        model,
        "-c",
        f'model_reasoning_effort="{reasoning}"',
        "-s",
        "read-only",
        "--skip-git-repo-check",
        "--output-schema",
        str(SCHEMA_PATH),
        "-o",
        str(out_msg),
        prompt,
    ]
    env = os.environ.copy()
    env["CODEX_HOME"] = str(Path.home() / ".codex")
    # Avoid broken corporate proxies for API calls
    for k in (
        "http_proxy",
        "https_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "all_proxy",
        "ALL_PROXY",
    ):
        env.pop(k, None)

    log.info("Codex batch %d (%d works) …", batch_idx, len(batch))
    started = time.monotonic()
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=env,
        cwd=str(DATA_DIR.parent),
    )
    err_path.write_text(proc.stderr or "", encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError(
            f"codex failed batch {batch_idx} rc={proc.returncode}: "
            f"{(proc.stderr or '')[-500:]}"
        )
    raw = out_msg.read_text(encoding="utf-8") if out_msg.exists() else (proc.stdout or "")
    mapping = parse_model_json(raw)
    elapsed = time.monotonic() - started
    log.info(
        "batch %d done in %.1fs — mapped %d/%d",
        batch_idx,
        elapsed,
        len(mapping),
        len(batch),
    )
    cache_file.write_text(
        json.dumps(
            {
                "batch_idx": batch_idx,
                "mapping": mapping,
                "raw": raw,
                "elapsed_s": elapsed,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return mapping


def rollup_composers(composers: pd.DataFrame, works: pd.DataFrame) -> pd.DataFrame:
    by_composer: dict[str, Counter] = {}
    for cid, fam in zip(works["composer_id"].astype(str), works["force_family"]):
        by_composer.setdefault(cid, Counter())[fam] += 1

    present, by_cat_json, totals = [], [], []
    for cid in composers["composer_id"].astype(str):
        ctr = by_composer.get(cid, Counter())
        ordered = [f for f in FORCE_FAMILIES if ctr.get(f)]
        present.append(pipe_join(ordered))
        payload = {f: int(ctr[f]) for f in ordered}
        by_cat_json.append(json.dumps(payload, ensure_ascii=False) if payload else "")
        totals.append(int(sum(ctr.values())))

    out = composers.copy()
    out["work_categories_present"] = present
    out["works_count_by_category"] = by_cat_json
    out["works_count_total"] = totals
    if "imslp_works_count" in out.columns:
        out["imslp_works_count"] = totals
    return out


def run(args: argparse.Namespace) -> None:
    src = args.from_dump
    out_id = getattr(args, "to", None) or next_revision_id()
    composers_path = DATA_DIR / f"composers_{src}.tsv"
    works_path = DATA_DIR / f"works_{src}.tsv"
    if not composers_path.exists() or not works_path.exists():
        raise FileNotFoundError(f"Need {composers_path.name} and {works_path.name}")

    composers = pd.read_csv(composers_path, sep="\t", low_memory=False)
    works = pd.read_csv(works_path, sep="\t", low_memory=False)
    if "force_family" not in works.columns:
        raise RuntimeError("Source works lack force_family — run enrich_dump.py first")

    todo = works[works["force_family"] == "unclassified"].copy()
    if args.limit:
        todo = todo.head(args.limit)
    log.info(
        "Unclassified to send: %d / %d works (batch_size=%d)",
        len(todo),
        int((works["force_family"] == "unclassified").sum()),
        args.batch_size,
    )

    if args.dry_run:
        print(todo[["work_id", "title"]].head(20).to_string(index=False))
        return

    codex = find_codex(args.codex)
    cache_dir = CACHE_DIR / "llm_force_family" / f"{src}_{args.model}_{args.reasoning}"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Stable id = row position in works for join safety (work_id can collide across composers rare)
    records = []
    for idx, row in todo.iterrows():
        records.append(
            {
                "id": str(idx),
                "title": str(row.get("title") or row.get("work_id") or ""),
                "work_id": str(row.get("work_id") or ""),
            }
        )

    mapping_all: dict[str, str] = {}
    for i in range(0, len(records), args.batch_size):
        batch_recs = records[i : i + args.batch_size]
        batch_payload = [{"id": r["id"], "title": r["title"]} for r in batch_recs]
        batch_idx = i // args.batch_size
        try:
            mapping = classify_batch(
                batch_payload,
                codex=codex,
                model=args.model,
                reasoning=args.reasoning,
                cache_dir=cache_dir,
                batch_idx=batch_idx,
            )
        except Exception as exc:  # noqa: BLE001
            log.error("batch %d failed: %s — stopping (cache kept for resume)", batch_idx, exc)
            raise
        mapping_all.update(mapping)
        if args.sleep_s:
            time.sleep(args.sleep_s)

    # Apply
    updated = 0
    still = 0
    for idx_str, fam in mapping_all.items():
        idx = int(idx_str)
        if idx not in works.index:
            continue
        if works.at[idx, "force_family"] != "unclassified":
            continue
        works.at[idx, "force_family"] = fam
        works.at[idx, "force_family_src"] = "llm"
        # Refresh genre_form if empty
        if not str(works.at[idx, "genre_form"] or "").strip():
            works.at[idx, "genre_form"] = map_genre_form(
                str(works.at[idx, "imslp_genre_categories"] or ""),
                str(works.at[idx, "title"] or ""),
            )
        updated += 1
    still = int((works["force_family"] == "unclassified").sum())
    log.info("LLM filled %d rows; unclassified remaining %d", updated, still)

    works = works.reindex(columns=WORK_COLUMNS)
    composers = rollup_composers(composers, works)
    composers["schema_version"] = SCHEMA_VERSION
    composers["dump_date"] = out_id

    # column order
    cols = list(composers.columns)
    for c in NEW_COMPOSER_COLS:
        if c in cols:
            cols.remove(c)
    if "imslp_works_count" in cols:
        i = cols.index("imslp_works_count") + 1
        cols = cols[:i] + NEW_COMPOSER_COLS + cols[i:]
    else:
        cols = cols + NEW_COMPOSER_COLS
    composers = composers.reindex(columns=cols)

    c_path = DATA_DIR / f"composers_{out_id}.tsv"
    w_path = DATA_DIR / f"works_{out_id}.tsv"
    for path in (c_path, w_path):
        if path.exists():
            raise FileExistsError(f"Refusing to overwrite existing dump: {path}")
    composers.to_csv(c_path, sep="\t", index=False)
    works.to_csv(w_path, sep="\t", index=False)
    fam_counts = Counter(works["force_family"].tolist())
    meta = {
        "dump_id": out_id,
        "tool_version": TOOL_VERSION,
        "schema_version": SCHEMA_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "derived_from_dump_id": src,
        "enrichment": "llm_force_family",
        "llm": {
            "model": args.model,
            "reasoning_effort": args.reasoning,
            "batch_size": args.batch_size,
            "limit": args.limit,
            "updated_rows": updated,
            "unclassified_remaining": still,
            "cache_dir": str(cache_dir.relative_to(DATA_DIR.parent)),
        },
        "output_files": {"composers": c_path.name, "works": w_path.name},
        "composer_columns": list(composers.columns),
        "work_columns": WORK_COLUMNS,
        "row_counts": {
            "composers": int(len(composers)),
            "works": int(len(works)),
            "force_family": dict(fam_counts),
        },
        "notes": [
            "LLM only touches rows that were unclassified after rules/title heuristics",
            "force_family_src=llm for those rows; never overwrites imslp_tags/title",
        ],
    }
    meta_path = DATA_DIR / f"dump_meta_{out_id}.json"
    if meta_path.exists():
        raise FileExistsError(f"Refusing to overwrite {meta_path}")
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    log.info("Wrote %s / %s / %s", c_path.name, w_path.name, meta_path.name)
    log.info(
        "classified pct now %.1f%%",
        100.0 * (1.0 - still / max(len(works), 1)),
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--from-dump", required=True, help="Source dump_id with force_family")
    p.add_argument(
        "--to",
        "--date",
        dest="to",
        help="Output dump_id (rNNN preferred; calendar still accepted)",
    )
    p.add_argument("--batch-size", type=int, default=40)
    p.add_argument("--limit", type=int, default=None, help="Only first N unclassified")
    p.add_argument("--model", default="gpt-6-luna")
    p.add_argument("--reasoning", default="xhigh", help="model_reasoning_effort")
    p.add_argument("--codex", default=None, help="Path to codex binary")
    p.add_argument("--sleep-s", type=float, default=0.0, help="Pause between batches")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    run(parse_args())
