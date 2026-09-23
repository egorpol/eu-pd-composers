# eu-pd-composers

Find notable 20th-century classical composers whose works are likely safer to study or redistribute in EU-style life+70 regimes, then point at available IMSLP material.

**Current direction:** ship a **versioned data dump** + keep notebooks as **build reference**. Filtering UI and richer work-level tags come later.

See [CHANGELOG.md](CHANGELOG.md) and [VERSIONING.md](VERSIONING.md). Next tool release: **v2.0.0** (not 0.2 — GitHub already has `v1.0` / `v1.1`).

## Layout

```
data/                         # versioned TSV dumps + manifest
scripts/build_dump.py         # current dump builder (preferred)
scripts/heartbeat.py          # alive/progress logs for long scrapes
requirements.txt
CHANGELOG.md
VERSIONING.md
PublicDomainSheetMusicFinder.ipynb   # original end-to-end scrape (historical reference)
imslp_extract.ipynb                  # list works for one IMSLP composer page
experimental/                        # unfinished 2025 refactor — superseded by scripts/
```

See [`data/README.md`](data/README.md) for dump dates, schema, and how to build a new dump.

## Sources

- **Names / bio fields:** Wikipedia list of 20th-century classical composers (+ pageviews for ranking)
- **Scores / files:** IMSLP (existence links in dump; work listing via `imslp_extract.ipynb`)

Public-domain status is a **heuristic**, not legal clearance. Editions, arrangements, libretti, and first-publication rules can still block redistribution.

## Setup

```bash
pip install -r requirements.txt
```

## Usage

1. Browse / filter the TSVs under `data/` (spreadsheet, pandas, or a future UI).
2. Rebuild with `python scripts/build_dump.py` (writes dated files; see `data/README.md`).
   Long runs log a heartbeat every 30s by default (`--heartbeat-interval`).
3. Root notebooks remain as historical reference only.

## License

Code: MIT (see `LICENSE`). Linked Wikipedia / IMSLP content remains under their respective terms.
