#!/usr/bin/env python3
"""Review queue of suspect composer identities, with candidate Wikidata items (network).

A composer row is suspect when the Wikipedia list row and its Wikidata item
disagree on a life year by `--min-gap` years or more, or when the item is not a
human or has no composing occupation. For each suspect the queue proposes items
for the same name with a composing occupation and life years within one year of
the list row, found by a Wikidata search and then by fr / de Wikipedia
searches. Proposals feed `data/overrides/composers.tsv` after review; this
script writes no dump.

  python scripts/identity_candidates.py --dump r017 --out build/identity/r017.tsv
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_dump import get_soup, parse_composer_table  # noqa: E402
from common import (  # noqa: E402
    WIKI_LIST_URL, WIKIDATA_API, cache_get, cache_set, dump_tsv_path, make_session,
    pipe_split, request_json,
)
from wikidata_enrich import COMPOSING_OCC, enrich_from_entity, fetch_entities  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("identity_candidates")

SEARCH_NAMESPACE = "identity_search"
WIKI_SEARCHES = (("fr", "compositeur"), ("de", "Komponist"))
COLUMNS = (
    "composer_id", "name_display", "wikipedia_url", "list_years", "wikidata_years", "year_gap", "signals",
    "nationality_list", "citizenship_iso", "occupations", "candidates_n", "candidate_qid", "candidate_label",
    "candidate_years", "candidate_occupations", "candidate_source", "candidate_enwiki", "proposal",
)


def _int(value: Any) -> Optional[int]:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError, OverflowError):
        return None


def _years(birth: Optional[int], death: Optional[int]) -> str:
    return f"{birth if birth is not None else '?'}–{death if death is not None else ''}"


def _url_key(url: Any) -> str:
    return unquote(str(url or "")).rstrip("/").split("/wiki/")[-1].replace(" ", "_")


def year_gap(list_birth: Optional[int], list_death: Optional[int],
             wd_birth: Optional[int], wd_death: Optional[int]) -> Optional[int]:
    gaps = [abs(a - b) for a, b in ((list_birth, wd_birth), (list_death, wd_death))
            if a is not None and b is not None]
    return max(gaps) if gaps else None


def years_agree(list_birth: Optional[int], list_death: Optional[int],
                birth: Optional[int], death: Optional[int]) -> bool:
    """Every year both sides know agrees within one year, and at least one is compared."""
    pairs = [(a, b) for a, b in ((list_birth, birth), (list_death, death)) if a is not None and b is not None]
    return bool(pairs) and all(abs(a - b) <= 1 for a, b in pairs)


def _cached_search(session, key: str, url: str, params: dict) -> dict:
    cached = cache_get(SEARCH_NAMESPACE, key)
    if cached is None:
        cached = request_json(session, url, params=params)
        cache_set(SEARCH_NAMESPACE, key, cached)
    return cached


def search_wikidata(session, name: str) -> list[str]:
    data = _cached_search(session, f"wd_{name}", WIKIDATA_API, {
        "action": "wbsearchentities", "search": name, "language": "en", "type": "item",
        "limit": "20", "format": "json",
    })
    return [hit["id"] for hit in data.get("search", []) if hit.get("id")]


def search_wikipedia(session, lang: str, name: str, keyword: str) -> list[str]:
    """QIDs of the top articles for "<name> <keyword>" on a Wikipedia."""
    data = _cached_search(session, f"{lang}_{name}_{keyword}", f"https://{lang}.wikipedia.org/w/api.php", {
        "action": "query", "generator": "search", "gsrsearch": f"{name} {keyword}", "gsrlimit": "10",
        "prop": "pageprops", "ppprop": "wikibase_item", "format": "json",
    })
    pages = sorted(data.get("query", {}).get("pages", {}).values(), key=lambda p: p.get("index", 99))
    return [p["pageprops"]["wikibase_item"] for p in pages if p.get("pageprops", {}).get("wikibase_item")]


def enwiki_titles(session, qids: list[str]) -> dict[str, str]:
    """English Wikipedia article title per QID (the entity cache has no sitelinks)."""
    if not qids:
        return {}
    data = _cached_search(session, "enwiki_" + "_".join(sorted(qids)), WIKIDATA_API, {
        "action": "wbgetentities", "ids": "|".join(sorted(qids)), "props": "sitelinks",
        "sitefilter": "enwiki", "format": "json",
    })
    return {qid: (ent.get("sitelinks", {}).get("enwiki") or {}).get("title", "")
            for qid, ent in data.get("entities", {}).items()}


def candidate_rows(session, qid: str, name: str, list_birth: Optional[int],
                   list_death: Optional[int]) -> list[dict[str, Any]]:
    """Items for this name whose years agree with the list row, composers first."""
    found: dict[str, str] = {}
    for cand in search_wikidata(session, name):
        found.setdefault(cand, "wikidata_search")
    for lang, keyword in WIKI_SEARCHES:
        for cand in search_wikipedia(session, lang, name, keyword):
            found.setdefault(cand, f"{lang}wiki_search")
    found.pop(qid, None)
    entities = fetch_entities(sorted(found), session)
    rows = []
    for cand, source in found.items():
        ent = entities.get(cand)
        if not ent:
            continue
        wd = enrich_from_entity(ent)
        if "not_human" in pipe_split(wd["qa_flags"]):
            continue
        if not years_agree(list_birth, list_death, wd["birth_year"], wd["death_year"]):
            continue
        composer = bool(set(pipe_split(wd["occupations"])) & COMPOSING_OCC)
        rows.append({
            "candidate_qid": cand, "candidate_label": wd.get("wikidata_label") or "",
            "candidate_years": _years(wd["birth_year"], wd["death_year"]),
            "candidate_occupations": wd["occupations"], "candidate_source": source,
            "_composer": composer,
        })
    rows.sort(key=lambda r: (not r["_composer"], r["candidate_source"] != "wikidata_search"))
    titles = enwiki_titles(session, [r["candidate_qid"] for r in rows[:40]])
    for r in rows:
        r["candidate_enwiki"] = titles.get(r["candidate_qid"], "")
    return rows


def suspects(composers: pd.DataFrame, list_rows: list[dict[str, Any]], min_gap: int) -> list[dict[str, Any]]:
    by_url = {_url_key(r.get("URL")): r for r in list_rows if r.get("URL")}
    out = []
    for _, row in composers.iterrows():
        qid = row["composer_id"]
        ent = cache_get("wikidata_entity", qid)
        if ent is None:
            continue
        wd = enrich_from_entity(ent)
        listed = by_url.get(_url_key(row.get("wikipedia_url")), {})
        list_birth, list_death = _int(listed.get("Year of birth")), _int(listed.get("Year of death"))
        gap = year_gap(list_birth, list_death, wd["birth_year"], wd["death_year"])
        signals = [f for f in ("not_human", "no_composer_occupation") if f in pipe_split(wd["qa_flags"])]
        if gap is not None and gap >= min_gap:
            signals.insert(0, f"list_years_gap_{gap}")
        if not signals:
            continue
        out.append({
            "composer_id": qid, "name_display": row["name_display"], "wikipedia_url": row.get("wikipedia_url", ""),
            "list_birth": list_birth, "list_death": list_death,
            "list_years": _years(list_birth, list_death) if listed else "",
            "wikidata_years": _years(wd["birth_year"], wd["death_year"]),
            "year_gap": "" if gap is None else str(gap), "signals": "|".join(signals),
            "nationality_list": listed.get("Nationality", row.get("nationality_label_wiki", "")),
            "citizenship_iso": row.get("citizenship_iso", ""), "occupations": wd["occupations"],
        })
    return out


def run(args: argparse.Namespace) -> None:
    composers = pd.read_csv(dump_tsv_path("composers", args.dump), sep="\t", dtype=str, keep_default_na=False)
    session = make_session()
    list_rows = parse_composer_table(get_soup(WIKI_LIST_URL, session))
    log.info("%s: %d composers; %d list rows", args.dump, len(composers), len(list_rows))
    rows = []
    for suspect in suspects(composers, list_rows, args.min_gap):
        cands = candidate_rows(session, suspect["composer_id"], suspect["name_display"],
                               suspect["list_birth"], suspect["list_death"])
        best = cands[0] if cands else {}
        composers_n = sum(c["_composer"] for c in cands)
        if composers_n == 1 and best.get("_composer"):
            proposal = "rekey"
        elif composers_n > 1:
            proposal = "ambiguous"
        elif suspect["signals"].startswith("list_years_gap"):
            proposal = "no_candidate"
        else:
            proposal = "check_occupation"
        rows.append({**suspect, "candidates_n": str(len(cands)), **{k: v for k, v in best.items() if not k.startswith("_")},
                     "proposal": proposal})
    out = pd.DataFrame(rows).reindex(columns=COLUMNS).fillna("")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.out, sep="\t", index=False)
    log.info("Wrote %d suspects to %s: %s", len(out), args.out, out["proposal"].value_counts().to_dict())


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dump", required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--min-gap", type=int, default=5, help="Smallest list vs Wikidata year gap that is suspect")
    run(p.parse_args())


if __name__ == "__main__":
    main()
