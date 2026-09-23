# Data dumps

Versioned snapshots. Treat TSV + `dump_meta_*.json` as the product; scripts under `scripts/` are how they were built.

## Current dump (schema v2)

| File | Rows | dump_id |
|---|---|---|
| `composers_2026-09-24.tsv` | 3371 | `2026-09-24` |
| `dump_meta_2026-09-24.json` | — | companion meta |

**Columns:** Name, Year of birth, Year of death, Nationality, Notable 20th-century works, Remarks, URL, eu_pd_year, Pageviews, IMSLP_URL, IMSLP_Exists

| Field | Value |
|---|---|
| tool_version | `2.0.0-dev` |
| schema_version | `2` |
| Name source | Wikipedia [List of 20th-century classical composers](https://en.wikipedia.org/wiki/List_of_20th-century_classical_composers) |
| Pageviews | Wikimedia API monthly totals `20250101`–`20251231` |
| IMSLP | heuristic `Last,_First` category URL + HTTP existence |
| EU heuristic | `eu_pd_year = Year of death + 71` |
| IMSLP hits | 1146 / 3371 |
| Likely EU PD in 2026 (death ≤ 1954) | 759 |

## Legacy dump (schema v1)

| File | Rows | dump_id |
|---|---|---|
| `composers.tsv` | 3367 | `v0.1-legacy` |
| `composers_imslp.tsv` | 3367 | `v0.1-legacy` |

First committed 2023-08-02. Prefer the dated schema-v2 file above for new work.

## Caveats

- IMSLP presence ≠ public domain in your jurisdiction.
- Name → IMSLP URL matching is naive and under-recalls.
- Living composers and recent deaths are included; filter before research use.
- Never overwrite dated dumps — add a new date + meta file.

## Building a new dump

```bash
pip install -r requirements.txt
python scripts/build_dump.py --limit 5 --dry-run --no-pageviews --no-imslp
python scripts/build_dump.py --date YYYY-MM-DD
```

Writes one `composers_YYYY-MM-DD.tsv` + `dump_meta_YYYY-MM-DD.json`.
