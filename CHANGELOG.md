# Changelog

All notable changes to this project are documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).  
Tool releases: [SemVer](https://semver.org/) Git tags.  
Dump snapshots: revision `dump_id` (`rNNN`) — see [VERSIONING.md](VERSIONING.md).

## [Unreleased]

### Fixed
- `remap_work_evidence.py` no longer clears a work's IMSLP evidence when its page is missing from the local `imslp_page_cats` cache: the prior cells are kept, counted (`missing_cache`, `kept_prior`, `no_pageid` in the report and dump meta) and logged as a warning. An offline `derive` from r015 with an incomplete local cache had wiped 159 cells on 63 works

### Deferred
- Louis Barron (1920–1989) missing: the Wikipedia list links both Barrons to the duo article and swaps their years
- Unresolved identities: John Mitchell (b. 1941, unlinked on the list), Robert Graham Manson (`no_composer_occupation`); 7 `unverified_heuristic` matches
- Upstream fixes on Wikipedia (list links) and Wikidata (4 wrong P839 values)
- Enable the monthly refresh schedule once IMSLP has been notified (`refresh.yml`, commented out)
- Lyricist-aware PD flags: librettist life dates (names are now in `imslp_librettists`)
- LLM force decision ledger (today: force labels carry forward from the previous revision)
- Dataset licence + `CITATION.cff`; gold set and error rates
- `--work-files` for `has_files`

## [3.5.0] - 2026-10-10

LLM style release. Schema stays **3** (four added composer columns). Shipped dump: **r015**.

### Added
- All-composer LLM style labelling pass (`llm_style_pass.py`) with a committed, append-only decision ledger (`data/llm_ledger/`): `gpt-6.1-sol` @low (Codex) and `grok-4.7-low` (Cursor), closed-book (`style-v1`) and Wikipedia-grounded (`style-v2`: lead + own knowledge) conditions, 3,369 composers, 0 failures. Agreement report vs Wikidata and IMSLP periods (`style_agreement.py`)
- Dump **r015** = r014 + the replay below (2,842 composers with consensus styles; 74 legacy LLM `style_tags` retired)
- Offline grounded style consensus replay from the committed ledger (`apply_llm_styles.py`, last pipeline stage): four `llm_style_*` composer columns, policy/hash/count provenance, and latest-row selection. Schema stays 3
- Viewer consensus facet matching, agreed period and per-model votes; LLM period slugs share IMSLP facet names. Policy and model names are stored once in the manifest

### Changed
- `style_tags` / `style_tags_src` are Wikidata-only: legacy `llm*` tags are cleared and carry-forward no longer restores or reports them. Dump diffs count LLM consensus, and release checks validate its vocabulary and legacy retirement

## [3.4.2] - 2026-10-09

### Fixed
- The first GitHub-runner refresh failed after its 2-hour crawl. `apply_overrides.py` (offline) needs the Wikidata entities of the people that overrides re-key to, and a cold crawl only fetches the entities the Wikipedia list links to. The new `scripts/refetch_override_entities.py` fetches the re-key targets and their citizenship countries, and `pipeline.py refresh` runs it **first**, as a preflight, so a bad overrides file fails in seconds rather than after the crawl

## [3.4.1] - 2026-10-09

### Fixed
- IMSLP politeness: `build_dump.py` paced IMSLP at 0.1–0.15 s between requests, faster than the announced ≤ 1 request/s. `common.request_json` now enforces a per-host minimum interval (IMSLP 1.0 s, `EU_PD_IMSLP_MIN_INTERVAL`) on every call path and honours `Retry-After` on 429/503. A cold refresh therefore takes about 2–2.5 hours

## [3.4.0] - 2026-10-09

Viewer release; data unchanged (dump **r013**).

### Changed
- **One live EU public-domain filter** replaces the stored "EU PD status" (frozen at build year) and the overlapping live presets: PD now / enters PD next 1 January / in 2–5 years / later / no death date, computed in the browser. The results column shows "PD" or the year a composer enters PD
- **One Style filter with a Style source selector** (Wikidata · IMSLP works · LLM) replaces "Movement" and "Period": movements and IMSLP periods in one list in rough chronological order, with live counts per selected source. Choose sources to compare them
- Filters are grouped into **Composer** and **Works on IMSLP**. The panel is collapsible (closed by default below 900 px, showing the active-filter count). Facet headers align, with hints behind ⓘ. All lists share one height, it is single-column on phones, and there are no overlapping controls at any width
- IMSLP period aliases merged (`Romántico` → Romantic, `Traditional` → Traditional (folk)); old shared links map to the new filters

## [3.3.0] - 2026-10-09

Automation release. Schema stays **3** (four added works columns). Shipped dump: **r013**.

### Added
- `scripts/pipeline.py`: `refresh` (crawl + every stage in a staging dir, promotes one revision, exports the viewer, gates) and `derive` (offline stages). Failures promote nothing and keep the staging dir
- `scripts/carry_forward.py`: earlier LLM force labels, GenInfo fields and LLM styles survive a fresh crawl, joined on stable keys and never over a category-derived label; lost decisions are reported
- `scripts/diff_dumps.py`: review report between revisions (PD flips, identity changes, works added/removed, label transitions, schema changes)
- `check_release.py --against rPREV`: fails on >3% composer change, >5% works drop, or a lost column
- `.github/workflows/refresh.yml` (manual; monthly schedule ready but commented out) and `docs/AUTOMATION.md`. The job validates itself (tests + gates) and opens a PR with the diff
- Works columns `imslp_style`, `imslp_first_published`, `imslp_copyright_flags`, `imslp_librettists` (`scripts/remap_work_evidence.py`)
- Viewer:
  - label-source filter (all / no LLM / category-based);
  - IMSLP style facet; work filters combine per work;
  - matching-works count and sort;
  - PD presets computed in the browser ("PD now", "enters PD next 1 January", "within 5 years");
  - IMSLP copyright badges and first-publication year;
  - shareable URL state;
  - unknown values sort last;
  - plain-language IMSLP match statuses

### Changed
- `EU_PD_DATA_DIR` / `EU_PD_CACHE_DIR` env overrides; the cache no longer moves with the data dir
- PD reference year is recorded at crawl time and propagated (`pd_reference_year`), so a January refresh computes the new year
- All remap steps are re-runnable: string-safe reads, `dump_date` stamped only where the column exists, overrides skip re-keys already applied, later-stage QA flags and collision evidence preserved
- Viewer export: missing pageviews are `null` (not 0); the pageview window label comes from the data

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
