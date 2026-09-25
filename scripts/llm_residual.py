#!/usr/bin/env python3
"""Residual LLM pass: force_family (works) + style_tags (composers).

Uses Codex CLI + gpt-6-luna (default reasoning xhigh). Reads residual queues
from an existing dump (or llm_residual_queue.json context), never overwrites
imslp_tags / solid imslp_geninfo / wikidata styles. Writes a new rNNN dump.

Example:
  python scripts/llm_residual.py --from-dump r002 --to r003 \\
    --works-limit 100 --composers-limit 100 --reasoning xhigh
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    CACHE_DIR,
    DATA_DIR,
    SCHEMA_VERSION,
    TOOL_VERSION,
    dump_tsv_path,
    next_revision_id,
    pipe_join,
    write_dump_meta,
    write_tsv_dump,
)
from force_family import FORCE_FAMILIES, map_genre_form  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("llm_residual")

ALLOWED_FORCE = set(FORCE_FAMILIES)
ALLOWED_STYLES = {
    "impressionism",
    "expressionism",
    "neoclassicism",
    "serialism",
    "minimalism",
    "postminimalism",
    "spectralism",
    "electroacoustic",
    "avant_garde",
    "national_folk",
    "late_romantic",
    "atonal_modernism",
    "polystylism",
}
FORCE_SCHEMA = Path(__file__).resolve().parent / "llm_force_family_schema.json"
STYLE_SCHEMA = Path(__file__).resolve().parent / "llm_style_schema.json"
DEFAULT_CODEX = Path.home() / ".codex/plugins/.plugin-appserver/codex"
PROTECTED_FORCE_SRC = {"imslp_tags"}
# imslp_geninfo kept unless still other/unclassified (then LLM may upgrade)
SRC_LABEL = "llm_luna_xhigh"
# Prior LLM passes that already settled on other — skip unless --retry-other
SETTLED_OTHER_SRC = {"llm_luna_xhigh", "llm_grok"}


def _as_str(value) -> str:
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


def codex_json(
    prompt: str,
    *,
    codex: Path,
    model: str,
    reasoning: str,
    schema: Path,
    cache_dir: Path,
    batch_idx: int,
    prefix: str,
) -> dict:
    schema_text = schema.read_text(encoding="utf-8") if schema.exists() else ""
    digest = hashlib.sha256(
        f"{model}\0{reasoning}\0{schema_text}\0{prompt}".encode("utf-8")
    ).hexdigest()[:16]
    cache_file = cache_dir / f"{prefix}_{batch_idx:05d}_{digest}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))
    # Do not reuse index-only legacy caches — they may be for a different prompt.

    out_msg = cache_dir / f"{prefix}_{batch_idx:05d}_{digest}.last.txt"
    err_path = cache_dir / f"{prefix}_{batch_idx:05d}_{digest}.err"
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
        str(schema),
        "-o",
        str(out_msg),
        prompt,
    ]
    env = os.environ.copy()
    env["CODEX_HOME"] = str(Path.home() / ".codex")
    for k in (
        "http_proxy",
        "https_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "all_proxy",
        "ALL_PROXY",
    ):
        env.pop(k, None)

    log.info("Codex %s batch %d …", prefix, batch_idx)
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
            f"codex failed {prefix} batch {batch_idx} rc={proc.returncode}: "
            f"{(proc.stderr or '')[-800:]}"
        )
    raw = out_msg.read_text(encoding="utf-8") if out_msg.exists() else (proc.stdout or "")
    elapsed = time.monotonic() - started
    payload = {
        "batch_idx": batch_idx,
        "cache_key": digest,
        "raw": raw,
        "elapsed_s": elapsed,
    }
    # normalize JSON
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    payload["parsed"] = json.loads(text)
    cache_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log.info("%s batch %d done in %.1fs", prefix, batch_idx, elapsed)
    return payload


def force_prompt(batch: list[dict]) -> str:
    families = "|".join(FORCE_FAMILIES)
    return f"""You classify classical sheet-music works into exactly one force_family.

Allowed values only: {families}

Rules:
- Prefer performing forces (piano / orchestra / chorus / chamber / etc.), not era or style.
- Use instrumentation_raw / piece_style_raw when present — they come from IMSLP General Information.
- harmonium / reed organ → keyboard_other; computer / tape / electronics → electronic.
- pedagogical for methods/études/schools; stage_opera only for actual stage works.
- If still unclear after context, use other (not unclassified) for typical salon/character pieces.
- Return JSON: {{"items":[{{"id":"...","force_family":"..."}}]}} with one entry per input id.

Works JSON:
{json.dumps(batch, ensure_ascii=False)}
"""


def style_prompt(batch: list[dict]) -> str:
    tags = "|".join(sorted(ALLOWED_STYLES))
    return f"""You assign zero or more style_tags for 20th-century classical composers.

Allowed tags only: {tags}

Rules:
- Classical / modernist composers only; leave style_tags empty [] if unknown or not classical.
- Prefer specific tags (serialism, spectralism, minimalism) over vague ones.
- late_romantic for fin-de-siècle / post-Romantic language; atonal_modernism for free atonal / expressionist modernism.
- national_folk for nationalist / folk-influenced classical (not pop/folk singers).
- electroacoustic for tape / electronic / concrete; avant_garde for experimental / Fluxus-adjacent.
- Return JSON: {{"items":[{{"id":"...","style_tags":["..."]}}]}} with one entry per input id.
- Use at most 3 tags per composer, usually 1.

Composers JSON:
{json.dumps(batch, ensure_ascii=False)}
"""


def select_works(
    works: pd.DataFrame, limit: int | None, *, retry_other: bool = False
) -> pd.DataFrame:
    fam = works["force_family"].map(_as_str)
    src = works["force_family_src"].map(_as_str)
    skip_src = set(PROTECTED_FORCE_SRC)
    if not retry_other:
        skip_src |= SETTLED_OTHER_SRC
    # Residual: other/unclassified, not protected tags
    mask = fam.isin({"other", "unclassified"}) & ~src.isin(skip_src)
    todo = works[mask].copy()
    if "instrumentation_raw" in todo.columns:
        todo["_has_gi"] = (todo["instrumentation_raw"].map(_as_str) != "").astype(int)
    else:
        todo["_has_gi"] = 0
    todo = todo.sort_values(["_has_gi", "work_id"], ascending=[False, True])
    if limit is None:
        return todo
    if limit <= 0:
        return todo.iloc[0:0].copy()
    return todo.head(limit)


def select_composers(composers: pd.DataFrame, limit: int | None) -> pd.DataFrame:
    todo = composers[
        (composers["scope_class"].fillna("") == "classical_core")
        & (composers["style_tags"].map(_as_str) == "")
    ].copy()
    todo["pageviews_enwiki"] = pd.to_numeric(
        todo.get("pageviews_enwiki"), errors="coerce"
    ).fillna(0)
    todo = todo.sort_values("pageviews_enwiki", ascending=False)
    if limit is None:
        return todo
    if limit <= 0:
        return todo.iloc[0:0].copy()
    return todo.head(limit)


def rollup_composers(composers: pd.DataFrame, works: pd.DataFrame) -> pd.DataFrame:
    by_composer: dict[str, Counter] = {}
    for cid, fam in zip(works["composer_id"].astype(str), works["force_family"]):
        by_composer.setdefault(str(cid), Counter())[_as_str(fam)] += 1
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


def run_force(
    works: pd.DataFrame,
    todo: pd.DataFrame,
    *,
    codex: Path,
    model: str,
    reasoning: str,
    batch_size: int,
    cache_dir: Path,
    src_label: str,
) -> int:
    updated = 0
    records = []
    for idx, row in todo.iterrows():
        records.append(
            {
                "row_idx": int(idx) if isinstance(idx, (int,)) else idx,
                "id": str(idx),
                "title": _as_str(row.get("title")),
                "work_id": _as_str(row.get("work_id")),
                "url": _as_str(row.get("imslp_work_url")),
                "instrumentation_raw": _as_str(row.get("instrumentation_raw")),
                "piece_style_raw": _as_str(row.get("piece_style_raw")),
                "current_force": _as_str(row.get("force_family")),
                "current_src": _as_str(row.get("force_family_src")),
            }
        )

    for i in range(0, len(records), batch_size):
        batch = records[i : i + batch_size]
        payload = [
            {
                "id": r["id"],
                "title": r["title"],
                "instrumentation_raw": r["instrumentation_raw"],
                "piece_style_raw": r["piece_style_raw"],
                "current_force": r["current_force"],
            }
            for r in batch
        ]
        batch_idx = i // batch_size
        result = codex_json(
            force_prompt(payload),
            codex=codex,
            model=model,
            reasoning=reasoning,
            schema=FORCE_SCHEMA,
            cache_dir=cache_dir,
            batch_idx=batch_idx,
            prefix="force",
        )
        items = result["parsed"].get("items") or []
        mapping = {
            str(it.get("id")): str(it.get("force_family"))
            for it in items
            if str(it.get("force_family", "")) in ALLOWED_FORCE
        }
        for r in batch:
            fam = mapping.get(r["id"])
            if not fam:
                continue
            idx = r["row_idx"]
            src = _as_str(works.at[idx, "force_family_src"])
            if src in PROTECTED_FORCE_SRC:
                continue
            old = _as_str(works.at[idx, "force_family"])
            if old == fam and src == src_label:
                continue
            works.at[idx, "force_family"] = fam
            works.at[idx, "force_family_src"] = src_label
            if not _as_str(works.at[idx, "genre_form"]):
                works.at[idx, "genre_form"] = map_genre_form(
                    _as_str(works.at[idx, "imslp_genre_categories"]),
                    _as_str(works.at[idx, "title"]),
                )
            updated += 1
        log.info("force progress mapped batch %d — running total %d", batch_idx, updated)
    return updated


def run_styles(
    composers: pd.DataFrame,
    todo: pd.DataFrame,
    works: pd.DataFrame,
    *,
    codex: Path,
    model: str,
    reasoning: str,
    batch_size: int,
    cache_dir: Path,
    src_label: str,
) -> int:
    if "style_tags_src" not in composers.columns:
        composers["style_tags_src"] = ""

    titles_by_c: dict[str, list[str]] = {}
    for r in works.itertuples():
        cid = str(r.composer_id)
        titles_by_c.setdefault(cid, [])
        if len(titles_by_c[cid]) < 5:
            t = _as_str(getattr(r, "title", ""))
            if t:
                titles_by_c[cid].append(t)

    records = []
    for idx, row in todo.iterrows():
        cid = _as_str(row.get("composer_id"))
        records.append(
            {
                "row_idx": idx,
                "id": cid,
                "name": _as_str(row.get("name_display")),
                "birth_year": row.get("birth_year") if pd.notna(row.get("birth_year")) else None,
                "death_year": row.get("death_year") if pd.notna(row.get("death_year")) else None,
                "occupations": _as_str(row.get("occupations")),
                "notable_works": _as_str(row.get("notable_works"))[:240],
                "top_imslp_titles": titles_by_c.get(cid, []),
            }
        )

    updated = 0
    for i in range(0, len(records), batch_size):
        batch = records[i : i + batch_size]
        payload = [
            {
                "id": r["id"],
                "name": r["name"],
                "birth_year": r["birth_year"],
                "death_year": r["death_year"],
                "occupations": r["occupations"],
                "notable_works": r["notable_works"],
                "top_imslp_titles": r["top_imslp_titles"],
            }
            for r in batch
        ]
        batch_idx = i // batch_size
        result = codex_json(
            style_prompt(payload),
            codex=codex,
            model=model,
            reasoning=reasoning,
            schema=STYLE_SCHEMA,
            cache_dir=cache_dir,
            batch_idx=batch_idx,
            prefix="style",
        )
        items = result["parsed"].get("items") or []
        mapping: dict[str, list[str]] = {}
        for it in items:
            iid = str(it.get("id", ""))
            tags = [
                t
                for t in (it.get("style_tags") or [])
                if t in ALLOWED_STYLES
            ]
            # dedupe preserve order
            seen = set()
            clean = []
            for t in tags:
                if t not in seen:
                    seen.add(t)
                    clean.append(t)
            if iid:
                mapping[iid] = clean[:3]

        for r in batch:
            tags = mapping.get(r["id"])
            if tags is None:
                continue
            idx = r["row_idx"]
            existing = _as_str(composers.at[idx, "style_tags"])
            existing_src = _as_str(composers.at[idx, "style_tags_src"])
            if existing and existing_src == "wikidata":
                continue
            if existing and not tags:
                continue
            if tags:
                composers.at[idx, "style_tags"] = pipe_join(tags)
                composers.at[idx, "style_tags_src"] = src_label
                updated += 1
        log.info("style progress batch %d — running total %d", batch_idx, updated)
    return updated


def run(args: argparse.Namespace) -> None:
    src = args.from_dump
    out_id = args.to or next_revision_id()
    src_label = args.src_label or (
        "llm_grok" if "grok" in args.model.lower() else SRC_LABEL
    )
    composers = pd.read_csv(dump_tsv_path("composers", src), sep="\t", low_memory=False)
    works = pd.read_csv(dump_tsv_path("works", src), sep="\t", low_memory=False)

    w_limit = None if args.works_limit < 0 else args.works_limit
    c_limit = None if args.composers_limit < 0 else args.composers_limit
    w_todo = select_works(works, w_limit, retry_other=args.retry_other)
    c_todo = select_composers(composers, c_limit)
    log.info(
        "Queue works=%d composers=%d → %s (model=%s reasoning=%s src=%s)",
        len(w_todo),
        len(c_todo),
        out_id,
        args.model,
        args.reasoning,
        src_label,
    )

    if args.dry_run:
        print("WORKS", w_todo[["work_id", "force_family", "force_family_src"]].head(20).to_string(index=False))
        print("COMPOSERS", c_todo[["composer_id", "name_display", "pageviews_enwiki"]].head(20).to_string(index=False))
        return

    codex = find_codex(args.codex)
    cache_dir = (
        CACHE_DIR
        / "llm_residual"
        / (
            f"{src}_{args.model}_{args.reasoning}_w{args.works_limit}_c{args.composers_limit}"
            + ("_retry" if args.retry_other else "")
        )
    )
    cache_dir.mkdir(parents=True, exist_ok=True)

    force_updated = 0
    style_updated = 0
    if len(w_todo):
        force_updated = run_force(
            works,
            w_todo,
            codex=codex,
            model=args.model,
            reasoning=args.reasoning,
            batch_size=args.batch_size,
            cache_dir=cache_dir,
            src_label=src_label,
        )
    if len(c_todo):
        style_updated = run_styles(
            composers,
            c_todo,
            works,
            codex=codex,
            model=args.model,
            reasoning=args.reasoning,
            batch_size=args.batch_size,
            cache_dir=cache_dir,
            src_label=src_label,
        )

    composers = rollup_composers(composers, works)
    composers["schema_version"] = SCHEMA_VERSION
    composers["dump_date"] = out_id

    c_path = write_tsv_dump(composers, "composers", out_id)
    w_path = write_tsv_dump(works, "works", out_id)
    fam_counts = Counter(works["force_family"].map(_as_str).tolist())
    src_counts = Counter(works["force_family_src"].map(_as_str).tolist())
    style_nonempty = int((composers["style_tags"].map(_as_str) != "").sum())
    write_dump_meta(
        out_id,
        {
            "dump_id": out_id,
            "tool_version": TOOL_VERSION,
            "schema_version": SCHEMA_VERSION,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "derived_from_dump_id": src,
            "enrichment": "llm_residual_force+style",
            "llm": {
                "model": args.model,
                "reasoning_effort": args.reasoning,
                "works_limit": args.works_limit,
                "composers_limit": args.composers_limit,
                "batch_size": args.batch_size,
                "force_updated": force_updated,
                "style_updated": style_updated,
                "cache_dir": str(cache_dir.relative_to(DATA_DIR.parent)),
                "src_label": src_label,
                "retry_other": args.retry_other,
            },
            "output_files": {"composers": c_path.name, "works": w_path.name},
            "row_counts": {
                "composers": int(len(composers)),
                "works": int(len(works)),
                "force_family": dict(fam_counts),
                "force_family_src": dict(src_counts),
                "style_tags_nonempty": style_nonempty,
            },
            "notes": [
                f"force_family_src={src_label} for residual other/unclassified (never overwrites imslp_tags)",
                f"style_tags_src={src_label} for empty classical_core styles (never overwrites wikidata)",
                "Prompts include geninfo instrumentation when present",
            ],
        },
    )
    log.info(
        "Wrote %s / %s — force_updated=%d style_updated=%d style_nonempty=%d",
        c_path.name,
        w_path.name,
        force_updated,
        style_updated,
        style_nonempty,
    )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--from-dump", required=True)
    p.add_argument("--to", help="Output revision id (default: next rNNN)")
    p.add_argument(
        "--works-limit",
        type=int,
        default=100,
        help="Max works (0=none, -1=all residual)",
    )
    p.add_argument(
        "--composers-limit",
        type=int,
        default=100,
        help="Max composers (0=none, -1=all residual)",
    )
    p.add_argument("--batch-size", type=int, default=25)
    p.add_argument("--model", default="gpt-6-luna")
    p.add_argument("--reasoning", default="xhigh")
    p.add_argument(
        "--src-label",
        default=None,
        help="force_family_src / style_tags_src label (default: llm_grok if model has grok else llm_luna_xhigh)",
    )
    p.add_argument(
        "--retry-other",
        action="store_true",
        help="Re-send works already tagged other by llm_luna_xhigh / llm_grok",
    )
    p.add_argument("--codex", default=None)
    p.add_argument("--dry-run", action="store_true")
    run(p.parse_args())


if __name__ == "__main__":
    main()
