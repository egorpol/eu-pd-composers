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
| `data/manifest.json` | dump_id, facets (incl. IMSLP style vocabulary + force-source tiers), pageviews window label, disclaimer |
| `data/composers.json` | filterable composer rows (`film` when applicable; missing pageviews are `null`) |
| `data/works_by_composer.json` | `composer_id` → work list (`st` / `fp` / `cf` when present) |

## Filters & URL state

Work-level filters (force, IMSLP style, label source) combine per work: a composer matches when at least one work satisfies all three. Label source also narrows the detail work list and matching-works counts. Live PD presets (`PD now` / enters next 1 January / within 5 years) are computed from `eu_year` and the current calendar year; the stored EU PD facet stays dump-frozen. Filters, sort, and selected composer id are mirrored into the query string (`history.replaceState`) for shareable links.

## GitHub Pages

Workflow: `.github/workflows/pages.yml` publishes the `viewer/` folder on push to **`main`**.

Live site: https://egorpol.github.io/eu-pd-composers/

In the GitHub repo: **Settings → Pages → Source = GitHub Actions**.
