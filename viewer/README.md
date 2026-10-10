# Static filter viewer

Composer-first UI over the versioned dump. No backend.

## Local

```bash
# Refresh JSON from a dump
python scripts/export_viewer_json.py --dump r016

# Serve (module scripts need HTTP)
python -m http.server 8080 --directory viewer
# open http://localhost:8080/
```

## Data

| File | Role |
|---|---|
| `data/manifest.json` | dump_id, facets (incl. IMSLP period vocabulary + force-source tiers), `style_tags_coverage`, pageviews window label, disclaimer |
| `data/composers.json` | filterable composer rows (`film` when applicable; missing pageviews are `null`) |
| `data/works_by_composer.json` | `composer_id` → work list (`st` / `fp` / `cf` when present; `hf: false` when the page links no score or recording; `fh` = file hosts when any file is off IMSLP's main server) |

## Filters & URL state

The filter panel is a collapsible `<details>` (“Filters · N active”): open by default above 900 px, closed below. Search stays visible above it. Facets use a responsive grid (`auto-fill`, `minmax(15rem, 1fr)`; single column below ~560 px). Long facet hints sit behind an ⓘ toggle so option lists share a top edge and a shared max height (~11 rem) with scrolling.

Filters are grouped:

- **Composer** — EU public domain (live), Scope, Citizenship, **Style** (with **Style source**).
- **Works on IMSLP** — Has works for, Label source, “Only with IMSLP works”. A composer matches when at least one work satisfies all work filters.

**EU public domain** has five mutually exclusive buckets computed in the browser from `eu_year` and the current calendar year (`PD now` / enters next 1 January / enters within Y+2–Y+5 / later / no death date). Multi-select is a union; default is `PD now`. The results “EU” column and detail pane show this live state (not the dump-frozen `eu` status). The “as of YEAR” note lives in the ⓘ hint.

**Style** is one composer-level facet: the union of Wikidata composer tags
(`styles` / `style_src`), grounded LLM consensus (`ls`) and IMSLP work periods
(`st` on works). Export maps LLM period slugs to the same IMSLP names
(`romantic` → `Romantic`, `early_20th_century` → `Early 20th century`), so a
single option matches across sources. Options are ordered era-first; each option
shows a live composer count for the selected sources. **Style source** (Wikidata ·
IMSLP works · LLM, all on by default) chooses which sources count toward matching
and those counts. A composer matches when any selected style is present under
any selected source; individual model votes do not count as consensus.

The optional composer fields `ls` (consensus list), `lp` (agreed period) and `lv`
(votes in manifest labeller order) are omitted when absent. In `lv`, `[]` means
abstention and `null` means a missing row. Policy, labeller identities and display
names live once in `manifest.llm_style_policy`; `style_tags_coverage.llm` counts
composers with non-empty consensus. Coverage `tagged` is the union of composers
with Wikidata tags or LLM consensus.

Per-work IMSLP periods remain visible as chips. The compact detail style line shows
`Style — Wikidata: … · IMSLP works: … · LLM: Impressionism (GPT-6.1 Sol:
Impressionism, Neoclassicism · Grok 4.7: Impressionism); period: Early 20th century`.
Both rows with no shared styles show `LLM: no consensus` and the votes (`—` for
abstention); one missing row shows `incomplete labelling`. Never-labelled composers
omit the LLM part.

Each work row shows IMSLP's own rights evidence as badges (tooltips give IMSLP's
meaning; none is a legal determination). Copyright warnings: not PD in EU / US,
in copyright and hosted by permission (`nonpd_licensed`, `permission_granted`),
licensed via BMI / ASCAP / GEMA, PD in US only (`pd_us_*`). Neutral notes: WIMA
files, no files on the page (`hf: false`), and files on IMSLP's US or life+50
servers (`fh`).

Filters, sort, and selected composer id are mirrored into the query string (`history.replaceState`) for shareable links (`style`, `styleSrc=wikidata,imslp,llm`). Legacy URLs keep working: `eu=pd` → `pd=now`; `eu=not_pd` → `pd=next,soon,later`; `eu=unknown_death` → `pd=unknown`; `pd=within5` → `pd=next,soon`; `ist=X` / `imslpStyle=X` → `style=X` with `styleSrc=imslp`; bare `style=Y` still applies with all sources on.

## Layout breakpoints

| Width | Behaviour |
|---|---|
| ≥901 px | Filters panel open by default; results + detail side by side |
| ≤900 px | Filters panel closed by default; results/detail stack vertically |
| ≤560 px | Facet grid becomes a single column |
| ≥320 px | Controls row wraps with gaps; no overlapping controls |

## GitHub Pages

Workflow: `.github/workflows/pages.yml` publishes the `viewer/` folder on push to **`main`**.

Live site: https://egorpol.github.io/eu-pd-composers/

In the GitHub repo: **Settings → Pages → Source = GitHub Actions**.
