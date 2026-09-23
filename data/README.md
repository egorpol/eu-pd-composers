# Data dumps

Versioned snapshots produced by the notebooks under the repo root.
Treat these files as the product; treat the notebooks / `experimental/` as how they were built.

## Current release

| File | Rows | Schema |
|---|---|---|
| `composers.tsv` | 3367 | Name, Year of birth, Year of death, Nationality, Notable 20th-century works, Remarks, URL, Pageviews |
| `composers_imslp.tsv` | 3367 | same as above + `IMSLP_URL`, `IMSLP_Exists` |

### Provenance (best effort)

| Field | Value |
|---|---|
| Dump id | `v0.1-legacy` |
| First committed | 2023-08-02 (`49be8ba`) |
| Name source | Wikipedia: [List of 20th-century classical composers](https://en.wikipedia.org/wiki/List_of_20th-century_classical_composers) |
| Popularity metric | Wikimedia Pageviews API, monthly totals (notebook hardcodes `20230101`–`20231231`; earlier README mentioned 2022 — treat ranking as approximate) |
| Score source | IMSLP category page existence check (heuristic `Last,_First` URL) |
| Jurisdiction heuristic | EU-style life+70 proxy via year of death (not legal advice; editions/arrangements/libretti can differ) |
| License of dump tables | Derived facts + URLs; reuse under project MIT. Linked Wikipedia/IMSLP content remains under their terms. |

### Caveats

- IMSLP presence ≠ public domain in your jurisdiction. Prefer IMSLP’s own CAN/US/EU tags for score-level status.
- Name → IMSLP URL matching is naive and under-recalls.
- Living composers and recent deaths are included in the full dump; filter before research use.
- Do not silently overwrite these files. New scrapes should land as a new dated dump (e.g. `composers_2026-09-24.tsv`) plus an updated row in this manifest.

## Building a new dump

```bash
pip install -r requirements.txt
# Smoke test (no writes):
python scripts/build_dump.py --limit 5 --dry-run --no-pageviews --no-imslp
# Full rebuild (slow; polite rate limits):
python scripts/build_dump.py
```

Writes a single `data/composers_YYYY-MM-DD.tsv` plus `dump_meta_YYYY-MM-DD.json`. Never overwrites an existing dated file.

### Target columns for v0.2+

| Column | Notes |
|---|---|
| existing v0.1 columns | keep |
| `eu_pd_year` | `Year of death + 71` calendar heuristic |
| `dump_id` / meta JSON | provenance next to the TSV |
| later | IMSLP EU/US/CA tags, Wikidata QID, work-level tags |
