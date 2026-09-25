# Data dumps

Versioned snapshots. Treat TSV + `dump_meta_*.json` as the product; scripts under `scripts/` are how they were built.

Pipeline diagram: [`docs/PIPELINE.md`](../docs/PIPELINE.md).

## Current dump (schema v3)

| File | Rows | dump_id |
|---|---|---|
| `composers_r008.tsv` | 3368 | `r008` |
| `works_r008.tsv` | 28993 | `r008` |
| `dump_meta_r008.json` | — | companion meta |

Only the **latest** revision is kept in `data/` in git. Older `rNNN` / calendar dumps are recoverable from **git history** (not from gitignored `data/cache/`). Prefer `r008` for the viewer (`scripts/export_viewer_json.py --dump r008`).

`r008` applies Sol pre-main fixes: force rules (ignore `(arr)`, opera/voice beat concerto), merge duplicate `composer_id`s, `unknown_death` for missing death years (on top of r007’s verified style QID map).

## Caveats (read these)

- **Not legal advice.** `eu_pd_*` is a death-year + 71 calendar heuristic against the dump snapshot year. Missing death → `unknown_death` (not `living`).
- **Work pages, not score files.** IMSLP links are work pages; `has_files` is not a verified inventory.
- **Tags are research aids and often wrong.** `force_family` and `style_tags` come from IMSLP categories, scraped General Information, Wikidata QIDs, title heuristics, and LLMs. Prefer `force_family_src` / `style_tags_src` and verify on IMSLP/Wikidata.
- Trust order for force: `imslp_tags` > `imslp_geninfo` > `title` > `llm` / `llm_luna_xhigh` / `llm_grok`.
- IMSLP match / hosted scores ≠ public domain in your jurisdiction.
- `unverified_heuristic` IMSLP matches need caution.
- Never overwrite dumps — add a new `rNNN` + meta file.

## Building / enriching

```bash
pip install -r requirements.txt
python scripts/build_dump.py --date YYYY-MM-DD
python scripts/promote_revision.py --from-dump … --to rNNN
python scripts/remap_force.py --from-dump r007 --to r008
python scripts/export_viewer_json.py --dump r008
python scripts/check_release.py --dump r008 --viewer-data viewer/data
```

Caches: `data/cache/` (gitignored).
