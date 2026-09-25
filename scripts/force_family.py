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


def _concerto_instrumentation(low: str) -> bool:
    """True for soloist+orchestra forms; never voice/chorus+orchestra."""
    if "voice" in low or "chorus" in low or "choir" in low:
        return False
    if "with soloists" in low:
        return True
    # e.g. "for violin, orchestra" — not "for voices, orchestra"
    return bool(re.search(r"for (?!voices?\b)[^,]+, orchestra$", low))


def force_family_from_categories(genre_cell: str) -> tuple[str, str]:
    """Return (force_family, src) from IMSLP category tokens (non-arr only)."""
    tokens = _original_tokens(genre_cell)
    if not tokens:
        # Only arrangement categories (or empty) — caller may fall back to title/geninfo.
        if any(_is_arr_token(t) for t in _tokens(genre_cell)):
            return "unclassified", "arr_only"
        return "unclassified", "empty"

    hits: list[str] = []

    for t in tokens:
        low = t.lower()

        if t in {"Operas", "Operettas", "Musicals"} or (
            low.startswith("for voices") and "stage" in low
        ):
            hits.append("stage_opera")
        if t in {"Ballets"}:
            hits.append("stage_ballet")

        if t in {"Concertos"} or _concerto_instrumentation(low):
            hits.append("concerto")
        if t in {"Symphonies", "Overtures"} or low == "for orchestra":
            hits.append("orchestral")
        if "orchestra" in low and "solo" not in low and t not in {"Concertos"}:
            if low.startswith("for orchestra") or "chorus, orchestra" in low:
                if "chorus" in low or "voices" in low:
                    hits.append("choral")
                else:
                    hits.append("orchestral")

        if any(
            x in low
            for x in (
                "band",
                "wind ensemble",
                "brass ensemble",
                "for wind",
                "for brass",
            )
        ):
            hits.append("wind_band")

        if t in {"Songs", "Lieder", "Mélodies", "Chansons", "Arias"}:
            hits.append("solo_voice")
        if low in {
            "for voice, piano",
            "for voices with keyboard",
            "for voice, orchestra",
            "for voices with orchestra",
            "for voices with solo instruments",
        } or low.startswith("for voice"):
            if "chorus" not in low and "choir" not in low:
                hits.append("solo_voice")

        if any(
            x in low
            for x in (
                "chorus",
                "choir",
                "choral",
                "for mixed chorus",
                "unaccompanied chorus",
                "for voices and chorus",
                "for voices, mixed chorus",
            )
        ):
            hits.append("choral")
        if t in {"Masses", "Requiems", "Cantatas", "Oratorios", "Motets"}:
            hits.append("choral")

        if t in {"For piano 4 hands", "For 2 pianos", "For 3 pianos"} or "piano 4 hands" in low or "2 pianos" in low:
            hits.append("piano_ensemble")
        if t == "For piano" or low == "for piano":
            hits.append("piano_solo")
        if low == "for 1 player" and any(
            x == "For piano" or x.lower() == "for piano" for x in tokens
        ):
            hits.append("piano_solo")

        if t == "For organ" or low == "for organ":
            hits.append("organ")
        if any(x in low for x in ("harpsichord", "clavichord", "celesta", "harmonium")):
            hits.append("keyboard_other")

        if any(x in low for x in ("guitar", "lute", "vihuela", "theorbo")):
            hits.append("guitar")

        if t in {"Quartets", "Quintets", "Trios", "Duets", "Sextets", "Septets", "Octets"}:
            hits.append("chamber")
        if re.match(r"for [2-9] players$", low):
            hits.append("chamber")
        if re.match(r"for 2 violins, viola, cello$", low):
            hits.append("chamber")
        if re.search(r"for [^,]+, [^,]+, [^,]+", low) and "orchestra" not in low and "piano" in low:
            hits.append("chamber")
        if re.match(r"for [^,]+, piano$", low) and not low.startswith("for voice"):
            # violin, piano / cello, piano → solo_instrument (sonata duo)
            hits.append("solo_instrument")

        if low.startswith("for ") and "orchestra" not in low and "chorus" not in low:
            # generic solo instruments without piano already handled
            solo_only = re.match(r"for ([a-z0-9 \-]+)$", low)
            if solo_only:
                inst = solo_only.group(1)
                if inst in {"piano", "organ"}:
                    pass
                elif inst in {"guitar", "lute"}:
                    hits.append("guitar")
                elif "piano" not in inst and "voice" not in inst:
                    if inst not in {
                        "1 player",
                        "2 players",
                        "3 players",
                        "4 players",
                        "5 players",
                    }:
                        hits.append("solo_instrument")

        if any(x in low for x in ("electronic", "tape", "electro")):
            hits.append("electronic")
        if t in {"Etudes", "Studies", "Methods", "Exercises"}:
            hits.append("pedagogical")
        if any(x in low for x in ("film", "incidental", "radio")):
            hits.append("film_media")

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
    elif "chamber" in hits and "solo_instrument" in hits:
        if any(t in {"Quartets", "Quintets", "Trios"} for t in tokens):
            best = "chamber"
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

    if any(x in low for x in ("band", "wind ensemble", "brass ensemble", "brass band")):
        return "wind_band", "imslp_geninfo"

    if "orchestra" in low:
        # Soloist(s) + orchestra ≈ concerto; chorus+orchestra already caught as choral.
        if re.search(
            r"\b(violin|viola|cello|flute|oboe|clarinet|bassoon|trumpet|horn|"
            r"trombone|piano|saxophone|harp|organ)\b",
            low,
        ) and "voice" not in low:
            return "concerto", "imslp_geninfo"
        return "orchestral", "imslp_geninfo"

    voice_hit = bool(
        re.search(
            r"\b(voice|voices|soprano|mezzo|alto|contralto|tenor|baritone|"
            r"bass-baritone|bass|narrator)s?\b",
            low,
        )
    )
    if voice_hit:
        return "solo_voice", "imslp_geninfo"

    if "organ" in low and "piano" not in low:
        return "organ", "imslp_geninfo"
    if re.search(r"\b(guitar|lute|ukulele|mandolin)\b", low):
        return "guitar", "imslp_geninfo"

    if re.search(
        r"\b(piano 4 hands|4 hands|2 pianos|pianos|piano 6 hands|piano 3 hands)\b", low
    ):
        return "piano_ensemble", "imslp_geninfo"

    # Single keyboard
    if low in {"piano", "pianos", "piano solo", "harpsichord", "clavichord"} or (
        re.fullmatch(r"piano(s)?", low)
    ):
        return "piano_solo", "imslp_geninfo"

    parts = [p.strip() for p in re.split(r",|/;| and ", low) if p.strip()]
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
