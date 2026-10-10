#!/usr/bin/env python3
"""Export a dump to compact JSON for the static viewer.

Reads composers_*.tsv + works_*.tsv and writes viewer/data/:
  manifest.json, composers.json, works_by_composer.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "viewer" / "data"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DATA_DIR, dump_meta_path, format_viewer_dump_label  # noqa: E402
from apply_llm_styles import policy_metadata  # noqa: E402
from style_vocab import IMSLP_PERIOD_TO_SLUG  # noqa: E402

# Follows EU_PD_DATA_DIR, so staged pipeline runs export the dump they just built.
DATA = DATA_DIR

# IMSLP period label aliases → canonical display names (merge counts in facets).
IMSLP_STYLE_ALIASES = {
    "Romántico": "Romantic",
    "Traditional": "Traditional (folk)",
}
# Keep the first canonical spelling, rather than the later aliases in the map.
LLM_STYLE_TO_FACET = {}
for _period, _slug in IMSLP_PERIOD_TO_SLUG.items():
    LLM_STYLE_TO_FACET.setdefault(_slug, IMSLP_STYLE_ALIASES.get(_period, _period))


def _normalize_imslp_style(name: str) -> str:
    return IMSLP_STYLE_ALIASES.get(name, name)


def _llm_facet_value(slug: str) -> str:
    return LLM_STYLE_TO_FACET.get(slug, slug)


def _llm_votes(value, labellers: list[dict]) -> list[list[str] | None]:
    text = str(_clean(value) or "")
    if not text:
        return []
    votes = dict(part.split("=", 1) for part in text.split(";") if "=" in part)
    return [[_llm_facet_value(s) for s in _pipe_list(votes[labeller["key"]])]
            if labeller["key"] in votes else None for labeller in labellers]


def _clean(v):
    if v is None:
        return ""
    try:
        if isinstance(v, float) and math.isnan(v):
            return ""
    except (TypeError, ValueError):
        pass
    if pd.isna(v):
        return ""
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def _pipe_list(v) -> list[str]:
    text = str(_clean(v) or "")
    if not text:
        return []
    return [p for p in text.split("|") if p]


def _optional_int(v):
    cleaned = _clean(v)
    if cleaned == "" or cleaned is None:
        return None
    try:
        return int(cleaned)
    except (TypeError, ValueError):
        return None


def _pageviews_window_label(windows: list[str]) -> str:
    """Derive a short label (e.g. '2025') from pageviews_window values in the dump."""
    counts: Counter[str] = Counter(w for w in windows if w)
    if not counts:
        return ""
    window = counts.most_common(1)[0][0]
    # "2025-01..2025-12" → "2025" when both ends share a year; else keep raw.
    parts = window.split("..")
    years = []
    for part in parts:
        token = part.strip()[:4]
        if token.isdigit():
            years.append(token)
    if years and len(set(years)) == 1:
        return years[0]
    return window


def export(dump_id: str, out_dir: Path) -> None:
    composers_path = DATA / f"composers_{dump_id}.tsv"
    works_path = DATA / f"works_{dump_id}.tsv"
    if not composers_path.exists() or not works_path.exists():
        raise FileNotFoundError(f"Need {composers_path.name} and {works_path.name}")

    composers = pd.read_csv(composers_path, sep="\t", low_memory=False)
    works = pd.read_csv(works_path, sep="\t", low_memory=False)

    has_imslp_style = "imslp_style" in works.columns
    has_imslp_fp = "imslp_first_published" in works.columns
    has_imslp_cf = "imslp_copyright_flags" in works.columns
    has_file_hosts = "imslp_file_hosts" in works.columns

    meta = {}
    meta_path = dump_meta_path(dump_id)
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    created_at = meta.get("created_at_utc")
    llm_policy = (meta.get("llm_styles") or {}).get("policy") or policy_metadata()

    composer_rows = []
    for _, row in composers.iterrows():
        views = _optional_int(row.get("pageviews_enwiki"))
        entry = {
            "id": str(_clean(row.get("composer_id"))),
            "name": str(_clean(row.get("name_display"))),
            "sort": str(_clean(row.get("name_sort"))),
            "aliases": _pipe_list(row.get("name_aliases")),
            "birth": _clean(row.get("birth_year")),
            "death": _clean(row.get("death_year")),
            "wiki": str(_clean(row.get("wikipedia_url"))),
            "wikidata": str(_clean(row.get("wikidata_url"))),
            "cit": _pipe_list(row.get("citizenship_iso")),
            "scope": str(_clean(row.get("scope_class")) or "classical_core"),
            "eu_year": _clean(row.get("eu_pd_year")),
            "eu": str(_clean(row.get("eu_pd_status"))),
            "styles": _pipe_list(row.get("style_tags")),
            "style_src": str(_clean(row.get("style_tags_src"))),
            "imslp": str(_clean(row.get("imslp_url"))),
            "imslp_status": str(_clean(row.get("imslp_match_status"))),
            "forces": _pipe_list(row.get("work_categories_present")),
            "works_n": int(_clean(row.get("works_count_total")) or 0),
            "views": views,
            "views_window": str(_clean(row.get("pageviews_window"))),
        }
        if entry["style_src"].startswith("llm"):
            entry["styles"] = []
            entry["style_src"] = ""
        if str(_clean(row.get("is_film_composer"))).lower() == "true":
            entry["film"] = True
        llm_styles = [_llm_facet_value(s) for s in _pipe_list(row.get("llm_style_tags"))]
        llm_period = _llm_facet_value(str(_clean(row.get("llm_style_period")) or ""))
        llm_votes = _llm_votes(row.get("llm_style_votes"), llm_policy["labellers"])
        if llm_styles:
            entry["ls"] = llm_styles
        if llm_period:
            entry["lp"] = llm_period
        if llm_votes:
            entry["lv"] = llm_votes
        composer_rows.append(entry)

    by_composer: dict[str, list] = defaultdict(list)
    imslp_style_counts: Counter[str] = Counter()
    force_src_tiers: set[str] = set()

    for _, row in works.iterrows():
        cid = str(_clean(row.get("composer_id")))
        src = str(_clean(row.get("force_family_src")))
        if src:
            force_src_tiers.add(src)
        work_entry: dict = {
            "t": str(_clean(row.get("title"))),
            "u": str(_clean(row.get("imslp_work_url"))),
            "f": str(_clean(row.get("force_family"))),
            "s": src,
        }
        if has_imslp_style:
            styles = [_normalize_imslp_style(s) for s in _pipe_list(row.get("imslp_style"))]
            # Deduplicate after alias merge (e.g. Traditional|Traditional (folk)).
            styles = list(dict.fromkeys(styles))
            if styles:
                work_entry["st"] = styles
                imslp_style_counts.update(styles)
        if has_imslp_fp:
            fp = _optional_int(row.get("imslp_first_published"))
            if fp is not None:
                work_entry["fp"] = fp
        if has_imslp_cf:
            flags = _pipe_list(row.get("imslp_copyright_flags"))
            if flags:
                work_entry["cf"] = flags
        # Only the exceptions: pages without scores, and files off IMSLP's main server.
        if str(_clean(row.get("has_files"))).lower() == "false":
            work_entry["hf"] = False
        if has_file_hosts:
            hosts = _pipe_list(row.get("imslp_file_hosts"))
            if any(host != "ca" for host in hosts):
                work_entry["fh"] = hosts
        by_composer[cid].append(work_entry)

    out_dir.mkdir(parents=True, exist_ok=True)
    composers_out = out_dir / "composers.json"
    works_out = out_dir / "works_by_composer.json"
    manifest_out = out_dir / "manifest.json"

    composers_out.write_text(
        json.dumps(composer_rows, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    works_out.write_text(
        json.dumps(by_composer, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    scopes = sorted({c["scope"] for c in composer_rows if c["scope"]})
    eu_statuses = sorted({c["eu"] for c in composer_rows if c["eu"]})
    forces = sorted({f for c in composer_rows for f in c["forces"]})
    styles = sorted({s for c in composer_rows for s in c["styles"]})
    countries = sorted({x for c in composer_rows for x in c["cit"]})
    imslp_styles = dict(
        sorted(imslp_style_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    )

    tagged_composers = [c for c in composer_rows if c["styles"] or c.get("ls")]
    wikidata_tagged = sum(1 for c in composer_rows if c["styles"] and c["style_src"] == "wikidata")
    llm_tagged = sum(1 for c in composer_rows if c.get("ls"))
    style_tags_coverage = {
        "tagged": len(tagged_composers),
        "total": len(composer_rows),
        "wikidata": wikidata_tagged,
        "llm": llm_tagged,
    }

    label = format_viewer_dump_label(dump_id, created_at)
    manifest = {
        "dump_id": dump_id,
        "dump_label": label,
        "created_at_utc": created_at,
        "schema_version": 3,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "counts": {
            "composers": len(composer_rows),
            "works": int(len(works)),
            "composers_with_works": sum(1 for c in composer_rows if c["works_n"] > 0),
        },
        "files": {
            "composers": "composers.json",
            "works_by_composer": "works_by_composer.json",
        },
        "facets": {
            "eu_pd_status": eu_statuses,
            "scope_class": scopes,
            "force_family": forces,
            "style_tags": styles,
            "citizenship_iso": countries,
            "imslp_style": imslp_styles,
            "force_family_src": sorted(force_src_tiers),
        },
        "style_tags_coverage": style_tags_coverage,
        "llm_style_policy": llm_policy,
        "disclaimer": (
            "EU public-domain status is a live death-year + 70 years heuristic "
            "(counted from 1 January), not legal advice; composers without a death "
            "date are shown as having no death date. Linked IMSLP entries are work "
            "pages, not a verified score-file inventory. Force and style "
            "tags are research aids and often wrong (incomplete IMSLP/Wikidata data, "
            "heuristics, or LLM guesses)—verify before relying on them."
        ),
        "pageviews_window_label": _pageviews_window_label(
            [c["views_window"] for c in composer_rows]
        ),
    }
    manifest_out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    def _rel(path: Path) -> str:
        try:
            return str(path.relative_to(REPO))
        except ValueError:
            return str(path)

    print(
        f"Wrote {_rel(composers_out)} "
        f"({composers_out.stat().st_size // 1024} KB), "
        f"{_rel(works_out)} ({works_out.stat().st_size // 1024} KB), "
        f"{_rel(manifest_out)}"
    )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dump", default="r008", help="dump_id to export")
    p.add_argument(
        "--out",
        default=str(OUT),
        help="Output directory (default: viewer/data)",
    )
    args = p.parse_args()
    export(args.dump, Path(args.out))


if __name__ == "__main__":
    main()
