# Pipeline overview

How `eu-pd-composers` builds composer + works dumps. Heuristic PD only — not legal advice.

## End-to-end flow

```mermaid
flowchart TB
  subgraph sources [Sources]
    WIKI[Wikipedia list page<br/>20th-century classical composers]
    WD[Wikidata API]
    PV[Wikimedia Pageviews API]
    IMSLP[IMSLP MediaWiki API]
  end

  subgraph scrape [scripts/build_dump.py — network]
    PARSE[Parse wikitable → raw rows]
    QID[Resolve enwiki title → QID]
    ENRICH[wbgetentities: dates, P27, P106,<br/>P135/P136, P800, P839]
    PAGEVIEWS[Monthly pageviews]
    MATCH{IMSLP match}
    P839[P839 category]
    HEUR[Heuristic Last, First]
    WORKS[categorymembers → work pages]
    CATS[Optional: work categories]
    FF1[force_family rules on scrape]
  end

  subgraph offline [Offline enrich — no scrape]
    ENR[scripts/enrich_dump.py<br/>IMSLP tags + title → force_family]
    GI[scripts/enrich_geninfo.py<br/>IMSLP General Information → imslp_geninfo]
    STY[STYLE_QID remap + prepare_llm_residual]
    LLM[scripts/llm_force_family.py<br/>gpt-6-luna xhigh via Codex<br/>residual only — run when approved]
    ROLL[Composer rollups]
  end

  subgraph out [Immutable dumps data/]
    C[composers_rNNN.tsv]
    WK[works_rNNN.tsv]
    META[dump_meta_rNNN.json]
  end

  WIKI --> PARSE --> QID
  QID --> WD --> ENRICH
  PARSE --> PAGEVIEWS
  PV --> PAGEVIEWS
  ENRICH --> MATCH
  MATCH -->|has P839| P839 --> WORKS
  MATCH -->|else| HEUR --> WORKS
  IMSLP --> P839
  IMSLP --> HEUR
  IMSLP --> WORKS --> CATS --> FF1
  PARSE --> C
  ENRICH --> C
  PAGEVIEWS --> C
  MATCH --> C
  FF1 --> WK
  C --> ENR
  WK --> ENR
  ENR --> GI --> STY
  STY -.->|approved| LLM
  LLM --> ROLL --> C
  LLM --> WK
  GI --> ROLL
  C --> META
  WK --> META
```

## force_family decision order

```mermaid
flowchart LR
  A[Work row] --> B{Non-arr IMSLP categories<br/>usable?}
  B -->|yes| C[Map For … / Operas / Songs / …<br/>force_family_src = imslp_tags]
  B -->|no / arr-only / weak| GI{General Information<br/>Instrumentation?}
  GI -->|hit| GIsrc[force_family_src = imslp_geninfo]
  GI -->|miss| D{Title heuristics?}
  D -->|hit| E[force_family_src = title]
  D -->|miss| F[unclassified / other]
  F --> G[LLM gpt-6-luna xhigh<br/>force_family_src = llm]
  C --> H[Composer rollups]
  GIsrc --> H
  E --> H
  G --> H
```

Arrangement `(arr)` category tokens never set force; opera/voice beat concerto; original orchestra beats piano reductions.

## Match statuses (IMSLP)

| `imslp_match_status` | Meaning |
|---|---|
| `matched` | Wikidata P839 category verified on IMSLP |
| `unverified_heuristic` | `Last,_First` category exists (collision risk) |
| `not_found` | No category resolved |
| `not_checked` | `--no-imslp` |

## Typical commands

```bash
# Full scrape (long; heartbeats every 30s) — calendar id OK for raw scrapes
python scripts/build_dump.py --date YYYY-MM-DD

# Promote into revision series
python scripts/promote_revision.py --from-dump YYYY-MM-DD --to rNNN

# Recompute force + merge duplicate QIDs + PD labels → next revision
python scripts/remap_force.py --from-dump r007 --to r008

# GenInfo pilot + Wikidata style remap → next revision
python scripts/enrich_geninfo.py --from-dump rNNN --to rNNN+1 --limit 200 --remap-styles

# Prepare residual LLM queue (does not call Codex)
python scripts/prepare_llm_residual.py --dump rNNN

# LLM fill when approved (Codex + gpt-6-luna, reasoning xhigh)
python scripts/llm_residual.py --from-dump rNNN --to rNNN+1 --reasoning xhigh
```

Caches: `data/cache/` (gitignored). GenInfo under `imslp_geninfo/`; LLM batches under hashed cache keys. Older dumps: git history only.

## Filter viewer

```bash
python scripts/export_viewer_json.py --dump r008
python scripts/check_release.py --dump r008 --viewer-data viewer/data
python -m http.server 8080 --directory viewer
```

Static UI under `viewer/`; GitHub Pages workflow publishes that folder from **`main`**.
