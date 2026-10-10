"""Parse IMSLP work pages into evidence cells.

Categories give style / publication / copyright / librettist cells; the files
linked from the page give `has_files` and the IMSLP servers hosting them.
"""

from __future__ import annotations

import re
from typing import Iterable

# Exact category title → normalised flag tokens (a category may expand to several).
COPYRIGHT_CATEGORY_FLAGS: dict[str, tuple[str, ...]] = {
    "WorkNonPD-EU": ("nonpd_eu",),
    "WorkNonPD-USandEU": ("nonpd_eu", "nonpd_us"),
    "WorkNonPD-US": ("nonpd_us",),
    "Work-NonPD-US-renewed": ("nonpd_us",),
    "Items under copyright in USA due to GATT/TRIPS": ("nonpd_us",),
    "USA-C-URAA-RoST": ("nonpd_us",),
    "Files PD in the EU due to RoST": ("pd_eu_rost",),
    "Files PD in Canada due to RoST": ("pd_ca_rost",),
    "Items with text under copyright in Canada": ("nonpd_ca_text",),
    "FileNonPD-PermissionGranted": ("permission_granted",),
    # IMSLP: "not in the public domain anywhere", hosted by permission or under Creative Commons.
    "Works not in public domain": ("nonpd_licensed",),
    "WorkPD-USonly": ("pd_us_only",),
    "Work-PD-US-notrenewed": ("pd_us_notrenewed",),
    "PD-US-no notice": ("pd_us_no_notice",),
    "WIMA files": ("wima",),
    "WIMA duplicate files": ("wima",),
    "Works Licensed through BMI": ("pro_licensed",),
    "Works Licensed through ASCAP": ("pro_licensed",),
    "Works Licensed through GEMA": ("pro_licensed",),
}
COPYRIGHT_TOKENS = frozenset(t for tokens in COPYRIGHT_CATEGORY_FLAGS.values() for t in tokens)

EVIDENCE_COLUMNS = (
    "imslp_style",
    "imslp_first_published",
    "imslp_copyright_flags",
    "imslp_librettists",
)

_STYLE_SUFFIX = " style"
_LIBRETTIST_SUFFIX = "/Librettist"
_PUB_YEAR_RE = re.compile(r"^Works first published in (\d{4})$")
# NonPD / copyright / public domain / licensed are safe substrings; RoST needs
# boundaries so names like Rostropovich / Frost / Crosti are not treated as
# copyright categories. PD- and WIMA match case-sensitively ("hpd" tags, names);
# "Licensed through" skips publisher categories such as "Scores published by BMI Canada".
_UNMAPPED_COPYRIGHT_RE = re.compile(
    r"NonPD|(?<![A-Za-z0-9])RoST(?![A-Za-z0-9])|copyright|public domain|licensed through"
    r"|creative commons|permission|(?-i:PD-|WIMA)",
    re.IGNORECASE,
)

FILE_HOSTS_COLUMN = "imslp_file_hosts"
# Covers, thumbnails (TN-…) and previews (PV-…) are not scores or recordings.
_IMAGE_EXTENSIONS = frozenset({"bmp", "gif", "jpeg", "jpg", "png", "svg", "tif", "tiff", "webp"})
# Files on IMSLP's other servers are named PMLP<page>-<SERVER><n>-…; any other
# name (PMLP<page>-…, nested PMLP tokens, legacy names) is on the main server.
_SERVER_TOKEN_RE = re.compile(r"^PMLP\d+-(PML[A-Z]+)\d+-")
FILE_HOST_BY_TOKEN = {"PMLP": "ca", "PMLUS": "us", "PMLASIA": "asia"}
FILE_HOSTS = frozenset(FILE_HOST_BY_TOKEN.values())


def work_evidence(categories: list[str]) -> dict[str, str]:
    """Derive the four evidence cells from a raw IMSLP category list (no Category: prefix)."""
    styles: list[str] = []
    seen_styles: set[str] = set()
    years: list[int] = []
    flag_tokens: set[str] = set()
    librettists: list[str] = []
    seen_librettists: set[str] = set()

    for cat in categories:
        if cat.endswith(_STYLE_SUFFIX):
            name = cat[: -len(_STYLE_SUFFIX)]
            if name and name not in seen_styles:
                seen_styles.add(name)
                styles.append(name)

        match = _PUB_YEAR_RE.match(cat)
        if match:
            years.append(int(match.group(1)))

        tokens = COPYRIGHT_CATEGORY_FLAGS.get(cat)
        if tokens is not None:
            flag_tokens.update(tokens)

        if cat.endswith(_LIBRETTIST_SUFFIX):
            name = cat[: -len(_LIBRETTIST_SUFFIX)]
            if name and name not in seen_librettists:
                seen_librettists.add(name)
                librettists.append(name)

    return {
        "imslp_style": "|".join(styles),
        "imslp_first_published": str(min(years)) if years else "",
        "imslp_copyright_flags": "|".join(sorted(flag_tokens)),
        "imslp_librettists": "|".join(librettists),
    }


def score_files(files: Iterable[str]) -> list[str]:
    """Linked files that are scores, parts or recordings (not images)."""
    return [f for f in files if f.rpartition(".")[2].lower() not in _IMAGE_EXTENSIONS]


def file_host(name: str) -> str:
    """IMSLP server holding a file: ca (main), us, asia, or a lowercased new token."""
    match = _SERVER_TOKEN_RE.match(name)
    if not match:
        return "ca"
    token = match.group(1)
    return FILE_HOST_BY_TOKEN.get(token, token[3:].lower())


def file_evidence(files: list[str]) -> dict[str, str]:
    """`has_files` and sorted file hosts from a work page's linked file titles (no File: prefix)."""
    scores = score_files(files)
    return {
        "has_files": "true" if scores else "false",
        FILE_HOSTS_COLUMN: "|".join(sorted({file_host(f) for f in scores})),
    }


def unmapped_copyright_categories(categories: Iterable[str]) -> list[str]:
    """Categories that look copyright-related but are absent from the mapping table."""
    out: list[str] = []
    for cat in categories:
        if cat in COPYRIGHT_CATEGORY_FLAGS:
            continue
        if _UNMAPPED_COPYRIGHT_RE.search(cat):
            out.append(cat)
    return out
