# Changelog

All notable changes to this project are documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).  
Tool releases: [SemVer](https://semver.org/) Git tags.  
Dump snapshots: dated `dump_id` — see [VERSIONING.md](VERSIONING.md).

## [Unreleased] → targeting **v3.0.0** (schema 3)

### Added
- Schema **v3** dual dump: `composers_*.tsv` + `works_*.tsv` (Opus-aligned P0)
- `scripts/wikidata_enrich.py` — Wikipedia title → QID, citizenship, styles, P839
- `scripts/imslp.py` — IMSLP match (P839 first) + paginated work listing
- `scripts/common.py` — shared session (no broken proxies), pipe lists, cache
- Sample dump `2026-09-25` (`--limit 5`, 5 composers / 364 works)
- Full dump `2026-09-26` (3371 composers / 29021 works)
- `scripts/force_family.py` + `scripts/enrich_dump.py` — Opus force_family / genre_form mapping offline
- Enriched dump `2026-09-27` (same inventory + force_family rollups; 59% works classified)
- `scripts/llm_force_family.py` — Codex + gpt-6-luna fill for remaining unclassified works
- Pilot dump `2026-09-28` (80 LLM rows); full LLM dump `2026-09-29` (gpt-6-luna, 98.6%)
- Residual Grok pass → dump `2026-09-30` (394 rows via grok-4.7-high; 100% classified)
- `docs/PIPELINE.md` — Mermaid overview of wiki/Wikidata/IMSLP → enrich → LLM

### Changed
- `scripts/build_dump.py` rebuilt for schema 3 (breaking vs schema 2 column set)
- Demoted wiki `Nationality` / notables / remarks to structured + `legacy_*` fields

### Deferred
- Better coverage of empty IMSLP genre cats (re-fetch / looser filters)
- LLM style fill-ins
- `--work-files` for `has_files`
- Full 3371-row inventory dump
- Filter UI

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
- Unfinished 2025 utils refactor parked under `experimental/`

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

[Unreleased]: https://github.com/egorpol/eu-pd-composers/compare/v1.1...HEAD
[1.1.0]: https://github.com/egorpol/eu-pd-composers/compare/v1.0...v1.1
[1.0.0]: https://github.com/egorpol/eu-pd-composers/releases/tag/v1.0
