# Data dumps

Versioned snapshots. Treat TSV + `dump_meta_*.json` as the product; scripts under `scripts/` are how they were built.

Pipeline diagram: [`docs/PIPELINE.md`](../docs/PIPELINE.md).

## Current dump (schema v3 + force_family + LLM)

| File | Rows | dump_id |
|---|---|---|
| `composers_2026-09-30.tsv` | 3371 | `2026-09-30` |
| `works_2026-09-30.tsv` | 29021 | `2026-09-30` |
| `dump_meta_2026-09-30.json` | — | companion meta |

**100%** works have a `force_family`. Residual 394 after gpt-6-luna filled by **Grok 4.7 high** (`force_family_src=llm_grok`).

## Dump lineage

| dump_id | What |
|---|---|
| `2026-09-26` | Full scrape (Wiki + Wikidata + IMSLP works) |
| `2026-09-27` | + rules/title `force_family` (~59% classified) |
| `2026-09-28` | LLM pilot (80 rows, gpt-6-luna) |
| `2026-09-29` | Full gpt-6-luna fill (98.6%; 394 left) |
| `2026-09-30` | Grok 4.7 high residual → **100% classified** |

| Older | Notes |
|---|---|
| `2026-09-25` | schema v3 smoke (`--limit 5`) |
| `2026-09-24` | schema v2 single TSV |
| `v0.1-legacy` | `composers.tsv` / `composers_imslp.tsv` |

## Caveats

- IMSLP match / hosted scores ≠ public domain in your jurisdiction.
- `eu_pd_*` is a calendar heuristic, not legal advice.
- `force_family_src`: `imslp_tags` > `title` > `llm` (trust in that order).
- `unverified_heuristic` IMSLP matches need caution.
- Never overwrite dated dumps — add a new date + meta file.

## Building / enriching

```bash
pip install -r requirements.txt
python scripts/build_dump.py --date YYYY-MM-DD
python scripts/enrich_dump.py --from-dump 2026-09-26 --date YYYY-MM-DD
python scripts/llm_force_family.py --from-dump 2026-09-27 --date YYYY-MM-DD
```

Caches: `data/cache/` (gitignored). LLM batches resume under `data/cache/llm_force_family/`.
