#!/usr/bin/env python3
"""Web-search sweep for composer score pages (experimental; network via Codex or Cursor web search).

For each non-public-domain composer, runs one web search (default `"<name>" score`;
`--query` takes another template) and records the raw result URLs. Codex CLI (`codex exec` with live web search) acts only
as the search backend: it is told to copy result URLs in order, not to judge them.
`homepage_licences.py --targets` then checks every result page and its site's
homepage for Creative Commons licences and score links (search proposes, rules
verify). Batches are cached by content, so an interrupted run resumes.

`--agent cursor` uses headless `cursor-agent` instead. Its web search only runs with
`--force`, so each batch gets an empty workspace whose `.cursor/cli.json` denies shell,
writes and reads; URLs come from the search tool results in the event stream, never from
the model's reply, and any other tool call that succeeds stops the sweep.

  python scripts/search_sweep.py --dump r020 --sample 50 --include Q1795042
  python scripts/search_sweep.py --dump r020 --sample 50 --include Q1795042 \
      --query '"{name}" "creative commons"' --tag _pilot_cc
  python scripts/search_sweep.py --dump r020 --parallel 3
  python scripts/search_sweep.py --dump r020 --agent cursor --batch-size 10 --parallel 3
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import shutil
import subprocess
import sys
import tempfile
import threading
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

CURSOR_PROMPT = """You are a web-search backend. For each input line below (composer id, TAB, query), call
your web search tool exactly once with exactly that query text, including any double quotes. Use no
other tool. Do not summarise results. When all searches are done, reply with the single word DONE.

Input:
{lines}
"""

# `--force` is the only way headless cursor-agent runs web search; these deny rules keep it
# from approving anything else (verified 2026-10-11: shell, edit and read calls are refused).
CURSOR_PERMISSIONS = {"permissions": {"allow": [], "deny": ["Shell(*)", "Write(**)", "Read(**)"]}}
CURSOR_SAFE_TOOLS = {"webSearchToolCall", "getMcpToolsToolCall"}
CURSOR_BIN = shutil.which("cursor-agent") or str(Path.home() / ".local" / "bin" / "cursor-agent")
_LINK_RE = re.compile(r"^\d+\.\s+\[(.*)\]\((https?://\S+)\)\s*$")
STOP = threading.Event()


class SandboxBreach(RuntimeError):
    """A cursor tool other than web search succeeded; the sweep stops."""


_LINE_RE = re.compile(r"^(Q\d+)\t(\d+)\t(\S*)\t?(.*)$")
_TOKENS_RE = re.compile(r"tokens used\s*\n\s*([\d,]+)")


DEFAULT_QUERY = '"{name}" score'


def query_for(name: str, template: str = DEFAULT_QUERY) -> str:
    return template.format(name=name)


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


def normalize_query(text: str) -> str:
    """Casefolded, quotes dropped, whitespace collapsed (Cursor strips the query's quotes)."""
    return " ".join(text.replace('"', " ").casefold().split())


def search_links(chunk: str) -> list[tuple[str, str]]:
    """(url, title) pairs from the numbered `Links:` block of a Cursor search result."""
    links, inside = [], False
    for line in chunk.splitlines():
        if line.strip() == "Links:":
            inside = True
            continue
        if inside:
            m = _LINK_RE.match(line.strip())
            if not m:
                break
            links.append((m.group(2), " ".join(m.group(1).split())))
    return links


def parse_cursor_stream(
    lines: list[str], queries: dict[str, str], names: dict[str, str], top: int
) -> tuple[dict[str, list[str]], dict[str, int]]:
    """Batch-format lines per composer id from successful search calls, plus token usage."""
    by_query = {normalize_query(q): cid for cid, q in queries.items()}
    found: dict[str, list[str]] = {}
    usage: dict[str, int] = {}
    for raw in lines:
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "result":
            usage = event.get("usage") or {}
        if event.get("type") != "tool_call" or event.get("subtype") != "completed":
            continue
        kind, call = next(iter((event.get("tool_call") or {}).items()), (None, {}))
        result = call.get("result") or {}
        if "success" not in result:
            continue
        if kind not in CURSOR_SAFE_TOOLS:
            raise SandboxBreach(f"cursor tool {kind} succeeded: {json.dumps(call.get('args'))[:200]}")
        if kind != "webSearchToolCall":
            continue
        term = normalize_query((call.get("args") or {}).get("searchTerm", ""))
        cid = by_query.get(term)
        if cid is None:
            hits = [c for c, n in names.items() if normalize_query(n) in term]
            cid = hits[0] if len(hits) == 1 else None
        if cid is None:
            log.warning("search term %r matches no composer in the batch", term)
            continue
        if cid in found:
            continue
        links = [link for ref in result["success"].get("references") or [] for link in search_links(ref.get("chunk", ""))]
        found[cid] = [f"{cid}\t{rank}\t{url}\t{title}" for rank, (url, title) in enumerate(links[:top], 1)]
        found[cid] = found[cid] or [f"{cid}\t0\t\t"]
    return found, usage


def run_cursor(prompt: str, timeout: float, model: str) -> list[str]:
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / ".cursor").mkdir()
        (Path(tmp) / ".cursor" / "cli.json").write_text(json.dumps(CURSOR_PERMISSIONS), encoding="utf-8")
        cmd = [
            CURSOR_BIN, "-p", "--model", model, "--force", "--trust", "--workspace", tmp,
            "--output-format", "stream-json", prompt,
        ]
        try:
            proc = subprocess.run(cmd, cwd=tmp, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            log.warning("cursor-agent timed out after %.0fs", timeout)
            out = exc.stdout or b""
            return (out.decode("utf-8", "replace") if isinstance(out, bytes) else out).splitlines()
        if proc.returncode != 0:
            log.warning("cursor-agent exit %s: %s", proc.returncode, proc.stderr[-500:])
        return proc.stdout.splitlines()


def run_batch_cursor(batch: pd.DataFrame, out_dir: Path, args: argparse.Namespace) -> Path:
    queries = {r.composer_id: query_for(r.name_display, args.query) for r in batch.itertuples()}
    names = {r.composer_id: r.name_display for r in batch.itertuples()}
    key = hashlib.sha1(f"cursor|{args.model}|{args.top}|{json.dumps(queries)}".encode()).hexdigest()[:12]
    out = out_dir / f"batch_{key}.txt"
    if STOP.is_set() or (out.exists() and not (args.retry_missing and "# missing=" in out.read_text(encoding="utf-8"))):
        return out
    found: dict[str, list[str]] = {}
    tokens = {"inputTokens": 0, "outputTokens": 0, "cacheReadTokens": 0}
    for _ in range(2):
        todo = {cid: q for cid, q in queries.items() if cid not in found}
        prompt = CURSOR_PROMPT.format(lines="\n".join(f"{cid}\t{q}" for cid, q in todo.items()))
        try:
            got, usage = parse_cursor_stream(run_cursor(prompt, args.timeout, args.model), todo, names, args.top)
        except SandboxBreach:
            STOP.set()
            raise
        found.update(got)
        for k in tokens:
            tokens[k] += int(usage.get(k) or 0)
        if len(found) == len(queries):
            break
    missing = [cid for cid in queries if cid not in found]
    header = f"# tokens_used={tokens['inputTokens'] + tokens['outputTokens']}\n# cache_read_tokens={tokens['cacheReadTokens']}\n"
    if missing:
        header += f"# missing={','.join(missing)}\n"
        log.warning("batch %s: no search ran for %d of %d composers", key, len(missing), len(queries))
    out.write_text(header + "".join(line + "\n" for cid in queries for line in found.get(cid, [])), encoding="utf-8")
    log.info("batch %s: %d composers, tokens %s", key, len(batch), header.split("\n")[0].split("=")[1])
    return out


def run_batch(batch: pd.DataFrame, out_dir: Path, args: argparse.Namespace) -> Path:
    if args.agent == "cursor":
        return run_batch_cursor(batch, out_dir, args)
    lines = "\n".join(f"{r.composer_id}\t{query_for(r.name_display, args.query)}" for r in batch.itertuples())
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
    log.info("%s: %d composers in %d batches (%s %s @%s)", args.dump, len(chosen), len(batches), args.agent, args.model, args.effort)

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
    res.insert(2, "query", res["name_display"].map(lambda n: query_for(n, args.query)))
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
    p.add_argument("--query", default=DEFAULT_QUERY, help="Query template with {name} (default: %(default)s)")
    p.add_argument("--parallel", type=int, default=1, help="Concurrent agent batches")
    p.add_argument("--agent", choices=["codex", "cursor"], default="codex")
    p.add_argument("--model", help="Default: gpt-6.1-sol for codex, auto for cursor")
    p.add_argument("--effort", default="low")
    p.add_argument("--timeout", type=float, default=1800)
    p.add_argument("--tag", default="", help="Suffix for the merged TSV (e.g. _pilot)")
    p.add_argument("--out-dir", default=str(REPO_ROOT / "build" / "open_licence" / "search"))
    p.add_argument("--retry-missing", action="store_true", help="Re-run cursor batches with composers left unsearched")
    args = p.parse_args()
    args.model = args.model or ("auto" if args.agent == "cursor" else "gpt-6.1-sol")
    run(args)


if __name__ == "__main__":
    main()
