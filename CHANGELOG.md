# Changelog

All notable changes to this project are documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).  
Tool releases: [SemVer](https://semver.org/) Git tags.  
Dump snapshots: dated `dump_id` — see [VERSIONING.md](VERSIONING.md).

## [Unreleased] → targeting **v2.0.0**

### Added
- `scripts/build_dump.py` — dated Wikipedia / pageviews / IMSLP dump builder (never overwrites)
- `scripts/heartbeat.py` — alive/progress logs for long scrapes
- `data/` layout with dump manifest
- `VERSIONING.md` — separate tool SemVer vs dump calendar ids
- `requirements.txt`, `.gitattributes` (LF)

### Changed
- Project renamed to **eu-pd-composers** (repo: `egorpol/eu-pd-composers`)
- Oriented around **versioned data dumps** + scripts as build reference
- Unfinished 2025 utils refactor parked under `experimental/`

### Removed
- `llm_parse_local.ipynb` (local LM Studio path)

### Planned before tagging v2.0.0
- Fresh dated dump with `eu_pd_year` + `dump_meta_*.json` (`schema_version: 2`)
- Single primary TSV (decision pending)
- Minimal filter UI (may slip to 2.1)

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

[Unreleased]: https://github.com/egorpol/eu-pd-composers/compare/v1.1...HEAD
[1.1.0]: https://github.com/egorpol/eu-pd-composers/compare/v1.0...v1.1
[1.0.0]: https://github.com/egorpol/eu-pd-composers/releases/tag/v1.0
