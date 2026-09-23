# Public Domain Sheet Music Finder

Find notable 20th-century classical composers whose works are likely safer to study or redistribute in EU-style life+70 regimes, then point at available IMSLP material.

**Current direction:** ship a **versioned data dump** + keep notebooks as **build reference**. Filtering UI and richer work-level tags come later.

## Layout

```
data/                         # versioned TSV dumps + manifest
  README.md
  composers.tsv
  composers_imslp.tsv
PublicDomainSheetMusicFinder.ipynb   # original end-to-end scrape (reference)
imslp_extract.ipynb                  # list works for one IMSLP composer page
llm_parse_local.ipynb                # optional local-LLM Wikipedia helpers (legacy)
experimental/                        # unfinished utils refactor — ignore for normal use
```

See [`data/README.md`](data/README.md) for dump dates, schema, and caveats.

## Sources

- **Names / bio fields:** Wikipedia list of 20th-century classical composers (+ pageviews for ranking)
- **Scores / files:** IMSLP (existence links in dump; work listing via `imslp_extract.ipynb`)

Public-domain status is a **heuristic**, not legal clearance. Editions, arrangements, libretti, and first-publication rules can still block redistribution.

## Setup

```bash
pip install requests beautifulsoup4 pandas tqdm numpy
```

Optional for `llm_parse_local.ipynb`: a local OpenAI-compatible endpoint (e.g. LM Studio).

## Usage

1. Browse / filter the TSVs under `data/` (spreadsheet, pandas, or a future UI).
2. Run root notebooks only if you need to **rebuild** a dump — then write a **new** dated file and update `data/README.md`.
3. Ignore `experimental/` until the refactor is finished.

## License

Code: MIT (see `LICENSE`). Linked Wikipedia / IMSLP content remains under their respective terms.
