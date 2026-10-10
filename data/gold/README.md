# Gold set: annotation guide

A hand-checked, stratified sample of the dump that turns agreement between sources into measured error rates. One annotator labels every item; a 10% re-check measures intra-annotator consistency.

## Files (`data/gold/<dump>/`)

| File | Who opens it | Content |
|---|---|---|
| `composers_sheet.tsv`, `works_sheet.tsv` | Annotator | Items to check: identifiers and links only, then empty answer columns |
| `composers_recheck.tsv`, `works_recheck.tsv` | Annotator, later | About 10% of the same items in a new order, for the re-check |
| `composers_design.tsv`, `works_design.tsv` | Scorer only | Stratum, stratum size, sample size and weight of each item |
| `meta.json` | — | Dump, seed, allocation |

`data/gold/practice.json` holds four practice items outside any sample, with worked answers, for trying the rules and the form.

The current sample is r018: 200 composers and 300 works, drawn with `python scripts/gold_set.py sample --dump r018` (seed 20261010). The r016 sample was the pilot; its few answers score against r016 only. Small strata are oversampled (square-root allocation, at least 8 items each), and the scorer weights them back.

**Composer strata.** Composers with `qa_flags`; rejected or unverified IMSLP matches; then EU PD status (`pd`, `not_pd`, `unknown_death`) × IMSLP link (Wikidata P839, confirmed name match, not found).
**Work strata.** Force-label source (IMSLP categories, title rule, LLM, unlabelled); IMSLP-labelled works also split by rights basis (EU warning, other rights category, none). Within a stratum, works are spread across instrumentation families in proportion.

## Workflow

1. Fill `composers_sheet.tsv` and `works_sheet.tsv` in a spreadsheet (keep TSV, UTF-8; do not reorder or delete rows), or in the annotation form (below). Budget about 3 minutes per composer and 1 minute per work, so roughly 15 hours.
2. **Stay blind.** While annotating, do not open the dump, the viewer, the design files or this repo's labels for these items. The sheets deliberately omit death years, PD status, instrumentation and rights labels.
3. At least **7 days** after finishing an item set, fill its `*_recheck.tsv` without looking at your first answers.
4. Score: `python scripts/gold_set.py score --dump r018 --out data/gold/r018/report.md --json data/gold/r018/report.json`. Partly filled sheets score fine; blank rows are skipped, and invalid values are listed and ignored. Score only after finishing a pass: with few items filled, the rates show whether single answers matched the dump, which unblinds the rest.

Use `unsure` sparingly; write the reason in `notes`. Leave a cell empty only if you have not checked it yet.

### Annotation form

`python scripts/gold_set.py form --dump r018` builds the form from the sheets into `build/gold_form/r018/`. The page holds only what the sheets show, plus the practice items. It shows one item at a time with its links (including the GND and VIAF records the Wikidata item names) and name searches (IMSLP, GND, LoC, BnF, Grove), offers only valid values, and keeps the re-check tabs locked until 7 days after a set's last answer.

- `gold_form.html` is the claude.ai Artifact page (r018: [the published form](https://claude.ai/artifact/KB6qqcJ7fqTGZcirwWyuNw), private to its owner; the r016 pilot form is a separate artifact, because item ids repeat across samples). Answers go to the artifact's store (collections `answers`, `recheck`, `practice`, one document per item). To export, Claude saves `answers` and `recheck` with `ArtifactData list` (`out_dir`), then `python scripts/gold_set.py import-form --dump r018 --store <out_dir>` writes them into the sheets; an item in the store replaces all its answer cells, and invalid values are listed.
- `gold_form_local.html` opens in any browser. Answers stay in that browser's storage; copy each sheet into `data/gold/<dump>/` from Progress & export.

## Composers

| Column | Values | Meaning |
|---|---|---|
| `wikidata_same_person` | `yes` / `no` / `unsure` | Is the Wikidata item the composer the Wikipedia list means? Compare the Wikipedia article (`wikipedia_url`) with the item: name, dates, occupation, nationality. A namesake (painter, politician, father) is `no` |
| `imslp_check` | row has `imslp_url`: `same` / `different` / `unsure` | Does the IMSLP category belong to this composer? Compare IMSLP's life dates, links and works with the article |
| | row has no `imslp_url`: `found` / `none` / `unsure` | Does IMSLP have a composer category for this person? Search IMSLP for the name and its variants (transliterations, maiden names). `found` needs the URL in `imslp_found_url` |
| `imslp_found_url` | URL | Only with `found` |
| `death_status` | `dead` / `living` / `unknown` | `living` needs positive evidence of life after 2020 (recent activity, an institutional page, an interview). If you cannot tell, use `unknown` |
| `death_year` | `YYYY` | With `dead`. Leave empty if the person died but no source gives the year |
| `death_source` | GND number, or short name + URL or id | The source you relied on (see the hierarchy). A bare number is a GND id (`118776312`, written to the sheet as `GND 118776312`); name any other source (`LoC n92085993`, `Wikidata Q10274029`, `Grove https://…`) |
| `notes` | free text | Conflicts between sources, doubts, page changes |

### Source hierarchy for life dates

The sheet's `gnd_id` and `viaf_id` come from the Wikidata item (a few GND ids from a lobid.org name match). If the item is the wrong person, so are those records: check their dates and occupation against the article, and search by name when they do not match.

Use the highest level that has the information, and two independent sources where they are quick to find.

1. National authority files: GND (Deutsche Nationalbibliothek), BnF, Library of Congress Name Authority (id.loc.gov), or their VIAF cluster.
2. Reference works: Grove Music Online / Oxford Music Online, MGG, Baker's, national biographical dictionaries.
3. Institutional or press sources: music information centres, publishers' composer pages, obituaries in reputable newspapers.
4. Wikipedia or Wikidata, only when nothing above has the date. Wikidata is the dump's own source, so record it honestly in `death_source`.

When sources disagree, take the year most independent authorities give, and write the conflict in `notes`.

Decided cases:

- **The list row links the wrong article** (a namesake's page, such as a physicist's): `wikidata_same_person` = `no`. Answer the IMSLP and death columns for the composer the list row means (its nationality and dates identify him), and write the mix-up in `notes`. The scorer then counts the identity error and the death-year or PD errors it causes.

## Works

| Column | Values | Meaning |
|---|---|---|
| `force_family` | a family below, or `unsure` | The instrumentation of the composer's **original** version |
| `genre_form` | a form below, `none`, or `unsure` | The form of the composer's original work |
| `has_files` | `yes` / `no` / `unsure` | Does the work page list at least one score, part or recording file? Files hosted on IMSLP's other servers count; cover images do not |
| `rights_basis` | a basis below, or `unsure` | IMSLP's own rights category, from the category list at the bottom of the work page |
| `notes` | free text | Doubts, several original versions, a page that changed |

### Instrumentation families

Judge the original scoring, ignoring arrangements by others. If the composer made several versions, choose the one that comes **later** in this list (the dataset uses the same rule when several labels apply).

| Family | Use for |
|---|---|
| `piano_solo` | one pianist (including left hand alone) |
| `piano_ensemble` | two or more pianists or pianos, or piano with other keyboards, no other instruments |
| `organ` | organ alone |
| `keyboard_other` | harpsichord, clavichord, celesta, harmonium and similar, alone |
| `solo_instrument` | one non-keyboard instrument, alone or with one keyboard (e.g. violin and piano) |
| `guitar` | anything with guitar or lute-family instruments and no voices, orchestra, band or electronics |
| `chamber` | two or more instrumentalists otherwise, no voices and no orchestra |
| `orchestral` | orchestra or string orchestra without soloists |
| `concerto` | soloist(s) with orchestra |
| `wind_band` | wind band, brass band, wind or brass ensemble |
| `solo_voice` | one or two voices with any accompaniment (including orchestra), or one or two voices alone |
| `choral` | any chorus, or three or more voices alone |
| `stage_opera` | opera, operetta, musical, staged vocal work |
| `stage_ballet` | ballet |
| `film_media` | film, television or radio music |
| `electronic` | with electronics or tape, and no voices, chorus, orchestra or band |
| `pedagogical` | methods, exercises, studies written as teaching material |
| `other` | none of the above (for example, unspecified instrumentation) |

Decided cases:

- **An instrument named next to an orchestra or string orchestra** (harp and string orchestra, 2 violins and small orchestra) is a soloist: `concerto`. It is `orchestral` only when the score treats it as an ordinary orchestral part, with no solo marking or separate solo staff.
- **Files only for an arrangement** still give `has_files` = `yes`; the family still follows the original.
- **IMSLP's "For …" categories and tags** are a starting point, not the answer. The dataset derives many labels from them, so check them against the work page's Instrumentation line and the score.

### Forms

Judge the composer's original; an arrangement takes the original's form. A piece drawn from a larger work (an overture, aria or suite from an opera or ballet) takes the larger work's form. If several apply, pick the first in this list (the dataset uses the same order).

| Form | Use for |
|---|---|
| `opera` | opera, operetta, singspiel, zarzuela or other staged sung drama |
| `ballet` | ballet or other staged dance work |
| `concerto` | called concerto, concertino, concerto grosso or sinfonia concertante |
| `symphony` | symphony, sinfonietta, or a sinfonia in several movements; a symphonic poem is not |
| `sonata` | sonata, sonatina or trio sonata, any instrumentation |
| `suite` | suite or partita: contrasting movements published as one work |
| `variations` | variations on a theme, under any title |
| `etude` | étude or study, alone or in a set; a method or exercise book is not |
| `prelude_fugue` | prelude, fugue or fughetta, alone or paired; chorale preludes count |
| `string_quartet` | for exactly 2 violins, viola and cello; other quartets are not |
| `dance` | dance or set of dances (waltz, mazurka, polonaise, minuet…); marches count |
| `mass_requiem` | Mass or Requiem, or a single movement or Proper text of one |
| `oratorio_cantata` | oratorio, cantata or Passion: unstaged, voices and instruments, several numbers |
| `overture_tone_poem` | concert overture, symphonic poem or tone poem |
| `song_cycle` | songs the composer published as one connected cycle |
| `song` | a song, Lied, mélodie, part-song or set of songs that is not a cycle |
| `none` | none of these applies (a nocturne, fantasia, motet, caprice…) |

Decided cases:

- **Arrangements**: judge the original; `arrangement` is not an answer (the scorer counts the dataset's `arrangement` label as its own error class).
- **A piece drawn from a larger work** takes the larger work's form, as in Library of Congress practice ("Operas -- Excerpts"): an opera's overture is `opera`, a ballet suite is `ballet`.
- **`concerto`** only for works called concerto (concertino, concerto grosso, sinfonia concertante). Other works for soloist and orchestra take their own form or `none`; their family can still be `concerto`.
- **`string_quartet`** only for 2 violins, viola and cello; IMSLP's Quartets category also holds piano quartets.
- **`song`** covers what the dataset currently labels `other` (IMSLP Songs/Lieder); the scorer reads the dump's `other` as `song` and an empty cell as `none`.

### Rights basis

First match in this order, from the categories at the bottom of the work page:

| Basis | IMSLP categories |
|---|---|
| `eu_warning` | WorkNonPD-EU, WorkNonPD-USandEU |
| `us_only` | WorkPD-USonly (public domain in the US only; the opposite of a "Non-PD US" warning) |
| `licensed` | Works not in public domain, FileNonPD-PermissionGranted, Works Licensed through BMI / ASCAP / GEMA |
| `wima` | WIMA files |
| `us_routes` | Work-PD-US-notrenewed, PD-US-no notice |
| `none` | none of these |

WorkNonPD-US alone is a US warning, not a basis: it gives `none`.

The **Copyright** line in a file's box ("Public Domain - Non-PD US, Non-PD EU") is IMSLP's status for that file: Public Domain means in Canada, and the rest are warnings. It does not set the basis; only the categories do. When the file line and the categories disagree, answer from the categories and write the difference in `notes`.

## What the scores mean

Composers: wrong Wikidata person; wrong IMSLP link; missed IMSLP link; wrong death year (exact, and off by two years or more); missing death year; **false PD** (the dump says PD now, but the composer died too recently or is alive); missed PD. Works: wrong instrumentation family; form differs; no form in the dump although the original has one; dump gives a form but none of the listed forms applies; dump says arrangement while the original has a listed form; wrong `has_files`; wrong rights basis. Each rate has a 95% Wilson interval, for the checked items and weighted to the whole dump. The re-check reports agreement and Cohen's κ per field.
