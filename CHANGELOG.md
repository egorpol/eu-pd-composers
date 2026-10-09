# Changelog

All notable changes to this project are documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).  
Tool releases: [SemVer](https://semver.org/) Git tags.  
Dump snapshots: revision `dump_id` (`rNNN`) — see [VERSIONING.md](VERSIONING.md).

## [Unreleased]

### Deferred
- Louis Barron (1920–1989) missing: the Wikipedia list links both Barrons to the duo article and swaps their years
- Unresolved identities: John Mitchell (b. 1941, unlinked on the list), Robert Graham Manson (`no_composer_occupation`); 7 `unverified_heuristic` matches
- Upstream fixes on Wikipedia (list links) and Wikidata (4 wrong P839 values)
- IMSLP style / copyright-flag / first-publication categories as work columns (already in the page-category cache)
- Viewer: label-source filter, PD-year range presets, client-side PD year, URL state
- Scheduled crawl workflow (refresh → PR with diff), LLM decision ledger
- LLM style fill-ins
- `--work-files` for `has_files`

## [3.2.0] - 2026-10-09

Identity release. Schema stays **3** (one added column, two added status values). Shipped dump: **r012** (via r011).

### Fixed
- Name-guessed IMSLP categories were never checked against the person: they are now compared with IMSLP's own life dates and Wikipedia link. 279 of 298 guesses confirmed, 10 rejected as someone else's page (e.g. composer Karl Marx → the philosopher, William Wordsworth → the poet), 7 still unverified
- Four Wikidata P839 links point at a different person's IMSLP page (John White, Jindřich Feld → his father, John Lambert, Karl Höller → Georg Höller); rejected with `imslp_p839_wrong`
- Category collisions: the younger Leo Smit (1921–1999) and William Reed (1910–2002) no longer receive the works of their PD namesakes; no IMSLP page is listed under two composers
- Six composers pointed at the wrong Wikidata item (politician, painter, writer, disambiguation pages, a duo); re-keyed via overrides with Wikidata fields re-derived. Jaroslav Kvapil (†1958) is no longer marked `pd`

### Added
- `data/overrides/composers.tsv` + `scripts/overrides.py` / `apply_overrides.py`: validated, sourced, hand-reviewed overrides applied last (re-key with re-derivation, value overrides, drops)
- `scripts/refetch_composer_pages.py` (IMSLP composer pages into `imslp_cat_page` cache), `scripts/imslp_identity.py`, `scripts/remap_imslp_matches.py`
- Column `imslp_match_evidence`; statuses `rejected_heuristic`, `rejected_p839`; methods `exact_name+life_dates`, `exact_name+wikilink`; qa flags `imslp_dates_conflict`, `imslp_p839_wrong`, `manual_override`
- `check_release.py` gates: an IMSLP category held by more than one active composer; works owned by composers without an active match
- Dumps **r011** (overrides) and **r012** (identity check)

## [3.1.0] - 2026-10-09

Data-correctness release. Schema stays **3** (two added columns). Shipped dump: **r010** (via r009).

### Fixed
- IMSLP work categories were truncated: one `cllimit=100` was shared by 20 pages and continuation was ignored, so 62.8% of works were stored with no categories and the empty result was cached. Categories are now fetched by page id with continuation (`imslp_page_cats` cache); empty cells drop to ~1% and `imslp_tags` label 98.3% of works (LLM-derived labels 43.8% → 1.3%)
- `force_family` category rules matched substrings: every flute work became `guitar` ("lute"), string orchestras `solo_instrument`, harpsichord/harmonium `solo_instrument`, vocal duets `solo_instrument`, string trios `other`. `For …` categories are now parsed by parts and whole words
- `For 2 players` / `Quartets` no longer override explicit instrumentation, so violin + piano works are `solo_instrument` as documented (≈1,200 works moved from `chamber`)
- Wikidata dates ignored claim rank: deprecated values are skipped and preferred rank wins (Kabalevsky †1987, Rieding †1916); deprecated-only death years no longer ship (Tania León is living)
- Composers who also wrote film music were `scope_class=film_media` and hidden by the viewer default (Prokofiev, Gershwin, Weill, Honegger and 241 others); they are now `classical_core`
- GenInfo instrumentation mapper: voice + orchestra is `solo_voice`, double bass is not a voice, harpsichord is `keyboard_other`
- IMSLP pages merged into another page (now redirects) keep their previous categories instead of becoming empty

### Added
- Columns `is_film_composer` and `qa_flags` (rank conflicts, imprecise dates, implausible lifespans, non-human or non-composer QIDs, years taken from the Wikipedia list)
- `scripts/refetch_work_categories.py` (resumable complete category crawl), `remap_force.py --refresh-categories`, `scripts/remap_composers.py`
- `check_release.py` gates: empty-category share, stored `imslp_tags` labels vs current rules, rollups, orphan works, PD arithmetic; warnings for shared IMSLP pages, `qa_flags`, LLM share
- Offline pytest suite (`tests/`) and `.github/workflows/checks.yml` running tests + release gates on every PR
- Dumps **r009** (categories + force) and **r010** (composer fixes)

### Changed
- `docs/PIPELINE.md`: documents the `For …` conventions and the actual LLM history (bulk title-only pass ran gpt-6-luna at low reasoning)
- Dump meta records category-refresh counts and force source/family transitions

## [3.0.0] - 2026-09-25

Schema **v3** dual composers/works dumps, Wikidata + IMSLP enrichment, static filter viewer on GitHub Pages. Shipped dump: **r008**.

### Added
- Schema **v3** dual dump: `composers_*.tsv` + `works_*.tsv`
- `scripts/wikidata_enrich.py` — Wikipedia title → QID, citizenship, styles, P839
- `scripts/imslp.py` — IMSLP match (P839 first) + paginated work listing
- `scripts/common.py` — shared session, pipe lists, cache, revision ids
- `scripts/force_family.py` + enrich / GenInfo / remap / residual LLM scripts
- `scripts/export_viewer_json.py` + `scripts/check_release.py`
- Static filter viewer under `viewer/`
- GitHub Pages deploy (`.github/workflows/pages.yml` → https://egorpol.github.io/eu-pd-composers/)
- `docs/PIPELINE.md` — Mermaid overview of the build path
- Revision dumps through **r008** (force/ID/PD fixes; verified style QID map)

### Changed
- `scripts/build_dump.py` rebuilt for schema 3 (breaking vs schema 2 column set)
- Demoted wiki `Nationality` / notables / remarks to structured + `legacy_*` fields
- Missing death year → `eu_pd_status=unknown_death` (not `living`)
- Force rules ignore `(arr)` categories; opera/voice beat concerto; orchestra beats piano reductions
- Pages deploys from **`main`** only

### Removed
- Legacy notebooks (`PublicDomainSheetMusicFinder.ipynb`, `imslp_extract.ipynb`)
- `experimental/` unfinished 2025 utils park
- Older dump snapshots from the working tree (recoverable from git history)

## [Unreleased] historical notes toward **v2.0.0**

### Added
- `scripts/build_dump.py` — dated Wikipedia / pageviews / IMSLP dump builder (never overwrites)
- `scripts/heartbeat.py` — alive/progress logs for long scrapes
- `data/` layout with dump manifest
- `VERSIONING.md` — separate tool SemVer vs dump calendar ids
- `requirements.txt`, `.gitattributes` (LF)
- Schema v2 dump `composers_2026-09-24.tsv` (3371 rows)

### Changed
- Project renamed to **eu-pd-composers** (repo: `egorpol/eu-pd-composers`)
- Oriented around **versioned data dumps** + scripts as build reference

### Removed
- `llm_parse_local.ipynb` (local LM Studio path)

## [1.1.0] - 2024-04-17

Published on GitHub as tag `v1.1` (release title `v.1.1`).

### Added
- `imslp_extract.ipynb` — list works for a selected IMSLP composer
- Experimental `llm_parse_local.ipynb`

### Changed
- Refactors / docs on the main scrape notebook

## [1.0.0] - 2024-04-17

Published on GitHub as tag `v1.0`. Data files first appeared in-repo earlier (2023-08); this is the first formal release tag.

### Added
- Wikipedia → pageviews → IMSLP existence pipeline (notebook)
- `composers.tsv` / `composers_imslp.tsv`
- MIT license

[Unreleased]: https://github.com/egorpol/eu-pd-composers/compare/v3.0.0...HEAD
[3.0.0]: https://github.com/egorpol/eu-pd-composers/compare/v1.1...v3.0.0
[1.1.0]: https://github.com/egorpol/eu-pd-composers/compare/v1.0...v1.1
[1.0.0]: https://github.com/egorpol/eu-pd-composers/releases/tag/v1.0
