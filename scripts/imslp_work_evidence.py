"""Parse IMSLP work-page categories into style / publication / copyright / librettist cells."""

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
}

EVIDENCE_COLUMNS = (
    "imslp_style",
    "imslp_first_published",
    "imslp_copyright_flags",
    "imslp_librettists",
)

_STYLE_SUFFIX = " style"
_LIBRETTIST_SUFFIX = "/Librettist"
_PUB_YEAR_RE = re.compile(r"^Works first published in (\d{4})$")
# NonPD / copyright are safe substrings; RoST needs boundaries so names like
# Rostropovich / Frost / Crosti are not treated as copyright categories.
_UNMAPPED_COPYRIGHT_RE = re.compile(
    r"NonPD|(?<![A-Za-z0-9])RoST(?![A-Za-z0-9])|copyright",
    re.IGNORECASE,
)


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


def unmapped_copyright_categories(categories: Iterable[str]) -> list[str]:
    """Categories that look copyright-related but are absent from the mapping table."""
    out: list[str] = []
    for cat in categories:
        if cat in COPYRIGHT_CATEGORY_FLAGS:
            continue
        if _UNMAPPED_COPYRIGHT_RE.search(cat):
            out.append(cat)
    return out
