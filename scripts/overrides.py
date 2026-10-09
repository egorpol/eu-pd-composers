"""Load and apply hand-reviewed composer overrides to dump DataFrames."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pandas as pd

import common
from build_dump import eu_pd_fields
from common import pipe_join, pipe_split
from remap_force import rollup_composers
from wikidata_enrich import apply_year_fallbacks, enrich_from_entity

OVERRIDE_COLUMNS = (
    "composer_id",
    "field",
    "value",
    "reason",
    "source",
    "reviewer",
    "date",
)
REQUIRED_META = ("reason", "source", "reviewer", "date")
OP_DROP = "_drop"
OP_REKEY = "composer_id"
ROLLUP_COLUMNS = (
    "work_categories_present",
    "works_count_by_category",
    "works_count_total",
    "imslp_works_count",
)
PD_COLUMNS = ("eu_pd_year", "eu_pd_status", "years_until_eu_pd")
MANUAL_FLAG = "manual_override"
QID_RE = re.compile(r"^Q\d+$")
QID_FLAGS_TO_CLEAR = ("not_human", "no_composer_occupation")


def _as_str(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value)


def _pd_cell(field: str, fields: dict[str, Any]) -> str:
    value = fields[field]
    if value == "" or value is None:
        return ""
    return str(value)


def _year_text(value: Any) -> str:
    if value is None or value == "":
        return ""
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return ""


def _is_qid(value: str) -> bool:
    return bool(QID_RE.match(value))


def _append_manual_flag(qa_flags: str) -> str:
    parts = pipe_split(qa_flags)
    if MANUAL_FLAG not in parts:
        parts.append(MANUAL_FLAG)
    return pipe_join(parts)


def _drop_qid_flags(qa_flags: str) -> str:
    return pipe_join(f for f in pipe_split(qa_flags) if f not in QID_FLAGS_TO_CLEAR)


def _append_qa_flag(qa_flags: str, flag: str) -> str:
    parts = pipe_split(qa_flags)
    if flag not in parts:
        parts.append(flag)
    return pipe_join(parts)


def citizenship_iso_from_cache(citizenship_qids: str) -> tuple[str, list[str]]:
    """Map citizenship QIDs → ISO via cached P297 only (no network)."""
    codes: list[str] = []
    skipped: list[str] = []
    for qid in pipe_split(citizenship_qids):
        ent = common.cache_get("wikidata_entity", qid)
        if ent is None:
            skipped.append(qid)
            continue
        iso = None
        for claim in ent.get("claims", {}).get("P297", []):
            snak = claim.get("mainsnak", {})
            if snak.get("snaktype") != "value":
                continue
            val = snak.get("datavalue", {}).get("value")
            if isinstance(val, str) and len(val) == 2:
                iso = val.upper()
                break
        if iso is None:
            skipped.append(qid)
        elif iso not in codes:
            codes.append(iso)
    return pipe_join(codes), skipped


def _unique_errors(errors: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for err in errors:
        if err not in seen:
            seen.add(err)
            unique.append(err)
    return unique


def _raise_validation(errors: list[str]) -> None:
    unique = _unique_errors(errors)
    if unique:
        raise ValueError("overrides validation failed:\n" + "\n".join(unique))


def _validate_override_rows(overrides: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Shared per-row checks used by load_overrides and apply_composer_overrides.

    Validates non-empty composer_id/field, required metadata, duplicates,
    `_drop` value `true`, and `_drop` not combined with other fields.
    Returns a normalized copy (string cells; stripped composer_id/field) and
    any error messages (caller may append dump-specific errors before raising).
    """
    out = overrides.copy()
    for col in out.columns:
        out[col] = out[col].map(_as_str)
    if "composer_id" in out.columns:
        out["composer_id"] = out["composer_id"].map(lambda v: v.strip())
    if "field" in out.columns:
        out["field"] = out["field"].map(lambda v: v.strip())

    errors: list[str] = []
    for i, row in out.iterrows():
        line = int(i) + 2  # header is line 1 when loaded from TSV
        cid = _as_str(row.get("composer_id")).strip()
        field = _as_str(row.get("field")).strip()
        if not cid:
            errors.append(f"line {line}: missing composer_id")
        if not field:
            errors.append(f"line {line}: missing field")
        for meta in REQUIRED_META:
            if meta not in out.columns or not _as_str(row.get(meta)).strip():
                errors.append(f"line {line}: missing required metadata `{meta}`")

    if "composer_id" in out.columns and "field" in out.columns:
        dup_mask = out.duplicated(subset=["composer_id", "field"], keep=False)
        if dup_mask.any():
            for _, row in out.loc[dup_mask].iterrows():
                errors.append(
                    f"duplicate override for ({row['composer_id']}, {row['field']})"
                )

        for cid, group in out.groupby("composer_id", sort=False):
            fields = set(group["field"])
            if OP_DROP in fields and len(fields) > 1:
                errors.append(
                    f"{cid}: `_drop` cannot be combined with other fields "
                    f"({', '.join(sorted(fields - {OP_DROP}))})"
                )
            drop_rows = group[group["field"] == OP_DROP]
            for _, row in drop_rows.iterrows():
                if _as_str(row.get("value")).strip().lower() != "true":
                    errors.append(
                        f"{cid}: `_drop` value must be `true` "
                        f"(got {_as_str(row.get('value'))!r})"
                    )

    return out.reset_index(drop=True), errors


def load_overrides(path) -> pd.DataFrame:
    """Read overrides TSV; validate structure, metadata, duplicates, and drop combos."""
    path = Path(path)
    df = pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False)
    errors: list[str] = []

    missing_cols = [c for c in OVERRIDE_COLUMNS if c not in df.columns]
    if missing_cols:
        errors.append(f"overrides missing columns: {', '.join(missing_cols)}")
    extra = [c for c in df.columns if c not in OVERRIDE_COLUMNS]
    if extra:
        errors.append(f"overrides unknown columns: {', '.join(extra)}")
    if errors:
        raise ValueError("; ".join(errors))

    out = df.reindex(columns=list(OVERRIDE_COLUMNS)).copy()
    out, row_errors = _validate_override_rows(out)
    _raise_validation(row_errors)
    return out


def _rederive_qid_row(
    composers: pd.DataFrame,
    mask: pd.Series,
    new_id: str,
) -> dict[str, Any]:
    """Fill Wikidata-derived cells from the cached target entity."""
    ent = common.cache_get("wikidata_entity", new_id)
    if ent is None:
        raise ValueError(
            f"re-key target `{new_id}` has no cached wikidata_entity "
            f"(fetch it into data/cache before applying overrides)"
        )
    wd = enrich_from_entity(ent)
    wd.update(
        apply_year_fallbacks(
            wd,
            ent.get("claims", {}),
            fallback_birth=None,
            fallback_death=None,
        )
    )

    updates: dict[str, str] = {
        "name_aliases": _as_str(wd.get("name_aliases")),
        "birth_year": _year_text(wd.get("birth_year")),
        "death_year": _year_text(wd.get("death_year")),
        "date_precision": _as_str(wd.get("date_precision")),
        "citizenship_qids": _as_str(wd.get("citizenship_qids")),
        "occupations": _as_str(wd.get("occupations")),
        "scope_class": _as_str(wd.get("scope_class")),
        "scope_class_src": _as_str(wd.get("scope_class_src")),
        "is_film_composer": _as_str(wd.get("is_film_composer") or "false"),
        "qa_flags": _as_str(wd.get("qa_flags")),
        "notable_works_qids": _as_str(wd.get("notable_works_qids")),
        "style_tags": _as_str(wd.get("style_tags")),
        "style_tags_src": _as_str(wd.get("style_tags_src")),
    }
    cit_iso, cit_skipped = citizenship_iso_from_cache(updates["citizenship_qids"])
    updates["citizenship_iso"] = cit_iso

    for field, value in updates.items():
        if field in composers.columns:
            composers.loc[mask, field] = value

    imslp_info: dict[str, Any] = {
        "p839": wd.get("imslp_category_p839"),
        "action": "unchanged",
        "citizenship_iso_skipped": cit_skipped,
    }
    p839 = wd.get("imslp_category_p839")
    if p839 is None:
        imslp_info["action"] = "p839_none"
    elif "imslp_category" in composers.columns:
        current = _as_str(composers.loc[mask, "imslp_category"].iloc[0])
        if p839 == current:
            if "imslp_match_status" in composers.columns:
                composers.loc[mask, "imslp_match_status"] = "matched"
            if "imslp_match_method" in composers.columns:
                composers.loc[mask, "imslp_match_method"] = "wikidata_p839"
            imslp_info["action"] = "matched_p839"
        else:
            if "qa_flags" in composers.columns:
                composers.loc[mask, "qa_flags"] = _append_qa_flag(
                    _as_str(composers.loc[mask, "qa_flags"].iloc[0]),
                    "imslp_needs_recheck",
                )
            imslp_info["action"] = "imslp_needs_recheck"
            imslp_info["current_imslp_category"] = current
    return imslp_info


def _row_diff(
    before_row: pd.Series,
    after_row: pd.Series,
) -> dict[str, tuple[str, str]]:
    changes: dict[str, tuple[str, str]] = {}
    cols = [c for c in after_row.index if c in before_row.index]
    for col in cols:
        old = _as_str(before_row[col])
        new = _as_str(after_row[col])
        if old != new:
            changes[col] = (old, new)
    return changes


def apply_composer_overrides(
    composers: pd.DataFrame,
    works: pd.DataFrame,
    overrides: pd.DataFrame,
    *,
    pd_year: int,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Apply overrides; return new frames and a change report.

    Order: re-keys → re-derive re-keyed rows → value overrides (old file keys) →
    drops → PD fields + rollups for touched rows. Explicit value overrides win
    over re-derived values.
    """
    empty_report = {
        "overrides_applied": 0,
        "value_changes": [],
        "rekeys": [],
        "drops": [],
        "works_rekeyed": 0,
        "works_dropped": 0,
        "touched_composer_ids": [],
        "row_changes": [],
        "citizenship_iso_skipped": [],
    }
    if overrides is None or len(overrides) == 0:
        return composers.copy(), works.copy(), empty_report

    composers = composers.copy()
    works = works.copy()
    for col in composers.columns:
        composers[col] = composers[col].map(_as_str)
    for col in works.columns:
        works[col] = works[col].map(_as_str)

    tmp, errors = _validate_override_rows(overrides)

    composer_ids = set(composers["composer_id"].map(str))
    allowed_fields = set(composers.columns) | {OP_DROP}

    for i, row in tmp.iterrows():
        cid = _as_str(row["composer_id"]).strip()
        field = _as_str(row["field"]).strip()
        line = int(i) + 2
        if field and field not in allowed_fields:
            errors.append(f"line {line}: unknown field `{field}`")
        if cid and cid not in composer_ids:
            errors.append(f"line {line}: composer_id `{cid}` not present in dump")

    rekey_rows = tmp[tmp["field"] == OP_REKEY]
    rekey_targets: dict[str, str] = {}
    for _, row in rekey_rows.iterrows():
        old = row["composer_id"]
        new = _as_str(row["value"]).strip()
        if not new:
            errors.append(f"{old}: re-key target is empty")
            continue
        if new in composer_ids and new != old:
            errors.append(
                f"{old}: re-key target `{new}` already exists as another composer"
            )
        if new in rekey_targets.values():
            errors.append(f"{old}: re-key target `{new}` is used by another re-key")
        rekey_targets[old] = new
        if _is_qid(new) and common.cache_get("wikidata_entity", new) is None:
            errors.append(
                f"re-key target `{new}` has no cached wikidata_entity "
                f"(fetch it into data/cache before applying overrides)"
            )

    _raise_validation(errors)

    value_changes: list[dict[str, Any]] = []
    rekeys_report: list[dict[str, Any]] = []
    drops_report: list[dict[str, Any]] = []
    works_rekeyed = 0
    works_dropped = 0
    touched: set[str] = set()
    old_to_new: dict[str, str] = {}
    cit_skipped_all: list[str] = []

    before = composers.copy()
    by_cid = tmp.groupby("composer_id", sort=False)

    # --- 1. re-keys ---
    for cid, group in by_cid:
        rekey = group[group["field"] == OP_REKEY]
        if rekey.empty:
            continue
        row = rekey.iloc[0]
        new_id = _as_str(row["value"]).strip()
        cmask = composers["composer_id"] == cid
        wmask = works["composer_id"] == cid
        n_works = int(wmask.sum())
        composers.loc[cmask, "composer_id"] = new_id
        if _is_qid(new_id):
            if "wikidata_qid" in composers.columns:
                composers.loc[cmask, "wikidata_qid"] = new_id
            if "wikidata_url" in composers.columns:
                composers.loc[cmask, "wikidata_url"] = (
                    f"https://www.wikidata.org/wiki/{new_id}"
                )
        else:
            if "wikidata_qid" in composers.columns:
                composers.loc[cmask, "wikidata_qid"] = ""
            if "wikidata_url" in composers.columns:
                composers.loc[cmask, "wikidata_url"] = ""
            if "qa_flags" in composers.columns:
                composers.loc[cmask, "qa_flags"] = _drop_qid_flags(
                    _as_str(composers.loc[cmask, "qa_flags"].iloc[0])
                )
        works.loc[wmask, "composer_id"] = new_id
        works_rekeyed += n_works
        old_to_new[cid] = new_id
        touched.add(new_id)
        rekeys_report.append(
            {
                "old_composer_id": cid,
                "new_composer_id": new_id,
                "works_rekeyed": n_works,
                "reason": _as_str(row["reason"]),
                "rederived": False,
                "imslp": None,
            }
        )

    # --- 2. re-derive re-keyed QID rows ---
    for rk in rekeys_report:
        new_id = rk["new_composer_id"]
        if not _is_qid(new_id):
            continue
        cmask = composers["composer_id"] == new_id
        imslp_info = _rederive_qid_row(composers, cmask, new_id)
        rk["rederived"] = True
        rk["imslp"] = imslp_info
        cit_skipped_all.extend(imslp_info.get("citizenship_iso_skipped") or [])

    # --- 3. value overrides (file still uses the old key) ---
    for cid, group in by_cid:
        lookup_id = old_to_new.get(cid, cid)
        for _, row in group.iterrows():
            field = row["field"]
            if field in (OP_REKEY, OP_DROP):
                continue
            value = _as_str(row["value"])
            mask = composers["composer_id"] == lookup_id
            if not mask.any():
                continue
            old = _as_str(composers.loc[mask, field].iloc[0])
            composers.loc[mask, field] = value
            touched.add(lookup_id)
            value_changes.append(
                {
                    "composer_id": cid,
                    "applied_composer_id": lookup_id,
                    "field": field,
                    "old": old,
                    "new": value,
                    "reason": _as_str(row["reason"]),
                }
            )

    # --- 4. drops ---
    for cid, group in by_cid:
        drop = group[group["field"] == OP_DROP]
        if drop.empty:
            continue
        row = drop.iloc[0]
        lookup_id = old_to_new.get(cid, cid)
        cmask = composers["composer_id"] == lookup_id
        wmask = works["composer_id"] == lookup_id
        n_works = int(wmask.sum())
        composers = composers.loc[~cmask].reset_index(drop=True)
        works = works.loc[~wmask].reset_index(drop=True)
        works_dropped += n_works
        touched.discard(lookup_id)
        drops_report.append(
            {
                "composer_id": cid,
                "works_dropped": n_works,
                "reason": _as_str(row["reason"]),
            }
        )

    # --- 5. PD recompute + manual_override on touched survivors ---
    for cid in sorted(touched):
        mask = composers["composer_id"] == cid
        if not mask.any():
            continue
        death_raw = _as_str(composers.loc[mask, "death_year"].iloc[0]).strip()
        death: int | None
        if not death_raw:
            death = None
        else:
            try:
                death = int(float(death_raw))
            except (TypeError, ValueError):
                death = None
        fields = eu_pd_fields(death, pd_year)
        for col in PD_COLUMNS:
            if col in composers.columns:
                composers.loc[mask, col] = _pd_cell(col, fields)
        if "qa_flags" in composers.columns:
            current = _as_str(composers.loc[mask, "qa_flags"].iloc[0])
            composers.loc[mask, "qa_flags"] = _append_manual_flag(current)

    # --- rollups: write rollup columns into touched rows only ---
    if touched:
        rolled = rollup_composers(composers, works)
        for col in ROLLUP_COLUMNS:
            if col not in composers.columns or col not in rolled.columns:
                continue
            for cid in touched:
                mask = composers["composer_id"] == cid
                rmask = rolled["composer_id"] == cid
                if not mask.any() or not rmask.any():
                    continue
                value = rolled.loc[rmask, col].iloc[0]
                composers.loc[mask, col] = (
                    "" if value is None or value == "" else str(value)
                )

    composers = composers.reset_index(drop=True)
    works = works.reset_index(drop=True)

    # Full per-row diffs for the dry-run report (old key → final row).
    new_to_old = {new: old for old, new in old_to_new.items()}
    before_by_id = before.set_index("composer_id", drop=False)
    row_changes: list[dict[str, Any]] = []
    for cid in sorted(touched):
        mask = composers["composer_id"] == cid
        if not mask.any():
            continue
        old_id = new_to_old.get(cid, cid)
        if old_id not in before_by_id.index:
            continue
        changes = _row_diff(before_by_id.loc[old_id], composers.loc[mask].iloc[0])
        if changes:
            row_changes.append(
                {
                    "composer_id": cid,
                    "old_composer_id": old_id,
                    "changes": {
                        field: {"old": old, "new": new}
                        for field, (old, new) in changes.items()
                    },
                }
            )

    # Deduplicate skipped citizenship QIDs while preserving order.
    seen_cit: set[str] = set()
    cit_skipped_unique: list[str] = []
    for qid in cit_skipped_all:
        if qid not in seen_cit:
            seen_cit.add(qid)
            cit_skipped_unique.append(qid)

    report = {
        "overrides_applied": int(len(overrides)),
        "value_changes": value_changes,
        "rekeys": rekeys_report,
        "drops": drops_report,
        "works_rekeyed": works_rekeyed,
        "works_dropped": works_dropped,
        "touched_composer_ids": sorted(touched),
        "row_changes": row_changes,
        "citizenship_iso_skipped": cit_skipped_unique,
    }
    return composers, works, report
