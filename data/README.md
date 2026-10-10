# Data dumps

Versioned snapshots. Treat TSV + `dump_meta_*.json` as the product; scripts under `scripts/` are how they were built.

Pipeline diagram: [`docs/PIPELINE.md`](../docs/PIPELINE.md).

## Current dump (schema v3)

| File | Rows | dump_id |
|---|---|---|
| `composers_r016.tsv` | 3369 | `r016` |
| `works_r016.tsv` | 29026 | `r016` |
| `dump_meta_r016.json` | — | companion meta |

Only the **latest** revision is kept in `data/` in git. Older `rNNN` / calendar dumps are recoverable from **git history** (not from gitignored `data/cache/`). The viewer is exported from `r016` (`scripts/export_viewer_json.py --dump r016`).

### What changed since r015

- **r016 — IMSLP rights categories and files.** r015 plus an offline replay of `scripts/remap_work_evidence.py` over the cached work-page categories and the new file lists (`imslp_page_files`, fetched 2026-10-10). Composers are unchanged.
  - Six more `imslp_copyright_flags` tokens, so 34.1% of works now carry a flag (was 22.3%):

    | Token | IMSLP category | Works |
    |---|---|---:|
    | `nonpd_licensed` | Works not in public domain ("not PD anywhere", hosted by permission or under Creative Commons) | 2,306 |
    | `pd_us_only` | WorkPD-USonly | 890 |
    | `pd_us_notrenewed` | Work-PD-US-notrenewed | 312 |
    | `pd_us_no_notice` | PD-US-no notice | 22 |
    | `wima` | WIMA files (Werner Icking Music Archive) | 384 |
    | `pro_licensed` | Works Licensed through BMI / ASCAP / GEMA | 147 |

  - `has_files` is filled: `true` when the page links any score, part or recording (images such as covers and thumbnails do not count). 28,923 works `true`, 42 `false`, 61 empty (the page now redirects).
  - New column `imslp_file_hosts` after `has_files`: the IMSLP servers holding those files, read from the file names. `ca` = main (Canadian) server; `us` = `PMLUS…` files, IMSLP's server for works PD in the US only (1,150 works); `asia` = `PMLASIA…` files, its server for works PD under life+50 (831 works).
  - 5 other evidence cells differ from r015: 4 style / publication cells because the local category cache predates the r014 crawl by about 12 hours, and 1 librettist list from 63 work pages fetched fresh for r016.

### What changed since r013

- **r014 — first full refresh** with `scripts/pipeline.py refresh` (crawl 2026-10-09, pageviews October 2025 – September 2026): 3,369 composers (+1), 29,026 works (+46).
- **r015 — LLM style consensus.** r014 plus an offline replay of the committed style ledger (`scripts/apply_llm_styles.py`): four `llm_style_*` composer columns, 2,842 composers with agreed styles and 2,748 with an agreed period. The 74 legacy LLM `style_tags` are retired, so `style_tags` is Wikidata-only. See [Composer styles](#composer-styles).

### What changed since r012

- **r013 — work-level IMSLP evidence**, built with `scripts/pipeline.py derive` (one revision; the stages are listed in its meta). Four new works columns come from each work page's own IMSLP categories:

| Column | Coverage | Content |
|---|---|---|
| `imslp_style` | 99.7% | IMSLP period style, pipe list (`Romantic`, `Early 20th century`, `Modern`, …) |
| `imslp_first_published` | 73.8% | earliest `Works first published in YYYY` |
| `imslp_copyright_flags` | — | IMSLP's own flags, normalised: `nonpd_eu` (4,575 works), `nonpd_us`, `pd_eu_rost`, `pd_ca_rost`, `nonpd_ca_text`, `permission_granted` |
| `imslp_librettists` | 27.2% | `Last, First` from `…/Librettist` categories |

  IMSLP flags 4,510 of 7,825 works by `not_pd` composers as not PD in the EU, but only 65 of 20,351 works by `pd` composers. Those 65 are mostly vocal or posthumously published works, the cases a composer-only heuristic cannot see. The flag is IMSLP's judgement, not legal advice either.

### What changed since r010

- **r011 — reviewed overrides** from [`overrides/composers.tsv`](overrides/composers.tsv): six rows pointed at the wrong Wikidata person because the Wikipedia list links a plain title (Michael Howard → the politician, Allen Sapp → a painter, Jaroslav Kvapil → the writer, Ricardo Castro and John Mitchell → disambiguation pages, Bebe Barron → the duo). They are re-keyed and their Wikidata fields re-derived from the right entity; Kvapil (†1958) is therefore no longer `pd`. r011 is an intermediate step and still contains the category collisions that r012 resolves.
- **r012 — IMSLP identity check.** Name-guessed IMSLP categories are compared with the life dates and Wikipedia link on IMSLP's own composer page. 279 of 298 guesses are confirmed (`matched`), 10 belonged to someone else (`rejected_heuristic`, e.g. the composer Karl Marx had the philosopher's page) and 7 stay `unverified_heuristic`. Four Wikidata P839 links point at a different person's page (`rejected_p839`, e.g. Jindřich Feld → his father). Both category collisions are resolved (Leo Smit, William Reed). 13 work rows that belonged to the wrong person are dropped; no IMSLP category is shared any more.

### What changed since r008

- **r009 — complete IMSLP categories.** r008's category fetch was truncated (one `cllimit=100` shared by 20 pages, continuation ignored), so 62.8% of works had no categories and most force labels fell back to title-only LLM guesses. r009 rebuilds `imslp_genre_categories` from a complete per-page crawl (empty: ~1%) and re-derives `force_family` with parsed `For …` rules (fixes e.g. flute→guitar). `imslp_tags` now label 98.3% of works; LLM-derived labels dropped from 43.8% to 1.3%. Instrument + one keyboard (violin/piano sonatas) is `solo_instrument`; 3+ unaccompanied voices are `choral`.
- **r010 — composer fixes from cached Wikidata.** Deprecated claims are ignored and preferred rank wins (Kabalevsky †1987, Rieding †1916; three deprecated-only death years removed → `unknown_death`). Composers who also wrote film music are `classical_core` with `is_film_composer=true` (Prokofiev, Gershwin, Weill, Honegger were hidden by the default viewer filter). New `qa_flags` column marks rows to double-check.

Each `dump_meta_rNNN.json` records counts and before/after transitions for its step.

## IMSLP match status and evidence (r012)

| `imslp_match_status` | `imslp_match_method` | Meaning |
|---|---|---|
| `matched` | `wikidata_p839` | Wikidata P839 names the category and IMSLP's dates do not contradict it |
| `matched` | `exact_name+life_dates` | Name guess confirmed: IMSLP's birth/death years agree (±1) |
| `matched` | `exact_name+wikilink` | Name guess with a small date disagreement, confirmed by IMSLP's Wikipedia link |
| `unverified_heuristic` | `exact_name` | Name guess IMSLP's page cannot confirm (no comparable years) |
| `rejected_heuristic` | `exact_name` | Name guess whose IMSLP page is a different person; works removed, `imslp_url` empty |
| `rejected_p839` | `wikidata_p839` | Wikidata P839 points at a different person's page; works removed, flag `imslp_p839_wrong` |
| `not_found` | — | No category |

`imslp_match_evidence` (pipe list) records why: `dates_agree`, `dates_conflict_weak` (a year pair agrees, or all gaps ≤ 10 years), `dates_conflict_strong` (no pair agrees and a gap > 10 years), `dates_unknown`, `wikilink_agree` / `wikilink_differs`, `performer_page`, `page_missing`, `collision_won` / `collision_lost`. Small disagreements keep the match and add `imslp_dates_conflict` to `qa_flags`.

## Overrides

Hand-reviewed fixes live in [`overrides/composers.tsv`](overrides/composers.tsv) (see [`overrides/README.md`](overrides/README.md)) and are applied last with `scripts/apply_overrides.py`, so they survive rebuilds. Touched rows carry `manual_override` in `qa_flags`.

## Composer styles

`style_tags` (pipe-joined slugs) and `style_tags_src` are Wikidata-only after
`scripts/apply_llm_styles.py`. Legacy `llm*` styles are cleared, and carry-forward
no longer restores them. The committed [style ledger](llm_ledger/README.md) is
replayed after overrides and identity/work evidence stages, without calling models.
Schema version stays 3; these four columns are inserted after `style_tags_src`:

| Column | Values |
|---|---|
| `llm_style_tags` | Slugs shared by both policy labellers, in Codex order, pipe-joined; empty for disagreement, abstention or missing labels |
| `llm_style_period` | Agreed `primary_period` slug; empty for disagreement, `unknown` or missing labels |
| `llm_style_votes` | Each present labeller's ordered styles: `gpt-6.1-sol@low=impressionism\|neoclassicism;grok-4.7-low=impressionism`; abstention is `model=`, missing labellers are omitted |
| `llm_style_src` | `llm_consensus_grounded_v2` when both rows exist, even with no consensus; otherwise empty |

The policy selects only `grounded` / `style-v2`, Codex `gpt-6.1-sol` at `low`
and Cursor `grok-4.7-low` at empty effort. For each composer/labeller the latest
`created_at` wins, with later ledger lines breaking ties across input hashes.
The output meta's `llm_styles` records the policy, ledger SHA-256 and coverage counts.
Dump cells retain ledger slugs; viewer export maps period slugs to IMSLP facet names.

## Columns added in r010

| Column | Values |
|---|---|
| `is_film_composer` | `true` / `false` — Wikidata occupation includes film composer |
| `qa_flags` | pipe list, empty if none: `birth_rank_conflict`, `death_rank_conflict` (best-rank Wikidata claims disagree), `birth_imprecise`, `death_imprecise` (decade/century precision; the year is the interval's last year), `implausible_lifespan`, `death_before_birth`, `not_human` (QID is not a person), `no_composer_occupation` (QID may be a different person), `birth_from_list`, `death_from_list` (year from the Wikipedia list, not Wikidata); since r012 also `imslp_dates_conflict`, `imslp_p839_wrong`, `manual_override` |

## Gold set

[`gold/`](gold/README.md) holds a stratified sample of r016 (200 composers, 300 works) with blind annotation sheets, the annotation guide and, once annotated, error rates with Wilson intervals (`scripts/gold_set.py`). Until then, no field has a measured error rate.

## Caveats (read these)

- **Not legal advice.** `eu_pd_*` is a death-year + 71 calendar heuristic against the dump snapshot year, for the composer only. It ignores lyricists/librettists of vocal and stage works, editions, and national deviations. Missing death → `unknown_death` (not `living`).
- **Work pages, not checked scores.** `has_files` means the work page links at least one non-image file; nobody checked that a file is complete, legible or the right work. Files on the `us` and `asia` servers are hosted there because they are not public domain in Canada; check their status in your country.
- **Tags are research aids and often wrong.** `force_family` comes from IMSLP categories, scraped General Information, title heuristics and earlier LLM passes. `style_tags` comes from Wikidata; `llm_style_tags` is model consensus from the ledger. Check the source/vote columns and verify on IMSLP/Wikidata.
- Trust order for force: `imslp_tags` > `imslp_geninfo` > `title` > `llm` / `llm_luna_xhigh` / `llm_grok`. The remaining ~370 LLM labels come from title-only passes (`llm`: gpt-6-luna at low reasoning) and residual passes; no accuracy evaluation exists yet.
- Rows with `qa_flags` deserve a manual look before you rely on their dates or identity.
- IMSLP match / hosted scores ≠ public domain in your jurisdiction.
- `unverified_heuristic` IMSLP matches need caution.
- Never overwrite dumps — add a new `rNNN` + meta file.

## Building / enriching

The whole chain is one command ([`docs/PIPELINE.md`](../docs/PIPELINE.md), [`docs/AUTOMATION.md`](../docs/AUTOMATION.md)):

```bash
pip install -r requirements.txt
python scripts/pipeline.py refresh --base r016                  # crawl + all stages → next revision
python scripts/pipeline.py derive --from-dump r016 --to r017    # offline stages only

python scripts/export_viewer_json.py --dump r016
python scripts/check_release.py --dump r016 --viewer-data viewer/data
```

The stages it runs, in order, each as `--from-dump rX --to rY`: `remap_force.py` → `carry_forward.py --base rPREV` → `remap_composers.py` → `apply_overrides.py` → `remap_imslp_matches.py` (after `refetch_composer_pages.py --dump rX`, network, ~1 min) → `remap_work_evidence.py` (after `refetch_work_files.py --dump rX` on refresh, network, ~10 min) → `apply_llm_styles.py` (replays the committed ledger; never calls a model).

Cache fillers (network, resumable): `refetch_work_categories.py --dump rX` (complete IMSLP work categories, ~25 min), `refetch_work_files.py --dump rX` (files linked from each work page, ~10 min), `refetch_composer_pages.py`, `refetch_override_entities.py`. The one-off passes that produced older labels (GenInfo scrape, residual LLM force labels, calendar-dump promotion) are in [`scripts/legacy/`](../scripts/legacy/README.md).

Caches: `data/cache/` (gitignored).
