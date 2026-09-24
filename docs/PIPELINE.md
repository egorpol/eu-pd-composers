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
    LLM[scripts/llm_force_family.py<br/>gpt-6-luna via Codex CLI<br/>only unclassified rows]
    ROLL[Composer rollups:<br/>work_categories_present,<br/>works_count_by_category]
  end

  subgraph out [Immutable dumps data/]
    C[composers_YYYY-MM-DD.tsv]
    WK[works_YYYY-MM-DD.tsv]
    META[dump_meta_YYYY-MM-DD.json]
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
  ENR --> LLM
  LLM --> ROLL --> C
  LLM --> WK
  C --> META
  WK --> META
```

## force_family decision order

```mermaid
flowchart LR
  A[Work row] --> B{imslp_genre_categories<br/>usable?}
  B -->|yes| C[Map For … / Operas / Songs / …<br/>force_family_src = imslp_tags]
  B -->|no / weak| D{Title heuristics?}
  D -->|hit| E[force_family_src = title]
  D -->|miss| F[unclassified]
  F --> G[LLM gpt-6-luna batch<br/>force_family_src = llm]
  C --> H[Composer rollups]
  E --> H
  G --> H
```

## Match statuses (IMSLP)

| `imslp_match_status` | Meaning |
|---|---|
| `matched` | Wikidata P839 category verified on IMSLP |
| `unverified_heuristic` | `Last,_First` category exists (collision risk) |
| `not_found` | No category resolved |
| `not_checked` | `--no-imslp` |

## Typical commands

```bash
# Full scrape (long; heartbeats every 30s)
python scripts/build_dump.py --date YYYY-MM-DD

# Rules + title force_family (offline)
python scripts/enrich_dump.py --from-dump 2026-09-26 --date YYYY-MM-DD

# LLM fill for remaining unclassified (Codex + gpt-6-luna)
python scripts/llm_force_family.py --from-dump 2026-09-27 --date YYYY-MM-DD
python scripts/llm_force_family.py --from-dump 2026-09-27 --limit 80 --date YYYY-MM-DD  # pilot
```

Caches: `data/cache/` (gitignored). LLM batches resume from `data/cache/llm_force_family/`.
