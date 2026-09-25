# Static filter viewer

Composer-first UI over the versioned dump. No backend.

## Local

```bash
# Refresh JSON from a dump
python scripts/export_viewer_json.py --dump r008

# Serve (module scripts need HTTP)
python -m http.server 8080 --directory viewer
# open http://localhost:8080/
```

## Data

| File | Role |
|---|---|
| `data/manifest.json` | dump_id, facets, disclaimer |
| `data/composers.json` | filterable composer rows |
| `data/works_by_composer.json` | `composer_id` → work list |

## GitHub Pages

Workflow: `.github/workflows/pages.yml` publishes the `viewer/` folder on push to **`main`**.

In the GitHub repo: **Settings → Pages → Source = GitHub Actions**.
