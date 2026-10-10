"""Licence identifiers for open-licence evidence (IMSLP file blocks, composer homepages).

Creative Commons licences are named by their SPDX identifiers (https://spdx.org/licenses/):
`CC-BY-NC-SA-4.0`, `CC0-1.0`, `CC-PDM-1.0`. A ported licence keeps its jurisdiction as a
suffix (`CC-BY-SA-3.0-DE`), as SPDX does for the ports it lists. Wording without a version
number gives the bare family (`CC-BY-NC`), so a missing version stays visible.
"""

from __future__ import annotations

import re
from typing import Optional

_CC_URL_RE = re.compile(
    r"creativecommons\.org/(?:"
    r"licenses/(?P<code>by-nd-nc|by(?:-nc)?(?:-nd|-sa)?)(?=[/\"'\s?#]|$)"
    r"(?:/(?P<ver>\d\.\d)(?:/(?P<port>[a-z]{2}(?:-[a-z]{2})?)(?=[/\"'\s?#]|$))?)?"
    r"|publicdomain/(?P<pd>zero|mark)(?:/(?P<pdver>\d\.\d))?"
    r")",
    re.I,
)

_VERSION_RE = re.compile(r"\b(\d\.\d)\b")


def _spdx(elements: list[str], version: str, port: str = "") -> str:
    out = "CC-" + "-".join(elements)
    if version:
        out += f"-{version}"
        if port:
            out += f"-{port.upper()}"
    return out


def spdx_from_cc_url(url: str) -> Optional[str]:
    """SPDX id for a creativecommons.org licence or public-domain tool URL, else None.

    `by-nd-nc` is the CC 1.0 spelling of BY-NC-ND.
    """
    m = _CC_URL_RE.search(url or "")
    if not m:
        return None
    if m.group("pd") == "zero":
        return "CC0-" + (m.group("pdver") or "1.0")
    if m.group("pd") == "mark":
        return "CC-PDM-" + (m.group("pdver") or "1.0")
    code = m.group("code").lower()
    if code == "by-nd-nc":
        code = "by-nc-nd"
    return _spdx(code.upper().split("-"), m.group("ver") or "", m.group("port") or "")


def spdx_from_cc_text(text: str) -> Optional[str]:
    """SPDX id for Creative Commons wording ("Creative Commons Attribution-ShareAlike 4.0",
    "CC BY-NC 3.0", "CC0"), else None. Needs "Creative Commons" or a "CC" token."""
    s = (text or "").lower()
    if re.search(r"\bcc0\b|creative commons zero|cc[\s-]*zero", s):
        return "CC0-1.0"
    if "public domain mark" in s:
        return "CC-PDM-1.0"
    cc_words = "creative commons" in s
    cc_token = re.search(r"\bcc[\s-]+(by\b[\w\s-]*)", s)
    if not cc_words and not cc_token:
        return None
    if cc_words:
        nc = bool(re.search(r"non[\s-]*commercial", s))
        nd = bool(re.search(r"no[\s-]*deriv", s))
        sa = bool(re.search(r"share[\s-]*alike", s))
        by = "attribution" in s or nc or nd or sa
    else:
        tokens = set(re.split(r"[\s-]+", cc_token.group(1)))
        by, nc, nd, sa = "by" in tokens, "nc" in tokens, "nd" in tokens, "sa" in tokens
    if not by:
        return None
    elements = ["BY"] + (["NC"] if nc else []) + (["ND"] if nd else []) + (["SA"] if sa and not nd else [])
    ver = _VERSION_RE.search(s)
    return _spdx(elements, ver.group(1) if ver else "")


def is_cc_family(spdx_id: str) -> bool:
    return (spdx_id or "").startswith(("CC-", "CC0-"))
