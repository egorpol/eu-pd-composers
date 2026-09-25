# Data dumps

Versioned snapshots. Treat TSV + `dump_meta_*.json` as the product; scripts under `scripts/` are how they were built.

Pipeline diagram: [`docs/PIPELINE.md`](../docs/PIPELINE.md).

## Current dump (schema v3)

| File | Rows | dump_id |
|---|---|---|
| `composers_r007.tsv` | 3370 | `r007` |
| `works_r007.tsv` | 29013 | `r007` |
| `dump_meta_r007.json` | — | companion meta |

Only the **latest** revision is kept in git. Prefer `r007` for the viewer (`scripts/export_viewer_json.py --dump r007`).

`r007` rebuilds Wikidata `style_tags` from a verified QID map (previous map had many wrong QIDs, e.g. symphony→atonal_modernism).

## Caveats (read these)

- **Not legal advice.** `eu_pd_*` is a death-year + 71 calendar heuristic.
- **Tags are research aids and often wrong.** `force_family` and `style_tags` come from IMSLP categories, scraped General Information, Wikidata QIDs, title heuristics, and LLMs. Upstream data can be missing or mislabeled; models guess. Prefer `force_family_src` / `style_tags_src` and verify on IMSLP/Wikidata before relying on a tag.
- Trust order for force: `imslp_tags` > `imslp_geninfo` > `title` > `llm` / `llm_luna_xhigh` / `llm_grok`.
- IMSLP match / hosted scores ≠ public domain in your jurisdiction.
- `unverified_heuristic` IMSLP matches need caution.
- Never overwrite dumps — add a new `rNNN` + meta file.

## Building / enriching

```bash
pip install -r requirements.txt
python scripts/build_dump.py --date YYYY-MM-DD
python scripts/promote_revision.py --from-dump … --to rNNN
python scripts/remap_styles.py --from-dump r006 --to r007 --refresh-wikidata
python scripts/export_viewer_json.py --dump r007
```

Caches: `data/cache/` (gitignored). Pre-main review notes: [`docs/REVIEW_pre_main_sol.md`](../docs/REVIEW_pre_main_sol.md).
