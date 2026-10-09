# Composer overrides

Hand-reviewed corrections that survive dump rebuilds. Applied as the **last** transform step; they win over Wikipedia, Wikidata, IMSLP, and every automatic remap.

## File

`composers.tsv` — one override per row. Header:

```
composer_id	field	value	reason	source	reviewer	date
```

| Column | Meaning |
|---|---|
| `composer_id` | Row key (Wikidata QID) in the dump being transformed. For a re-key, other rows for the same person in this file still use the **old** key. |
| `field` | An existing composers column, or an operation (below). |
| `value` | New cell text (empty string clears a cell). |
| `reason` | Why this override exists (required). |
| `source` | URL or citation (required). |
| `reviewer` | Who verified it (required). |
| `date` | Review date `YYYY-MM-DD` (required). |

## Operations

- **`composer_id`** — re-key the composer to a new id. For a QID target (`Q…`), also sets `wikidata_qid` / `wikidata_url` and **re-derives** Wikidata fields from `data/cache/wikidata_entity` (offline; missing cache is an error). For a non-QID target (e.g. `wiki:John_Mitchell`), clears `wikidata_qid` / `wikidata_url`, skips re-derivation, and drops `not_human` / `no_composer_occupation` from `qa_flags`. Works rows follow the new `composer_id`. Target must not already be another composer in the dump.
- **`_drop`** with value `true` — remove the composer and its works (list entries that are not composers). Use sparingly. Cannot be combined with other fields for the same key.

## Ordering

Within one apply pass:

1. Re-keys
2. Re-derive Wikidata fields on re-keyed QID rows (from cache)
3. Value overrides (still addressed by the **old** key in this file; they win over re-derived values)
4. Drops
5. PD fields + work rollups for touched rows; append `manual_override` to `qa_flags`

## Apply

```bash
python scripts/apply_overrides.py --from-dump r010 --to r011
python scripts/apply_overrides.py --from-dump r010 --to r999 --dry-run
```

Default overrides path: `data/overrides/composers.tsv`. An empty (header-only) file is a no-op apart from setting `dump_date` on the output revision.
