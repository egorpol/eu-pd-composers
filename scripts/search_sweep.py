#!/usr/bin/env python3
"""Web-search sweep for composer score pages (experimental; network via Codex web search).

For each non-public-domain composer, runs one web search for `"<name>" score` and
records the raw result URLs. Codex CLI (`codex exec` with live web search) acts only
as the search backend: it is told to copy result URLs in order, not to judge them.
`homepage_licences.py --targets` then checks every result page and its site's
homepage for Creative Commons licences and score links (search proposes, rules
verify). Batches are cached by content, so an interrupted run resumes.

  python scripts/search_sweep.py --dump r019 --sample 50 --include Q1795042
  python scripts/search_sweep.py --dump r019 --parallel 3
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import REPO_ROOT, dump_tsv_path  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("search_sweep")

PROMPT = """You are a web-search backend. For each input line below (composer id, TAB, query), call
the web_search tool exactly once with exactly that query, then output the results you got back,
in the order returned, at most {top} per query, one per line as:
<composer id><TAB><rank><TAB><url><TAB><title>
If a search returns nothing, output one line: <composer id><TAB>0<TAB><TAB>
Output nothing else: no headings, no commentary, no code fences. Never invent or edit URLs;
copy them as returned. Do not judge relevance; include every result up to {top}.

Input:
{lines}
"""

_LINE_RE = re.compile(r"^(Q\d+)\t(\d+)\t(\S*)\t?(.*)$")
_TOKENS_RE = re.compile(r"tokens used\s*\n\s*([\d,]+)")


def query_for(name: str) -> str:
    return f'"{name}" score'


def select_composers(composers: pd.DataFrame, args: argparse.Namespace) -> pd.DataFrame:
    nonpd = composers[composers["eu_pd_status"].fillna("") != "pd"].sort_values("composer_id")
    if args.sample:
        include = set(args.include or [])
        rest = nonpd[~nonpd["composer_id"].isin(include)].sample(
            n=max(0, args.sample - len(include)), random_state=args.seed
        )
        nonpd = pd.concat([nonpd[nonpd["composer_id"].isin(include)], rest])
    if args.limit:
        nonpd = nonpd.head(args.limit)
    return nonpd


def parse_output(text: str, batch_ids: set[str]) -> list[dict[str, str]]:
    rows = []
    for line in text.splitlines():
        m = _LINE_RE.match(line.strip("\r"))
        if not m or m.group(1) not in batch_ids:
            continue
        url = m.group(3)
        if url and not url.startswith(("http://", "https://")):
            continue
        rows.append({"composer_id": m.group(1), "rank": m.group(2), "url": url, "title": m.group(4).strip()})
    return rows


def run_batch(batch: pd.DataFrame, out_dir: Path, args: argparse.Namespace) -> Path:
    lines = "\n".join(f"{r.composer_id}\t{query_for(r.name_display)}" for r in batch.itertuples())
    prompt = PROMPT.format(top=args.top, lines=lines)
    key = hashlib.sha1(f"{args.model}|{prompt}".encode()).hexdigest()[:12]
    out = out_dir / f"batch_{key}.txt"
    if out.exists():
        return out
    with tempfile.TemporaryDirectory() as tmp:
        last = Path(tmp) / "last.txt"
        cmd = [
            "codex", "exec", "-m", args.model, "-c", "web_search=live",
            "-c", f"model_reasoning_effort={args.effort}", "-s", "read-only",
            "--skip-git-repo-check", "-o", str(last), prompt,
        ]
        proc = subprocess.run(cmd, cwd=tmp, capture_output=True, text=True, timeout=args.timeout)
        tokens = _TOKENS_RE.search(proc.stderr + proc.stdout)
        if proc.returncode != 0 or not last.exists():
            log.warning("batch %s failed (exit %s): %s", key, proc.returncode, proc.stderr[-500:])
            return out
        body = last.read_text(encoding="utf-8")
    out.write_text(f"# tokens_used={tokens.group(1).replace(',', '') if tokens else ''}\n" + body, encoding="utf-8")
    log.info("batch %s: %d composers, tokens %s", key, len(batch), tokens.group(1) if tokens else "?")
    return out


def run(args: argparse.Namespace) -> None:
    composers = pd.read_csv(dump_tsv_path("composers", args.dump), sep="\t", dtype=str, keep_default_na=False)
    chosen = select_composers(composers, args)
    out_dir = Path(args.out_dir) / args.dump
    out_dir.mkdir(parents=True, exist_ok=True)
    batches = [chosen.iloc[i : i + args.batch_size] for i in range(0, len(chosen), args.batch_size)]
    log.info("%s: %d composers in %d batches (model %s @%s)", args.dump, len(chosen), len(batches), args.model, args.effort)

    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        paths = list(pool.map(lambda b: run_batch(b, out_dir, args), batches))

    rows, tokens = [], 0
    for batch, path in zip(batches, paths):
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        m = re.match(r"# tokens_used=(\d+)", text)
        tokens += int(m.group(1)) if m else 0
        for row in parse_output(text, set(batch["composer_id"])):
            rows.append({**row, "batch": path.stem})
    res = pd.DataFrame(rows, columns=["composer_id", "rank", "url", "title", "batch"])
    names = chosen.set_index("composer_id")["name_display"]
    res.insert(1, "name_display", res["composer_id"].map(names))
    res.insert(2, "query", res["name_display"].map(query_for))
    out = Path(args.out_dir) / f"search_results_{args.dump}{args.tag}.tsv"
    res.to_csv(out, sep="\t", index=False)
    answered = res["composer_id"].nunique()
    log.info(
        "%d result rows for %d of %d composers (%d without output); tokens used %d → %s",
        int((res["url"] != "").sum()), answered, len(chosen), len(chosen) - answered, tokens, out,
    )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dump", required=True)
    p.add_argument("--sample", type=int, default=0, help="Random sample of N non-PD composers (pilot)")
    p.add_argument("--include", nargs="*", help="Composer ids always in the sample")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--batch-size", type=int, default=25)
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--parallel", type=int, default=1, help="Concurrent codex batches")
    p.add_argument("--model", default="gpt-6.1-sol")
    p.add_argument("--effort", default="low")
    p.add_argument("--timeout", type=float, default=1800)
    p.add_argument("--tag", default="", help="Suffix for the merged TSV (e.g. _pilot)")
    p.add_argument("--out-dir", default=str(REPO_ROOT / "build" / "open_licence" / "search"))
    run(p.parse_args())


if __name__ == "__main__":
    main()
