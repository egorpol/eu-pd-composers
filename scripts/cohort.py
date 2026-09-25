"""20th-century cohort gates (Wikipedia list quality control).

The Wikipedia list sometimes links the wrong person (e.g. Johann Melchior
Gletle: list cells say 1926–1983, Wikidata is 1626–1683). Prefer Wikidata
years when present; drop rows that cannot be 20th-century composers.
"""

from __future__ import annotations

from typing import Any, Optional


# Died before 1900 → not a 20th-century composer.
MIN_DEATH_YEAR_INCLUSIVE = 1900
# List cell vs Wikidata disagreement larger than this + pre-modern WD birth → identity error.
YEAR_MISMATCH_THRESHOLD = 40


def _as_year(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        if isinstance(value, float) and str(value) == "nan":
            return None
    except Exception:
        pass
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", ""}:
        return None
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


def cohort_exclusion_reason(
    *,
    birth_year: Any,
    death_year: Any,
    list_birth_year: Any = None,
    list_death_year: Any = None,
) -> Optional[str]:
    """Return a short reason string if the row should be excluded, else None."""
    birth = _as_year(birth_year)
    death = _as_year(death_year)
    list_birth = _as_year(list_birth_year)
    list_death = _as_year(list_death_year)

    # Primary gate: known death before 1900 (e.g. baroque Gletle mis-linked on the list).
    if death is not None and death < MIN_DEATH_YEAR_INCLUSIVE:
        return f"death_year_{death}_before_{MIN_DEATH_YEAR_INCLUSIVE}"

    # Identity error: list claims modern years, Wikidata is centuries earlier.
    if list_birth is not None and birth is not None:
        if abs(list_birth - birth) >= YEAR_MISMATCH_THRESHOLD and birth < 1850:
            return f"list_birth_{list_birth}_vs_wikidata_{birth}"
    if list_death is not None and death is not None:
        if abs(list_death - death) >= YEAR_MISMATCH_THRESHOLD and death < MIN_DEATH_YEAR_INCLUSIVE:
            return f"list_death_{list_death}_vs_wikidata_{death}"

    return None
