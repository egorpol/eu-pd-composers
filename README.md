# eu-pd-composers

**Find public-domain scores and works for performance, analysis and research.**

A versioned dataset and a static browser for finding 20th-century classical composers whose music is likely public domain in the EU (life + 70 years), with links to their works on IMSLP.

**Live viewer:** [egorpol.github.io/eu-pd-composers](https://egorpol.github.io/eu-pd-composers/) · **Release:** v3.5.0 · **Dump:** r018 (schema 3) · [Changelog](CHANGELOG.md) · [Versioning](VERSIONING.md)

> **Not legal advice.** "Public domain" here is a heuristic based on the composer's death year only. It ignores lyricists and librettists, editions, arrangements, posthumous publication rights and national exceptions. Check the work itself before you copy, perform or publish it.

## What is in the current dump (r018)

| | |
|---|---|
| Composers | **3,369** from the English Wikipedia *List of 20th-century classical composers*, with Wikidata life dates (rank-aware, with precision), citizenship, occupations and styles, plus 12 months of Wikipedia pageviews |
| EU public-domain status (reference year 2026) | **777** PD now (died 1955 or earlier) · **1,870** not yet · **722** with no death date on record. 21 more enter the public domain on 1 January 2027, 82 more by 2031 |
| IMSLP identity | **1,254** composers matched to an IMSLP category: 974 through Wikidata's IMSLP ID, 280 by name, confirmed against IMSLP's own life dates or Wikipedia link. 14 wrong matches rejected; 7 name matches IMSLP cannot confirm; 2,094 not found |
| Works | **29,033** IMSLP work pages for 1,058 composers. **20,391** belong to the 657 PD-now composers who have works on IMSLP |
| Instrumentation (`force_family`) | 17 families (piano solo, solo voice, choral, orchestral, chamber, …) plus `unclassified` / `other`. 98.2% come straight from IMSLP's categories, 1.3% from earlier LLM guesses. Each family and `genre_form` value maps to Library of Congress terms (LCMPT medium, LCGFT genre/form) in [`data/vocab/lc_crosswalk.tsv`](data/vocab/lc_crosswalk.tsv); IMSLP's count of instrumental players for 13,873 works (47.8%) |
| Work-level IMSLP evidence | IMSLP period style (99.8% of works), first publication year (73.9%), IMSLP's own rights categories (34.1%), librettists (27.3%); whether the page links any score or recording (28,930 of 29,033) and which IMSLP server hosts it |
| Authority records | GND id for **2,897** composers (2,889 from Wikidata, 8 from a GND name search with matching life dates), VIAF id for **3,262**; 103 have neither. 14 GND death years differ from the dump's and are flagged |
| Composer styles | Wikidata movements for 153 composers; agreed labels from two LLMs, grounded in the Wikipedia lead, for 2,842 |

Every label carries its source (`*_src` columns, `qa_flags`, `imslp_match_evidence`, per-model LLM votes), and every revision is immutable with a `dump_meta_rNNN.json` that records how it was built. Only the latest revision lives in `data/`; older ones are in git history.

A useful cross-check: IMSLP flags 4,527 of the 7,834 works by not-yet-PD composers as not PD in the EU, but only 65 of the 20,391 works by PD-now composers. Those 65 are mostly vocal works and posthumous publications, which a composer-only rule cannot see. IMSLP gives that EU warning only for composers who died 1956–1971 (PD in Canada); later works carry other rights categories (US-only PD, hosted by permission) or sit on IMSLP's US and life+50 servers.

## Use cases

**Find playable public-domain repertoire.** Performers, teachers, ensembles and editors: open the [viewer](https://egorpol.github.io/eu-pd-composers/), keep *EU public domain = PD now*, then filter by instrumentation, style, citizenship or popularity. Open a work on IMSLP and check its scores, copyright notes and librettist.

**Plan around upcoming public-domain dates.** Publishers, archives and festivals: the EU public-domain filter also offers *enters next 1 January*, *in 2–5 years* and *later*; the dump column `eu_pd_year` gives the year.

**Use the data in research.** Load the TSVs (pipe-joined lists, empty string for missing):

```python
import pandas as pd
composers = pd.read_csv("data/composers_r018.tsv", sep="\t", dtype=str, keep_default_na=False)
works = pd.read_csv("data/works_r018.tsv", sep="\t", dtype=str, keep_default_na=False)
```

Cite the revision id (`r018`) and the release tag. Column reference and caveats: [`data/README.md`](data/README.md).

**Maintain the dataset.** These are the jobs a maintainer runs (details in [`docs/PIPELINE.md`](docs/PIPELINE.md) and [`docs/AUTOMATION.md`](docs/AUTOMATION.md)):

| Task | How | Network |
|---|---|---|
| Rebuild from current Wikipedia, Wikidata and IMSLP | Run the **refresh** workflow in GitHub Actions, or `python scripts/pipeline.py refresh --base r018`. It opens a PR with a readable diff | Yes, about 2–2.5 h (IMSLP at ≤ 1 request/s) |
| Fix a wrong person, date or IMSLP match | `python scripts/identity_candidates.py --dump r018 --out build/identity.tsv` lists rows whose list years, occupation or type disagree with Wikidata, with candidate items. Add a sourced row to [`data/overrides/composers.tsv`](data/overrides/README.md), then derive. A re-key to a new Wikidata item first needs `python scripts/refetch_override_entities.py` | For the review queue and new re-key targets |
| Change a rule (instrumentation mapping, identity check, PD arithmetic) | Edit the code, then `python scripts/pipeline.py derive --from-dump r018 --to r019` | No |
| Relabel composer styles with LLMs | `scripts/llm_style_pass.py` appends to the committed ledger in `data/llm_ledger/` (a local step that needs approval); `derive` replays it. The pipeline never calls a model | Model CLIs only |
| Review a revision | `python scripts/diff_dumps.py r018 r019 --out diff.md` and `python scripts/check_release.py --dump r019 --against r018` | No |
| Preview the viewer | `python scripts/export_viewer_json.py --dump r018`, then `python -m http.server 8080 --directory viewer` | No |
| Publish | Merge to `main`: CI runs the tests and release gates; Pages redeploys `viewer/` | — |

## How it works

```mermaid
flowchart LR
  subgraph crawl [Crawl: build_dump.py]
    W[Wikipedia list] --> D[Wikidata: dates, citizenship,<br/>styles, IMSLP ID]
    D --> I[IMSLP: composer category,<br/>work pages, categories]
    W --> P[Pageviews]
  end
  subgraph offline [Offline stages: pipeline.py]
    F[Instrumentation from<br/>IMSLP categories] --> C[Carry forward earlier<br/>GenInfo / LLM labels]
    C --> R[Rank-aware dates,<br/>QA flags, PD status]
    R --> O[Reviewed overrides]
    O --> A[GND / VIAF ids,<br/>GND death check]
    A --> M[IMSLP identity check]
    M --> E[Work evidence]
    E --> L[Replay LLM style<br/>ledger]
  end
  subgraph release [Release]
    G[Gates + diff] --> V[rNNN dump + meta]
    V --> X[Viewer JSON → Pages]
  end
  crawl --> offline --> release
```

1. **Crawl** (network): the Wikipedia list gives names; Wikidata gives identity and dates; IMSLP gives each composer's work pages and every page's complete category list. All responses are cached in `data/cache/` (gitignored).
2. **Offline stages** (no model calls): each stage reads one revision and writes the next, so a single rule change re-derives everything downstream. Wrong-person IMSLP matches are rejected by comparing life dates; hand-reviewed overrides are applied before the identity check.
3. **Release**: gates fail the build on large unexplained changes (composers ±3%, works −5%, lost columns, inconsistent labels), and a diff report lists PD flips, identity changes and label transitions for review.

## How LLMs are used

LLMs label two things, and both labels are kept apart from the facts they could distort:

- **Instrumentation of 364 works** (1.3%) whose IMSLP categories name no instrument family. These are earlier guesses carried forward and marked `force_family_src = llm`; a category-derived label always replaces them.
- **Composer styles for 3,369 composers.** Two models (`gpt-6.1-sol` @low and `grok-4.7-low`), each given the composer's Wikipedia lead, label style and period; only labels both agree on reach the `llm_style_*` columns, with each model's vote kept.

LLMs never decide a composer's identity, life dates or public-domain status, the IMSLP match, IMSLP rights categories, authority ids or whether a work has files. Those come from Wikidata, the Wikipedia list, GND and IMSLP by fixed rules, and the exceptions are hand-reviewed overrides with a source per row.

Every style decision is a row in the append-only ledger in `data/llm_ledger/` (model, prompt version, input hash, output). The pipeline never calls a model: `apply_llm_styles.py` replays the ledger offline, so a rebuild reproduces the same labels. The instrumentation guesses predate the ledger and are carried forward from the previous revision. Two models agreeing is not the same as being right: the style labels are compared with Wikidata and IMSLP periods for agreement only, and their accuracy is still unmeasured.

## What it does not do (yet)

- **Public domain beyond the composer.** No lyricist, translator or arranger terms, no edition or posthumous-publication rights, no wartime extensions, no US status. IMSLP's own copyright flags are carried as evidence only.
- **Score availability.** `has_files` only says that a work page links a score, part or recording; file quality is not checked, files on IMSLP's US and life+50 servers are not public domain in Canada, and 199 matched composers have no works listed.
- **Measured accuracy.** The gold set (an r018 sample of 200 composers and 300 works) is being annotated, so there are no error rates yet. LLM style labels are only compared for agreement with Wikidata and IMSLP, which are not ground truth either.
- **Coverage beyond one list.** The cohort is one English Wikipedia list and inherits its selection biases. About 62% of composers have no IMSLP category, many because their music is still in copyright.
- **Scheduled refreshes.** The monthly schedule is ready but stays off until IMSLP has acknowledged the crawl.

## Repository layout

```text
data/
  composers_r018.tsv, works_r018.tsv, dump_meta_r018.json   # the product (latest revision only)
  overrides/composers.tsv     # hand-reviewed, sourced fixes
  llm_ledger/                 # append-only LLM style decisions (replayed offline)
  gold/                       # hand-checked stratified sample: annotation guide, sheets, scores
  vocab/lc_crosswalk.tsv      # force_family / genre_form → Library of Congress LCMPT / LCGFT terms
viewer/                       # static filter UI, published by GitHub Pages
scripts/
  pipeline.py                 # refresh (crawl + all stages) / derive (offline stages)
  build_dump.py               # crawl: Wikipedia → Wikidata → pageviews → IMSLP
  remap_force.py, carry_forward.py, remap_composers.py, apply_overrides.py, remap_authorities.py,
  remap_imslp_matches.py, remap_work_evidence.py, apply_llm_styles.py   # offline stages, in order
  refetch_*.py                # cache fillers (network, resumable)
  check_release.py, diff_dumps.py, export_viewer_json.py               # gates, review, viewer
  llm_style_pass.py, fetch_wikipedia_leads.py, style_agreement.py, pilot_sample.py   # LLM style study
  rights_basis.py             # IMSLP rights evidence vs the composer rule, by death year
  identity_candidates.py      # review queue of suspect identities with candidate Wikidata items
  gold_set.py, gold_form.html # gold-set sampler (blind sheets), annotation form and scorer (Wilson intervals)
  lc_crosswalk.py             # checks the LC crosswalk against the dump vocabulary and id.loc.gov
  common.py, imslp.py, wikidata_enrich.py, authorities.py, force_family.py, …   # shared modules
  legacy/                     # one-off passes that produced older labels; not run by the pipeline
tests/                        # offline pytest suite (no network)
docs/PIPELINE.md, docs/AUTOMATION.md
```

## Setup

```bash
pip install -r requirements.txt        # requirements-dev.txt adds pytest
python -m pytest tests -q
```

## License

Code: MIT (see `LICENSE`). Data drawn from Wikipedia, Wikidata and IMSLP remains under their respective terms.
