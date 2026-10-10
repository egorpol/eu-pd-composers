# Legacy scripts

One-off passes from the r001–r008 era. `scripts/pipeline.py` does not run them, but some of their labels are still in the dump: `carry_forward.py` keeps them where IMSLP categories, General Information and title rules all fail. They stay here as the provenance record for those labels and can still run (`python scripts/legacy/<name>.py --help`). They import the shared modules from `scripts/`.

| Script | What it did | Labels still in r015 |
|---|---|---|
| `llm_force_family.py` | Bulk title-only LLM pass for works with no usable categories (Codex, gpt-6-luna at low reasoning), r001 | 279 works with `force_family_src=llm` |
| `prepare_llm_residual.py`, `llm_residual.py` | Residual LLM pass: force for still-unclassified works, styles for untagged composers (gpt-6-luna xhigh); `llm_grok` labels were produced outside this repo and imported | 47 works `llm_luna_xhigh`, 38 works `llm_grok`. The composer style labels were retired in r015 |
| `enrich_geninfo.py` | Scraped IMSLP *General Information* instrumentation for unclassified works; `--remap-styles` re-applied the Wikidata style map | 2 works with `force_family_src=imslp_geninfo` |
| `remap_styles.py` | Re-applied `STYLE_QID_TO_TAG` from cached Wikidata entities | None directly; `build_dump.py` applies the map at crawl time |
| `enrich_dump.py` | Early offline force / genre-form pass | None; superseded by `remap_force.py` |
| `filter_cohort.py` | Filtered an existing dump through the cohort gates in `scripts/cohort.py` | None; `build_dump.py` applies the gates while crawling |
| `inventory_style_gaps.py` | Listed composers whose style tags needed a recheck (takes a dump id as its only argument) | None; superseded by `llm_style_pass.py` |
| `promote_revision.py` | Copied a calendar-dated dump to a revision id (`rNNN`) | None; `pipeline.py` promotes revisions itself |
| `llm_force_family_schema.json`, `llm_style_schema.json` | Output schemas for the LLM passes above | — |

No accuracy evaluation exists for the remaining LLM force labels (about 1.3% of works).
