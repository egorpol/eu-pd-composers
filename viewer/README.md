# Static filter viewer

Composer-first UI over the versioned dump. No backend.

## Local

```bash
# Refresh JSON from a dump
python scripts/export_viewer_json.py --dump r013

# Serve (module scripts need HTTP)
python -m http.server 8080 --directory viewer
# open http://localhost:8080/
```

## Data

| File | Role |
|---|---|
| `data/manifest.json` | dump_id, facets (incl. IMSLP period vocabulary + force-source tiers), `style_tags_coverage`, pageviews window label, disclaimer |
| `data/composers.json` | filterable composer rows (`film` when applicable; missing pageviews are `null`) |
| `data/works_by_composer.json` | `composer_id` → work list (`st` / `fp` / `cf` when present) |

## Filters & URL state

Filters are grouped:

- **Composer** — EU public domain (live), Scope, Citizenship, Movement (per composer).
- **Works on IMSLP** — Has works for, Period (per work), Label source, “Only with IMSLP works”. A composer matches when at least one work satisfies all work filters.

**EU public domain** has five mutually exclusive buckets computed in the browser from `eu_year` and the current calendar year (`PD now` / enters next 1 January / enters within Y+2–Y+5 / later / no death date). Multi-select is a union; default is `PD now`. The results “EU” column and detail pane show this live state (not the dump-frozen `eu` status).

**Period (per work)** is IMSLP’s period label on each work page (`st` / facet `imslp_style`). **Movement (per composer)** is Wikidata (or LLM) style tags (`styles` / facet `style_tags`); only a minority of composers are tagged.

Filters, sort, and selected composer id are mirrored into the query string (`history.replaceState`) for shareable links. Legacy URLs keep working: `eu=pd` → `pd=now`; `eu=not_pd` → `pd=next,soon,later`; `eu=unknown_death` → `pd=unknown`; `pd=within5` → `pd=next,soon`.

## GitHub Pages

Workflow: `.github/workflows/pages.yml` publishes the `viewer/` folder on push to **`main`**.

Live site: https://egorpol.github.io/eu-pd-composers/

In the GitHub repo: **Settings → Pages → Source = GitHub Actions**.
