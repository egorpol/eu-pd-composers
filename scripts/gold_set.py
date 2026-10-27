#!/usr/bin/env python3
"""Gold set: stratified sample, blind annotation sheets, and error-rate scoring.

  python scripts/gold_set.py sample --dump r020        # gold v2 (with style) → data/gold/r020/
  python scripts/gold_set.py sample --dump r018 --gold-version 1   # how the r018 sample was drawn
  python scripts/gold_set.py form --dump r016          # annotation form → build/gold_form/r016/
  python scripts/gold_set.py import-form --dump r016 --store DIR   # form answers → sheets
  python scripts/gold_set.py score --dump r016 --out data/gold/r016/report.md
  python scripts/gold_set.py subset --dump r018 --composers 20 --works 30   # second-annotator overlap
  python scripts/gold_set.py score --dump r018 --gold-dir data/gold/r018_overlap --annotator S01 \
      --compare data/gold/r018                            # adds inter-annotator agreement
  python scripts/gold_set.py kit --dump r018_overlap      # second annotator's repo → build/annotator_kit/
  python scripts/gold_set.py kit --dump r020 --annotator S02 --packages 1,3   # a contributor's packages
  python scripts/gold_set.py kit --dump r020 --annotator S09 --packages 1 --repo someone/eu-pd-gold-S09
  python scripts/gold_set.py score --dump r020 --pool     # every contributor's answers, one per item

Offline. `sample` is deterministic for a dump and seed, and refuses to overwrite
existing files because they hold annotations. Sheets show identifiers and links
only, never the dump values under test; strata and weights live in separate
design files. `score` compares filled sheets with the dump and reports each
error rate with a Wilson interval, per stratum and weighted to the population.
Gold v2 adds style answers on composers and deals the sample into packages: the
first is the core every contributor annotates, the rest are batches.
Annotation rules: data/gold/README.md.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import DATA_DIR, REPO_ROOT, TOOL_VERSION, dump_meta_path, dump_tsv_path, pipe_split  # noqa: E402
from force_family import FORCE_FAMILIES  # noqa: E402
from llm_style_pass import cache_get_readonly  # noqa: E402
from rights_basis import BASIS_LABELS, rights_basis  # noqa: E402
from style_agreement import (  # noqa: E402
    LEDGER_PATH, cohens_kappa, dominant_imslp_period, jaccard, krippendorff_alpha, load_ledger, masi_distance,
    wikidata_styles,
)
from style_vocab import ERA_SLUGS, STYLE_SLUG_SET, STYLE_VOCAB  # noqa: E402
from wikidata_enrich import STYLE_QID_TO_TAG, _claim_entity_ids  # noqa: E402

DEFAULT_SEED = 20261010
GOLD_VERSION = 2
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
# Gold v2 composer sheets also hold style answers, in the LLM ledger's format: 0–3 vocabulary slugs, most
# characteristic first, joined by "|" (or `unknown`), and one primary period.
STYLE_FIELDS = ("styles", "primary_period", "style_source")
MAX_STYLES = 3
STYLE_DEFINITIONS = DATA_DIR / "gold" / "style_definitions.tsv"
FREE_TEXT = {"imslp_found_url", "death_source", "notes", "death_year", "style_source"}
VOCAB = {
    "wikidata_same_person": {"yes", "no", "unsure"},
    "imslp_check": {"same", "different", "found", "none", "unsure"},
    "death_status": {"dead", "living", "unknown"},
    "force_family": {f for f in FORCE_FAMILIES if f != "unclassified"} | {"unsure"},
    "genre_form": set(GOLD_GENRE_FORMS) | {"unsure"},
    "has_files": {"yes", "no", "unsure"},
    "rights_basis": set(BASIS_LABELS) | {"unsure"},
    "primary_period": set(ERA_SLUGS) | {"unknown"},
}
LINKED_CHECKS = {"same", "different", "unsure"}
UNLINKED_CHECKS = {"found", "none", "unsure"}

LLM_SOURCES = {"llm", "llm_luna_xhigh", "llm_grok"}
UNSTABLE_STATUSES = {"rejected_heuristic", "rejected_p839", "unverified_heuristic"}


def answer_fields(kind: str, columns: Any) -> tuple[str, ...]:
    """A sheet's answer columns: the v1 fields, plus the style fields before notes when the sheet has them."""
    fields = GOLD_FIELDS[kind]
    if kind == "composers" and "styles" in columns:
        return (*fields[:-1], *STYLE_FIELDS, fields[-1])
    return fields


def style_set(value: str) -> set[str]:
    """Slugs of a `styles` cell; `unknown` (no basis to judge) is the empty set."""
    return {s for s in str(value or "").split("|") if s and s != "unknown"}


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


def style_status(row: pd.Series) -> str:
    """The dump's style source for a composer: a Wikidata movement, LLM consensus only, or neither."""
    if row["style_tags_src"] == "wikidata":
        return "wikidata"
    return "consensus" if row["llm_style_tags"] else "no_consensus"


def composer_stratum_v2(row: pd.Series) -> str:
    """Gold v2: qa_flags rows and unstable IMSLP matches first, then style source × PD status."""
    if row["qa_flags"] or row["imslp_match_status"] in UNSTABLE_STATUSES:
        return "qa_or_unstable"
    return f"{style_status(row)}/{row['eu_pd_status']}"


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


def deal_packages(strata: pd.Series, packages: int, rng: np.random.Generator) -> np.ndarray:
    """Package number (1-based) per row. Each stratum's members, shuffled, are dealt round-robin with a
    running offset, so every package holds each stratum in proportion and package sizes differ by at most one."""
    out = np.zeros(len(strata), dtype=int)
    position = 0
    for stratum in sorted(set(strata)):
        members = np.flatnonzero(strata.to_numpy() == stratum)
        for row in members[rng.permutation(len(members))]:
            out[row] = position % packages + 1
            position += 1
    return out


def _sheet_rows(kind: str, sample: pd.DataFrame, composers: pd.DataFrame, style: bool = False) -> pd.DataFrame:
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
    for col in answer_fields(kind, STYLE_FIELDS if style else ()):
        shown[col] = ""
    return shown


def gold_dir(dump_id: str) -> Path:
    return DATA_DIR / "gold" / dump_id


def ensure_gold_columns(kind: str, frame: pd.DataFrame) -> pd.DataFrame:
    """Older sheets may lack a gold field; treat missing ones as empty. Style fields stay only where present."""
    fields = answer_fields(kind, frame.columns)
    shown = [c for c in frame.columns if c not in fields]
    return frame.reindex(columns=[*shown, *fields], fill_value="")


def alias_dump_genre_form(value: str) -> str:
    """Empty → `none`; dumps before r019 call `song` `other` (IMSLP Songs/Lieder)."""
    if value == "other":
        return "song"
    return value if value else "none"


def build_sample(
    composers: pd.DataFrame, works: pd.DataFrame, *, seed: int, n_composers: int, n_works: int,
    minimum: int, recheck_share: float, version: int = GOLD_VERSION, packages: int = 1,
) -> dict[str, dict[str, pd.DataFrame]]:
    """Return {set: {sheet, recheck, design}} frames; deterministic for inputs and seed.

    Version 1 is the r016/r018 design. Version 2 stratifies composers by style source × PD status, adds
    the style answer columns, and deals each set into `packages` (item ids run in package order, so
    package 1, the core, comes first); the re-check is drawn from the core.
    """
    rng = np.random.default_rng(seed)
    out: dict[str, dict[str, pd.DataFrame]] = {}
    v2 = version >= 2
    for kind, frame, total, spread in (
        ("composers", composers, n_composers, "llm_style_period" if v2 else None),
        ("works", works, n_works, "force_family"),
    ):
        frame = frame.sort_values(ID_COLUMN[kind]).reset_index(drop=True)
        stratum_of = work_stratum if kind == "works" else composer_stratum_v2 if v2 else composer_stratum
        strata = frame.apply(stratum_of, axis=1)
        sample = stratified_sample(frame, strata, total, minimum, rng, spread)
        # Shuffle so position and item id say nothing about the stratum.
        sample = sample.iloc[rng.permutation(len(sample))].reset_index(drop=True)
        if v2:
            sample["package"] = deal_packages(sample["stratum"], packages, rng)
            sample = sample.sort_values("package", kind="stable").reset_index(drop=True)
        width = max(3, len(str(len(sample))))
        sample.insert(0, "item", [f"{ITEM_PREFIX[kind]}{i + 1:0{width}d}" for i in range(len(sample))])
        pool = sample.index[sample["package"] == 1] if v2 else sample.index
        recheck_n = min(len(pool), max(1, round(recheck_share * len(sample)))) if recheck_share > 0 else 0
        recheck_items = set(sample.loc[rng.choice(pool, recheck_n, replace=False), "item"])
        sheet = _sheet_rows(kind, sample, composers, style=v2)
        sheet.insert(0, "item", sample["item"].to_numpy())
        recheck = sheet[sheet["item"].isin(recheck_items)]
        recheck = recheck.iloc[rng.permutation(len(recheck))].reset_index(drop=True)
        design = pd.DataFrame({
            "item": sample["item"], ID_COLUMN[kind]: sample[ID_COLUMN[kind]], "stratum": sample["stratum"],
            "stratum_size": sample["stratum_size"], "stratum_sample": sample["stratum_sample"],
            "weight": (sample["stratum_size"] / sample["stratum_sample"]).round(4),
            "recheck": sample["item"].isin(recheck_items).map({True: "true", False: "false"}),
        })
        if v2:
            design["package"] = sample["package"]
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
        for col in answer_fields(kind, sheets[kind].columns):
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
                          minimum=args.min_per_stratum, recheck_share=args.recheck_share,
                          version=args.gold_version, packages=args.packages)
    meta: dict[str, Any] = {
        "dump_id": args.dump, "gold_version": args.gold_version, "seed": args.seed, "tool_version": TOOL_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "allocation": "square-root of stratum size, floor --min-per-stratum, capped at stratum size",
        "min_per_stratum": args.min_per_stratum, "recheck_share": args.recheck_share,
    }
    if args.gold_version >= 2:
        meta.update(packages=args.packages, core_package=1,
                    packaging="each stratum dealt round-robin across packages; item ids run in package order; "
                              "re-check items come from the core package")
    write_gold_dir(Path(args.out_dir) if args.out_dir else gold_dir(args.dump), result, meta)


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


def style_definitions() -> list[dict[str, str]]:
    """The style vocabulary in its order, with the annotators' definitions where written (else the one-liner)."""
    read = dict(sep="\t", dtype=str, keep_default_na=False)
    rows = pd.read_csv(STYLE_DEFINITIONS, **read).set_index("slug") if STYLE_DEFINITIONS.exists() else None
    out = []
    for slug, one_liner in STYLE_VOCAB:
        row = rows.loc[slug].to_dict() if rows is not None and slug in rows.index else {}
        out.append({"slug": slug, "era": "yes" if slug in ERA_SLUGS else "", **row,
                    "definition": row.get("definition") or one_liner})
    return out


def form_data(directory: Path, dump_id: str, keep: dict[str, set[str]] | None = None) -> dict[str, Any]:
    """Shown columns of the sheets and re-check sheets, plus the practice items. Never answers or design.

    `keep` limits each sheet to those items (a contributor's packages) and leaves the re-check out.
    """
    read = dict(sep="\t", dtype=str, keep_default_na=False)
    data: dict[str, Any] = {"dump": dump_id}
    for kind in SETS:
        for part, key in (("sheet", kind), ("recheck", f"{kind}_recheck")):
            frame = pd.read_csv(directory / f"{kind}_{part}.tsv", **read)
            if kind == "composers" and part == "sheet":
                data["style"] = "styles" in frame.columns
            if keep is not None:
                frame = frame[frame["item"].isin(keep[kind])] if part == "sheet" else frame.iloc[0:0]
            # Sheets drawn before a column joined SHOWN (r016 has no gnd_id) get it empty.
            columns = ["item", *SHOWN[kind]]
            data[key] = frame.reindex(columns=columns, fill_value="").to_dict(orient="records")
    practice = json.loads((DATA_DIR / "gold" / "practice.json").read_text(encoding="utf-8"))
    data["practice"] = {kind: practice[kind] for kind in SETS}
    if data["style"]:
        data["styles"] = style_definitions()
        data["eras"] = list(ERA_SLUGS)
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


# A contributor's kit gets the guide, the rule sections of the README (plus the style definitions), the local
# form and blank sheets; never design or meta files, whose strata name PD status, rights and style sources.
GUIDE = DATA_DIR / "gold" / "annotator_guide.md"
RULES_SPAN = ("## Composers\n", "## What the scores mean\n")
# Budget per item in minutes, as the guide and the form state it.
MINUTES = {"composers": 3, "composers_style": 8, "works": 1}
# Hosted kit: owner/name (letters, digits, ".", "_", "-").
REPO_NAME = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
GUIDE_MARKERS = {"local", "link"}
GUIDE_OPEN = re.compile(r"^<!--\s*([A-Za-z0-9_-]+)\s*-->$")
GUIDE_CLOSE = re.compile(r"^<!--\s*/([A-Za-z0-9_-]+)\s*-->$")


def select_guide_variant(text: str, variant: str) -> str:
    """Keep the chosen variant's inner lines; drop the other; remove every marker line."""
    if variant not in GUIDE_MARKERS:
        raise ValueError(f"Unknown guide variant {variant!r}")
    out: list[str] = []
    active: str | None = None
    for line in text.splitlines(keepends=True):
        bare = line.strip("\n")
        opened, closed = GUIDE_OPEN.fullmatch(bare), GUIDE_CLOSE.fullmatch(bare)
        if opened:
            name = opened.group(1)
            if name not in GUIDE_MARKERS:
                raise ValueError(f"Unknown guide marker {name!r}")
            if active is not None:
                raise ValueError(f"Nested guide marker {name!r} inside {active!r}")
            active = name
            continue
        if closed:
            name = closed.group(1)
            if name not in GUIDE_MARKERS:
                raise ValueError(f"Unknown guide marker {name!r}")
            if active != name:
                raise ValueError(f"Unbalanced guide marker: closing {name!r} while {active!r} is open")
            active = None
            continue
        if active is None or active == variant:
            out.append(line)
    if active is not None:
        raise ValueError(f"Unclosed guide marker {active!r}")
    return "".join(out)


def style_rules_table() -> str:
    """The style definitions as a markdown section, for a contributor's RULES.md."""
    lines = ["## Style definitions", "", "| Tag | Period | Definition | Typical composers | Not this |",
             "|---|---|---|---|---|"]
    for row in style_definitions():
        cells = [f"`{row['slug']}`", row.get("period", ""), row["definition"], row.get("typical", ""),
                 row.get("not", "")]
        lines.append("| " + " | ".join(c.replace("|", "/") for c in cells) + " |")
    return "\n".join(lines) + "\n"


def run_kit(args: argparse.Namespace) -> Path:
    """A contributor's repo: guide, rules, local form and blank sheets (only their packages, if given)."""
    repo = getattr(args, "repo", None)
    if repo is not None:
        if not args.annotator:
            raise SystemExit("kit --repo requires --annotator (a contributor code such as S02)")
        if not REPO_NAME.fullmatch(repo):
            raise SystemExit(f"kit --repo must look like owner/name (letters, digits, ., _, -); got {repo!r}")
    read = dict(sep="\t", dtype=str, keep_default_na=False)
    directory = Path(args.gold_dir) if args.gold_dir else gold_dir(args.dump)
    default_out = REPO_ROOT / "build" / "annotator_kit" / args.dump
    out = Path(args.out_dir) if args.out_dir else default_out / args.annotator if args.annotator else default_out
    sheets = {kind: ensure_gold_columns(kind, pd.read_csv(directory / f"{kind}_sheet.tsv", **read)) for kind in SETS}
    for kind, sheet in sheets.items():
        # The gold dir's sheets may already hold the maintainer's answers; a kit gets blank answer cells.
        sheet[list(answer_fields(kind, sheet.columns))] = ""
    keep = None
    if args.packages:
        wanted = {int(p) for p in str(args.packages).split(",")}
        keep = {}
        for kind in SETS:
            design = pd.read_csv(directory / f"{kind}_design.tsv", **read)
            if "package" not in design.columns:
                raise ValueError(f"{directory / f'{kind}_design.tsv'} has no packages (a gold v1 sample)")
            keep[kind] = set(design.loc[design["package"].astype(int).isin(wanted), "item"])
            sheets[kind] = sheets[kind][sheets[kind]["item"].isin(keep[kind])]
    style = "styles" in sheets["composers"].columns
    minutes = (len(sheets["composers"]) * MINUTES["composers_style" if style else "composers"]
               + len(sheets["works"]) * MINUTES["works"])
    readme = (DATA_DIR / "gold" / "README.md").read_text(encoding="utf-8")
    rules = readme[readme.index(RULES_SPAN[0]):readme.index(RULES_SPAN[1])]
    if style:
        rules = rules.rstrip() + "\n\n" + style_rules_table()
    rules_text = "# Annotation rules\n\n" + rules.rstrip() + "\n"
    guide = GUIDE.read_text(encoding="utf-8")
    guide = guide[guide.index("# "):]
    guide = select_guide_variant(guide, "link" if repo else "local")
    for key, value in (("__GOLD_DIR__", f"data/gold/{args.dump}"), ("__N_COMPOSERS__", len(sheets["composers"])),
                       ("__N_WORKS__", len(sheets["works"])), ("__HOURS__", f"{minutes / 60 + 0.25:.1f}")):
        guide = guide.replace(key, str(value))
    target = out / "data" / "gold" / args.dump
    target.mkdir(parents=True, exist_ok=True)
    for kind in SETS:
        sheets[kind].to_csv(target / f"{kind}_sheet.tsv", sep="\t", index=False)
    (out / "README.md").write_text(guide, encoding="utf-8")
    (out / "RULES.md").write_text(rules_text, encoding="utf-8")
    data = form_data(directory, args.dump, keep)
    if repo:
        data["github"] = {"repo": repo, "branch": "main", "dir": f"data/gold/{args.dump}",
                          "annotator": args.annotator, "guide": "README.html", "rules": "RULES.html"}
    page = render_form(data, FORM_TEMPLATE.read_text(encoding="utf-8"))
    if repo:
        site = out / "site"
        site.mkdir(parents=True, exist_ok=True)
        # Pages is public even from a private repo: robots noindex, and the page holds questions only.
        site_page = LOCAL_PAGE.replace("</head>", '<meta name="robots" content="noindex">\n</head>')
        (site / "index.html").write_text(site_page.format(page=page), encoding="utf-8")
        (site / "README.md").write_text(guide, encoding="utf-8")
        (site / "RULES.md").write_text(rules_text, encoding="utf-8")
        # A kit rebuilt with --repo in the same folder must not keep a local form whose answers never reach GitHub.
        (out / "gold_form.html").unlink(missing_ok=True)
        print(f"annotator repo files → {out} ({len(sheets['composers'])} composers, {len(sheets['works'])} works)")
        print(f"Pages site → {site} (serve from a branch that holds only these files)")
    else:
        (out / "gold_form.html").write_text(LOCAL_PAGE.format(page=page), encoding="utf-8")
        print(f"annotator repo files → {out} ({len(sheets['composers'])} composers, {len(sheets['works'])} works)")
    return out


# Form store collection → sheet part it fills.
FORM_COLLECTIONS = {"answers": "sheet", "recheck": "recheck"}
# A bare identifier in death_source is a GND number (Wikidata P227 format).
GND_ID = re.compile(r"1[01]?\d{7}[0-9X]|[47]\d{6}-\d|[1-9]\d{0,7}-[0-9X]|3\d{7}[0-9X]")


def _cell(field: str, value: Any) -> str:
    if isinstance(value, list):
        value = "|".join(str(v) for v in value)
    text = " ".join(str(value or "").split())
    if field == "styles":
        return "|".join(s.strip() for s in text.split("|") if s.strip())
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
                for field in answer_fields(kind, sheet.columns):
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
        for field in answer_fields(kind, sheet.columns):
            value = row[field]
            if not value:
                continue
            if field == "styles":
                tags = value.split("|")
                if value != "unknown" and (len(tags) > MAX_STYLES or len(set(tags)) < len(tags)
                                           or not set(tags) <= STYLE_SLUG_SET):
                    problems.append(f"{row['item']} styles={value!r} (want 1–{MAX_STYLES} distinct tags "
                                    "joined by |, or unknown)")
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
    fields = [f for f in answer_fields(kind, sheet.columns) if f != "notes"]
    return sheet[fields].ne("").any(axis=1)


def _complete(kind: str, sheet: pd.DataFrame) -> pd.Series:
    """Rows with every answer the form requires (as its `complete()`)."""
    if kind == "works":
        return sheet[["force_family", "genre_form", "has_files", "rights_basis"]].ne("").all(axis=1)
    ok = sheet[["wikidata_same_person", "imslp_check", "death_status"]].ne("").all(axis=1)
    ok &= (sheet["imslp_check"] != "found") | sheet["imslp_found_url"].str.strip().ne("")
    ok &= (sheet["death_status"] == "unknown") | sheet["death_source"].str.strip().ne("")
    if "styles" in sheet.columns:
        ok &= sheet[["styles", "primary_period"]].ne("").all(axis=1)
        ok &= (sheet["styles"] != "unknown") | (sheet["primary_period"] == "unknown")
        ok &= (sheet["styles"] == "unknown") | sheet["style_source"].str.strip().ne("")
    return ok


def _clear_invalid(kind: str, sheet: pd.DataFrame, problems: list[str]) -> pd.DataFrame:
    sheet = sheet.copy()
    for problem in problems:
        item, cell = problem.split(" ", 1)
        field = cell.split("=", 1)[0]
        sheet.loc[sheet["item"] == item, field] = ""
    return sheet


def scored_rows(kind: str, sheet: pd.DataFrame, design: pd.DataFrame, dump: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Annotated sheet rows (invalid cells cleared) joined with their stratum and the dump's values (dump_*)."""
    problems = validate(kind, sheet)
    sheet = _clear_invalid(kind, sheet, problems)
    sheet = sheet[_annotated(kind, sheet)]
    key = ID_COLUMN[kind]
    dump_cols = dump.add_prefix("dump_").rename(columns={f"dump_{key}": key})
    rows = sheet.merge(design[["item", "stratum", "stratum_size"]], on="item", how="left")
    return rows.merge(dump_cols, on=key, how="left"), problems


def score_set(kind: str, sheet: pd.DataFrame, design: pd.DataFrame, dump: pd.DataFrame,
              metrics: dict[str, tuple[str, Callable]]) -> dict[str, Any]:
    rows, problems = scored_rows(kind, sheet, design, dump)
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


# Tags a source can give: Wikidata's crosswalk maps movements and late_romantic, so its recall counts those.
WIKIDATA_VOCAB = set(STYLE_QID_TO_TAG.values())
# The crosswalk maps Wikidata's broad "Romantic music" and "Romanticism" to late_romantic too; the score also
# reads them as romantic (decided 2026-10-26: score both ways, fix the crosswalk once gold answers are in).
BROAD_ROMANTIC_QIDS = {"Q207591", "Q37068"}
LATE_ROMANTIC_QIDS = {q for q, t in STYLE_QID_TO_TAG.items() if t == "late_romantic"} - BROAD_ROMANTIC_QIDS


def broad_romantic_remap(composers: pd.DataFrame) -> dict[str, set[str]] | None:
    """Wikidata tags with the broad Romantic items read as romantic, for the composers whose late_romantic
    comes from them. The dump keeps tags, not QIDs, so this reads the Wikidata entity cache: None when an
    entry is missing, so the remapped row is never half-applied."""
    out: dict[str, set[str]] = {}
    for _, row in composers.iterrows():
        tags = wikidata_styles(row)
        if "late_romantic" not in tags:
            continue
        entity = cache_get_readonly("wikidata_entity", str(row["wikidata_qid"]))
        if entity is None:
            return None
        claims = entity.get("claims", {})
        qids = set(_claim_entity_ids(claims, "P135") + _claim_entity_ids(claims, "P136"))
        if qids & BROAD_ROMANTIC_QIDS:
            out[row["composer_id"]] = (tags if qids & LATE_ROMANTIC_QIDS else tags - {"late_romantic"}) | {"romantic"}
    return out


def ledger_labellers(dump_ids: set[str], path: Path = LEDGER_PATH) -> dict[str, dict[str, dict[str, Any]]]:
    """Latest ledger decision per labeller ("model effort · condition · prompt") and composer, for the
    labellers that cover at least half of the dump (pilot-only runs are left out)."""
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for row in sorted(load_ledger(path), key=lambda r: str(r.get("created_at", ""))):
        model = " ".join(x for x in (row["model"], row.get("effort") or "") if x)
        out.setdefault(f"{model} · {row['condition']} · {row['prompt_version']}", {})[row["composer_id"]] = row
    return {name: labels for name, labels in sorted(out.items()) if len(dump_ids & set(labels)) >= len(dump_ids) / 2}


def style_sources(rows: pd.DataFrame, labellers: dict[str, dict[str, dict[str, Any]]],
                  imslp_periods: dict[str, set[str]],
                  wikidata_remap: dict[str, set[str]] | None = None) -> dict[str, Any]:
    """Each style source against the annotator's answers.

    Tag precision (source tags the annotator also gave) and recall (annotator tags within the source's
    vocabulary that the source gave) are ratio estimates over tags, weighted by stratum; a source that
    abstains or has no consensus gives no tags. Composers the annotator marked `unknown` are left out of
    the tag measures. Period agreement compares primary periods where both name one. With
    `wikidata_remap` (from `broad_romantic_remap`), Wikidata is scored a second time with those tags.
    """
    rows = rows[rows["styles"] != ""]
    human = [style_set(v) for v in rows["styles"]]
    periods = [p if p in ERA_SLUGS else "" for p in rows["primary_period"]]
    pipe = lambda v: {t for t in str(v or "").split("|") if t}
    wikidata = [wikidata_styles(pd.Series({"style_tags": t, "style_tags_src": src})) or None
                for t, src in zip(rows["dump_style_tags"], rows["dump_style_tags_src"])]
    sources: dict[str, tuple[set[str], list[set[str] | None], list[str]]] = {
        "LLM consensus (dump)": (STYLE_SLUG_SET, [pipe(v) for v in rows["dump_llm_style_tags"]],
                                 [str(v or "") for v in rows["dump_llm_style_period"]]),
        "Wikidata movements (dump)": (WIKIDATA_VOCAB, wikidata, [""] * len(rows)),
    }
    if wikidata_remap is not None:
        sources["Wikidata, broad Romantic as romantic"] = (
            WIKIDATA_VOCAB | {"romantic"}, [wikidata_remap.get(c, t) for c, t in zip(rows["composer_id"], wikidata)],
            [""] * len(rows))
    for name, labels in labellers.items():
        got = [labels.get(c) for c in rows["composer_id"]]
        sources[name] = (STYLE_SLUG_SET, [set(g["styles"] or []) if g else None for g in got],
                         [g["primary_period"] if g and g["primary_period"] in ERA_SLUGS else "" for g in got])
    imslp = [imslp_periods.get(c, set()) for c in rows["composer_id"]]
    sources["IMSLP work periods (dump)"] = (set(), [None] * len(rows),
                                            [next(iter(p)) if len(p) == 1 and next(iter(p)) in ERA_SLUGS else ""
                                             for p in imslp])
    strata = rows[["stratum", "stratum_size"]].reset_index(drop=True)
    known = [bool(h) for h in human]
    out: dict[str, Any] = {"labelled": sum(known), "unknown": len(rows) - sum(known)}
    for name, (vocab, tags, source_periods) in sources.items():
        rated = [k and t is not None for k, t in zip(known, tags)]
        rate = lambda eligible, error: stratified_rate(strata.assign(eligible=eligible, error=error))
        entry: dict[str, Any] = {"composers": sum(rated)}
        if vocab:
            entry["precision_error"] = rate([len(t) if r else 0 for r, t in zip(rated, tags)],
                                            [len(t - h) if r else 0 for r, t, h in zip(rated, tags, human)])
            entry["recall_error"] = rate([len(h & vocab) if r else 0 for r, h in zip(rated, human)],
                                         [len((h & vocab) - t) if r else 0 for r, t, h in zip(rated, tags, human)])
            entry["no_shared_tag"] = rate([int(r and bool(t)) for r, t in zip(rated, tags)],
                                          [int(r and bool(t) and not t & h) for r, t, h in zip(rated, tags, human)])
            scored = [jaccard(t, h) for r, t, h in zip(rated, tags, human) if r]
            entry["mean_jaccard"] = sum(scored) / len(scored) if scored else None
        pairs = [(sp, hp) for sp, hp in zip(source_periods, periods) if sp and hp]
        both = [int(bool(sp and hp)) for sp, hp in zip(source_periods, periods)]
        entry["period_error"] = rate(both, [int(b and sp != hp) for b, sp, hp in zip(both, source_periods, periods)])
        entry["period_kappa"] = cohens_kappa(pairs)
        out[name] = entry
    return out


def imslp_composer_periods(works: pd.DataFrame) -> dict[str, set[str]]:
    """Most frequent mapped IMSLP period among each composer's works (ties give several)."""
    periods: dict[str, list[str]] = {}
    for composer, period in zip(works["composer_id"], works["imslp_style"]):
        periods.setdefault(composer, []).extend(pipe_split(period))
    return {c: dominant_imslp_period(p) for c, p in periods.items()}


def agreement(kind: str, sheet: pd.DataFrame, recheck: pd.DataFrame, on: str = "item") -> dict[str, Any]:
    """Agreement per gold field between two passes over the same items: the first pass and the re-check
    (joined on item), or two annotators (joined on the composer or work id, since item ids differ).
    Styles are sets: exact agreement, mean Jaccard, and Krippendorff's α with MASI distance. `unknown` is an
    answer like any other (the empty set), so two `unknown`s agree, as they do in the nominal fields."""
    fields = [f for f in answer_fields(kind, sheet.columns)
              if (f in VOCAB or f in {"death_year", "styles"}) and f in recheck.columns]
    joined = sheet.merge(recheck, on=on, suffixes=("", "_re"))
    out = {}
    for field in fields:
        pairs = [(a, b) for a, b in zip(joined[field], joined[f"{field}_re"]) if a and b]
        if not pairs:
            out[field] = {"pairs": 0, "agree": None, "kappa": None}
            continue
        if field == "styles":
            sets = [(style_set(a), style_set(b)) for a, b in pairs]
            out[field] = {"pairs": len(pairs), "agree": sum(a == b for a, b in sets) / len(sets),
                          "jaccard": sum(jaccard(a, b) for a, b in sets) / len(sets),
                          "alpha": krippendorff_alpha([[frozenset(a), frozenset(b)] for a, b in sets], masi_distance)}
            continue
        agree = sum(a == b for a, b in pairs) / len(pairs)
        out[field] = {"pairs": len(pairs), "agree": agree,
                      "kappa": cohens_kappa(pairs) if field != "death_year" else None}
    return out


def agreement_multi(kind: str, sheets: dict[str, pd.DataFrame]) -> dict[str, Any]:
    """Krippendorff's α per field across every contributor, on items two or more of them answered
    (nominal; MASI distance for styles), plus the share of answer pairs that agree exactly."""
    key = ID_COLUMN[kind]
    fields = [f for f in answer_fields(kind, next(iter(sheets.values())).columns)
              if f in VOCAB or f in {"death_year", "styles"}]
    out = {}
    for field in fields:
        values: dict[str, list[Any]] = {}
        for sheet in sheets.values():
            if field not in sheet.columns:
                continue
            for unit, value in zip(sheet[key], sheet[field]):
                if value:
                    values.setdefault(unit, []).append(frozenset(style_set(value)) if field == "styles" else value)
        units = [v for v in values.values() if len(v) >= 2]
        pairs = [(a, b) for u in units for i, a in enumerate(u) for b in u[i + 1:]]
        out[field] = {"items": len(units), "pairs": len(pairs),
                      "agree": sum(a == b for a, b in pairs) / len(pairs) if pairs else None,
                      "alpha": krippendorff_alpha(units, masi_distance if field == "styles" else None)}
    return out


def _fmt_rate(rate: float | None, ci: tuple[float, float] | None) -> str:
    if rate is None or ci is None:
        return "—"
    return f"{100 * rate:.1f}% [{100 * ci[0]:.1f}, {100 * ci[1]:.1f}]"


def _agreement_table(title: str, result: dict[str, Any]) -> list[str]:
    lines = [title, "", "| Field | Pairs | Agreement | κ (styles: α, MASI) |", "|---|---:|---:|---:|"]
    for field, a in result.items():
        agree = "—" if a["agree"] is None else f"{100 * a['agree']:.0f}%"
        if a.get("jaccard") is not None:
            agree += f" (Jaccard {a['jaccard']:.2f})"
        chance = a.get("alpha") if field == "styles" else a.get("kappa")  # absent with no pairs
        lines.append(f"| {field} | {a['pairs']} | {agree} | {'—' if chance is None else f'{chance:.2f}'} |")
    return [*lines, ""]


def _contributors_table(result: dict[str, Any]) -> list[str]:
    lines = ["Agreement across contributors (items two or more answered; Krippendorff's α, MASI distance for "
             "styles):", "", "| Field | Items | Pairs | Exact agreement | α |", "|---|---:|---:|---:|---:|"]
    for field, a in result.items():
        agree = "—" if a["agree"] is None else f"{100 * a['agree']:.0f}%"
        alpha = "—" if a["alpha"] is None else f"{a['alpha']:.2f}"
        lines.append(f"| {field} | {a['items']} | {a['pairs']} | {agree} | {alpha} |")
    return [*lines, ""]


def _flip(m: dict[str, Any] | None) -> str:
    """An error-rate estimate shown as its complement (precision, recall, agreement), population-weighted."""
    if not m or m["weighted_rate"] is None:
        return "—"
    lo, hi = m["weighted_ci"]
    return _fmt_rate(1 - m["weighted_rate"], (1 - hi, 1 - lo))


def _style_table(result: dict[str, Any]) -> list[str]:
    lines = [f"### Style sources against the annotator ({result['labelled']} composers labelled, "
             f"{result['unknown']} marked unknown)", "",
             "Population estimates with 95% intervals. Precision: the source's tags the annotator also gave. "
             "Recall: the annotator's tags (within the source's vocabulary) the source gave. No consensus or "
             "abstaining counts as no tags.", "",
             "| Source | Composers | Tag precision | Tag recall | Shares a tag | Mean Jaccard | Period agreement | "
             "Period κ |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for name, e in result.items():
        if not isinstance(e, dict):
            continue
        jac = "—" if e.get("mean_jaccard") is None else f"{e['mean_jaccard']:.2f}"
        kappa = "—" if e["period_kappa"] is None else f"{e['period_kappa']:.2f}"
        lines.append(f"| {name} | {e['composers']} | {_flip(e.get('precision_error'))} | "
                     f"{_flip(e.get('recall_error'))} | {_flip(e.get('no_shared_tag'))} | {jac} | "
                     f"{_flip(e['period_error'])} | {kappa} |")
    return [*lines, ""]


def to_markdown(report: dict[str, Any]) -> str:
    who = f", annotator {report['annotator']}" if report.get("annotator") else ""
    if report.get("pooled_from"):
        who = ", pooled: " + "; ".join(
            kind + " " + (", ".join(f"{code} {n}" for code, n in counts.items() if n) or "none")
            for kind, counts in report["pooled_from"].items())
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
        if result.get("style_sources"):
            lines += _style_table(result["style_sources"])
        if result.get("recheck"):
            lines += _agreement_table("Intra-annotator re-check:", result["recheck"])
        if result.get("inter_annotator"):
            lines += _agreement_table(f"Inter-annotator agreement with `{report['compare_dir']}` "
                                      f"(items both annotated):", result["inter_annotator"])
        if result.get("contributors"):
            lines += _contributors_table(result["contributors"])
    return "\n".join(lines)


def pooled_sheets(kind: str, directory: Path) -> dict[str, pd.DataFrame]:
    """Every contributor's sheet: the gold dir's own (the maintainer, `main`), then annotators/<code>/."""
    read = dict(sep="\t", dtype=str, keep_default_na=False)
    sources = [("main", directory), *((p.name, p) for p in sorted((directory / "annotators").glob("*")) if p.is_dir())]
    return {code: ensure_gold_columns(kind, pd.read_csv(path / f"{kind}_sheet.tsv", **read))
            for code, path in sources if (path / f"{kind}_sheet.tsv").exists()}


def pool(kind: str, sheets: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, dict[str, int]]:
    """One answer per item: the first contributor in `sheets` order with a complete answer, else the first
    who answered it at all. Returns the pooled sheet and how many items each contributor supplied."""
    answered = [sheet[_annotated(kind, sheet)].assign(_by=code, _partial=lambda s: ~_complete(kind, s))
                for code, sheet in sheets.items()]
    pooled = (pd.concat(answered).sort_values("_partial", kind="stable").drop_duplicates("item", keep="first")
              .sort_values("item"))
    counts = pooled["_by"].value_counts().reindex(list(sheets), fill_value=0).to_dict()
    return pooled.drop(columns=["_by", "_partial"]), counts


def run_score(args: argparse.Namespace) -> dict[str, Any]:
    read = dict(sep="\t", dtype=str, keep_default_na=False)
    directory = Path(args.gold_dir) if args.gold_dir else gold_dir(args.dump)
    # A contributor's sheets live in annotators/<code>/ and share the gold dir's design.
    sheets = directory / "annotators" / args.annotator if args.annotator else directory
    meta_path = dump_meta_path(args.dump)
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    reference_year = int(meta.get("pd_reference_year") or 2026)
    report: dict[str, Any] = {"dump_id": args.dump, "reference_year": reference_year}
    if args.annotator:
        report["annotator"] = args.annotator
    if args.compare:
        report["compare_dir"] = args.compare
    dumps = {kind: pd.read_csv(dump_tsv_path(kind, args.dump), **read) for kind in SETS}
    for kind, metrics in (("composers", composer_metrics(reference_year)), ("works", work_metrics())):
        design = pd.read_csv(directory / f"{kind}_design.tsv", **read)
        design["stratum_size"] = design["stratum_size"].astype(int)
        if args.pool:
            contributors = pooled_sheets(kind, directory)
            sheet, report.setdefault("pooled_from", {})[kind] = pool(kind, contributors)
        else:
            sheet = ensure_gold_columns(kind, pd.read_csv(sheets / f"{kind}_sheet.tsv", **read))
        # A contributor's sheet may hold only their packages.
        if unknown := set(sheet["item"]) - set(design["item"]):
            raise ValueError(f"{kind}_sheet.tsv has items not in {kind}_design.tsv: {', '.join(sorted(unknown)[:5])}")
        report[kind] = score_set(kind, sheet, design, dumps[kind], metrics)
        if kind == "composers" and "styles" in sheet.columns:
            rows, _ = scored_rows(kind, sheet, design, dumps[kind])
            labellers = ledger_labellers(set(dumps["composers"]["composer_id"]))
            sample = dumps["composers"][dumps["composers"]["composer_id"].isin(rows["composer_id"])]
            report[kind]["style_sources"] = style_sources(rows, labellers, imslp_composer_periods(dumps["works"]),
                                                          broad_romantic_remap(sample))
        recheck_path = sheets / f"{kind}_recheck.tsv"
        if recheck_path.exists() and not args.pool:
            recheck = ensure_gold_columns(kind, pd.read_csv(recheck_path, **read))
            if len(recheck):
                report[kind]["recheck"] = agreement(kind, sheet, recheck)
        if args.compare:
            other = ensure_gold_columns(kind, pd.read_csv(Path(args.compare) / f"{kind}_sheet.tsv", **read))
            report[kind]["inter_annotator"] = agreement(kind, sheet, other, on=ID_COLUMN[kind])
        if args.pool and len(contributors) > 1:
            report[kind]["contributors"] = agreement_multi(kind, contributors)
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
    sample.add_argument("--gold-version", type=int, choices=(1, 2), default=GOLD_VERSION)
    sample.add_argument("--composers", type=int, help="Default: 100 (v2), 200 (v1)")
    sample.add_argument("--works", type=int, help="Default: 150 (v2), 300 (v1)")
    sample.add_argument("--packages", type=int, default=5, help="v2: packages to deal the sample into; 1 is the core")
    sample.add_argument("--min-per-stratum", type=int, help="Default: 6 (v2), 8 (v1)")
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
    kit = commands.add_parser("kit", help="Files for a contributor's repo or email (no design files)")
    kit.add_argument("--dump", required=True, help="Gold dir name, e.g. r020 or r018_overlap")
    kit.add_argument("--packages", help="Comma-separated package numbers (gold v2), e.g. 1,3. Default: all items")
    kit.add_argument("--annotator", help="Contributor code such as S02; names the output folder")
    kit.add_argument("--repo", help="OWNER/NAME: also write site/ for GitHub Pages (requires --annotator)")
    kit.add_argument("--gold-dir", help="Default: data/gold/<dump>/")
    kit.add_argument("--out-dir", help="Default: build/annotator_kit/<dump>/[<annotator>/]")
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
    score.add_argument("--pool", action="store_true",
                       help="Score every contributor's answers together (the gold dir's own first, then "
                            "annotators/<code>/), one per item, and add agreement across contributors")
    args = parser.parse_args()
    if args.command == "sample":
        v2 = args.gold_version >= 2
        args.composers = args.composers or (100 if v2 else 200)
        args.works = args.works or (150 if v2 else 300)
        args.min_per_stratum = args.min_per_stratum or (6 if v2 else 8)
        args.packages = args.packages if v2 else 1
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
