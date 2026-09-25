# eu-pd-composers

Find notable 20th-century classical composers whose works are likely safer to study or redistribute in EU-style life+70 regimes, then point at available IMSLP material.

**Live viewer:** [egorpol.github.io/eu-pd-composers](https://egorpol.github.io/eu-pd-composers/)

**Current direction:** versioned **composer + works** dumps (schema v3) + a static **filter viewer**. Tool line: **v3.0.0** (schema 3).

See [CHANGELOG.md](CHANGELOG.md) and [VERSIONING.md](VERSIONING.md).

## Layout

```
data/                            # versioned TSV dumps + dump_meta_*.json
viewer/                          # static filter UI (GitHub Pages)
scripts/build_dump.py            # Wikipedia → Wikidata → IMSLP scrape
scripts/enrich_dump.py           # offline force_family / rollups
scripts/export_viewer_json.py    # dump → viewer/data JSON
scripts/force_family.py
scripts/llm_force_family.py
scripts/wikidata_enrich.py
scripts/imslp.py
scripts/common.py
scripts/heartbeat.py
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
pip install -r requirements.txt
```

## Usage

```bash
# Scrape / enrich dumps (see data/README.md)
python scripts/build_dump.py --date YYYY-MM-DD
python scripts/enrich_dump.py --from-dump YYYY-MM-DD --date YYYY-MM-DD

# Refresh viewer JSON + local preview
python scripts/export_viewer_json.py --dump r008
python scripts/check_release.py --dump r008 --viewer-data viewer/data
python -m http.server 8080 --directory viewer
```

## GitHub Pages

The filter UI is published at **https://egorpol.github.io/eu-pd-composers/**.

Workflow [`.github/workflows/pages.yml`](.github/workflows/pages.yml) deploys the `viewer/` folder on every push to **`main`** (Pages source: GitHub Actions). Local preview uses the same files via `python -m http.server` as above.

## License

Code: MIT (see `LICENSE`). Linked Wikipedia / IMSLP content remains under their respective terms.
