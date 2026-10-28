"""CITATION.cff and .zenodo.json stay in sync for the Zenodo dataset release."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CFF_PATH = ROOT / "CITATION.cff"
ZENODO_PATH = ROOT / ".zenodo.json"
README_PATH = ROOT / "README.md"
CHANGELOG_PATH = ROOT / "CHANGELOG.md"
DATA_LICENSE_PATH = ROOT / "data" / "LICENSE.md"


def _parse_simple_cff(text: str) -> dict:
    """Minimal CFF reader for the flat layout we ship (no PyYAML)."""
    out: dict = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        if line.startswith(" ") or line.startswith("\t"):
            i += 1
            continue
        if ":" not in line:
            i += 1
            continue
        key, _, rest = line.partition(":")
        key = key.strip()
        rest = rest.strip()
        if rest == ">-":
            parts: list[str] = []
            i += 1
            while i < len(lines) and (lines[i].startswith("  ") or lines[i].startswith("\t")):
                parts.append(lines[i].strip())
                i += 1
            out[key] = " ".join(parts)
            continue
        if rest == "":
            # Block list (authors) or list of scalars (keywords)
            i += 1
            if i < len(lines) and lines[i].lstrip().startswith("- "):
                first_item = lines[i].lstrip()[2:]
                if ":" in first_item:
                    # Mapping list (authors)
                    items: list[dict] = []
                    while i < len(lines) and lines[i].lstrip().startswith("- "):
                        item: dict = {}
                        first = lines[i].lstrip()[2:]
                        fk, _, fv = first.partition(":")
                        item[fk.strip()] = _unquote(fv.strip())
                        i += 1
                        while i < len(lines) and lines[i].startswith("    "):
                            sk, _, sv = lines[i].strip().partition(":")
                            item[sk.strip()] = _unquote(sv.strip())
                            i += 1
                        items.append(item)
                    out[key] = items
                else:
                    vals: list[str] = []
                    while i < len(lines) and lines[i].lstrip().startswith("- "):
                        vals.append(_unquote(lines[i].lstrip()[2:].strip()))
                        i += 1
                    out[key] = vals
            continue
        out[key] = _unquote(rest)
        i += 1
    return out


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


def _norm_ws(text: str) -> str:
    return " ".join(text.split())


def _readme_release() -> str:
    m = re.search(r"\*\*Release:\*\*\s*v([\d.]+)", README_PATH.read_text(encoding="utf-8"))
    assert m, "README missing **Release:** vX.Y.Z"
    return m.group(1)


def _changelog_newest() -> str:
    m = re.search(r"^## \[(\d+\.\d+\.\d+)\]", CHANGELOG_PATH.read_text(encoding="utf-8"), re.M)
    assert m, "CHANGELOG missing ## [X.Y.Z] heading"
    return m.group(1)


def test_citation_and_zenodo_agree():
    cff = _parse_simple_cff(CFF_PATH.read_text(encoding="utf-8"))
    zenodo = json.loads(ZENODO_PATH.read_text(encoding="utf-8"))

    assert cff["title"] == zenodo["title"]
    assert _norm_ws(cff["abstract"]) == _norm_ws(_strip_html(zenodo["description"]))

    author = cff["authors"][0]
    creator = zenodo["creators"][0]
    assert f"{author['family-names']}, {author['given-names']}" == creator["name"]
    assert author["orcid"].rstrip("/").split("/")[-1] == creator["orcid"]
    assert author["affiliation"] == creator["affiliation"]

    assert set(cff["keywords"]) == set(zenodo["keywords"])
    assert cff["license"] == "CC-BY-SA-4.0"
    assert zenodo["license"] == "CC-BY-SA-4.0"
    assert isinstance(cff["license"], str)

    assert zenodo["upload_type"] == "dataset"
    for forbidden in ("version", "publication_date", "related_identifiers", "doi"):
        assert forbidden not in zenodo

    assert cff["type"] == "dataset"
    # concept DOI (all versions), minted by Zenodo after the first release
    assert re.fullmatch(r"10\.5281/zenodo\.\d+", cff["doi"])
    assert cff["identifiers"][0]["value"] == cff["doi"]
    assert f"https://doi.org/{cff['doi']}" in README_PATH.read_text(encoding="utf-8")
    release = _readme_release()
    assert cff["version"] == release
    assert _changelog_newest() == release
    # dumps and the crawler User-Agent record the builder version
    assert (ROOT / "scripts" / "common.py").read_text(encoding="utf-8").count(f'TOOL_VERSION = "{release}"') == 1

    text = DATA_LICENSE_PATH.read_text(encoding="utf-8")
    assert "CC BY-SA 4.0" in text
