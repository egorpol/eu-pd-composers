# eu-pd-composers

Find notable 20th-century classical composers whose works are likely safer to study or redistribute in EU-style life+70 regimes, then point at available IMSLP material.

**Live viewer:** [egorpol.github.io/eu-pd-composers](https://egorpol.github.io/eu-pd-composers/)

**Current direction:** versioned **composer + works** dumps (schema v3) + a static **filter viewer**. Tool line: **v3.4.0** (schema 3). Current dump: **r013**.

See [CHANGELOG.md](CHANGELOG.md) and [VERSIONING.md](VERSIONING.md).

## Layout

```
data/                            # versioned TSV dumps + dump_meta_*.json
viewer/                          # static filter UI (GitHub Pages)
scripts/pipeline.py                 # one command: refresh (crawl + all stages) or derive (offline stages)
scripts/diff_dumps.py               # readable diff between two revisions (PR body for refreshes)
scripts/build_dump.py               # Wikipedia → Wikidata → IMSLP scrape
scripts/refetch_work_categories.py  # complete IMSLP work categories for a dump (resumable)
scripts/remap_force.py              # offline: refresh categories, re-derive force_family
scripts/remap_composers.py          # offline: Wikidata dates, scope, qa_flags, PD fields
scripts/apply_overrides.py          # offline: hand-reviewed fixes from data/overrides/ (applied last)
scripts/refetch_composer_pages.py   # IMSLP composer pages (life dates, biography link) into cache
scripts/remap_imslp_matches.py      # offline: verify IMSLP matches by life dates, resolve collisions
scripts/remap_work_evidence.py      # offline: IMSLP style / first publication / copyright flags / librettists per work
scripts/carry_forward.py            # offline: keep earlier LLM / GenInfo decisions across a fresh crawl
scripts/export_viewer_json.py       # dump → viewer/data JSON
scripts/check_release.py            # release gates (also run in CI)
scripts/force_family.py
scripts/wikidata_enrich.py
scripts/imslp.py
scripts/common.py
tests/                              # offline regression tests (pytest)
docs/PIPELINE.md
```

- Dumps: [`data/README.md`](data/README.md)
- Pipeline: [`docs/PIPELINE.md`](docs/PIPELINE.md)
- Viewer: [`viewer/README.md`](viewer/README.md)

## Sources

- **Names:** Wikipedia list of 20th-century classical composers (+ optional pageviews)
- **Structured bio / IDs:** Wikidata
- **Scores:** IMSLP work pages (not per-file editions yet)

Public-domain status is a **heuristic**, not legal clearance.

**Force / style tags are imperfect.** They mix IMSLP categories, scraped fields, Wikidata maps, heuristics, and LLM guesses. Many will be wrong or incomplete — treat them as filters for exploration, not ground truth. Check `*_src` columns and verify on the source sites when it matters.


## Setup

```bash
pip install -r requirements.txt        # or requirements-dev.txt to run tests
python -m pytest tests -q
```

## Usage

```bash
# Whole chain in one command (see docs/PIPELINE.md and docs/AUTOMATION.md)
python scripts/pipeline.py refresh --base r013          # network crawl + all stages → next revision
python scripts/pipeline.py derive --from-dump r013 --to r014   # offline stages only

# Refresh viewer JSON + release gates + local preview
python scripts/export_viewer_json.py --dump r013
python scripts/check_release.py --dump r013 --viewer-data viewer/data
python -m http.server 8080 --directory viewer
```

## GitHub Pages

The filter UI is published at **https://egorpol.github.io/eu-pd-composers/**.

Workflow [`.github/workflows/pages.yml`](.github/workflows/pages.yml) deploys the `viewer/` folder on pushes to **`main`** that change `viewer/**` (or by manual dispatch; Pages source: GitHub Actions). [`.github/workflows/checks.yml`](.github/workflows/checks.yml) runs the tests and release gates on every PR. Local preview uses the same files via `python -m http.server` as above. Monthly dump refresh (manual / scheduled PR): [`docs/AUTOMATION.md`](docs/AUTOMATION.md).

## License

Code: MIT (see `LICENSE`). Linked Wikipedia / IMSLP content remains under their respective terms.
