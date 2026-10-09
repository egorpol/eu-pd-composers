# Data dumps

Versioned snapshots. Treat TSV + `dump_meta_*.json` as the product; scripts under `scripts/` are how they were built.

Pipeline diagram: [`docs/PIPELINE.md`](../docs/PIPELINE.md).

## Current dump (schema v3)

| File | Rows | dump_id |
|---|---|---|
| `composers_r012.tsv` | 3368 | `r012` |
| `works_r012.tsv` | 28980 | `r012` |
| `dump_meta_r012.json` | — | companion meta |

Only the **latest** revision is kept in `data/` in git. Older `rNNN` / calendar dumps are recoverable from **git history** (not from gitignored `data/cache/`). Prefer `r012` for the viewer (`scripts/export_viewer_json.py --dump r012`).

### What changed since r010

- **r011 — reviewed overrides** from [`overrides/composers.tsv`](overrides/composers.tsv): six rows pointed at the wrong Wikidata person because the Wikipedia list links a plain title (Michael Howard → the politician, Allen Sapp → a painter, Jaroslav Kvapil → the writer, Ricardo Castro and John Mitchell → disambiguation pages, Bebe Barron → the duo). They are re-keyed and their Wikidata fields re-derived from the right entity; Kvapil (†1958) is therefore no longer `pd`. r011 is an intermediate step and still contains the category collisions that r012 resolves.
- **r012 — IMSLP identity check.** Name-guessed IMSLP categories are compared with the life dates and Wikipedia link on IMSLP's own composer page. 279 of 298 guesses are confirmed (`matched`), 10 belonged to someone else (`rejected_heuristic`, e.g. the composer Karl Marx had the philosopher's page) and 7 stay `unverified_heuristic`. Four Wikidata P839 links point at a different person's page (`rejected_p839`, e.g. Jindřich Feld → his father). Both category collisions are resolved (Leo Smit, William Reed). 13 work rows that belonged to the wrong person are dropped; no IMSLP category is shared any more.

### What changed since r008

- **r009 — complete IMSLP categories.** r008's category fetch was truncated (one `cllimit=100` shared by 20 pages, continuation ignored), so 62.8% of works had no categories and most force labels fell back to title-only LLM guesses. r009 rebuilds `imslp_genre_categories` from a complete per-page crawl (empty: ~1%) and re-derives `force_family` with parsed `For …` rules (fixes e.g. flute→guitar). `imslp_tags` now label 98.3% of works; LLM-derived labels dropped from 43.8% to 1.3%. Instrument + one keyboard (violin/piano sonatas) is `solo_instrument`; 3+ unaccompanied voices are `choral`.
- **r010 — composer fixes from cached Wikidata.** Deprecated claims are ignored and preferred rank wins (Kabalevsky †1987, Rieding †1916; three deprecated-only death years removed → `unknown_death`). Composers who also wrote film music are `classical_core` with `is_film_composer=true` (Prokofiev, Gershwin, Weill, Honegger were hidden by the default viewer filter). New `qa_flags` column marks rows to double-check.

Each `dump_meta_rNNN.json` records counts and before/after transitions for its step.

## IMSLP match status and evidence (r012)

| `imslp_match_status` | `imslp_match_method` | Meaning |
|---|---|---|
| `matched` | `wikidata_p839` | Wikidata P839 names the category and IMSLP's dates do not contradict it |
| `matched` | `exact_name+life_dates` | Name guess confirmed: IMSLP's birth/death years agree (±1) |
| `matched` | `exact_name+wikilink` | Name guess with a small date disagreement, confirmed by IMSLP's Wikipedia link |
| `unverified_heuristic` | `exact_name` | Name guess IMSLP's page cannot confirm (no comparable years) |
| `rejected_heuristic` | `exact_name` | Name guess whose IMSLP page is a different person; works removed, `imslp_url` empty |
| `rejected_p839` | `wikidata_p839` | Wikidata P839 points at a different person's page; works removed, flag `imslp_p839_wrong` |
| `not_found` | — | No category |

`imslp_match_evidence` (pipe list) records why: `dates_agree`, `dates_conflict_weak` (a year pair agrees, or all gaps ≤ 10 years), `dates_conflict_strong` (no pair agrees and a gap > 10 years), `dates_unknown`, `wikilink_agree` / `wikilink_differs`, `performer_page`, `page_missing`, `collision_won` / `collision_lost`. Small disagreements keep the match and add `imslp_dates_conflict` to `qa_flags`.

## Overrides

Hand-reviewed fixes live in [`overrides/composers.tsv`](overrides/composers.tsv) (see [`overrides/README.md`](overrides/README.md)) and are applied last with `scripts/apply_overrides.py`, so they survive rebuilds. Touched rows carry `manual_override` in `qa_flags`.

## Columns added in r010

| Column | Values |
|---|---|
| `is_film_composer` | `true` / `false` — Wikidata occupation includes film composer |
| `qa_flags` | pipe list, empty if none: `birth_rank_conflict`, `death_rank_conflict` (best-rank Wikidata claims disagree), `birth_imprecise`, `death_imprecise` (decade/century precision; the year is the interval's last year), `implausible_lifespan`, `death_before_birth`, `not_human` (QID is not a person), `no_composer_occupation` (QID may be a different person), `birth_from_list`, `death_from_list` (year from the Wikipedia list, not Wikidata); since r012 also `imslp_dates_conflict`, `imslp_p839_wrong`, `manual_override` |

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

# Hand-reviewed overrides (offline), then IMSLP identity check (composer pages: network, ~1 min)
python scripts/apply_overrides.py --from-dump r010 --to r011
python scripts/refetch_composer_pages.py --dump r011
python scripts/remap_imslp_matches.py --from-dump r011 --to r012

python scripts/export_viewer_json.py --dump r012
python scripts/check_release.py --dump r012 --viewer-data viewer/data
```

Caches: `data/cache/` (gitignored).
