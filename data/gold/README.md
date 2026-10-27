# Gold set: annotation guide

A hand-checked, stratified sample of the dump that turns agreement between sources into measured error rates. It grows: each gold version is a new sample, and several contributors add answers to it. A shared core of items, which every contributor answers, measures how far people agree ([contributors](#contributors)); a 10% re-check measures one annotator's consistency over time.

**Gold v2** (sample `r020`) adds the composer's **style**, so the LLM style labels get a human check ([Style](#style-gold-v2)). Gold v1 (`r016` pilot, `r018`) has no style; its partly done r018 pass stays as it is and still scores.

## Files (`data/gold/<dump>/`)

| File | Who opens it | Content |
|---|---|---|
| `composers_sheet.tsv`, `works_sheet.tsv` | Annotator | Items to check: identifiers and links only, then empty answer columns |
| `composers_recheck.tsv`, `works_recheck.tsv` | Annotator, later | About 10% of the same items in a new order, for the re-check |
| `composers_design.tsv`, `works_design.tsv` | Scorer only | Stratum, stratum size, sample size and weight of each item; in v2 also its package |
| `meta.json` | — | Gold version, dump, seed, allocation |
| `annotators/<code>/` | Scorer | Each contributor's sheets, under a pseudonymous code such as `S01` |

`data/gold/practice.json` holds four practice items outside any sample, with worked answers, for trying the rules and the form. [`style_definitions.tsv`](style_definitions.tsv) defines the style tags; the form and the contributors' rules show it.

The current sample is **r020 (gold v2)**: 100 composers and 150 works, drawn with `python scripts/gold_set.py sample --dump r020` (seed 20261010). Small strata are oversampled (square-root allocation, at least 6 items each), and the scorer weights them back.

**Packages.** The v2 sample is dealt into 5 packages of 20 composers and 30 works, about 3 hours each. Each stratum is dealt round-robin across the packages, so any set of finished packages is itself a stratified subsample. Item ids run in package order: package 1 (`C001`–`C020`, `W001`–`W030`) is the **core** every contributor answers; packages 2–5 are batches that contributors take one at a time. The re-check items come from the core.

**Composer strata (v2).** Composers with `qa_flags` or a rejected or unverified IMSLP match; then the dump's style source (a Wikidata movement; LLM consensus only; neither) × EU PD status (`pd`, `not_pd`, `unknown_death`). Within a stratum, composers are spread across the LLM's primary periods in proportion.
**Work strata.** Force-label source (IMSLP categories, title rule, LLM, unlabelled); IMSLP-labelled works also split by rights basis (EU warning, other rights category, none). Within a stratum, works are spread across instrumentation families in proportion.

**Gold v1.** r018 has 200 composers and 300 works, drawn with `python scripts/gold_set.py sample --dump r018 --gold-version 1`; its composer strata are `qa_flags`, rejected or unverified IMSLP matches, then EU PD status × IMSLP link (Wikidata P839, confirmed name match, not found). The r016 sample was the pilot; its few answers score against r016 only.

## Workflow

1. Fill `composers_sheet.tsv` and `works_sheet.tsv` in a spreadsheet (keep TSV, UTF-8; do not reorder or delete rows), or in the annotation form (below). Budget about 8 minutes per composer with style (3 without) and 1 minute per work: about 3 hours per v2 package. Work through the packages in order, core first.
2. **Stay blind.** While annotating, do not open the dump, the viewer, the design files or this repo's labels for these items. The sheets deliberately omit death years, PD status, instrumentation, rights and style labels (the dump's LLM and Wikidata style tags, IMSLP periods).
3. At least **7 days** after finishing an item set, fill its `*_recheck.tsv` without looking at your first answers.
4. Score: `python scripts/gold_set.py score --dump r020 --out data/gold/r020/report.md --json data/gold/r020/report.json` (r018: `--dump r018`). Partly filled sheets score fine; blank rows are skipped, and invalid values are listed and ignored. Score only after finishing a pass: with few items filled, the rates show whether single answers matched the dump, which unblinds the rest. The sample stays r018 after later dumps ship: restore it with `git show v3.6.0:data/works_r018.tsv > data/works_r018.tsv` (likewise `composers_r018.tsv`, `dump_meta_r018.json`). To score a later dump's rules on the same items, pass `--dump r019 --gold-dir data/gold/r018`; ids are stable across derives.

Use `unsure` sparingly; write the reason in `notes`. Leave a cell empty only if you have not checked it yet.

### Annotation form

`python scripts/gold_set.py form --dump r020` builds the form from the sheets into `build/gold_form/r020/`. The page holds only what the sheets show, plus the practice items. It shows one item at a time with its links (including the GND record at `d-nb.info` and the VIAF cluster the Wikidata item names) and name searches (IMSLP, GND, LoC, BnF, Grove), offers only valid values, has a *Use GND <id>* button that fills `death_source` from that record (the year stays typed), and keeps the re-check tabs locked until 7 days after a set's last answer.

- `gold_form.html` is the claude.ai Artifact page (r020, gold v2: [the published form](https://claude.ai/artifact/A9viJggUEg98t66DyAUsAk); r018: [the earlier form](https://claude.ai/artifact/KB6qqcJ7fqTGZcirwWyuNw); both private to their owner. Each sample has its own artifact, because item ids repeat across samples). Answers go to the artifact's store (collections `answers`, `recheck`, `practice`, one document per item). To export, Claude saves `answers` and `recheck` with `ArtifactData list` (`out_dir`), then `python scripts/gold_set.py import-form --dump <dump> --store <out_dir>` writes them into the sheets; an item in the store replaces all its answer cells, and invalid values are listed.
- `gold_form_local.html` opens in any browser. Answers stay in that browser's storage; copy each sheet into `data/gold/<dump>/` from Progress & export.

### Contributors

Other people answer packages of the same sample independently. The core, which everyone answers, shows how far careful people agree; the batches add items, so the gold set grows with each contributor. Each contributor gets a pseudonymous code (`S01`, `S02`, …); only the maintainer keeps the link between code and person, outside this repository.

1. `python scripts/gold_set.py kit --dump r020 --annotator S02 --packages 1` writes the contributor's files to `build/annotator_kit/r020/S02/`: [annotator_guide.md](annotator_guide.md) as `README.md`, the rule sections of this page plus the style definitions as `RULES.md`, a local form and blank sheets holding only those packages. Design and meta files stay out, because their strata name PD status, rights and style sources. A later batch is a new kit (`--packages 3`). `--repo OWNER/NAME` (with `--annotator`) builds a hosted form instead of the local one: it writes `site/` (the form as `index.html`, plus the guide and rules) and gives the guide its link variant. Serve `site/` with GitHub Pages from a separate branch of the contributor's repository that holds only those files, never from `main`, where the answers arrive. A Pages site is public even from a private repository, so the page holds only the questions. The contributor's link is the page URL plus `#key=<token>`: a fine-grained token that the maintainer, as the repository's owner, creates for that repository alone (Contents: read and write). The form commits each changed sheet to `data/gold/<dump>/` on `main` a few seconds after a change and loads the answers back on any computer; practice answers stay in the browser.
2. **Submission.** By default each contributor gets a private GitHub repository with the kit and commits the sheets exported from the form at the end of each sitting (the commits are also their backup). Someone without GitHub downloads the sheets from the form (*Progress & export* → *Download*) and sends them by email. With a hosted form (`--repo`), answers are already on `main` and there is nothing to send.
3. The maintainer finishes the core before opening anyone's answers, then copies each contributor's sheets into `data/gold/r020/annotators/<code>/`. For a hosted form these are `data/gold/<dump>/{composers,works}_sheet.tsv` from `main` of the contributor's repository.
4. `python scripts/gold_set.py score --dump r020 --pool --out data/gold/r020/report.md` scores every contributor's answers together, one per item (the maintainer's first, then the contributors in code order), and adds Krippendorff's α per field across contributors on the items two or more of them answered (`unknown` is an answer like any other, so two `unknown`s agree). When several contributors answered an item, the pooled score takes the first complete answer in that order. `--annotator S02` scores one contributor alone; `--compare <gold dir>` gives pairwise agreement and Cohen's κ.

**Gold v1 overlap (r018).** `python scripts/gold_set.py subset --dump r018` drew `data/gold/r018_overlap/` (20 composers and 30 works, at least one per r018 stratum, `source_item` maps back) for a second annotator; v2 packages replace it.

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
| `styles` (v2) | 1–3 style tags joined by `\|`, or `unknown` | The composer's style, most characteristic tag first ([Style](#style-gold-v2)) |
| `primary_period` (v2) | a period tag, or `unknown` | The period where the bulk of the output sits |
| `style_source` (v2) | short name + URL | The source you relied on for the style |
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

### Style (gold v2)

Style describes the composer's music as a whole, in the same terms the dataset's LLM labellers used, so their labels can be checked. The tags are the dataset's style vocabulary: the IMSLP periods plus `late_romantic`, and 15 movements and traditions from Wikidata and IMSLP. Each is defined, with its dates, typical composers and nearest neighbour, in [style_definitions.tsv](style_definitions.tsv) (contributors find the same table at the end of `RULES.md` and in the form).

- **`styles`**: up to 3 tags, the most characteristic first. A period tag and movement tags combine (`romantic|national_folk`). Give a movement tag only when a source places the composer in that movement, or the music clearly uses its technique; otherwise give the period tag alone. When the style changed over a career, tag the phases that are best known, in order of weight (`neoclassicism|national_folk|serialism`).
- **`primary_period`**: one period tag (`ancient` … `modern`), where the bulk of the output sits. Judge by musical language more than by date: a composer writing in a post-Wagnerian idiom in the 1930s is `late_romantic`. Language decides both ways: a tonal, lyrical idiom without post-Wagnerian chromaticism or very large forms stays `romantic` after 1880, and a composer who keeps a plainly 19th-century idiom after 1900 is `romantic` or `late_romantic`, in `styles` and here. When the language does not decide, use IMSLP's dates (Romantic 1815–1910, Early 20th century 1900–1945, Modern from 1945).
- **`unknown`**: only when no source lets you place the composer at all; then `primary_period` is `unknown` too. If you can name the period, its tag is the style.
- **`early_20th_century` and `modern`** are fallbacks for 1900–1945 and post-1945 music in a 20th-century idiom that no movement tag fits.
- **Ragtime is not `jazz`**: tag its period, mostly `early_20th_century`. `jazz` is for stride and jazz as a main part of the output.

**Sources for style**, highest first: (1) reference works: Grove / Oxford Music Online, MGG, Baker's, national music encyclopaedias and music information centres; (2) scholarly writing, publishers' composer pages, signed programme or liner notes; (3) the scores on IMSLP, to confirm a technique (a twelve-tone row, tape parts); (4) Wikipedia, only when nothing above covers the composer, and then say so in `style_source`. **Do not use** Wikidata's movement or genre statements, IMSLP's *Piece Style* line or the project's viewer: they are the labels under test.

Spend about 5 minutes on style. If that is not enough to place the composer in a movement, give the period tag and say why in `notes`.

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
| `none` | none of these applies (a nocturne, fantasia, motet, caprice, divertimento, serenade…) |

Decided cases:

- **Arrangements**: judge the original; `arrangement` is not an answer (the scorer counts the dataset's `arrangement` label as its own error class).
- **A piece drawn from a larger work** takes the larger work's form, as in Library of Congress practice ("Operas -- Excerpts"): an opera's overture is `opera`, a ballet suite is `ballet`.
- **`concerto`** only for works called concerto (concertino, concerto grosso, sinfonia concertante). Other works for soloist and orchestra take their own form or `none`; their family can still be `concerto`.
- **`string_quartet`** only for 2 violins, viola and cello; IMSLP's Quartets category also holds piano quartets.
- **`song`** is the dataset's `song` (IMSLP Songs/Lieder that are not song cycles; dumps before r019 call it `other`, which the scorer reads as `song`). The scorer reads an empty cell as `none`.

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

**Style (v2)** is a table of sources against the annotators: the dump's LLM consensus, each labeller in the LLM ledger (model, effort, condition, prompt), Wikidata movements and IMSLP work periods. The dump's crosswalk maps Wikidata's broad "Romantic music" (Q207591) and "Romanticism" (Q37068) to `late_romantic`, as it does post-romanticism; in r020 all 22 Wikidata `late_romantic` tags come from the two broad items. A second Wikidata row reads them as `romantic`, so the crosswalk can be fixed from the gold answers. It needs the Wikidata entity cache (the dump keeps tags, not QIDs) and is left out without it. Per source: tag precision (its tags the annotator also gave), tag recall (the annotator's tags, within that source's vocabulary, that it gave; no consensus or abstaining counts as no tags), the share of composers where they share a tag, mean Jaccard, and primary-period agreement with κ. Composers marked `unknown` are left out of the tag measures. Agreement on `styles` between two passes or contributors is exact set agreement, mean Jaccard and Krippendorff's α with MASI distance (Passonneau 2006), which gives partial credit when tag sets overlap.
