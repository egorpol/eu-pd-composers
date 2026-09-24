# eu-pd-composers

Find notable 20th-century classical composers whose works are likely safer to study or redistribute in EU-style life+70 regimes, then point at available IMSLP material.

**Current direction:** versioned **composer + works** dumps (schema v3), deterministic Python builders. Style/`force_family` review comes after the IMSLP inventory. Filtering UI later.

See [CHANGELOG.md](CHANGELOG.md) and [VERSIONING.md](VERSIONING.md). Tool line: **v3.0.0-dev** (schema 3); v2.0.0 tagged dump remains as schema-2 history.

## Layout

```
data/                            # versioned TSV dumps + dump_meta_*.json
data/cache/                      # HTTP cache (gitignored)
scripts/build_dump.py            # orchestrator (Wikipedia → Wikidata → IMSLP)
scripts/wikidata_enrich.py       # QID resolve + claims → columns
scripts/imslp.py                 # P839/heuristic match + work listing
scripts/common.py                # shared HTTP / pipe lists / paths
scripts/heartbeat.py             # alive/progress logs
requirements.txt
CHANGELOG.md
VERSIONING.md
PublicDomainSheetMusicFinder.ipynb   # original scrape (historical)
imslp_extract.ipynb                  # one-composer notebook (historical)
experimental/                        # unfinished 2025 refactor
```

See [`data/README.md`](data/README.md) for dump dates, schema, and how to build.

## Sources

- **Names:** Wikipedia list of 20th-century classical composers (+ optional pageviews)
- **Structured bio / IDs:** Wikidata (citizenship, dates, styles, IMSLP P839, …)
- **Scores:** IMSLP composer category → work pages (not per-file editions yet)

Public-domain status is a **heuristic**, not legal clearance.

## Setup

```bash
pip install -r requirements.txt
```

## Usage

```bash
# Smoke
python scripts/build_dump.py --limit 5 --no-pageviews --dry-run

# Sample / full dated dump (never overwrites)
python scripts/build_dump.py --date YYYY-MM-DD
```

Long runs log a heartbeat every 30s (`--heartbeat-interval`). Responses cache under `data/cache/`.

## License

Code: MIT (see `LICENSE`). Linked Wikipedia / IMSLP content remains under their respective terms.
