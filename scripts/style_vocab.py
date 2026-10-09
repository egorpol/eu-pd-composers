"""Controlled style vocabulary for the LLM style-labelling pass.

Slugs are the only values allowed in ledger `styles` / `primary_period` (era subset).
IMSLP period strings and Wikidata tags map onto this list for agreement metrics.
"""

from __future__ import annotations

# Ordered (slug, one-line definition). Order matches the viewer era-first facet idea.
STYLE_VOCAB: list[tuple[str, str]] = [
    ("ancient", "Music of antiquity and the earliest notated Western traditions."),
    ("medieval", "European sacred and secular music roughly before 1400."),
    ("renaissance", "Polyphonic Renaissance style, roughly 15th–16th centuries."),
    ("baroque", "Baroque idiom, roughly 1600–1750 (continuo, contrapuntal forms)."),
    ("classical", "High Classical style, roughly mid-18th to early 19th century."),
    ("romantic", "Nineteenth-century Romantic harmonic and expressive language."),
    ("late_romantic", "Fin-de-siècle / post-Romantic language into the early 20th century."),
    ("early_20th_century", "Early 20th-century idioms not covered by a more specific movement tag."),
    ("impressionism", "Impressionist / colouristic harmony and timbre (e.g. Debussy circle)."),
    ("expressionism", "Expressionist intensity, often atonal or hyper-chromatic."),
    ("national_folk", "Nationalist or folk-influenced classical composition."),
    ("neoclassicism", "20th-century neoclassical clarity, forms, or pastiche of earlier styles."),
    ("atonal_modernism", "Free atonal / modernist language outside strict serial technique."),
    ("serialism", "Twelve-tone or serial / post-serial pitch organisation."),
    ("modern", "Broad modern classical style when no finer movement tag fits."),
    ("avant_garde", "Experimental, radical, or Fluxus-adjacent art-music practice."),
    ("electroacoustic", "Tape, electronic, musique concrète, or fixed-media composition."),
    ("minimalism", "Minimalist pulse, repetition, and gradual process."),
    ("postminimalism", "Post-minimalist developments beyond classic minimalism."),
    ("spectralism", "Spectral / timbre-harmonic composition."),
    ("polystylism", "Deliberate juxtaposition or collage of multiple historical styles."),
    ("jazz", "Jazz or jazz-idiom composition (IMSLP Jazz period)."),
    ("traditional_folk", "Traditional / folk repertoire as classified by IMSLP."),
    ("non_western_classical", "Non-Western classical traditions as classified by IMSLP."),
]

STYLE_SLUGS: list[str] = [slug for slug, _ in STYLE_VOCAB]
STYLE_DEFINITIONS: dict[str, str] = dict(STYLE_VOCAB)
STYLE_SLUG_SET: frozenset[str] = frozenset(STYLE_SLUGS)

# Era / period axis for primary_period (plus "unknown" at prompt time).
ERA_SLUGS: list[str] = [
    "ancient",
    "medieval",
    "renaissance",
    "baroque",
    "classical",
    "romantic",
    "late_romantic",
    "early_20th_century",
    "modern",
]
ERA_SLUG_SET: frozenset[str] = frozenset(ERA_SLUGS)
PRIMARY_PERIOD_ALLOWED: frozenset[str] = ERA_SLUG_SET | frozenset({"unknown"})

# IMSLP work `imslp_style` period strings → vocabulary slugs.
IMSLP_PERIOD_TO_SLUG: dict[str, str] = {
    "Romantic": "romantic",
    "Early 20th century": "early_20th_century",
    "Modern": "modern",
    "Classical": "classical",
    "Baroque": "baroque",
    "Renaissance": "renaissance",
    "Medieval": "medieval",
    "Ancient": "ancient",
    "Jazz": "jazz",
    "Traditional (folk)": "traditional_folk",
    "Non-western classical": "non_western_classical",
    # Aliases seen in the dump
    "Romántico": "romantic",
    "Traditional": "traditional_folk",
}

# Wikidata-derived composer style_tags are already vocabulary slugs.
WIKIDATA_TAG_TO_SLUG: dict[str, str] = {
    "impressionism": "impressionism",
    "expressionism": "expressionism",
    "neoclassicism": "neoclassicism",
    "serialism": "serialism",
    "minimalism": "minimalism",
    "postminimalism": "postminimalism",
    "spectralism": "spectralism",
    "electroacoustic": "electroacoustic",
    "avant_garde": "avant_garde",
    "national_folk": "national_folk",
    "late_romantic": "late_romantic",
    "atonal_modernism": "atonal_modernism",
    "polystylism": "polystylism",
}


def map_imslp_period(period: str) -> str | None:
    """Map one IMSLP period token to a vocab slug, or None if unknown."""
    text = (period or "").strip()
    if not text:
        return None
    return IMSLP_PERIOD_TO_SLUG.get(text)


def map_wikidata_tag(tag: str) -> str | None:
    """Identity-map a Wikidata style tag if it is in the controlled vocabulary."""
    text = (tag or "").strip()
    if not text:
        return None
    return WIKIDATA_TAG_TO_SLUG.get(text) or (text if text in STYLE_SLUG_SET else None)
