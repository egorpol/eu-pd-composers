"""Map IMSLP genre categories / titles → Opus force_family (+ optional genre_form)."""

from __future__ import annotations

import re
from typing import Optional

# Opus-aligned controlled vocabulary (one primary tag per work).
FORCE_FAMILIES = (
    "piano_solo",
    "piano_ensemble",
    "organ",
    "keyboard_other",
    "solo_instrument",
    "guitar",
    "chamber",
    "orchestral",
    "concerto",
    "wind_band",
    "solo_voice",
    "choral",
    "stage_opera",
    "stage_ballet",
    "film_media",
    "electronic",
    "pedagogical",
    "other",
    "unclassified",
)

GENRE_FORMS = (
    "sonata",
    "symphony",
    "suite",
    "variations",
    "etude",
    "prelude_fugue",
    "dance",
    "string_quartet",
    "song_cycle",
    "mass_requiem",
    "oratorio_cantata",
    "overture_tone_poem",
    "concerto",
    "opera",
    "ballet",
    "arrangement",
    "other",
)

# Higher index = higher priority when several signals fire.
_PRIORITY = {name: i for i, name in enumerate(FORCE_FAMILIES)}


def _strip_arr(token: str) -> str:
    return re.sub(r"\s*\(arr\)\s*$", "", token, flags=re.I).strip()


def _is_arr_token(token: str) -> bool:
    return bool(re.search(r"\(arr\)\s*$", token, flags=re.I))


def _tokens(genre_cell: str) -> list[str]:
    if not genre_cell or str(genre_cell) in {"nan", "None"}:
        return []
    return [t.strip() for t in str(genre_cell).split("|") if t.strip()]


def _original_tokens(genre_cell: str) -> list[str]:
    """Category tokens that are not arrangements — used for force_family."""
    return [_strip_arr(t) for t in _tokens(genre_cell) if not _is_arr_token(t)]


# --- IMSLP "For …" instrumentation categories -------------------------------
# Parsed into parts ("For 2 violins, viola, cello" → 2 violins | viola | cello)
# and classified by whole words, never by substring ("flute" ≠ "lute").

_PART_SPLIT = re.compile(r",\s*|\s+with\s+|\s+and\s+")
_COUNT = re.compile(r"^(\d+)\s+(.+)$")
_CHORUS_RE = re.compile(r"\b(chorus|choruses|choir|choirs)\b")
_VOICE_RE = re.compile(r"\b(voices?|narrators?|speakers?)\b")
_ORCH_RE = re.compile(r"\borchestras?\b|^strings$|^string orchestra$")
_BAND_RE = re.compile(r"\bbands?\b|\b(wind|brass) ensembles?\b|^winds$")
_ELECTRONIC_RE = re.compile(r"\b(electronics?|electronic sounds|tape|synthesizers?|computer)\b")
_GUITAR_RE = re.compile(r"\b(guitars?|lutes?|archlutes?|vihuelas?|theorbos?)\b")
_KEYBOARD_OTHER_RE = re.compile(
    r"\b(harpsichords?|clavichords?|celestas?|harmoniums?|fortepianos?|virginals?|spinets?)\b"
)
_PIANO_RE = re.compile(r"^pianos?(\s+(\d+\s+hands|left hand|right hand))?$")
_PLAYERS_RE = re.compile(r"^players?$")


def _for_parts(low: str) -> list[tuple[str, int, str]]:
    """'for 2 violins, viola' → [('instrument', 2, 'violins'), ('instrument', 1, 'viola')]."""
    body = low[len("for ") :] if low.startswith("for ") else low
    parts: list[tuple[str, int, str]] = []
    for raw in _PART_SPLIT.split(body):
        part = raw.strip()
        if not part:
            continue
        count = 1
        m = _COUNT.match(part)
        if m:
            count, part = int(m.group(1)), m.group(2).strip()
        unaccompanied = part.startswith("unaccompanied ")
        name = part.removeprefix("unaccompanied ").strip()
        if _CHORUS_RE.search(name):
            kind = "chorus"
        elif _VOICE_RE.search(name):
            kind = "voice_unacc" if unaccompanied else "voice"
        elif _ORCH_RE.search(name):
            kind = "orchestra"
        elif _BAND_RE.search(name):
            kind = "band"
        elif _ELECTRONIC_RE.search(name):
            kind = "electronic"
        elif name in {"soloist", "soloists"}:
            kind = "soloists"
        elif _PLAYERS_RE.match(name):
            kind = "players"
        elif _PIANO_RE.match(name):
            hands = re.search(r"(\d+)\s+hands", name)
            if hands and int(hands.group(1)) >= 3:
                count = max(count, 2)
            kind = "piano"
        elif name == "organ" or name == "organs":
            kind = "organ"
        elif _KEYBOARD_OTHER_RE.search(name) or name == "keyboard":
            kind = "keyboard_other" if name != "keyboard" else "keyboard"
        elif _GUITAR_RE.search(name):
            kind = "guitar"
        else:
            kind = "instrument"
        parts.append((kind, count, name))
    return parts


def force_family_from_for_category(token: str) -> Optional[str]:
    """Map one IMSLP 'For …' category to a force family.

    Returns None for tokens that carry no force on their own ('For 1 player').
    Conventions: any chorus → choral; solo voice(s) with any accompaniment →
    solo_voice; 3+ unaccompanied voices → choral; soloist(s) + orchestra or
    string orchestra → concerto; one instrument ± one keyboard → solo_instrument;
    2+ instrumentalists otherwise → chamber.
    """
    low = token.lower().strip()
    if not low.startswith("for "):
        return None
    parts = _for_parts(low)
    if not parts:
        return None
    kinds = [k for k, _, _ in parts]

    if "chorus" in kinds:
        return "choral"
    if "voice" in kinds or "voice_unacc" in kinds:
        voice_parts = [(k, n) for k, n, _ in parts if k.startswith("voice")]
        if len(voice_parts) == len(parts):
            if "voice_unacc" in kinds or sum(n for _, n in voice_parts) >= 3:
                return "choral"
        if "stage" in low:
            return "stage_opera"
        return "solo_voice"
    if "orchestra" in kinds:
        others = [k for k in kinds if k != "orchestra"]
        return "concerto" if others else "orchestral"
    if "band" in kinds:
        return "wind_band"
    if "electronic" in kinds:
        return "electronic"
    if kinds == ["players"]:
        return "chamber" if parts[0][1] >= 2 else None
    if "guitar" in kinds:
        return "guitar"

    keyboards = [(k, n) for k, n, _ in parts if k in {"piano", "organ", "keyboard_other", "keyboard"}]
    others = [(k, n) for k, n, _ in parts if k not in {"piano", "organ", "keyboard_other", "keyboard"}]
    if not others:
        total = sum(n for _, n in keyboards)
        kb_kinds = {k for k, _ in keyboards}
        if kb_kinds == {"piano"}:
            return "piano_ensemble" if total >= 2 else "piano_solo"
        if kb_kinds == {"organ"}:
            return "organ"
        if kb_kinds <= {"keyboard_other", "keyboard"}:
            return "keyboard_other"
        return "piano_ensemble"
    players = sum(n for _, n in others)
    if players == 1 and sum(n for _, n in keyboards) <= 1:
        return "solo_instrument"
    return "chamber"


# Ensemble-size labels only decide when no explicit instrumentation does
# ('For 2 players' must not turn a 'For violin, piano' sonata into chamber).
_WEAK_CHAMBER_TOKENS = {"Quartets", "Quintets", "Trios", "Duets", "Sextets", "Septets", "Octets"}


def force_family_from_categories(genre_cell: str) -> tuple[str, str]:
    """Return (force_family, src) from IMSLP category tokens (non-arr only)."""
    tokens = _original_tokens(genre_cell)
    if not tokens:
        # Only arrangement categories (or empty) — caller may fall back to title/geninfo.
        if any(_is_arr_token(t) for t in _tokens(genre_cell)):
            return "unclassified", "arr_only"
        return "unclassified", "empty"

    hits: list[str] = []
    weak_hits: list[str] = []

    for t in tokens:
        low = t.lower()

        if low.startswith("for "):
            family = force_family_from_for_category(t)
            if family is None:
                continue
            if re.match(r"for \d+ players?$", low):
                weak_hits.append(family)
            else:
                hits.append(family)
            continue

        if t in {"Operas", "Operettas", "Musicals"}:
            hits.append("stage_opera")
        if t in {"Ballets"}:
            hits.append("stage_ballet")
        if t in {"Concertos"}:
            hits.append("concerto")
        if t in {"Symphonies", "Overtures"}:
            hits.append("orchestral")
        if t in {"Songs", "Lieder", "Mélodies", "Chansons", "Arias"}:
            hits.append("solo_voice")
        if t in {"Masses", "Requiems", "Cantatas", "Oratorios", "Motets"}:
            hits.append("choral")
        if t in _WEAK_CHAMBER_TOKENS:
            weak_hits.append("chamber")
        if any(x in low for x in ("electronic", "tape", "electro")):
            hits.append("electronic")
        if t in {"Etudes", "Studies", "Methods", "Exercises"}:
            hits.append("pedagogical")
        if any(x in low for x in ("film", "incidental", "radio")):
            hits.append("film_media")

    if not hits:
        hits = weak_hits
    if not hits:
        return "other", "categories_unmapped"

    # Prefer more specific families (higher priority index among hits).
    best = max(hits, key=lambda h: _PRIORITY.get(h, -1))
    # Tie-break: stage / vocal beat concerto; original orchestra beats piano reductions.
    if "stage_opera" in hits:
        best = "stage_opera"
    elif "stage_ballet" in hits:
        best = "stage_ballet"
    elif "choral" in hits and ("solo_voice" in hits or "concerto" in hits or "orchestral" in hits):
        best = "choral"
    elif "solo_voice" in hits and "concerto" in hits:
        best = "solo_voice"
    elif "concerto" in hits:
        best = "concerto"
    elif "orchestral" in hits and "piano_ensemble" in hits:
        best = "orchestral"
    elif "piano_ensemble" in hits:
        best = "piano_ensemble"
    elif "choral" in hits and "solo_voice" in hits:
        best = "choral"
    return best, "imslp_tags"


def force_family_from_title(title: str) -> tuple[str, str]:
    """Fallback when IMSLP categories are empty or unhelpful."""
    if not title:
        return "unclassified", "empty"
    t = title.lower()

    if re.search(r"\b(opera|operetta|singspiel)\b", t):
        return "stage_opera", "title"
    if re.search(r"\bballet\b", t):
        return "stage_ballet", "title"
    if re.search(r"\b(concerto|concertino|konzert)\b", t):
        return "concerto", "title"
    if re.search(r"\b(symphony|sinfon(ia|ie)|overture|tone poem|symphonic poem)\b", t):
        return "orchestral", "title"
    if re.search(r"\b(mass|requiem|cantata|oratorio|motet|magnificat|stabat|te deum)\b", t):
        return "choral", "title"
    if re.search(
        r"\b(string quartet|quartet|quintet|trio|sextet|octet|duos?|duets?)\b", t
    ):
        return "chamber", "title"
    if re.search(
        r"\b(lied|lieder|songs?|m[eé]lodie[s]?|chanson[s]?|arias?|romances?)\b", t
    ):
        return "solo_voice", "title"
    if re.search(r"\b(4[ -]?hands|duet for two pianos|2 pianos)\b", t):
        return "piano_ensemble", "title"
    if re.search(r"\b(organ|orgue)\b", t):
        return "organ", "title"
    if re.search(r"\b(guitar|lute)\b", t):
        return "guitar", "title"
    if re.search(
        r"\b(violin|viola|cello|violoncello|flute|clarinet|oboe|bassoon|trumpet|"
        r"horn|trombone|saxophone|harp|violiniste)\b",
        t,
    ):
        return "solo_instrument", "title"
    if re.search(r"\b(etudes?|études?|study|studies|method|école|schule)\b", t):
        return "pedagogical", "title"
    # Keyboard character pieces — best-effort when force cats are missing.
    if re.search(
        r"\b(piano|klavier|nocturne|impromptu|intermezzo|bagatelle|moment musical|"
        r"valse|waltz|mazurka|polonaise|scherzo|ballade|capriccio|caprice|"
        r"fantaisie|fantasy|prelude|pr[eé]lude|fugue|toccata|arabesque|"
        r"rhapsod|humoresque|barcarolle|berceuse)\b",
        t,
    ):
        return "piano_solo", "title"
    if re.search(r"\b(suite|serenade|s[eé]r[eé]nade|divertimento)\b", t):
        return "other", "title"
    if re.search(r"\b(pieces?|pi[eè]ces?|morceaux|album)\b", t):
        return "other", "title"
    return "unclassified", "title_unmapped"


def force_family_from_instrumentation(instrumentation: str) -> tuple[str, str]:
    """Map IMSLP General Information Instrumentation text → force_family."""
    if not instrumentation or str(instrumentation).strip() in {"", "nan", "None"}:
        return "unclassified", "empty"
    low = str(instrumentation).lower().strip()
    low = re.sub(r"\s+", " ", low)

    if any(
        x in low
        for x in (
            "chorus",
            "choir",
            "choruses",
            "mixed voices",
            "female voices",
            "male voices",
            "childrens voices",
            "children's voices",
        )
    ):
        return "choral", "imslp_geninfo"

    # Instrument names containing a voice word ("double bass", "alto saxophone")
    # must not read as singers.
    no_instruments = re.sub(
        r"\b(double bass|contrabass|(soprano|alto|tenor|baritone|bass)\s+"
        r"(saxophone|clarinet|flute|recorder|trombone|tuba|oboe|viol|drum|guitar)s?)\b",
        "",
        low,
    )
    voice_hit = bool(
        re.search(
            r"\b(voice|voices|soprano|mezzo|alto|contralto|tenor|baritone|"
            r"bass-baritone|bass|narrator)s?\b",
            no_instruments,
        )
    )
    if voice_hit:
        return "solo_voice", "imslp_geninfo"

    if re.search(r"\b(band|wind ensemble|brass ensemble|brass band)\b", low):
        return "wind_band", "imslp_geninfo"

    parts = [p.strip() for p in re.split(r",|/|;|\+| and ", low) if p.strip()]
    if "orchestra" in low:
        # "violin and orchestra" ≈ concerto; a full orchestral roster that happens
        # to list piano or harp is still orchestral.
        soloist = re.search(
            r"\b(violin|viola|cello|flute|oboe|clarinet|bassoon|trumpet|horn|"
            r"trombone|piano|saxophone|harp|organ)\b",
            low,
        )
        if soloist and len(parts) <= 3:
            return "concerto", "imslp_geninfo"
        return "orchestral", "imslp_geninfo"

    if "organ" in low and "piano" not in low:
        return "organ", "imslp_geninfo"
    if re.search(r"\b(guitar|lute|ukulele|mandolin)\b", low):
        return "guitar", "imslp_geninfo"

    if re.search(
        r"\b(piano 4 hands|4 hands|2 pianos|pianos|piano 6 hands|piano 3 hands)\b", low
    ):
        return "piano_ensemble", "imslp_geninfo"

    if low in {"harpsichord", "clavichord", "harmonium", "celesta"}:
        return "keyboard_other", "imslp_geninfo"
    if low in {"piano", "pianos", "piano solo"} or re.fullmatch(r"piano(s)?", low):
        return "piano_solo", "imslp_geninfo"

    solo_kw = (
        "violin",
        "viola",
        "cello",
        "violoncello",
        "flute",
        "clarinet",
        "oboe",
        "bassoon",
        "trumpet",
        "horn",
        "trombone",
        "saxophone",
        "harp",
        "percussion",
        "timpani",
        "double bass",
    )
    if len(parts) == 1 and any(k in parts[0] for k in solo_kw):
        return "solo_instrument", "imslp_geninfo"
    if len(parts) == 2 and "piano" in low and any(k in low for k in solo_kw):
        # Sonata-like duo → solo_instrument (same as IMSLP "For violin, piano")
        return "solo_instrument", "imslp_geninfo"
    if len(parts) >= 2 and "orchestra" not in low:
        if low.strip() in {"piano", "organ"}:
            return "piano_solo" if "piano" in low else "organ", "imslp_geninfo"
        return "chamber", "imslp_geninfo"

    if "piano" in low and "," not in low:
        return "piano_solo", "imslp_geninfo"
    if any(k in low for k in solo_kw):
        return "solo_instrument", "imslp_geninfo"

    return "other", "imslp_geninfo"


def map_force_family(genre_cell: str, title: str = "") -> tuple[str, str]:
    family, src = force_family_from_categories(genre_cell)
    if family in {"unclassified", "other"} and title:
        t_family, t_src = force_family_from_title(title)
        if t_family not in {"unclassified"}:
            return t_family, t_src
        if family == "other":
            return family, src
    return family, src


def map_force_family_with_geninfo(
    genre_cell: str,
    title: str = "",
    instrumentation: str = "",
    *,
    current_family: str = "",
    current_src: str = "",
    recompute: bool = False,
) -> tuple[str, str]:
    """Prefer imslp_tags; then geninfo; never downgrade strong tags.

    When recompute=True, ignore current_family/src and re-derive from categories
    (then geninfo, then title) — used after classifier rule fixes.
    """
    if (
        not recompute
        and current_src == "imslp_tags"
        and current_family not in {"", "unclassified", "other"}
    ):
        return current_family, current_src

    cat_family, cat_src = force_family_from_categories(genre_cell)
    if cat_family not in {"unclassified", "other"} and cat_src == "imslp_tags":
        return cat_family, cat_src

    if instrumentation:
        g_family, g_src = force_family_from_instrumentation(instrumentation)
        if g_family not in {"unclassified"}:
            return g_family, g_src

    if (
        not recompute
        and current_family
        and current_src in {"imslp_geninfo"}
        and current_family != "unclassified"
    ):
        return current_family, current_src

    mapped_family, mapped_src = map_force_family(genre_cell, title)
    if mapped_family not in {"unclassified", "other"}:
        return mapped_family, mapped_src

    # Preserve LLM/geninfo fills when categories stay weak after a rule recompute.
    if current_family and current_family not in {"unclassified"}:
        if current_src.startswith("llm") or current_src == "imslp_geninfo":
            return current_family, current_src

    return mapped_family, mapped_src


def map_genre_form(genre_cell: str, title: str = "") -> str:
    tokens = {_strip_arr(t) for t in _tokens(genre_cell)}
    blob = " ".join(tokens).lower() + " " + (title or "").lower()

    if "Operas" in tokens or re.search(r"\bopera\b", blob):
        return "opera"
    if "Ballets" in tokens or re.search(r"\bballet\b", blob):
        return "ballet"
    if "Concertos" in tokens or re.search(r"\bconcerto\b", blob):
        return "concerto"
    if "Symphonies" in tokens or re.search(r"\bsymphon", blob):
        return "symphony"
    if "Sonatas" in tokens or re.search(r"\bsonata\b", blob):
        return "sonata"
    if "Suites" in tokens or re.search(r"\bsuite\b", blob):
        return "suite"
    if "Variations" in tokens or re.search(r"\bvariations?\b", blob):
        return "variations"
    if "Etudes" in tokens or re.search(r"\b(etude|étude)\b", blob):
        return "etude"
    if ("Preludes" in tokens or "Fugues" in tokens) or re.search(
        r"\b(prelude|fugue)\b", blob
    ):
        return "prelude_fugue"
    if "Quartets" in tokens or re.search(r"\bstring quartet\b", blob):
        return "string_quartet"
    if any(x in tokens for x in {"Waltzes", "Marches", "Mazurkas", "Polonaises", "Dances"}):
        return "dance"
    if any(x in tokens for x in {"Masses", "Requiems"}) or re.search(
        r"\b(mass|requiem)\b", blob
    ):
        return "mass_requiem"
    if any(x in tokens for x in {"Cantatas", "Oratorios"}) or re.search(
        r"\b(cantata|oratorio)\b", blob
    ):
        return "oratorio_cantata"
    if "Overtures" in tokens or re.search(r"\b(overture|tone poem)\b", blob):
        return "overture_tone_poem"
    if "(arr)" in (genre_cell or "").lower() or re.search(r"\barr\.?\b", blob):
        return "arrangement"
    if any(x in tokens for x in {"Songs", "Lieder"}) or re.search(r"\bsong cycle\b", blob):
        return "song_cycle" if "cycle" in blob else "other"
    return ""


def map_work_row(genre_cell: str, title: str) -> dict[str, str]:
    family, src = map_force_family(genre_cell, title)
    form = map_genre_form(genre_cell, title)
    return {
        "force_family": family,
        "force_family_src": src,
        "genre_form": form,
    }
