# Data dumps

Versioned snapshots. Treat TSV + `dump_meta_*.json` as the product; scripts under `scripts/` are how they were built.

Pipeline diagram: [`docs/PIPELINE.md`](../docs/PIPELINE.md).

## Current dump (schema v3)

| File | Rows | dump_id |
|---|---|---|
| `composers_r006.tsv` | 3370 | `r006` |
| `works_r006.tsv` | 29013 | `r006` |
| `dump_meta_r006.json` | — | companion meta |

`r006` = style QID fix (no more false electroacoustic from Q1338153) + Grok 4.7 high residual on remaining `other` works. Prefer `r006` for the viewer (`scripts/export_viewer_json.py --dump r006`).

## Dump lineage

| dump_id | What |
|---|---|
| `r006` | Style QID fix + Grok residual (31 still other) |
| `r005` | Wikidata style refresh only |
| `r004` | Full luna-xhigh force residual |
| `r003` | LLM residual pilot: 100 force + 74 styles |
| `r002` | GenInfo pilot (~200) + style QID remap |
| `r001` | Canonical revision of cohort-filtered inventory |
| `2026-09-30` | Grok residual → 100% `force_family` |
| `2026-09-29` | Full gpt-6-luna fill |
| `2026-09-28` | LLM pilot (80 rows) |
| `2026-09-27` | Rules/title `force_family` |
| `2026-09-26` | Full scrape |

| Older | Notes |
|---|---|
| `2026-09-25` | schema v3 smoke (`--limit 5`) |
| `2026-09-24` | schema v2 single TSV |
| `v0.1-legacy` | `composers.tsv` / `composers_imslp.tsv` |

## Caveats

- IMSLP match / hosted scores ≠ public domain in your jurisdiction.
- `eu_pd_*` is a calendar heuristic, not legal advice.
- `force_family_src` trust order: `imslp_tags` > `imslp_geninfo` > `title` > `llm` / `llm_grok`.
- `unverified_heuristic` IMSLP matches need caution.
- Never overwrite dumps — add a new `rNNN` + meta file.

## Building / enriching

```bash
pip install -r requirements.txt
python scripts/build_dump.py --date YYYY-MM-DD          # historical calendar id OK
python scripts/promote_revision.py --from-dump 2026-10-01 --to r001
python scripts/enrich_geninfo.py --from-dump r001 --to r002 --limit 200 --remap-styles
python scripts/prepare_llm_residual.py --dump r002      # queue only; no Codex
python scripts/export_viewer_json.py --dump r002
```

Caches: `data/cache/` (gitignored). GenInfo under `data/cache/imslp_geninfo/`. LLM batches resume under `data/cache/llm_force_family/`.
