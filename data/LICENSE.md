# Data licence

The data published under `data/` (dump TSVs, `dump_meta_*.json`, `overrides/`, `gold/`, `llm_ledger/`, `vocab/`) and the viewer's exported JSON (`viewer/data/`) is under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). The code (`scripts/`, `viewer/*.js|html|css`, `tests/`) is under [MIT](../LICENSE).

**Suggested credit:** eu-pd-composers by Egor Polyakov, CC BY-SA 4.0, https://github.com/egorpol/eu-pd-composers. Cite the release as in [`CITATION.cff`](../CITATION.cff).

## Sources

| Source | What the dump takes | Source terms | How credited |
|---|---|---|---|
| [English Wikipedia list](https://en.wikipedia.org/wiki/List_of_20th-century_classical_composers) | The cohort (names, article links); `nationality_label_wiki` for all 3,369 composers; `legacy_notable_works_raw` (771; cleaned as `notable_works`) and `legacy_remarks_raw` (510). These cells are copied unchanged, except `notable_works`, a cleaned copy of the notable-works cell. Wikipedia article leads were LLM input for style labels only and are **not** in the repository | CC BY-SA 4.0 (also GFDL) | Link to the page (authors in its history) |
| [IMSLP](https://imslp.org/wiki/IMSLP:General_disclaimer) | Work titles, category lists, librettist names, first-publication years, file licence ids, rights categories, short instrumentation/style strings. No score files are in the repository; each IMSLP file has its own licence | CC BY-SA 4.0 | Link to the IMSLP page |
| Wikidata | Life dates, citizenship, occupations, styles, IMSLP / GND / VIAF ids | CC0 1.0 | Wikidata item (`wikidata_qid`) |
| GND (Deutsche Nationalbibliothek; via Wikidata and lobid.org) | Authority ids; death-year cross-check | CC0 1.0 | GND id (`https://d-nb.info/gnd/<id>`) |
| VIAF (OCLC) | Authority ids | ODC-By 1.0 | Canonical URI `https://viaf.org/viaf/<id>` |
| Wikimedia pageviews | 12-month pageview counts | CC0 1.0 | Wikimedia pageviews |
| Library of Congress (LCMPT / LCGFT via id.loc.gov) | Terms in `data/vocab/lc_crosswalk.tsv` | US government work, public domain | LOC URIs in the crosswalk |
| This project | EU public-domain estimate; scope / force / genre-form classifications; hand-reviewed overrides; LLM labels; gold-set annotations; LC crosswalk | CC BY-SA 4.0 (this file) | Cite as above |

Facts such as names and dates belong to nobody; this licence covers only what the project can license. Source terms still apply to the copied parts.

The EU public-domain estimate is a heuristic, **not legal advice**. See [`README.md`](../README.md).
