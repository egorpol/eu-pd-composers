# Data dumps

Versioned snapshots. Treat TSV + `dump_meta_*.json` as the product; scripts under `scripts/` are how they were built.

## Current dump (schema v3)

| File | Rows | dump_id |
|---|---|---|
| `composers_2026-09-26.tsv` | 3371 | `2026-09-26` |
| `works_2026-09-26.tsv` | 29021 | `2026-09-26` |
| `dump_meta_2026-09-26.json` | — | companion meta |

**Schema v3 (Opus-aligned P0):** Wikidata QIDs, citizenship ISO/QIDs, `scope_class`, Wikidata `style_tags`, IMSLP match via P839 (+ heuristic fallback), companion **works** TSV (one row per IMSLP work page).

| Field | Value |
|---|---|
| tool_version | `3.0.0-dev` |
| schema_version | `3` |
| Name source | Wikipedia [List of 20th-century classical composers](https://en.wikipedia.org/wiki/List_of_20th-century_classical_composers) |
| Enrichment | Wikidata (P27, P106, P135/P136, P800, P839, P569/P570) |
| Pageviews | Wikimedia monthly `20250101`–`20251231` (4 null) |
| IMSLP | P839 → `matched` (978); heuristic name (298); `not_found` (2095) |
| Works | 29021 pages (~27 mean per matched composer) |
| EU heuristic | `eu_pd_year = death_year + 71` → `eu_pd_status` |
| EU PD / not / living | 781 / 1871 / 719 |
| `scope_class` | classical_core 3124 · film_media 245 · other 2 |
| `style_tags` non-empty | 356 |
| Lists | pipe-separated (`\|`) |
| Build log | `data/logs/dump_2026-09-26.log` (~48 min: pageviews 17m + enrich/IMSLP 31m) |

**Composer columns:** see `dump_meta_*.json` → `composer_columns`  
**Work columns:** `work_id`, `composer_id`, `imslp_pageid`, `imslp_work_url`, `title`, `imslp_genre_categories`, `has_files`, `fetched_at`

Deferred: `force_family` mapping, LLM style fill-ins, `--work-files` for `has_files`.

## Sample dump (schema v3, limited)

| File | Rows | dump_id |
|---|---|---|
| `composers_2026-09-25.tsv` | 5 | `2026-09-25` |
| `works_2026-09-25.tsv` | 364 | `2026-09-25` |

Pipeline smoke test (`--limit 5 --no-pageviews`). Prefer `2026-09-26` for real work.

## Previous dump (schema v2)

| File | Rows | dump_id |
|---|---|---|
| `composers_2026-09-24.tsv` | 3371 | `2026-09-24` |

Single TSV; heuristic IMSLP existence only.

## Legacy dump (schema v1)

| File | Rows | dump_id |
|---|---|---|
| `composers.tsv` | 3367 | `v0.1-legacy` |
| `composers_imslp.tsv` | 3367 | `v0.1-legacy` |

## Caveats

- IMSLP match / hosted scores ≠ public domain in your jurisdiction.
- `eu_pd_*` is a calendar heuristic, not legal advice.
- `nationality_label_wiki` is display-only; filter on `citizenship_iso` / QIDs.
- `style_tags` are Wikidata-mapped only (sparse).
- `unverified_heuristic` IMSLP matches need caution (name collisions).
- Never overwrite dated dumps — add a new date + meta file.

## Building a new dump

```bash
pip install -r requirements.txt
python scripts/build_dump.py --limit 5 --no-pageviews --dry-run
python scripts/build_dump.py --date YYYY-MM-DD
```

Writes `composers_YYYY-MM-DD.tsv`, `works_YYYY-MM-DD.tsv`, `dump_meta_YYYY-MM-DD.json`.  
Heartbeats every 30s by default. Cache: `data/cache/` (gitignored).
