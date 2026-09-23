# Changelog

All notable changes to this project are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning aims for [SemVer](https://semver.org/) once **0.2.0** ships.

## [Unreleased]

### Added
- `scripts/build_dump.py` — dated Wikipedia / pageviews / IMSLP dump builder (does not overwrite existing dumps)
- `scripts/heartbeat.py` — periodic alive/progress logs for long scrapes
- `data/` layout with dump manifest (`data/README.md`)
- `requirements.txt`, `.gitattributes` (LF)

### Changed
- Repo oriented around **versioned data dumps** + scripts as build reference
- Unfinished 2025 utils refactor parked under `experimental/`

### Removed
- `llm_parse_local.ipynb` (local LM Studio / OpenAI-compatible Wikipedia Q&A path)

### Planned for 0.2.0
- Fresh dated dump (`composers_YYYY-MM-DD.tsv` + meta JSON) with `eu_pd_year`
- Repo rename (name TBD — see discussion / README notes)
- Minimal filter UI over the dump
- Drop or freeze legacy root notebook as pure historical reference

## [0.1.1] - 2024-04

### Added
- `imslp_extract.ipynb` — list works for a selected IMSLP composer
- Experimental `llm_parse_local.ipynb` (removed in Unreleased)

### Changed
- Refactors / docs updates on `PublicDomainSheetMusicFinder.ipynb`

## [0.1.0] - 2023-08-02

### Added
- Initial Wikipedia → pageviews → IMSLP existence pipeline (notebook)
- `composers.tsv` and `composers_imslp.tsv` data dumps
- MIT license

[Unreleased]: https://github.com/klirr2007/PublicDomainSheetMusicFinder/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/klirr2007/PublicDomainSheetMusicFinder/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/klirr2007/PublicDomainSheetMusicFinder/releases/tag/v0.1.0
