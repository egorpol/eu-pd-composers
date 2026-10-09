# Data dumps

Versioned snapshots. Treat TSV + `dump_meta_*.json` as the product; scripts under `scripts/` are how they were built.

Pipeline diagram: [`docs/PIPELINE.md`](../docs/PIPELINE.md).

## Current dump (schema v3)

| File | Rows | dump_id |
|---|---|---|
| `composers_r010.tsv` | 3368 | `r010` |
| `works_r010.tsv` | 28993 | `r010` |
| `dump_meta_r010.json` | — | companion meta |

Only the **latest** revision is kept in `data/` in git. Older `rNNN` / calendar dumps are recoverable from **git history** (not from gitignored `data/cache/`). Prefer `r010` for the viewer (`scripts/export_viewer_json.py --dump r010`).

### What changed since r008

- **r009 — complete IMSLP categories.** r008's category fetch was truncated (one `cllimit=100` shared by 20 pages, continuation ignored), so 62.8% of works had no categories and most force labels fell back to title-only LLM guesses. r009 rebuilds `imslp_genre_categories` from a complete per-page crawl (empty: ~1%) and re-derives `force_family` with parsed `For …` rules (fixes e.g. flute→guitar). `imslp_tags` now label 98.3% of works; LLM-derived labels dropped from 43.8% to 1.3%. Instrument + one keyboard (violin/piano sonatas) is `solo_instrument`; 3+ unaccompanied voices are `choral`.
- **r010 — composer fixes from cached Wikidata.** Deprecated claims are ignored and preferred rank wins (Kabalevsky †1987, Rieding †1916; three deprecated-only death years removed → `unknown_death`). Composers who also wrote film music are `classical_core` with `is_film_composer=true` (Prokofiev, Gershwin, Weill, Honegger were hidden by the default viewer filter). New `qa_flags` column marks rows to double-check.

Each `dump_meta_rNNN.json` records counts and before/after transitions for its step.

## Columns added in r010

| Column | Values |
|---|---|
| `is_film_composer` | `true` / `false` — Wikidata occupation includes film composer |
| `qa_flags` | pipe list, empty if none: `birth_rank_conflict`, `death_rank_conflict` (best-rank Wikidata claims disagree), `birth_imprecise`, `death_imprecise` (decade/century precision; the year is the interval's last year), `implausible_lifespan`, `death_before_birth`, `not_human` (QID is not a person), `no_composer_occupation` (QID may be a different person), `birth_from_list`, `death_from_list` (year from the Wikipedia list, not Wikidata) |

## Caveats (read these)

- **Not legal advice.** `eu_pd_*` is a death-year + 71 calendar heuristic against the dump snapshot year, for the composer only. It ignores lyricists/librettists of vocal and stage works, editions, and national deviations. Missing death → `unknown_death` (not `living`).
- **Work pages, not score files.** IMSLP links are work pages; `has_files` is not a verified inventory.
- **Tags are research aids and often wrong.** `force_family` and `style_tags` come from IMSLP categories, scraped General Information, Wikidata QIDs, title heuristics, and LLMs. Prefer `force_family_src` / `style_tags_src` and verify on IMSLP/Wikidata.
- Trust order for force: `imslp_tags` > `imslp_geninfo` > `title` > `llm` / `llm_luna_xhigh` / `llm_grok`. The remaining ~370 LLM labels come from title-only passes (`llm`: gpt-6-luna at low reasoning) and residual passes; no accuracy evaluation exists yet.
- Rows with `qa_flags` deserve a manual look before you rely on their dates or identity.
- IMSLP match / hosted scores ≠ public domain in your jurisdiction.
- `unverified_heuristic` IMSLP matches need caution.
- Never overwrite dumps — add a new `rNNN` + meta file.

## Building / enriching

```bash
pip install -r requirements.txt
python scripts/build_dump.py --date YYYY-MM-DD
python scripts/promote_revision.py --from-dump … --to rNNN

# Complete IMSLP categories (network, ~25 min, resumable) → re-derive force
python scripts/refetch_work_categories.py --dump r008
python scripts/remap_force.py --from-dump r008 --to r009 --refresh-categories

# Wikidata dates / scope / qa_flags from cache (offline)
python scripts/remap_composers.py --from-dump r009 --to r010

python scripts/export_viewer_json.py --dump r010
python scripts/check_release.py --dump r010 --viewer-data viewer/data
```

Caches: `data/cache/` (gitignored).
