#!/usr/bin/env python3
"""LLM style-labelling pass with an append-only decision ledger.

Labels every selected composer with styles from the controlled vocabulary under
one of two input conditions (closed | grounded) via Codex or cursor-agent.
Never sends Wikidata style tags, IMSLP periods, occupations, or other existing
labels to the model — independence matters for agreement research.

Example:
  python scripts/llm_style_pass.py --dump r013 --condition closed \\
    --backend codex --model gpt-6.1-sol --effort low \\
    --ids data/llm_ledger/pilot_ids.txt --dry-run
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
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common  # noqa: E402
from common import (  # noqa: E402
    DATA_DIR,
    dump_tsv_path,
    wikipedia_title_from_url,
)
from fetch_wikipedia_leads import CACHE_NS as WIKI_LEAD_NS  # noqa: E402
from style_vocab import (  # noqa: E402
    ERA_SLUGS,
    PRIMARY_PERIOD_ALLOWED,
    STYLE_SLUG_SET,
    STYLE_VOCAB,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("llm_style_pass")

PROMPT_VERSION = "style-v1"
SCHEMA_PATH = Path(__file__).resolve().parent / "llm_style_pass_schema.json"
LEDGER_DIR = DATA_DIR / "llm_ledger"
LABELS_PATH = LEDGER_DIR / "style_labels.jsonl"
RUNS_PATH = LEDGER_DIR / "runs.jsonl"
CONFIDENCE_ALLOWED = frozenset({"low", "medium", "high"})

# Fields that must never appear in the model prompt (independence).
FORBIDDEN_PROMPT_FIELDS = frozenset(
    {
        "style_tags",
        "style_tags_src",
        "imslp_style",
        "occupations",
        "force_family",
        "force_family_src",
        "piece_style_raw",
        "notable_works",
        "scope_class",
        "qa_flags",
    }
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


def _as_year(value: Any) -> int | None:
    text = _as_str(value)
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def scrub_child_env(base: Optional[dict[str, str]] = None) -> dict[str, str]:
    """Drop AppImage LD_LIBRARY_PATH /tmp/.mount_* PATH leaks from child env."""
    env = dict(base if base is not None else os.environ)
    env.pop("LD_LIBRARY_PATH", None)
    path = env.get("PATH", "")
    cleaned = [p for p in path.split(":") if p and not p.startswith("/tmp/.mount_")]
    env["PATH"] = ":".join(cleaned)
    return env


def load_id_file(path: Path) -> list[str]:
    ids: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        ids.append(line)
    return ids


def cache_get_readonly(namespace: str, key: str) -> Any | None:
    """Read a cache entry without creating directories (data/cache may be read-only)."""
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in key)[:180]
    path = common.CACHE_DIR / namespace / f"{safe}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def input_hash(record: dict[str, Any], prompt_version: str = PROMPT_VERSION) -> str:
    payload = {"prompt_version": prompt_version, "record": record}
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def build_input_record(row: Any, *, condition: str, lead: Optional[str] = None) -> dict[str, Any]:
    record: dict[str, Any] = {
        "composer_id": _as_str(row.get("composer_id") if hasattr(row, "get") else row.composer_id),
        "name_display": _as_str(row.get("name_display") if hasattr(row, "get") else row.name_display),
        "birth_year": _as_year(row.get("birth_year") if hasattr(row, "get") else row.birth_year),
        "death_year": _as_year(row.get("death_year") if hasattr(row, "get") else row.death_year),
        "citizenship_iso": _as_str(
            row.get("citizenship_iso") if hasattr(row, "get") else row.citizenship_iso
        ),
    }
    if condition == "grounded":
        record["wikipedia_lead"] = lead or ""
    return record


def load_ledger_keys(path: Path) -> set[tuple[str, str, str, str, str, str]]:
    """Resume keys: (composer_id, condition, model, effort, prompt_version, input_hash)."""
    keys: set[tuple[str, str, str, str, str, str]] = set()
    if not path.exists():
        return keys
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        keys.add(
            (
                str(obj.get("composer_id", "")),
                str(obj.get("condition", "")),
                str(obj.get("model", "")),
                str(obj.get("effort", "")),
                str(obj.get("prompt_version", "")),
                str(obj.get("input_hash", "")),
            )
        )
    return keys


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def vocab_block() -> str:
    lines = [f"- {slug}: {defn}" for slug, defn in STYLE_VOCAB]
    return "\n".join(lines)


def build_prompt(records: list[dict[str, Any]], *, condition: str) -> str:
    if condition == "closed":
        condition_rule = (
            "Closed condition: use only your own knowledge of each person. "
            "No external lookup; do not invent biographical facts beyond what you know."
        )
    else:
        condition_rule = (
            "Grounded condition: base the answer on the supplied wikipedia_lead text for each "
            "person. If the text is insufficient, abstain rather than guessing from memory."
        )
    eras = "|".join(ERA_SLUGS)
    return f"""You label classical composers with styles from a controlled vocabulary.

Prompt version: {PROMPT_VERSION}

Task: for each composer in the input, choose 0–3 style slugs (most characteristic first),
one primary_period, and a confidence.

Vocabulary (slug: definition):
{vocab_block()}

Rules:
- styles: only slugs from the vocabulary above; at most 3; most characteristic first; [] if abstaining.
- primary_period: exactly one of [{eras}] or "unknown".
- confidence: low | medium | high.
- Abstain (styles=[], primary_period="unknown") rather than guess.
- {condition_rule}
- Return exactly one JSON object:
  {{"labels":[{{"id":"<composer_id>","styles":["..."],"primary_period":"...","confidence":"..."}}]}}
- Include every requested composer_id exactly once; no extras.

Composers JSON:
{json.dumps(records, ensure_ascii=False)}
"""


def assert_prompt_clean(prompt: str) -> None:
    lowered = prompt.lower()
    for field in FORBIDDEN_PROMPT_FIELDS:
        # Allow the word only inside the forbid-list explanation? We never mention them.
        if field.lower() in lowered:
            raise ValueError(f"Forbidden field leaked into prompt: {field}")


def extract_json_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        raise ValueError("Empty model output")
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Tolerate prose wrapping a JSON object.
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            return json.loads(raw[start : end + 1])
        raise


def extract_cursor_text(payload: Any) -> str:
    """Pull the final assistant text out of cursor-agent --output-format json."""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return payload
    if not isinstance(payload, dict):
        return str(payload)
    for key in ("result", "text", "message", "content", "finalText"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val
    # stream-ish: list of messages
    msgs = payload.get("messages") or payload.get("items")
    if isinstance(msgs, list):
        for msg in reversed(msgs):
            if not isinstance(msg, dict):
                continue
            for key in ("text", "content", "result"):
                val = msg.get(key)
                if isinstance(val, str) and val.strip():
                    return val
    return json.dumps(payload)


def validate_labels(
    parsed: dict[str, Any],
    requested_ids: list[str],
) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    """Validate batch output. Returns mapping id→label and stats (unknown_slugs dropped)."""
    if not isinstance(parsed, dict) or "labels" not in parsed:
        raise ValueError("Response missing top-level 'labels'")
    labels = parsed["labels"]
    if not isinstance(labels, list):
        raise ValueError("'labels' must be an array")

    by_id: dict[str, dict[str, Any]] = {}
    stats = {"unknown_slugs_dropped": 0}
    for item in labels:
        if not isinstance(item, dict):
            raise ValueError("Each label must be an object")
        cid = str(item.get("id", "")).strip()
        if not cid:
            raise ValueError("Label missing id")
        if cid in by_id:
            raise ValueError(f"Duplicate id in response: {cid}")
        styles_raw = item.get("styles")
        if styles_raw is None:
            styles_raw = []
        if not isinstance(styles_raw, list):
            raise ValueError(f"styles must be a list for {cid}")
        styles: list[str] = []
        seen: set[str] = set()
        for s in styles_raw:
            slug = str(s).strip()
            if slug not in STYLE_SLUG_SET:
                stats["unknown_slugs_dropped"] += 1
                continue
            if slug in seen:
                continue
            seen.add(slug)
            styles.append(slug)
            if len(styles) >= 3:
                break
        period = str(item.get("primary_period", "")).strip()
        if period not in PRIMARY_PERIOD_ALLOWED:
            raise ValueError(f"Invalid primary_period for {cid}: {period!r}")
        conf = str(item.get("confidence", "")).strip()
        if conf not in CONFIDENCE_ALLOWED:
            raise ValueError(f"Invalid confidence for {cid}: {conf!r}")
        by_id[cid] = {
            "id": cid,
            "styles": styles,
            "primary_period": period,
            "confidence": conf,
        }

    req = list(requested_ids)
    got = set(by_id)
    want = set(req)
    if got != want:
        missing = sorted(want - got)
        extra = sorted(got - want)
        raise ValueError(f"Id mismatch missing={missing} extra={extra}")
    return by_id, stats


def find_codex(explicit: str | None) -> str:
    if explicit:
        return explicit
    env = os.environ.get("CODEX_BIN")
    if env:
        return env
    which = shutil.which("codex")
    if which:
        return which
    raise FileNotFoundError("codex binary not found; pass --codex or set CODEX_BIN")


def find_cursor_agent(explicit: str | None) -> str:
    if explicit:
        return explicit
    env = os.environ.get("CURSOR_AGENT_BIN")
    if env:
        return env
    which = shutil.which("cursor-agent") or shutil.which("agent")
    if which:
        return which
    raise FileNotFoundError("cursor-agent binary not found")


def cli_version_string(backend: str, binary: str) -> str:
    try:
        proc = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            env=scrub_child_env(),
            timeout=30,
        )
        text = (proc.stdout or proc.stderr or "").strip().splitlines()
        return text[0] if text else f"{backend}:unknown"
    except (OSError, subprocess.SubprocessError):
        return f"{backend}:unknown"


def run_codex(
    prompt: str,
    *,
    binary: str,
    model: str,
    effort: str,
    schema: Path,
) -> tuple[str, dict[str, Any]]:
    with tempfile.NamedTemporaryFile(prefix="llm_style_", suffix=".txt", delete=False) as tmp:
        out_path = Path(tmp.name)
    cmd = [
        binary,
        "exec",
        "-m",
        model,
        "-c",
        f'model_reasoning_effort="{effort}"',
        "-s",
        "read-only",
        "--skip-git-repo-check",
        "--output-schema",
        str(schema),
        "-o",
        str(out_path),
        "-",
    ]
    env = scrub_child_env()
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
    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            env=env,
            cwd=str(DATA_DIR.parent),
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"codex failed rc={proc.returncode}: {(proc.stderr or '')[-800:]}"
            )
        raw = out_path.read_text(encoding="utf-8") if out_path.exists() else (proc.stdout or "")
        usage: dict[str, Any] = {}
        # Best-effort: some CLI builds print token usage on stderr.
        err = proc.stderr or ""
        m = re.search(r"(\d+)\s*input.*?(\d+)\s*output", err, re.I | re.S)
        if m:
            usage = {"input_tokens": int(m.group(1)), "output_tokens": int(m.group(2))}
        return raw, usage
    finally:
        try:
            out_path.unlink(missing_ok=True)
        except OSError:
            pass


def run_cursor(
    prompt: str,
    *,
    binary: str,
    model: str,
) -> tuple[str, dict[str, Any]]:
    cmd = [
        binary,
        "-p",
        "--model",
        model,
        "--mode",
        "ask",
        "--output-format",
        "json",
    ]
    # Never pass --force / --yolo.
    env = scrub_child_env()
    proc = subprocess.run(
        cmd,
        input=prompt,
        capture_output=True,
        text=True,
        env=env,
        cwd=str(DATA_DIR.parent),
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"cursor-agent failed rc={proc.returncode}: {(proc.stderr or '')[-800:]}"
        )
    text = extract_cursor_text(proc.stdout or "")
    return text, {}


def call_backend(
    prompt: str,
    *,
    backend: str,
    binary: str,
    model: str,
    effort: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if backend == "codex":
        raw, usage = run_codex(
            prompt, binary=binary, model=model, effort=effort, schema=SCHEMA_PATH
        )
    elif backend == "cursor":
        raw, usage = run_cursor(prompt, binary=binary, model=model)
    else:
        raise ValueError(f"Unknown backend: {backend}")
    return extract_json_object(raw), usage


def label_batch_with_split(
    records: list[dict[str, Any]],
    *,
    condition: str,
    backend: str,
    binary: str,
    model: str,
    effort: str,
    batch_id: str,
    allow_split: bool = True,
) -> tuple[dict[str, dict[str, Any]], list[str], int, dict[str, Any]]:
    """Return (labels_by_id, unrecoverable_ids, unknown_slugs_dropped, usage).

    On failure, retry once with the batch split in half (no further splits).
    """
    ids = [r["composer_id"] for r in records]
    prompt = build_prompt(records, condition=condition)
    assert_prompt_clean(prompt)
    try:
        parsed, usage = call_backend(
            prompt, backend=backend, binary=binary, model=model, effort=effort
        )
        by_id, stats = validate_labels(parsed, ids)
        return by_id, [], stats["unknown_slugs_dropped"], usage
    except Exception as exc:  # noqa: BLE001 — batch boundary; split or record failure
        log.warning(
            "Batch %s failed (%s) size=%d allow_split=%s",
            batch_id,
            exc,
            len(records),
            allow_split,
        )
        if not allow_split or len(records) <= 1:
            return {}, ids, 0, {}
        mid = max(1, len(records) // 2)
        left, fail_l, drop_l, usage_l = label_batch_with_split(
            records[:mid],
            condition=condition,
            backend=backend,
            binary=binary,
            model=model,
            effort=effort,
            batch_id=f"{batch_id}a",
            allow_split=False,
        )
        right, fail_r, drop_r, usage_r = label_batch_with_split(
            records[mid:],
            condition=condition,
            backend=backend,
            binary=binary,
            model=model,
            effort=effort,
            batch_id=f"{batch_id}b",
            allow_split=False,
        )
        usage: dict[str, Any] = {}
        for u in (usage_l, usage_r):
            for k, v in u.items():
                if isinstance(v, (int, float)):
                    usage[k] = usage.get(k, 0) + v
        return {**left, **right}, fail_l + fail_r, drop_l + drop_r, usage


def prepare_records(
    composers: pd.DataFrame,
    *,
    condition: str,
    ids: Optional[list[str]],
    limit: Optional[int],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Build input records. Returns (records, missing_lead_ids for grounded)."""
    df = composers.copy()
    if ids is not None:
        want = set(ids)
        df = df[df["composer_id"].astype(str).isin(want)].copy()
        order = {cid: i for i, cid in enumerate(ids)}
        df["_ord"] = df["composer_id"].astype(str).map(order)
        df = df.sort_values("_ord").drop(columns=["_ord"])
    if limit is not None and limit >= 0:
        df = df.head(int(limit))

    records: list[dict[str, Any]] = []
    missing_leads: list[str] = []
    for _, row in df.iterrows():
        cid = _as_str(row.get("composer_id"))
        lead: Optional[str] = None
        if condition == "grounded":
            title = wikipedia_title_from_url(_as_str(row.get("wikipedia_url")))
            cached = cache_get_readonly(WIKI_LEAD_NS, title) if title else None
            extract = ""
            if isinstance(cached, dict):
                extract = _as_str(cached.get("extract"))
            if not extract:
                missing_leads.append(cid)
                continue
            lead = extract
        records.append(build_input_record(row, condition=condition, lead=lead))
    return records, missing_leads


def run(args: argparse.Namespace) -> int:
    effort = args.effort if args.effort is not None else ""
    if args.backend == "codex" and not effort:
        raise SystemExit("--effort is required for the codex backend")

    composers = pd.read_csv(dump_tsv_path("composers", args.dump), sep="\t", low_memory=False)
    ids = load_id_file(Path(args.ids)) if args.ids else None
    records, missing_leads = prepare_records(
        composers, condition=args.condition, ids=ids, limit=args.limit
    )
    if missing_leads:
        log.warning(
            "Grounded condition: skipping %d composers with missing Wikipedia leads",
            len(missing_leads),
        )
        print(f"missing_leads={len(missing_leads)}")
        for cid in missing_leads[:20]:
            print(f"  missing_lead {cid}")
        if len(missing_leads) > 20:
            print(f"  ... and {len(missing_leads) - 20} more")

    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    existing = load_ledger_keys(LABELS_PATH)
    todo: list[dict[str, Any]] = []
    skipped = 0
    for rec in records:
        h = input_hash(rec, PROMPT_VERSION)
        key = (
            rec["composer_id"],
            args.condition,
            args.model,
            effort,
            PROMPT_VERSION,
            h,
        )
        if key in existing:
            skipped += 1
            continue
        todo.append(rec)

    batch_size = max(1, int(args.batch_size))
    n_batches = (len(todo) + batch_size - 1) // batch_size if todo else 0
    log.info(
        "Prepared records=%d todo=%d skipped_resume=%d missing_leads=%d batches=%d",
        len(records),
        len(todo),
        skipped,
        len(missing_leads),
        n_batches,
    )

    if args.dry_run:
        first_prompt = build_prompt(todo[:batch_size] or records[:batch_size], condition=args.condition)
        if todo or records:
            assert_prompt_clean(first_prompt)
            print("=== FIRST PROMPT ===")
            print(first_prompt)
            print("=== END PROMPT ===")
        print(
            f"counts: selected={len(records)} todo={len(todo)} skipped_resume={skipped} "
            f"missing_leads={len(missing_leads)} batches={n_batches} batch_size={batch_size}"
        )
        return 0

    run_id = args.run_id or utc_now().replace(":", "").replace("-", "") + "_" + uuid.uuid4().hex[:8]
    binary = (
        find_codex(args.codex)
        if args.backend == "codex"
        else find_cursor_agent(args.cursor_agent)
    )
    version = cli_version_string(args.backend, binary)
    started = utc_now()
    labelled = 0
    failures: list[str] = []
    unknown_dropped = 0
    usage_total: dict[str, Any] = {}

    for i in range(0, len(todo), batch_size):
        batch = todo[i : i + batch_size]
        batch_id = f"{run_id}-b{i // batch_size:04d}"
        by_id, failed, dropped, usage = label_batch_with_split(
            batch,
            condition=args.condition,
            backend=args.backend,
            binary=binary,
            model=args.model,
            effort=effort,
            batch_id=batch_id,
        )
        unknown_dropped += dropped
        failures.extend(failed)
        for k, v in usage.items():
            if isinstance(v, (int, float)):
                usage_total[k] = usage_total.get(k, 0) + v

        rows_out: list[dict[str, Any]] = []
        created = utc_now()
        for rec in batch:
            cid = rec["composer_id"]
            if cid in failed or cid not in by_id:
                continue
            lab = by_id[cid]
            h = input_hash(rec, PROMPT_VERSION)
            rows_out.append(
                {
                    "composer_id": cid,
                    "condition": args.condition,
                    "backend": args.backend,
                    "model": args.model,
                    "effort": effort,
                    "prompt_version": PROMPT_VERSION,
                    "input_hash": h,
                    "styles": lab["styles"],
                    "primary_period": lab["primary_period"],
                    "confidence": lab["confidence"],
                    "run_id": run_id,
                    "batch_id": batch_id,
                    "created_at": created,
                }
            )
        if rows_out:
            append_jsonl(LABELS_PATH, rows_out)
            labelled += len(rows_out)
        log.info(
            "Batch %s done labelled_batch=%d failed=%d total_labelled=%d",
            batch_id,
            len(rows_out),
            len(failed),
            labelled,
        )

    ended = utc_now()
    run_row = {
        "run_id": run_id,
        "args": {
            "dump": args.dump,
            "condition": args.condition,
            "backend": args.backend,
            "model": args.model,
            "effort": effort,
            "ids": args.ids,
            "limit": args.limit,
            "batch_size": batch_size,
            "prompt_version": PROMPT_VERSION,
        },
        "backend_cli_version": version,
        "model": args.model,
        "effort": effort,
        "counts": {
            "selected": len(records),
            "todo": len(todo),
            "skipped_resume": skipped,
            "missing_leads": len(missing_leads),
            "labelled": labelled,
            "failures": len(failures),
            "unknown_slugs_dropped": unknown_dropped,
            "batches": n_batches,
        },
        "failures": failures,
        "token_usage": usage_total or None,
        "started_at": started,
        "ended_at": ended,
    }
    append_jsonl(RUNS_PATH, [run_row])
    log.info(
        "Run %s finished labelled=%d failures=%d skipped=%d",
        run_id,
        labelled,
        len(failures),
        skipped,
    )
    return 0 if not failures else 1


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dump", required=True)
    p.add_argument("--condition", required=True, choices=["closed", "grounded"])
    p.add_argument("--backend", required=True, choices=["codex", "cursor"])
    p.add_argument("--model", required=True)
    p.add_argument("--effort", default=None, help="Reasoning effort (required for codex)")
    p.add_argument("--ids", help="File with one composer_id per line")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--batch-size", type=int, default=40)
    p.add_argument("--run-id", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--codex", default=None, help="Path to codex binary")
    p.add_argument("--cursor-agent", default=None, help="Path to cursor-agent binary")
    raise SystemExit(run(p.parse_args()))


if __name__ == "__main__":
    main()
