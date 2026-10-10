#!/usr/bin/env python3
"""Run a staged refresh or offline derivation and promote one immutable revision."""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

import common
from apply_llm_styles import LLM_STYLE_COLUMNS, default_ledger_path
from imslp_work_evidence import EVIDENCE_COLUMNS, FILE_HOSTS_COLUMN

log = logging.getLogger("pipeline")
SCRIPTS = Path(__file__).resolve().parent
STEMS = ("composers", "works")
VIEWER_FILES = ("manifest.json", "composers.json", "works_by_composer.json")


def overrides_path() -> Path:
    return common.REPO_ROOT / "data" / "overrides" / "composers.tsv"


def pageviews_window(crawl_date: date) -> tuple[str, str]:
    first = crawl_date.replace(day=1)
    return first.replace(year=first.year - 1).strftime("%Y%m%d"), (first - timedelta(days=1)).strftime("%Y%m%d")


def dump_paths(directory: Path, dump_id: str) -> list[Path]:
    return [*(directory / f"{stem}_{dump_id}.tsv" for stem in STEMS),
            directory / f"dump_meta_{dump_id}.json"]


def copy_dump(dump_id: str, destination: Path) -> None:
    sources = dump_paths(common.DATA_DIR, dump_id)
    for source in sources:
        if not source.is_file():
            raise FileNotFoundError(source)
    for source in sources:
        target = destination / source.name
        with target.open("xb") as handle:
            handle.write(source.read_bytes())


def run_script(script: str, arguments: list[str], data_dir: Path, cache_dir: Path) -> None:
    env = os.environ.copy()
    env["EU_PD_DATA_DIR"] = str(data_dir.resolve())
    env["EU_PD_CACHE_DIR"] = str(cache_dir.resolve())
    command = [sys.executable, str(SCRIPTS / script), *arguments]
    log.info("Running %s", " ".join(command))
    subprocess.run(command, env=env, cwd=common.REPO_ROOT, check=True)


def read_meta(directory: Path, dump_id: str) -> dict[str, Any]:
    return json.loads((directory / f"dump_meta_{dump_id}.json").read_text(encoding="utf-8"))


def _record_stage(directory: Path, dump_id: str, script: str) -> dict[str, Any]:
    meta = read_meta(directory, dump_id)
    return {"script": script, "dump_id": dump_id,
            "enrichment": meta.get("enrichment", "build_dump"), "row_counts": meta["row_counts"]}


def offline_stages(
    source: str, base: str, staging: Path, cache: Path, *, refresh: bool, preserve_schema: bool = False,
) -> tuple[str, list[dict[str, Any]]]:
    stages = []
    current = source
    steps = [
        ("remap_force.py", []),
        ("carry_forward.py", ["--base", base]),
        ("remap_composers.py", []),
        ("apply_overrides.py", ["--overrides", str(overrides_path())]),
        ("remap_imslp_matches.py", []),
        ("remap_work_evidence.py", ["--preserve-schema"] if preserve_schema else []),
        ("apply_llm_styles.py", ["--ledger", str(default_ledger_path())]),
    ]
    for number, (script, extra) in enumerate(steps, start=1):
        if script == "remap_imslp_matches.py" and refresh:
            run_script("refetch_composer_pages.py", ["--dump", current], staging, cache)
        if script == "remap_work_evidence.py" and refresh:
            run_script("refetch_work_files.py", ["--dump", current], staging, cache)
        output = f"pipeline_stage_{number:02d}"
        run_script(script, ["--from-dump", current, "--to", output, *extra], staging, cache)
        stages.append(_record_stage(staging, output, script))
        current = output
    return current, stages


def prepare_result(
    staging: Path, final_stage: str, output: str, base: str,
    stages: list[dict[str, Any]], *, crawl_date: str | None,
    window: tuple[str, str] | None, source: str, refresh: bool, preserve_schema: bool = False,
) -> None:
    meta = read_meta(staging, final_stage)
    reference_meta = read_meta(staging, source)
    columns = {}
    for stem in STEMS:
        frame = pd.read_csv(staging / f"{stem}_{final_stage}.tsv", sep="\t", dtype=str, keep_default_na=False)
        if not refresh:
            original = pd.read_csv(staging / f"{stem}_{source}.tsv", sep="\t", dtype=str, keep_default_na=False, nrows=0)
            added = [c for c in frame.columns if c not in original.columns]
            removed = [c for c in original.columns if c not in frame.columns]
            # derive adds only supported evidence columns; --preserve-schema forbids any change.
            allowed = () if preserve_schema else (
                (*EVIDENCE_COLUMNS, FILE_HOSTS_COLUMN) if stem == "works" else LLM_STYLE_COLUMNS)
            if removed or any(c not in allowed for c in added):
                raise ValueError(f"derive changed {stem} schema: added {added}, removed {removed}")
            if preserve_schema:
                frame = frame.reindex(columns=original.columns)
        if "dump_date" in frame:
            frame["dump_date"] = output
        frame.to_csv(staging / f"{stem}_{output}.tsv", sep="\t", index=False, mode="x")
        columns[stem] = list(frame.columns)
    reference_year = int(reference_meta.get("pd_reference_year")
                         or (crawl_date or str(reference_meta.get("created_at_utc") or ""))[:4]
                         or datetime.now(timezone.utc).year)
    meta.update({
        "dump_id": output, "derived_from_dump_id": base,
        "enrichment": "pipeline_refresh" if refresh else "pipeline_derive",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "pd_reference_year": reference_year,
        "source_dump_id": source, "stages": stages,
        "crawl_date": crawl_date, "pageviews_range": list(window) if window else None,
        "output_files": {stem: f"{stem}_{output}.tsv" for stem in STEMS},
        "composer_columns": columns["composers"], "work_columns": columns["works"],
    })
    carry_stage = next(stage for stage in stages if stage["script"] == "carry_forward.py")
    carry_meta = read_meta(staging, carry_stage["dump_id"])
    meta["carry_forward"] = {key: carry_meta[key] for key in (
        "base_dump_id", "carried_forward", "lost_rows", "lost_decisions", "lost_decision_counts",
    )}
    with (staging / f"dump_meta_{output}.json").open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(meta, indent=2) + "\n")


def promote(staging: Path, dump_id: str, created: list[Path]) -> None:
    targets = dump_paths(common.DATA_DIR, dump_id)
    for target in targets:
        if target.exists():
            raise FileExistsError(f"Refusing to overwrite existing dump: {target}")
    common.DATA_DIR.mkdir(parents=True, exist_ok=True)
    for source, target in zip(dump_paths(staging, dump_id), targets):
        with target.open("xb") as handle:
            created.append(target)
            with source.open("rb") as incoming:
                shutil.copyfileobj(incoming, handle)


def run(args: argparse.Namespace) -> Path:
    refresh = args.command == "refresh"
    base = args.base if refresh else (args.base or args.from_dump)
    output = args.to or common.next_revision_id()
    if not common.is_revision_dump_id(output):
        raise ValueError(f"Expected a revision id for --to, got {output!r}")
    no_promote = not refresh and args.no_promote
    if not no_promote:
        for target in dump_paths(common.DATA_DIR, output):
            if target.exists():
                raise FileExistsError(f"Refusing to overwrite existing dump: {target}")
    cache = Path(args.cache_dir) if refresh and args.cache_dir else common.CACHE_DIR
    crawl_date = None
    window = None
    if refresh:
        crawl_date = date.fromisoformat(args.date) if args.date else date.today()
        if bool(args.pageviews_start) != bool(args.pageviews_end):
            raise ValueError("--pageviews-start and --pageviews-end must be supplied together")
        window = (args.pageviews_start, args.pageviews_end) if args.pageviews_start else pageviews_window(crawl_date)
        for value in window:
            if len(value) != 8 or not value.isdigit():
                raise ValueError(f"Invalid pageview date: {value!r}")
            datetime.strptime(value, "%Y%m%d")
        if window[0] > window[1]:
            raise ValueError("Pageview start must be before end")
    requested = None if refresh else args.staging
    if requested:
        staging = Path(requested).resolve()
        staging.mkdir(parents=True, exist_ok=True)
        if any(staging.iterdir()):
            raise ValueError(f"Staging directory must be empty: {staging}")
    else:
        staging = Path(tempfile.mkdtemp(prefix=".pipeline-", dir=common.DATA_DIR.parent))
    log.info("Staging directory: %s", staging)
    created: list[Path] = []
    viewer_backup: dict[Path, bytes | None] = {}
    try:
        copy_dump(base, staging)
        stages = []
        if refresh:
            source = crawl_date.isoformat()
            # Preflight: overrides re-key to entities a cold crawl never fetches.
            # Fetch them first so a bad overrides file fails in seconds, not after the crawl.
            run_script("refetch_override_entities.py", ["--overrides", str(overrides_path())], staging, cache)
            run_script("build_dump.py", ["--date", source, "--pageviews-start", window[0],
                                         "--pageviews-end", window[1]], staging, cache)
            stages.append(_record_stage(staging, source, "build_dump.py"))
        else:
            source = args.from_dump
            if source != base:
                copy_dump(source, staging)
        preserve_schema = not refresh and args.preserve_schema
        final_stage, offline = offline_stages(source, base, staging, cache, refresh=refresh,
                                              preserve_schema=preserve_schema)
        stages.extend(offline)
        prepare_result(staging, final_stage, output, base, stages,
                       crawl_date=crawl_date.isoformat() if crawl_date else None,
                       window=window, source=source, refresh=refresh, preserve_schema=preserve_schema)
        run_script("check_release.py", ["--dump", output, "--against", base], staging, cache)
        if no_promote:
            print(f"STAGING={staging}")
            return staging
        promote(staging, output, created)
        viewer_data = common.REPO_ROOT / "viewer" / "data"
        viewer_backup = {viewer_data / name: (viewer_data / name).read_bytes() if (viewer_data / name).exists() else None
                         for name in VIEWER_FILES}
        run_script("export_viewer_json.py", ["--dump", output, "--out", str(viewer_data)], common.DATA_DIR, cache)
        run_script("check_release.py", ["--dump", output, "--viewer-data", str(viewer_data),
                                        "--against", base], common.DATA_DIR, cache)
    except BaseException:
        for path in created:
            path.unlink(missing_ok=True)
        for path, content in viewer_backup.items():
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(content)
        log.error("Pipeline failed; staging kept at %s", staging)
        raise
    if not (refresh and args.keep_staging):
        shutil.rmtree(staging)
    else:
        print(f"STAGING={staging}")
    print(f"REVISION={output}")
    return staging


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    refresh = commands.add_parser("refresh", help="Crawl, enrich, validate and promote a new revision")
    refresh.add_argument("--base", required=True)
    refresh.add_argument("--to")
    refresh.add_argument("--date")
    refresh.add_argument("--cache-dir", type=Path)
    refresh.add_argument("--pageviews-start")
    refresh.add_argument("--pageviews-end")
    refresh.add_argument("--keep-staging", action="store_true")
    derive = commands.add_parser("derive", help="Run cached, offline stages on an existing revision")
    derive.add_argument("--from-dump", required=True)
    derive.add_argument("--to", required=True)
    derive.add_argument("--base")
    derive.add_argument("--no-promote", action="store_true")
    derive.add_argument("--staging", type=Path)
    derive.add_argument("--preserve-schema", action="store_true",
                        help="Fail on any schema change (idempotency checks); default allows adding evidence columns")
    return parser.parse_args(argv)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        run(parse_args())
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        log.error("%s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
