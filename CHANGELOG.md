# Changelog

All notable changes to this project are documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).  
Tool releases: [SemVer](https://semver.org/) Git tags.  
Dump snapshots: revision `dump_id` (`rNNN`) — see [VERSIONING.md](VERSIONING.md).

## [Unreleased]

### Deferred
- Better coverage of empty IMSLP genre cats (re-fetch / looser filters)
- LLM style fill-ins
- `--work-files` for `has_files`

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
