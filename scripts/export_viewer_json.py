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
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data"
OUT = REPO / "viewer" / "data"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import dump_meta_path, format_viewer_dump_label  # noqa: E402


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


def export(dump_id: str, out_dir: Path) -> None:
    composers_path = DATA / f"composers_{dump_id}.tsv"
    works_path = DATA / f"works_{dump_id}.tsv"
    if not composers_path.exists() or not works_path.exists():
        raise FileNotFoundError(f"Need {composers_path.name} and {works_path.name}")

    composers = pd.read_csv(composers_path, sep="\t", low_memory=False)
    works = pd.read_csv(works_path, sep="\t", low_memory=False)

    created_at = None
    meta_path = dump_meta_path(dump_id)
    if meta_path.exists():
        try:
            created_at = json.loads(meta_path.read_text(encoding="utf-8")).get(
                "created_at_utc"
            )
        except json.JSONDecodeError:
            created_at = None

    composer_rows = []
    for _, row in composers.iterrows():
        composer_rows.append(
            {
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
                "imslp": str(_clean(row.get("imslp_url"))),
                "imslp_status": str(_clean(row.get("imslp_match_status"))),
                "forces": _pipe_list(row.get("work_categories_present")),
                "works_n": int(_clean(row.get("works_count_total")) or 0),
                "views": _clean(row.get("pageviews_enwiki")) or 0,
            }
        )

    by_composer: dict[str, list] = defaultdict(list)
    for _, row in works.iterrows():
        cid = str(_clean(row.get("composer_id")))
        by_composer[cid].append(
            {
                "t": str(_clean(row.get("title"))),
                "u": str(_clean(row.get("imslp_work_url"))),
                "f": str(_clean(row.get("force_family"))),
                "s": str(_clean(row.get("force_family_src"))),
            }
        )

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
        },
        "disclaimer": (
            "EU public-domain status is a death-year + 71 calendar heuristic, "
            "not legal advice. Force and style tags are research aids and often wrong "
            "(incomplete IMSLP/Wikidata data, heuristics, or LLM guesses)—verify before relying on them."
        ),
    }
    manifest_out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print(
        f"Wrote {composers_out.relative_to(REPO)} "
        f"({composers_out.stat().st_size // 1024} KB), "
        f"{works_out.relative_to(REPO)} ({works_out.stat().st_size // 1024} KB), "
        f"{manifest_out.relative_to(REPO)}"
    )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dump", default="r007", help="dump_id to export")
    p.add_argument(
        "--out",
        default=str(OUT),
        help="Output directory (default: viewer/data)",
    )
    args = p.parse_args()
    export(args.dump, Path(args.out))


if __name__ == "__main__":
    main()
