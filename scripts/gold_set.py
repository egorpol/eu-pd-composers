#!/usr/bin/env python3
"""Gold set: stratified sample, blind annotation sheets, and error-rate scoring.

  python scripts/gold_set.py sample --dump r016        # writes data/gold/r016/
  python scripts/gold_set.py form --dump r016          # annotation form → build/gold_form/r016/
  python scripts/gold_set.py import-form --dump r016 --store DIR   # form answers → sheets
  python scripts/gold_set.py score --dump r016 --out data/gold/r016/report.md
  python scripts/gold_set.py subset --dump r018 --composers 20 --works 30   # second-annotator overlap
  python scripts/gold_set.py score --dump r018 --gold-dir data/gold/r018_overlap --annotator S01 \
      --compare data/gold/r018                            # adds inter-annotator agreement
  python scripts/gold_set.py kit --dump r018_overlap      # second annotator's repo → build/annotator_kit/

Offline. `sample` is deterministic for a dump and seed, and refuses to overwrite
existing files because they hold annotations. Sheets show identifiers and links
only, never the dump values under test; strata and weights live in separate
design files. `score` compares filled sheets with the dump and reports each
error rate with a Wilson interval, per stratum and weighted to the population.
Annotation rules: data/gold/README.md.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import DATA_DIR, REPO_ROOT, TOOL_VERSION, dump_meta_path, dump_tsv_path  # noqa: E402
from force_family import FORCE_FAMILIES  # noqa: E402
from rights_basis import BASIS_LABELS, rights_basis  # noqa: E402
from style_agreement import cohens_kappa  # noqa: E402

DEFAULT_SEED = 20261010
SETS = ("composers", "works")
ID_COLUMN = {"composers": "composer_id", "works": "work_id"}
ITEM_PREFIX = {"composers": "C", "works": "W"}

# Dataset map_genre_form order (first match wins), minus arrangement, plus none.
GOLD_GENRE_FORMS = (
    "opera", "ballet", "concerto", "symphony", "sonata", "suite", "variations", "etude",
    "prelude_fugue", "string_quartet", "dance", "mass_requiem", "oratorio_cantata",
    "overture_tone_poem", "song_cycle", "song", "none",
)

# Sheet columns: what the annotator sees, then what they fill in.
SHOWN = {
    "composers": ("composer_id", "name", "wikipedia_url", "wikidata_url", "imslp_url", "gnd_id", "viaf_id"),
    "works": ("work_id", "composer", "title", "imslp_work_url"),
}
GOLD_FIELDS = {
    "composers": ("wikidata_same_person", "imslp_check", "imslp_found_url", "death_status", "death_year",
                  "death_source", "notes"),
    "works": ("force_family", "genre_form", "has_files", "rights_basis", "notes"),
}
FREE_TEXT = {"imslp_found_url", "death_source", "notes", "death_year"}
VOCAB = {
    "wikidata_same_person": {"yes", "no", "unsure"},
    "imslp_check": {"same", "different", "found", "none", "unsure"},
    "death_status": {"dead", "living", "unknown"},
    "force_family": {f for f in FORCE_FAMILIES if f != "unclassified"} | {"unsure"},
    "genre_form": set(GOLD_GENRE_FORMS) | {"unsure"},
    "has_files": {"yes", "no", "unsure"},
    "rights_basis": set(BASIS_LABELS) | {"unsure"},
}
LINKED_CHECKS = {"same", "different", "unsure"}
UNLINKED_CHECKS = {"found", "none", "unsure"}

LLM_SOURCES = {"llm", "llm_luna_xhigh", "llm_grok"}
UNSTABLE_STATUSES = {"rejected_heuristic", "rejected_p839", "unverified_heuristic"}


# --- Sampling -------------------------------------------------------------

def composer_stratum(row: pd.Series) -> str:
    """qa_flags rows and rejected/unverified IMSLP matches first, then PD status × IMSLP link."""
    if row["qa_flags"]:
        return "qa_flagged"
    if row["imslp_match_status"] in UNSTABLE_STATUSES:
        return "imslp_rejected_or_unverified"
    if row["imslp_match_status"] == "matched":
        link = "p839" if row["imslp_match_method"] == "wikidata_p839" else "name"
    else:
        link = "not_found"
    return f"{row['eu_pd_status']}/{link}"


def work_stratum(row: pd.Series) -> str:
    """Force-label source; category-labelled works also split by IMSLP rights basis."""
    src = row["force_family_src"]
    if src in {"imslp_tags", "imslp_geninfo"}:
        basis = rights_basis(row["imslp_copyright_flags"])
        return f"imslp/{basis if basis in {'eu_warning', 'none'} else 'other_rights'}"
    if src == "title":
        return "title"
    if src in LLM_SOURCES:
        return "llm"
    return "unlabelled"


def allocate(sizes: dict[str, int], total: int, minimum: int) -> dict[str, int]:
    """Square-root allocation with a per-stratum floor, capped by stratum size, summing to `total`."""
    total = min(total, sum(sizes.values()))
    roots = {h: math.sqrt(n) for h, n in sizes.items()}
    raw = {h: total * roots[h] / sum(roots.values()) for h in sizes}
    alloc = {h: min(sizes[h], max(minimum, round(raw[h]))) for h in sizes}
    while (diff := total - sum(alloc.values())) != 0:
        if diff > 0:
            open_ = [h for h in sizes if alloc[h] < sizes[h]]
            h = max(open_, key=lambda k: (raw[k] - alloc[k], k))
            alloc[h] += 1
        else:
            spare = [h for h in sizes if alloc[h] > min(minimum, sizes[h])]
            h = min(spare, key=lambda k: (raw[k] - alloc[k], k))
            alloc[h] -= 1
    return alloc


def _pick(frame: pd.DataFrame, n: int, rng: np.random.Generator, spread_by: str | None) -> pd.DataFrame:
    """Simple random sample, or systematic over `spread_by` order so its values appear in proportion."""
    keyed = frame.assign(_key=rng.random(len(frame)))
    if spread_by is None:
        return keyed.sort_values("_key").head(n)
    ordered = keyed.sort_values([spread_by, "_key"]).reset_index(drop=True)
    step = len(ordered) / n
    start = rng.random() * step
    return ordered.iloc[[int(start + i * step) for i in range(n)]]


def stratified_sample(
    frame: pd.DataFrame, strata: pd.Series, total: int, minimum: int, rng: np.random.Generator,
    spread_by: str | None = None,
) -> pd.DataFrame:
    sizes = strata.value_counts().sort_index().to_dict()
    alloc = allocate(sizes, total, minimum)
    parts = []
    for stratum in sorted(sizes):
        members = frame[strata == stratum]
        part = _pick(members, alloc[stratum], rng, spread_by)
        parts.append(part.assign(stratum=stratum, stratum_size=sizes[stratum], stratum_sample=alloc[stratum]))
    return pd.concat(parts).drop(columns="_key")


def _sheet_rows(kind: str, sample: pd.DataFrame, composers: pd.DataFrame) -> pd.DataFrame:
    if kind == "composers":
        shown = pd.DataFrame({
            "composer_id": sample["composer_id"], "name": sample["name_display"],
            "wikipedia_url": sample["wikipedia_url"], "wikidata_url": sample["wikidata_url"],
            "imslp_url": sample["imslp_url"], "gnd_id": sample["gnd_id"], "viaf_id": sample["viaf_id"],
        })
    else:
        names = dict(zip(composers["composer_id"], composers["name_display"]))
        shown = pd.DataFrame({
            "work_id": sample["work_id"], "composer": sample["composer_id"].map(names).fillna(""),
            "title": sample["title"], "imslp_work_url": sample["imslp_work_url"],
        })
    for col in GOLD_FIELDS[kind]:
        shown[col] = ""
    return shown


def gold_dir(dump_id: str) -> Path:
    return DATA_DIR / "gold" / dump_id


def ensure_gold_columns(kind: str, frame: pd.DataFrame) -> pd.DataFrame:
    """Older sheets may lack a gold field; treat missing ones as empty."""
    shown = [c for c in frame.columns if c not in GOLD_FIELDS[kind]]
    return frame.reindex(columns=[*shown, *GOLD_FIELDS[kind]], fill_value="")


def alias_dump_genre_form(value: str) -> str:
    """Empty → `none`; dumps before r019 call `song` `other` (IMSLP Songs/Lieder)."""
    if value == "other":
        return "song"
    return value if value else "none"


def build_sample(
    composers: pd.DataFrame, works: pd.DataFrame, *, seed: int, n_composers: int, n_works: int,
    minimum: int, recheck_share: float,
) -> dict[str, dict[str, pd.DataFrame]]:
    """Return {set: {sheet, recheck, design}} frames; deterministic for inputs and seed."""
    rng = np.random.default_rng(seed)
    out: dict[str, dict[str, pd.DataFrame]] = {}
    for kind, frame, total, spread in (
        ("composers", composers, n_composers, None),
        ("works", works, n_works, "force_family"),
    ):
        frame = frame.sort_values(ID_COLUMN[kind]).reset_index(drop=True)
        strata = frame.apply(composer_stratum if kind == "composers" else work_stratum, axis=1)
        sample = stratified_sample(frame, strata, total, minimum, rng, spread)
        # Shuffle so position and item id say nothing about the stratum.
        sample = sample.iloc[rng.permutation(len(sample))].reset_index(drop=True)
        width = max(3, len(str(len(sample))))
        sample.insert(0, "item", [f"{ITEM_PREFIX[kind]}{i + 1:0{width}d}" for i in range(len(sample))])
        recheck_n = max(1, round(recheck_share * len(sample))) if recheck_share > 0 else 0
        recheck_items = set(sample["item"].iloc[rng.choice(len(sample), recheck_n, replace=False)])
        sheet = _sheet_rows(kind, sample, composers)
        sheet.insert(0, "item", sample["item"].to_numpy())
        recheck = sheet[sheet["item"].isin(recheck_items)]
        recheck = recheck.iloc[rng.permutation(len(recheck))].reset_index(drop=True)
        design = pd.DataFrame({
            "item": sample["item"], ID_COLUMN[kind]: sample[ID_COLUMN[kind]], "stratum": sample["stratum"],
            "stratum_size": sample["stratum_size"], "stratum_sample": sample["stratum_sample"],
            "weight": (sample["stratum_size"] / sample["stratum_sample"]).round(4),
            "recheck": sample["item"].isin(recheck_items).map({True: "true", False: "false"}),
        })
        out[kind] = {"sheet": sheet, "recheck": recheck, "design": design}
    return out


def build_subset(
    sheets: dict[str, pd.DataFrame], designs: dict[str, pd.DataFrame], *, seed: int, sizes: dict[str, int],
    minimum: int,
) -> dict[str, dict[str, pd.DataFrame]]:
    """Stratified subset of a drawn sample for a second annotator: blank sheets, new item ids and order.

    Allocation is square-root over the parent sample's stratum counts; the design keeps each stratum's
    population size, so the subset scores like any gold dir, and `source_item` maps back to the parent.
    The re-check sheets stay empty.
    """
    rng = np.random.default_rng(seed)
    out: dict[str, dict[str, pd.DataFrame]] = {}
    for kind in SETS:
        key = ID_COLUMN[kind]
        parent = designs[kind].sort_values("item").reset_index(drop=True)
        population = parent.groupby("stratum")["stratum_size"].first().astype(int)
        picked = stratified_sample(parent[["item", key]], parent["stratum"], sizes[kind], minimum, rng)
        picked = picked.iloc[rng.permutation(len(picked))].reset_index(drop=True)
        width = max(3, len(str(len(picked))))
        items = [f"{ITEM_PREFIX[kind]}{i + 1:0{width}d}" for i in range(len(picked))]
        shown = sheets[kind].set_index("item").reindex(columns=list(SHOWN[kind]), fill_value="")
        sheet = shown.loc[picked["item"]].reset_index(drop=True)
        sheet.insert(0, "item", items)
        for col in GOLD_FIELDS[kind]:
            sheet[col] = ""
        size = picked["stratum"].map(population)
        design = pd.DataFrame({
            "item": items, key: picked[key].to_numpy(), "stratum": picked["stratum"].to_numpy(),
            "stratum_size": size.to_numpy(), "stratum_sample": picked["stratum_sample"].to_numpy(),
            "weight": (size / picked["stratum_sample"]).round(4).to_numpy(), "recheck": "false",
            "source_item": picked["item"].to_numpy(),
        })
        out[kind] = {"sheet": sheet, "recheck": sheet.iloc[0:0], "design": design}
    return out


def write_gold_dir(directory: Path, result: dict[str, dict[str, pd.DataFrame]], meta: dict[str, Any]) -> None:
    """Write sheets, designs and meta.json (with per-stratum counts); never overwrites."""
    paths = {(kind, part): directory / f"{kind}_{part}.tsv" for kind in SETS for part in ("sheet", "recheck", "design")}
    meta_path = directory / "meta.json"
    existing = [p for p in [*paths.values(), meta_path] if p.exists()]
    if existing:
        raise FileExistsError("Refusing to overwrite gold files (they may hold annotations): "
                              + ", ".join(str(p) for p in existing))
    directory.mkdir(parents=True, exist_ok=True)
    for (kind, part), path in paths.items():
        result[kind][part].to_csv(path, sep="\t", index=False)
    meta["strata"] = {kind: result[kind]["design"].groupby("stratum")[["stratum_size", "stratum_sample"]].first()
                      .astype(int).to_dict(orient="index") for kind in SETS}
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    for kind in SETS:
        print(f"{kind}: {len(result[kind]['sheet'])} items, {len(result[kind]['recheck'])} recheck → "
              f"{paths[(kind, 'sheet')]}")
        for stratum, row in meta["strata"][kind].items():
            print(f"  {stratum}: {row['stratum_sample']} of {row['stratum_size']}")


def run_sample(args: argparse.Namespace) -> None:
    read = dict(sep="\t", dtype=str, keep_default_na=False)
    composers = pd.read_csv(dump_tsv_path("composers", args.dump), **read)
    works = pd.read_csv(dump_tsv_path("works", args.dump), **read)
    result = build_sample(composers, works, seed=args.seed, n_composers=args.composers, n_works=args.works,
                          minimum=args.min_per_stratum, recheck_share=args.recheck_share)
    write_gold_dir(Path(args.out_dir) if args.out_dir else gold_dir(args.dump), result, {
        "dump_id": args.dump, "seed": args.seed, "tool_version": TOOL_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "allocation": "square-root of stratum size, floor --min-per-stratum, capped at stratum size",
        "min_per_stratum": args.min_per_stratum, "recheck_share": args.recheck_share,
    })


def run_subset(args: argparse.Namespace) -> None:
    read = dict(sep="\t", dtype=str, keep_default_na=False)
    source = Path(args.gold_dir) if args.gold_dir else gold_dir(args.dump)
    sheets = {kind: pd.read_csv(source / f"{kind}_sheet.tsv", **read) for kind in SETS}
    designs = {kind: pd.read_csv(source / f"{kind}_design.tsv", **read) for kind in SETS}
    result = build_subset(sheets, designs, seed=args.seed, minimum=args.min_per_stratum,
                          sizes={"composers": args.composers, "works": args.works})
    source_name = source.resolve().relative_to(REPO_ROOT) if source.resolve().is_relative_to(REPO_ROOT) else source
    write_gold_dir(Path(args.out_dir) if args.out_dir else gold_dir(f"{args.dump}_overlap"), result, {
        "dump_id": args.dump, "subset_of": str(source_name), "seed": args.seed, "tool_version": TOOL_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "allocation": "square-root of the parent sample's stratum counts, floor --min-per-stratum; "
                      "stratum_size stays the population size",
        "min_per_stratum": args.min_per_stratum, "recheck_share": 0,
    })


# --- Annotation form ------------------------------------------------------

FORM_TEMPLATE = Path(__file__).resolve().parent / "gold_form.html"
FORM_PLACEHOLDER = "__GOLD_FORM_DATA__"
# The dump id in the title and heading keeps each dump's form apart in the artifact gallery.
FORM_DUMP_PLACEHOLDER = "__GOLD_FORM_DUMP__"
# The claude.ai artifact skeleton wraps the page; the local copy brings its own.
LOCAL_PAGE = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
              '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">\n'
              '</head>\n<body>\n{page}</body>\n</html>\n')


def form_data(directory: Path, dump_id: str) -> dict[str, Any]:
    """Shown columns of the sheets and re-check sheets, plus the practice items. Never answers or design."""
    read = dict(sep="\t", dtype=str, keep_default_na=False)
    data: dict[str, Any] = {"dump": dump_id}
    for kind in SETS:
        for part, key in (("sheet", kind), ("recheck", f"{kind}_recheck")):
            frame = pd.read_csv(directory / f"{kind}_{part}.tsv", **read)
            # Sheets drawn before a column joined SHOWN (r016 has no gnd_id) get it empty.
            columns = ["item", *SHOWN[kind]]
            data[key] = frame.reindex(columns=columns, fill_value="").to_dict(orient="records")
    practice = json.loads((DATA_DIR / "gold" / "practice.json").read_text(encoding="utf-8"))
    data["practice"] = {kind: practice[kind] for kind in SETS}
    return data


def render_form(data: dict[str, Any], template: str) -> str:
    if template.count(FORM_PLACEHOLDER) != 1:
        raise ValueError(f"Form template must hold {FORM_PLACEHOLDER} exactly once")
    # "<\/" keeps a title containing "</script>" from closing the data block early.
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return template.replace(FORM_DUMP_PLACEHOLDER, html.escape(data["dump"])).replace(FORM_PLACEHOLDER, payload)


def run_form(args: argparse.Namespace) -> list[Path]:
    directory = Path(args.gold_dir) if args.gold_dir else gold_dir(args.dump)
    out = Path(args.out_dir) if args.out_dir else REPO_ROOT / "build" / "gold_form" / args.dump
    page = render_form(form_data(directory, args.dump), FORM_TEMPLATE.read_text(encoding="utf-8"))
    out.mkdir(parents=True, exist_ok=True)
    paths = [out / "gold_form.html", out / "gold_form_local.html"]
    paths[0].write_text(page, encoding="utf-8")
    paths[1].write_text(LOCAL_PAGE.format(page=page), encoding="utf-8")
    print(f"artifact page → {paths[0]}\nlocal copy (open in a browser) → {paths[1]}")
    return paths


# A second annotator's private repo gets the guide, the rule sections of the README, the local form and the
# blank sheets; never design or meta files, whose strata name PD status and rights.
GUIDE = DATA_DIR / "gold" / "annotator_guide.md"
RULES_SPAN = ("## Composers\n", "## What the scores mean\n")


def run_kit(args: argparse.Namespace) -> Path:
    read = dict(sep="\t", dtype=str, keep_default_na=False)
    directory = Path(args.gold_dir) if args.gold_dir else gold_dir(args.dump)
    out = Path(args.out_dir) if args.out_dir else REPO_ROOT / "build" / "annotator_kit" / args.dump
    sheets = {kind: ensure_gold_columns(kind, pd.read_csv(directory / f"{kind}_sheet.tsv", **read)) for kind in SETS}
    for kind, sheet in sheets.items():
        if sheet[list(GOLD_FIELDS[kind])].ne("").any().any():
            raise ValueError(f"{directory / f'{kind}_sheet.tsv'} holds answers; a kit gets blank sheets only")
    readme = (DATA_DIR / "gold" / "README.md").read_text(encoding="utf-8")
    rules = readme[readme.index(RULES_SPAN[0]):readme.index(RULES_SPAN[1])]
    guide = GUIDE.read_text(encoding="utf-8")
    guide = guide[guide.index("# "):]
    for key, value in (("__GOLD_DIR__", f"data/gold/{args.dump}"), ("__N_COMPOSERS__", len(sheets["composers"])),
                       ("__N_WORKS__", len(sheets["works"]))):
        guide = guide.replace(key, str(value))
    target = out / "data" / "gold" / args.dump
    target.mkdir(parents=True, exist_ok=True)
    for kind in SETS:
        shutil.copyfile(directory / f"{kind}_sheet.tsv", target / f"{kind}_sheet.tsv")
    (out / "README.md").write_text(guide, encoding="utf-8")
    (out / "RULES.md").write_text("# Annotation rules\n\n" + rules.rstrip() + "\n", encoding="utf-8")
    page = render_form(form_data(directory, args.dump), FORM_TEMPLATE.read_text(encoding="utf-8"))
    (out / "gold_form.html").write_text(LOCAL_PAGE.format(page=page), encoding="utf-8")
    print(f"annotator repo files → {out}")
    return out


# Form store collection → sheet part it fills.
FORM_COLLECTIONS = {"answers": "sheet", "recheck": "recheck"}
# A bare identifier in death_source is a GND number (Wikidata P227 format).
GND_ID = re.compile(r"1[01]?\d{7}[0-9X]|[47]\d{6}-\d|[1-9]\d{0,7}-[0-9X]|3\d{7}[0-9X]")


def _cell(field: str, value: Any) -> str:
    text = " ".join(str(value or "").split())
    return f"GND {text}" if field == "death_source" and GND_ID.fullmatch(text) else text


def import_answers(directory: Path, store: Path) -> dict[str, Any]:
    """Write form answers (<store>/<collection>/<item>.json, one document per item) into the sheets.

    An item in the store replaces all its answer cells, so a cleared answer clears the cell; items not in
    the store keep theirs. A bare GND number in death_source becomes "GND <number>". Returns per-sheet counts, invalid cells and store items no sheet has.
    """
    read = dict(sep="\t", dtype=str, keep_default_na=False)
    summary: dict[str, Any] = {}
    for collection, part in FORM_COLLECTIONS.items():
        docs = {}
        for path in sorted((store / collection).glob("*.json")):
            doc = json.loads(path.read_text(encoding="utf-8"))
            docs[str(doc.get("item", path.stem))] = doc
        seen: set[str] = set()
        for kind in SETS:
            path = directory / f"{kind}_{part}.tsv"
            sheet = ensure_gold_columns(kind, pd.read_csv(path, **read))
            hits = sheet.index[sheet["item"].isin(docs)]
            for i in hits:
                doc = docs[sheet.at[i, "item"]]
                for field in GOLD_FIELDS[kind]:
                    sheet.at[i, field] = _cell(field, doc.get(field))
            if len(hits):
                sheet.to_csv(path, sep="\t", index=False)
            seen |= set(sheet["item"])
            summary[path.name] = {"imported": len(hits), "problems": validate(kind, sheet)}
        summary[f"{collection}_unknown"] = sorted(set(docs) - seen)
    return summary


def run_import(args: argparse.Namespace) -> dict[str, Any]:
    directory = Path(args.gold_dir) if args.gold_dir else gold_dir(args.dump)
    summary = import_answers(directory, Path(args.store))
    for name, entry in summary.items():
        if name.endswith("_unknown"):
            if entry:
                print(f"{name.removesuffix('_unknown')}: not in any sheet, skipped: {', '.join(entry)}")
            continue
        print(f"{name}: {entry['imported']} items imported")
        for problem in entry["problems"]:
            print(f"  invalid: {problem}")
    return summary


# --- Scoring --------------------------------------------------------------

def wilson(p: float, n: float, z: float = 1.96) -> tuple[float, float]:
    if n <= 0:
        return (0.0, 1.0)
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def stratified_rate(rows: pd.DataFrame) -> dict[str, Any]:
    """Ratio estimate of errors / eligible, weighted by stratum, with a Wilson interval on the effective n.

    `rows` holds every annotated item: stratum, stratum_size, eligible (0/1), error (0/1).
    Variance by linearisation with finite-population correction; the effective sample size
    p(1-p)/var feeds Wilson (Korn–Graubard style). Zero variance falls back to the eligible count.
    """
    eligible = int(rows["eligible"].sum())
    errors = int(rows["error"].sum())
    if eligible == 0:
        return {"eligible": 0, "errors": 0, "rate": None, "ci": None, "weighted_rate": None, "weighted_ci": None}
    raw = errors / eligible
    groups = rows.groupby("stratum")
    n_h = groups.size()
    size_h = groups["stratum_size"].first().astype(float)
    weight_h = size_h / n_h
    errors_w = (groups["error"].sum() * weight_h).sum()
    eligible_w = (groups["eligible"].sum() * weight_h).sum()
    estimate = errors_w / eligible_w
    u = rows["error"] - estimate * rows["eligible"]
    variance = 0.0
    for stratum, members in u.groupby(rows["stratum"]):
        n, size = len(members), size_h[stratum]
        if n > 1:
            variance += size * size * (1 - n / size) * members.var(ddof=1) / n
    variance /= eligible_w * eligible_w
    n_eff = estimate * (1 - estimate) / variance if variance > 0 else eligible
    return {"eligible": eligible, "errors": errors, "rate": raw, "ci": wilson(raw, eligible),
            "weighted_rate": estimate, "weighted_ci": wilson(estimate, n_eff)}


def _death_year(value: str) -> int | None:
    value = str(value).strip()
    return int(value) if len(value) == 4 and value.isdigit() else None


def composer_metrics(reference_year: int) -> dict[str, tuple[str, Callable[[pd.Series], tuple[bool, bool]]]]:
    """name → (description, row → (eligible, error)); rows join the sheet (gold) with the dump (dump_*)."""
    last_pd = reference_year - 71

    def gold_pd(row: pd.Series) -> str | None:
        year = _death_year(row["death_year"])
        if row["death_status"] == "living":
            return "not_pd"
        if row["death_status"] == "dead" and year is not None:
            return "pd" if year <= last_pd else "not_pd"
        return None

    return {
        "identity_wrong": ("Wikidata item is not the listed composer",
                           lambda r: (r["wikidata_same_person"] in {"yes", "no"}, r["wikidata_same_person"] == "no")),
        "imslp_link_wrong": ("Linked IMSLP category belongs to someone else",
                             lambda r: (bool(r["imslp_url"]) and r["imslp_check"] in {"same", "different"},
                                        r["imslp_check"] == "different")),
        "imslp_link_missed": ("No IMSLP link although IMSLP has the composer",
                              lambda r: (not r["imslp_url"] and r["imslp_check"] in {"found", "none"},
                                         r["imslp_check"] == "found")),
        "death_year_wrong": ("Death year differs from the authority source",
                             lambda r: (_death_year(r["death_year"]) is not None and r["death_status"] == "dead"
                                        and _death_year(r["dump_death_year"]) is not None,
                                        _death_year(r["death_year"]) != _death_year(r["dump_death_year"]))),
        "death_year_off_by_2plus": ("Death year off by two years or more",
                                    lambda r: (_death_year(r["death_year"]) is not None and r["death_status"] == "dead"
                                               and _death_year(r["dump_death_year"]) is not None,
                                               abs((_death_year(r["death_year"]) or 0)
                                                   - (_death_year(r["dump_death_year"]) or 0)) >= 2)),
        "death_missing": ("No death year in the dump although the composer has died",
                          lambda r: (_death_year(r["dump_death_year"]) is None
                                     and r["death_status"] in {"dead", "living"}, r["death_status"] == "dead")),
        "false_pd": ("Dump says PD now, but the composer died too recently or is alive",
                     lambda r: (r["dump_eu_pd_status"] == "pd" and gold_pd(r) is not None, gold_pd(r) == "not_pd")),
        "missed_pd": ("Dump says not PD (or no death date), but the composer is PD now",
                      lambda r: (r["dump_eu_pd_status"] != "pd" and gold_pd(r) is not None, gold_pd(r) == "pd")),
    }


def work_metrics() -> dict[str, tuple[str, Callable[[pd.Series], tuple[bool, bool]]]]:
    forms = VOCAB["genre_form"] - {"unsure"}

    def dump_form(row: pd.Series) -> str:
        return str(row.get("dump_genre_form", "") or "")

    return {
        "force_family_wrong": ("Instrumentation family differs",
                               lambda r: (r["force_family"] in VOCAB["force_family"] - {"unsure"},
                                          r["force_family"] != r["dump_force_family"])),
        "genre_form_wrong": ("Form differs",
                             lambda r: (r["genre_form"] in forms,
                                        r["genre_form"] != alias_dump_genre_form(dump_form(r)))),
        "genre_form_missed": ("No form in the dump although the original has one",
                              lambda r: (bool(r["genre_form"] in forms and not dump_form(r)),
                                         r["genre_form"] != "none")),
        "genre_form_spurious": ("Dump gives a form, but none of the listed forms applies",
                                lambda r: (bool(r["genre_form"] in forms and dump_form(r)
                                                and dump_form(r) != "arrangement"),
                                           r["genre_form"] == "none")),
        "genre_form_arrangement": ("Dump says arrangement; the original has a listed form",
                                   lambda r: (bool(r["genre_form"] in forms and dump_form(r) == "arrangement"),
                                              r["genre_form"] != "none")),
        "has_files_wrong": ("has_files differs",
                            lambda r: (r["has_files"] in {"yes", "no"} and r["dump_has_files"] in {"true", "false"},
                                       (r["has_files"] == "yes") != (r["dump_has_files"] == "true"))),
        "rights_basis_wrong": ("IMSLP rights basis differs",
                               lambda r: (r["rights_basis"] in set(BASIS_LABELS),
                                          r["rights_basis"] != rights_basis(r["dump_imslp_copyright_flags"]))),
    }


def validate(kind: str, sheet: pd.DataFrame) -> list[str]:
    """Invalid cells as 'item field=value' strings; blank cells are fine (not yet annotated)."""
    problems = []
    for _, row in sheet.iterrows():
        for field in GOLD_FIELDS[kind]:
            value = row[field]
            if not value:
                continue
            if field in VOCAB and value not in VOCAB[field]:
                problems.append(f"{row['item']} {field}={value!r}")
            if field == "death_year" and _death_year(value) is None:
                problems.append(f"{row['item']} death_year={value!r} (want YYYY)")
            if field == "imslp_check":
                allowed = LINKED_CHECKS if row["imslp_url"] else UNLINKED_CHECKS
                if value not in allowed:
                    problems.append(f"{row['item']} imslp_check={value!r} (want {'/'.join(sorted(allowed))})")
    return problems


def _annotated(kind: str, sheet: pd.DataFrame) -> pd.Series:
    fields = [f for f in GOLD_FIELDS[kind] if f != "notes"]
    return sheet[fields].ne("").any(axis=1)


def _clear_invalid(kind: str, sheet: pd.DataFrame, problems: list[str]) -> pd.DataFrame:
    sheet = sheet.copy()
    for problem in problems:
        item, cell = problem.split(" ", 1)
        field = cell.split("=", 1)[0]
        sheet.loc[sheet["item"] == item, field] = ""
    return sheet


def score_set(kind: str, sheet: pd.DataFrame, design: pd.DataFrame, dump: pd.DataFrame,
              metrics: dict[str, tuple[str, Callable]]) -> dict[str, Any]:
    problems = validate(kind, sheet)
    sheet = _clear_invalid(kind, sheet, problems)
    sheet = sheet[_annotated(kind, sheet)]
    key = ID_COLUMN[kind]
    dump_cols = dump.add_prefix("dump_").rename(columns={f"dump_{key}": key})
    rows = sheet.merge(design[["item", "stratum", "stratum_size"]], on="item", how="left")
    rows = rows.merge(dump_cols, on=key, how="left")
    out: dict[str, Any] = {"annotated": len(rows), "sampled": len(design), "invalid_cells": problems, "metrics": {}}
    for name, (description, rule) in metrics.items():
        flags = [rule(r) for _, r in rows.iterrows()]
        scored = rows[["stratum", "stratum_size"]].assign(
            eligible=[int(e) for e, _ in flags], error=[int(e and x) for e, x in flags])
        result = stratified_rate(scored)
        per_stratum = {}
        for stratum, members in scored.groupby("stratum"):
            n, k = int(members["eligible"].sum()), int(members["error"].sum())
            per_stratum[stratum] = {"eligible": n, "errors": k, "rate": k / n if n else None,
                                    "ci": wilson(k / n, n) if n else None}
        out["metrics"][name] = {"description": description, **result, "by_stratum": per_stratum}
    return out


def agreement(kind: str, sheet: pd.DataFrame, recheck: pd.DataFrame, on: str = "item") -> dict[str, Any]:
    """Agreement per gold field between two passes over the same items: the first pass and the re-check
    (joined on item), or two annotators (joined on the composer or work id, since item ids differ)."""
    fields = [f for f in GOLD_FIELDS[kind] if f in VOCAB or f == "death_year"]
    joined = sheet.merge(recheck, on=on, suffixes=("", "_re"))
    out = {}
    for field in fields:
        pairs = [(a, b) for a, b in zip(joined[field], joined[f"{field}_re"]) if a and b]
        if not pairs:
            out[field] = {"pairs": 0, "agree": None, "kappa": None}
            continue
        agree = sum(a == b for a, b in pairs) / len(pairs)
        out[field] = {"pairs": len(pairs), "agree": agree,
                      "kappa": cohens_kappa(pairs) if field != "death_year" else None}
    return out


def _fmt_rate(rate: float | None, ci: tuple[float, float] | None) -> str:
    if rate is None or ci is None:
        return "—"
    return f"{100 * rate:.1f}% [{100 * ci[0]:.1f}, {100 * ci[1]:.1f}]"


def _agreement_table(title: str, result: dict[str, Any]) -> list[str]:
    lines = [title, "", "| Field | Pairs | Agreement | κ |", "|---|---:|---:|---:|"]
    for field, a in result.items():
        agree = "—" if a["agree"] is None else f"{100 * a['agree']:.0f}%"
        kappa = "—" if a["kappa"] is None else f"{a['kappa']:.2f}"
        lines.append(f"| {field} | {a['pairs']} | {agree} | {kappa} |")
    return [*lines, ""]


def to_markdown(report: dict[str, Any]) -> str:
    who = f", annotator {report['annotator']}" if report.get("annotator") else ""
    lines = [f"# Gold set scores ({report['dump_id']}{who}, reference year {report['reference_year']})", "",
             "Error rates with 95% Wilson intervals. *Sample* is the share among checked items; *population* "
             "weights each stratum by its size (square-root allocation oversamples small strata).", ""]
    for kind in SETS:
        result = report[kind]
        lines += [f"## {kind.capitalize()}: {result['annotated']} of {result['sampled']} annotated", ""]
        if result["invalid_cells"]:
            lines += [f"Invalid cells ignored ({len(result['invalid_cells'])}): "
                      + "; ".join(result["invalid_cells"][:20]), ""]
        lines += ["| Metric | Checked | Errors | Sample rate | Population estimate |", "|---|---:|---:|---:|---:|"]
        for name, m in result["metrics"].items():
            lines.append(f"| {m['description']} (`{name}`) | {m['eligible']} | {m['errors']} | "
                         f"{_fmt_rate(m['rate'], m['ci'])} | {_fmt_rate(m['weighted_rate'], m['weighted_ci'])} |")
        lines += ["", "<details><summary>By stratum</summary>", ""]
        for name, m in result["metrics"].items():
            cells = [f"{s} {v['errors']}/{v['eligible']}" for s, v in m["by_stratum"].items() if v["eligible"]]
            lines.append(f"- `{name}`: " + (", ".join(cells) or "—"))
        lines += ["", "</details>", ""]
        if result.get("recheck"):
            lines += _agreement_table("Intra-annotator re-check:", result["recheck"])
        if result.get("inter_annotator"):
            lines += _agreement_table(f"Inter-annotator agreement with `{report['compare_dir']}` "
                                      f"(items both annotated):", result["inter_annotator"])
    return "\n".join(lines)


def run_score(args: argparse.Namespace) -> dict[str, Any]:
    read = dict(sep="\t", dtype=str, keep_default_na=False)
    directory = Path(args.gold_dir) if args.gold_dir else gold_dir(args.dump)
    # A second annotator's sheets live in annotators/<code>/ and share the gold dir's design.
    sheets = directory / "annotators" / args.annotator if args.annotator else directory
    meta_path = dump_meta_path(args.dump)
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    reference_year = int(meta.get("pd_reference_year") or 2026)
    report: dict[str, Any] = {"dump_id": args.dump, "reference_year": reference_year}
    if args.annotator:
        report["annotator"] = args.annotator
    if args.compare:
        report["compare_dir"] = args.compare
    for kind, metrics in (("composers", composer_metrics(reference_year)), ("works", work_metrics())):
        sheet = ensure_gold_columns(kind, pd.read_csv(sheets / f"{kind}_sheet.tsv", **read))
        design = pd.read_csv(directory / f"{kind}_design.tsv", **read)
        design["stratum_size"] = design["stratum_size"].astype(int)
        dump = pd.read_csv(dump_tsv_path(kind, args.dump), **read)
        if set(sheet["item"]) != set(design["item"]):
            raise ValueError(f"{kind}_sheet.tsv items do not match {kind}_design.tsv")
        report[kind] = score_set(kind, sheet, design, dump, metrics)
        recheck_path = sheets / f"{kind}_recheck.tsv"
        if recheck_path.exists():
            recheck = ensure_gold_columns(kind, pd.read_csv(recheck_path, **read))
            if len(recheck):
                report[kind]["recheck"] = agreement(kind, sheet, recheck)
        if args.compare:
            other = ensure_gold_columns(kind, pd.read_csv(Path(args.compare) / f"{kind}_sheet.tsv", **read))
            report[kind]["inter_annotator"] = agreement(kind, sheet, other, on=ID_COLUMN[kind])
    markdown = to_markdown(report)
    if args.out:
        Path(args.out).write_text(markdown, encoding="utf-8")
    else:
        print(markdown, end="")
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    sample = commands.add_parser("sample", help="Draw the stratified sample and write blank sheets")
    sample.add_argument("--dump", required=True)
    sample.add_argument("--seed", type=int, default=DEFAULT_SEED)
    sample.add_argument("--composers", type=int, default=200)
    sample.add_argument("--works", type=int, default=300)
    sample.add_argument("--min-per-stratum", type=int, default=8)
    sample.add_argument("--recheck-share", type=float, default=0.10)
    sample.add_argument("--out-dir", help="Default: data/gold/<dump>/")
    subset = commands.add_parser("subset", help="Draw a blank overlap subset of a gold dir for a second annotator")
    subset.add_argument("--dump", required=True)
    subset.add_argument("--seed", type=int, default=DEFAULT_SEED)
    subset.add_argument("--composers", type=int, default=20)
    subset.add_argument("--works", type=int, default=30)
    subset.add_argument("--min-per-stratum", type=int, default=1)
    subset.add_argument("--gold-dir", help="Parent sample. Default: data/gold/<dump>/")
    subset.add_argument("--out-dir", help="Default: data/gold/<dump>_overlap/")
    form = commands.add_parser("form", help="Build the annotation form page from the sheets")
    form.add_argument("--dump", required=True)
    form.add_argument("--gold-dir", help="Default: data/gold/<dump>/")
    form.add_argument("--out-dir", help="Default: build/gold_form/<dump>/")
    kit = commands.add_parser("kit", help="Files for a second annotator's private repo (no design files)")
    kit.add_argument("--dump", required=True, help="Gold dir name, e.g. r018_overlap")
    kit.add_argument("--gold-dir", help="Default: data/gold/<dump>/")
    kit.add_argument("--out-dir", help="Default: build/annotator_kit/<dump>/")
    imp = commands.add_parser("import-form", help="Write answers saved by the form into the sheets")
    imp.add_argument("--dump", required=True)
    imp.add_argument("--store", required=True, help="Directory with answers/ and recheck/, one <item>.json each")
    imp.add_argument("--gold-dir", help="Default: data/gold/<dump>/")
    score = commands.add_parser("score", help="Score filled sheets against the dump")
    score.add_argument("--dump", required=True)
    score.add_argument("--gold-dir", help="Default: data/gold/<dump>/")
    score.add_argument("--out", help="Markdown report path (default: stdout)")
    score.add_argument("--json", help="Optional JSON report path")
    score.add_argument("--annotator", help="Score <gold-dir>/annotators/<code>/ sheets against <gold-dir>'s design")
    score.add_argument("--compare", help="Gold dir of another annotator; adds inter-annotator agreement")
    args = parser.parse_args()
    if args.command == "sample":
        run_sample(args)
    elif args.command == "subset":
        run_subset(args)
    elif args.command == "form":
        run_form(args)
    elif args.command == "kit":
        run_kit(args)
    elif args.command == "import-form":
        run_import(args)
    else:
        run_score(args)


if __name__ == "__main__":
    main()
