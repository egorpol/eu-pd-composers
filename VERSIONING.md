# Versioning

This repo ships **two** versioned artifacts. Keep them separate.

## 1. Tool version (SemVer) — Git tags / GitHub Releases

Applies to code, schema contract, docs, and UI.

| Rule | Meaning |
|---|---|
| **MAJOR** | Breaking change to dump schema, CLI flags, or how consumers should read data |
| **MINOR** | New capabilities backward-compatible (extra columns *added*, new script, filter UI) |
| **PATCH** | Fixes / docs that do not change the dump contract |

**History (as published on GitHub):**

| Tag | When | Notes |
|---|---|---|
| `v1.0` | 2024-04-17 | First formal release (notebooks + TSVs) |
| `v1.1` | 2024-04-17 | IMSLP extract + LLM notebook (title was `v.1.1`) |
| next | — | **`v3.0.0`** — schema 3 dual composers/works dump, Wikidata + IMSLP works |
| next (historical) | — | **`v2.0.0`** — rename, dump-first layout, schema 2 single TSV |

Do **not** reset to `0.2` — that would go backwards from published `v1.1`.

Tags use the `vMAJOR.MINOR.PATCH` form going forward (`v2.0.0`, not `v2.0` or `v.2.0`).

## 2. Dump version (calendar id) — files under `data/`

Applies to the TSV snapshots themselves. Independent of tool SemVer.

| Field | Example |
|---|---|
| Filename | `composers_2026-09-24.tsv` |
| `dump_id` in meta JSON | `2026-09-24` |
| Optional label | `v2-compatible` if the schema matches tool major 2 |

Dumps are **immutable**: never overwrite; add a new dated file. A new tool release may still serve an older dump.

`data/dump_meta_YYYY-MM-DD.json` always records:

```json
{
  "dump_id": "2026-09-24",
  "tool_version": "2.0.0",
  "schema_version": 2,
  "created_at_utc": "...",
  "sources": { "...": "..." }
}
```

- **`tool_version`**: which builder produced it  
- **`schema_version`**: integer bumped only when columns/meaning change (usually with tool MAJOR)  
- **`dump_id`**: when the snapshot was taken  

## What consumers should pin

| You care about | Pin |
|---|---|
| Reproducible research table | `dump_id` (the TSV + meta) |
| Scripts / filter UI API | tool SemVer tag |
| “Same columns as docs” | `schema_version` |

## Changelog

Human-readable tool changes live in [CHANGELOG.md](CHANGELOG.md).  
Dump provenance lives in [data/README.md](data/README.md) + each `dump_meta_*.json`.
