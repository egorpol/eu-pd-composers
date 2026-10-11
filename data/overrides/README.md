# Composer overrides

Hand-reviewed corrections that survive dump rebuilds. Applied as the **last** transform step; they win over Wikipedia, Wikidata, IMSLP, and every automatic remap.

## Open catalogues

`open_catalogues.tsv` — composers who publish scores under an open licence on their own site (or a publisher's), separate from IMSLP file-block licences and from EU public domain. Consumed offline by `scripts/remap_open_licences.py` into `open_catalogue_url` and `open_catalogue_licence` on the composer row.

Header (tab-separated, this column order):

```
composer_id	name_display	catalogue_url	licence	rights_holder	n_score_links	evidence_url	evidence_quote	verified_on	note
```

| Column | Meaning |
|---|---|
| `composer_id` | Wikidata QID in the dump |
| `name_display` | Display name (for humans reading the file) |
| `catalogue_url` | http(s) URL of the open-score index |
| `licence` | SPDX / open-licence id accepted by `imslp_licences.is_open_licence` (CC family, CC0, PD-dedicated, CPDL, EFF OAL) |
| `rights_holder` | Who grants the licence (e.g. `composer`, `composer+publisher`) |
| `n_score_links` | Count of score links under that licence (integer) |
| `evidence_url` | http(s) page where the licence statement appears |
| `evidence_quote` | Exact quoted licence statement from that page |
| `verified_on` | Review date `YYYY-MM-DD` |
| `note` | Free-text caveats (partial catalogue, formats, …) |

**How to add a row.** Find a composer with a public score index that asserts an open licence. Record the catalogue URL, the SPDX licence id, a quote of the licence statement and the URL where it appears, then set `verified_on` to the review date. Check the wording by hand or with `scripts/homepage_licences.py` (network). Duplicate `composer_id`, a non-open `licence`, non-http(s) URLs, or blank required fields fail the remap stage loudly. A QID absent from the dump is a warning in stage meta, not an error.

```bash
python scripts/remap_open_licences.py --from-dump r020 --to r021
python scripts/remap_open_licences.py --from-dump r020 --to r999 --dry-run
```

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
