"""Offline identity evidence and collision resolution for IMSLP categories.

Page mappings contain raw cache entries, including redirect-target wikitext.
Only ``load_cached_pages`` performs I/O, through common.cache_get.
"""

from __future__ import annotations

import re
from typing import Any, Mapping
from urllib.parse import unquote

import pandas as pd

import common
from imslp import CAT_PAGE_NAMESPACE
from remap_composers import _as_int, _as_str

ACTIVE_STATUSES = {"matched", "unverified_heuristic"}
REJECTED_STATUSES = {"rejected_heuristic", "rejected_p839"}
EVIDENCE_COLUMN = "imslp_match_evidence"
_PERSON_TEMPLATE = re.compile(
    r"\{\{\s*#(?:fte\s*:\s*(person|composer|performer)\b|imslpcomposer\s*:)",
    re.I,
)


def _template_parameters(text: str, start: int) -> list[str]:
    """Split the outer template only; nested templates/links can contain pipes."""
    parameters: list[str] = []
    depth, links, pos, field_start = 1, 0, start, start
    while pos < len(text):
        pair = text[pos:pos + 2]
        if pair == "{{":
            depth += 1
        elif pair == "}}":
            depth -= 1
            if depth == 0:
                parameters.append(text[field_start:pos])
                return parameters
        elif pair == "[[":
            links += 1
        elif pair == "]]" and links:
            links -= 1
        elif text[pos] == "|" and depth == 1 and not links:
            parameters.append(text[field_start:pos])
            field_start = pos + 1
            pos += 1
            continue
        else:
            pos += 1
            continue
        pos += 2
    parameters.append(text[field_start:])
    return parameters


def _page_year(value: str) -> int | None:
    # Approximate/ambiguous text (e.g. c.1850 or 1850/51) is not an exact year.
    # Do not extract a year out of prose, another field, or a nested template.
    return _as_int(value) if re.fullmatch(r"\d{1,4}", value.strip()) else None


def _alternate_names(value: str) -> list[str]:
    # Modern pages often wrap names in {{AltNames|{{FN|First|Last}}|...}}.
    value = re.sub(
        r"\{\{\s*(FN|RFN|RFNspace)\s*\|([^{}]*)\}\}",
        lambda m: " ".join(p.strip() for p in m[2].split("|") if p.strip()),
        value, flags=re.I,
    )
    value = re.sub(r"\{\{\s*AltNames\s*\|([^{}]*)\}\}", r"\1", value, flags=re.I)
    value = re.sub(r"\[\[([^]|]+)(?:\|([^]]+))?\]\]", lambda m: m[2] or m[1], value)
    value = re.sub(r"<br\s*/?>", ";", value, flags=re.I)
    names = [part.strip() for part in re.split(r"[,;|\n]", value) if part.strip()]
    return list(dict.fromkeys(names))


def parse_person_page(wikitext: str) -> dict[str, Any]:
    """Read identity fields from the first person/composer/performer template.

    Born Month/Day and biographical prose never supply the year fields. Blank,
    absent and noninteger years are unknown. Wikipedia links come only from
    Biography Link, so a 'frequently confused with' link cannot replace them.
    """
    out: dict[str, Any] = {
        "template_kind": "none", "born_year": None, "died_year": None,
        "wikipedia_title": "", "alternate_names": [],
    }
    text = re.sub(r"<!--.*?-->", "", wikitext or "", flags=re.S)
    match = _PERSON_TEMPLATE.search(text)
    if not match:
        return out
    out["template_kind"] = (match[1] or "imslpcomposer").lower()
    fields = {}
    for parameter in _template_parameters(text, match.end()):
        if "=" in parameter:
            key, value = parameter.split("=", 1)
            fields[key.strip().casefold()] = value.strip()
    out["born_year"] = _page_year(fields.get("born year", ""))
    out["died_year"] = _page_year(fields.get("died year", ""))
    biography = fields.get("biography link", "")
    link = re.search(r"\[\[\s*:?(?:wikipedia|w)\s*:\s*([^]|]+)", biography, re.I)
    if not link:
        link = re.search(r"\{\{\s*wp\s*\|\s*([^|{}]+?)(?=\||\}\})", biography, re.I)
    if link:
        title = link[1].strip()
        # Explicit English interwiki links are equivalent to wikipedia:Title.
        if title.lower().startswith("en:"):
            title = title[3:]
        # A different language's title is not directly comparable to enwiki.
        if not re.match(r"^[a-z-]{2,12}:", title, re.I):
            out["wikipedia_title"] = title
    else:
        link = re.search(r"(?:https?:)?//en\.wikipedia\.org/wiki/([^\s\]]+)", biography, re.I)
        if link:
            out["wikipedia_title"] = link[1]
    out["alternate_names"] = _alternate_names(fields.get("alternate names", ""))
    return out


def compare_dates(
    birth_year: Any, death_year: Any, born_year: Any, died_year: Any,
) -> str:
    """Compare known pairs; strong conflicts have no agreeing pair and >10 gap.

    A difference beyond the one-year tolerance is weak when another pair
    agrees, or when all known differences are at most ten years.
    """
    pairs = [(_as_int(a), _as_int(b)) for a, b in (
        (birth_year, born_year), (death_year, died_year),
    )]
    differences = [abs(a - b) for a, b in pairs if a is not None and b is not None]
    if not differences:
        return "unknown"
    if all(difference <= 1 for difference in differences):
        return "agree"
    if min(differences) > 1 and max(differences) > 10:
        return "conflict_strong"
    return "conflict_weak"


def _normalise_wikipedia_title(title: str) -> str:
    title = unquote(title.split("#", 1)[0].split("?", 1)[0]).replace("_", " ").strip()
    return title[:1].upper() + title[1:]


def compare_wikipedia_link(wikipedia_url: Any, wikipedia_title: Any) -> str:
    """A differing link is evidence, never sufficient grounds for rejection."""
    row_title = common.wikipedia_title_from_url(_as_str(wikipedia_url))
    page_title = _as_str(wikipedia_title)
    if not row_title or not page_title:
        return "unknown"
    return "agree" if _normalise_wikipedia_title(row_title) == _normalise_wikipedia_title(page_title) else "differs"


def load_cached_pages(composers: pd.DataFrame) -> dict[str, Any]:
    """Load by the stored category key, without fetching or populating caches."""
    return {
        category: common.cache_get(CAT_PAGE_NAMESPACE, category)
        for category in dict.fromkeys(composers["imslp_category"].map(_as_str))
        if category
    }


def _append_qa_flag(out: pd.DataFrame, idx: Any, flag: str) -> None:
    """Append once while keeping existing flag spelling and order."""
    if "qa_flags" not in out:
        out["qa_flags"] = ""
    flags = _as_str(out.at[idx, "qa_flags"])
    if flag not in common.pipe_split(flags):
        out.at[idx, "qa_flags"] = flags + ("|" if flags else "") + flag


def resolve_matches(
    composers_df: pd.DataFrame, pages: Mapping[str, Any],
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Return a copy and one audit decision per composer with a category.

    Collisions use the original active claimants, even if date checks reject a
    claimant. Only P839 holders without strong date conflicts take priority.
    Multiple such holders have no unique winner under the rules: raise
    explicitly instead of choosing arbitrarily.
    """
    out = composers_df.copy()
    if EVIDENCE_COLUMN not in out:
        out.insert(out.columns.get_loc("imslp_match_method") + 1, EVIDENCE_COLUMN, "")
    decisions: list[dict[str, Any]] = []
    by_category: dict[str, list[dict[str, Any]]] = {}
    for idx, row in composers_df.iterrows():
        category = _as_str(row.get("imslp_category"))
        if not category:
            out.at[idx, EVIDENCE_COLUMN] = ""
            continue
        entry = pages.get(category)
        page_missing = bool(entry is not None and entry.get("missing"))
        parsed = parse_person_page(entry.get("wikitext", "") if entry and not page_missing else "")
        dates = compare_dates(row.get("birth_year"), row.get("death_year"), parsed["born_year"], parsed["died_year"])
        wikilink = compare_wikipedia_link(row.get("wikipedia_url"), parsed["wikipedia_title"])
        status = _as_str(row.get("imslp_match_status"))
        evidence = [f"dates_{dates}"]
        if wikilink != "unknown":
            evidence.append(f"wikilink_{wikilink}")
        if parsed["template_kind"] == "performer":
            evidence.append("performer_page")
        if page_missing:
            evidence.append("page_missing")
        if entry is None:
            reason = "page_not_cached"
        elif page_missing:
            reason = "page_missing"
        elif parsed["template_kind"] == "none":
            reason = "no_person_template"
        elif dates == "unknown":
            reason = "no_comparable_years"
        else:
            reason = f"dates_{dates}"
        decision = {
            "name": _as_str(row.get("name_display")),
            "qid": _as_str(row.get("composer_id")), "category": category,
            "birth_year": _as_int(row.get("birth_year")),
            "death_year": _as_int(row.get("death_year")),
            "imslp_born_year": parsed["born_year"], "imslp_died_year": parsed["died_year"],
            "template_kind": parsed["template_kind"], "dates": dates, "wikilink": wikilink,
            "old_status": status, "new_status": status,
            "method": _as_str(row.get("imslp_match_method")),
            "reason": reason, "evidence": evidence, "collision": None,
        }
        decisions.append(decision)
        if status not in ACTIVE_STATUSES:
            # Keep prior rejection evidence for audit on a subsequent revision.
            prior = _as_str(row.get(EVIDENCE_COLUMN))
            out.at[idx, EVIDENCE_COLUMN] = prior or common.pipe_join(evidence)
            decision["evidence"] = common.pipe_split(out.at[idx, EVIDENCE_COLUMN])
            decision["reason"] = f"existing_{status}"
            continue
        by_category.setdefault(category, []).append(decision)
        decision["_index"] = idx
        if status == "unverified_heuristic" and dates == "agree":
            decision["new_status"] = "matched"
            out.at[idx, "imslp_match_method"] = "exact_name+life_dates"
        elif dates == "conflict_weak":
            _append_qa_flag(out, idx, "imslp_dates_conflict")
            if status == "unverified_heuristic":
                decision["reason"] += f"; wikilink_{wikilink}"
                if wikilink == "agree":
                    decision["new_status"] = "matched"
                    out.at[idx, "imslp_match_method"] = "exact_name+wikilink"
        elif dates == "conflict_strong":
            if status == "matched" and decision["method"] == "wikidata_p839":
                decision["new_status"] = "rejected_p839"
                _append_qa_flag(out, idx, "imslp_p839_wrong")
            else:
                # Also applies to heuristics promoted in an earlier revision.
                decision["new_status"] = "rejected_heuristic"

    for category, claimants in by_category.items():
        if len(claimants) < 2:
            continue
        trusted = [d for d in claimants if d["old_status"] == "matched" and d["method"] == "wikidata_p839" and d["dates"] != "conflict_strong"]
        if len(trusted) > 1:
            raise ValueError(f"Multiple P839 holders for {category}: " + ", ".join(d["qid"] for d in trusted))
        winner = trusted[0] if trusted else None
        why = "wikidata_p839" if winner else "no_unique_agreeing_claimant"
        if not winner:
            agreeing = [d for d in claimants if d["dates"] == "agree"]
            if len(agreeing) == 1:
                winner, why = agreeing[0], "dates_agree"
            elif len(agreeing) > 1:
                linked = [d for d in agreeing if d["wikilink"] == "agree"]
                if len(linked) == 1:
                    winner, why = linked[0], "dates_agree+wikilink_agree"
        collision = {
            "category": category,
            "claimants": [{"qid": d["qid"], "name": d["name"], "dates": d["dates"], "wikilink": d["wikilink"]} for d in claimants],
            "winner": winner["qid"] if winner else None,
            "winner_name": winner["name"] if winner else "", "reason": why,
        }
        for decision in claimants:
            decision["collision"] = collision
            if decision is winner:
                decision["evidence"].append("collision_won")
            else:
                if decision["new_status"] != "rejected_p839":
                    decision["new_status"] = "rejected_heuristic"
                decision["evidence"].append("collision_lost")
                decision["reason"] += f"; collision_lost ({why})"
                # A promoted heuristic may lose a Wikipedia-link tie-break.
                out.at[decision["_index"], "imslp_match_method"] = decision["method"]

    for decision in decisions:
        if "_index" not in decision:
            continue
        idx = decision.pop("_index")
        out.at[idx, "imslp_match_status"] = decision["new_status"]
        out.at[idx, EVIDENCE_COLUMN] = common.pipe_join(decision["evidence"])
        if decision["new_status"] in REJECTED_STATUSES:
            out.at[idx, "imslp_url"] = ""
    return out, decisions
