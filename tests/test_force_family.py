"""Regression tests for force_family rules (real IMSLP category strings from r008)."""

import pytest

from force_family import (
    force_family_from_categories,
    force_family_from_for_category,
    force_family_from_instrumentation,
    map_genre_form,
    n_players_from_categories,
)


@pytest.mark.parametrize(
    "token, family",
    [
        # flute ≠ lute (r008 labelled every flute work as guitar)
        ("For flute", "solo_instrument"),
        ("For flute, piano", "solo_instrument"),
        ("For flute, oboe, clarinet, bassoon, horn", "chamber"),
        ("For flute, harp", "chamber"),
        ("For flute, orchestra", "concerto"),
        ("For 2 flutes", "chamber"),
        ("For lute", "guitar"),
        ("For guitar", "guitar"),
        ("For 2 guitars", "guitar"),
        ("For guitar, mandolin", "guitar"),
        # keyboards
        ("For piano", "piano_solo"),
        ("For piano left hand", "piano_solo"),
        ("For piano 4 hands", "piano_ensemble"),
        ("For 2 pianos", "piano_ensemble"),
        ("For organ", "organ"),
        ("For harpsichord", "keyboard_other"),
        ("For harmonium", "keyboard_other"),
        # strings / ensembles
        ("For violin", "solo_instrument"),
        ("For violin, piano", "solo_instrument"),
        ("For trumpet, organ", "solo_instrument"),
        ("For 2 violins", "chamber"),
        ("For 2 violins, piano", "chamber"),
        ("For violin, viola, cello", "chamber"),
        ("For 2 violins, viola, cello", "chamber"),
        ("For 2 violins, viola, cello, double bass", "chamber"),
        ("For 2 players", "chamber"),
        ("For 10 players", "chamber"),
        ("For 1 player", None),
        # orchestra
        ("For orchestra", "orchestral"),
        ("For orchestra without strings", "orchestral"),
        ("For strings", "orchestral"),
        ("For orchestra with soloists", "concerto"),
        ("For strings with soloists", "concerto"),
        ("For violin, strings", "concerto"),
        ("For piano, orchestra", "concerto"),
        ("For wind band", "wind_band"),
        ("For brass band", "wind_band"),
        ("For bandoneon", "solo_instrument"),
        ("For electronic sounds", "electronic"),
        # voices
        ("For voice, piano", "solo_voice"),
        ("For voices with keyboard", "solo_voice"),
        ("For voice, orchestra", "solo_voice"),
        ("For 2 voices, piano", "solo_voice"),
        ("For 4 voices, piano", "solo_voice"),
        ("For narrator, piano", "solo_voice"),
        ("For 1 voice", "solo_voice"),
        ("For voices", "solo_voice"),
        ("For 3 voices", "choral"),
        ("For 4 voices", "choral"),
        ("For unaccompanied voices", "choral"),
        ("For unaccompanied chorus", "choral"),
        ("For mixed chorus, organ", "choral"),
        ("For voices and chorus with orchestra", "choral"),
        ("For alto saxophone, piano", "solo_instrument"),
    ],
)
def test_for_category(token, family):
    assert force_family_from_for_category(token) == family


@pytest.mark.parametrize(
    "cell, family",
    [
        ("For 1 player|For flute", "solo_instrument"),
        # ensemble-size tokens must not override explicit instrumentation
        ("For 2 players|For violin, piano|Sonatas", "solo_instrument"),
        ("For 2 players|For violin, piano", "solo_instrument"),
        ("Duets|For 2 players|For flute, piano", "solo_instrument"),
        ("For 3 players|For violin, cello, piano|Trios", "chamber"),
        ("For 2 players|Sonatas", "chamber"),
        ("Quartets", "chamber"),
        # existing precedence rules
        ("Concertos|For 2 players|For violin, piano", "concerto"),
        ("Operas|For orchestra with soloists", "stage_opera"),
        ("For voices, mixed chorus, orchestra|Masses", "choral"),
        ("For 1 player|For piano", "piano_solo"),
        ("For piano (arr)|For orchestra", "orchestral"),
        ("Pieces", "other"),
    ],
)
def test_category_cell(cell, family):
    assert force_family_from_categories(cell)[0] == family


@pytest.mark.parametrize(
    "instrumentation, family",
    [
        ("harpsichord", "keyboard_other"),
        ("piano", "piano_solo"),
        ("double bass", "solo_instrument"),
        ("double bass, piano", "solo_instrument"),
        ("voice and orchestra", "solo_voice"),
        ("soprano, orchestra", "solo_voice"),
        ("alto saxophone, piano", "solo_instrument"),
        ("violin and orchestra", "concerto"),
        ("2 flutes, 2 oboes, 2 clarinets, 2 bassoons, 4 horns, harp, piano, orchestra", "orchestral"),
        ("mixed chorus, orchestra", "choral"),
        ("brass band", "wind_band"),
    ],
)
def test_instrumentation(instrumentation, family):
    assert force_family_from_instrumentation(instrumentation)[0] == family


@pytest.mark.parametrize(
    "cell, expected",
    [
        ("For 1 player|For piano", "1"),
        # Versions for different forces keep every original count; arrangements never count.
        ("For 1 player|For 2 players|For 3 players (arr)|For piano|For violin, piano", "1|2"),
        ("For 2 players (arr)|For violin, piano (arr)", ""),
        ("For 2 players with continuo|For 2 violins, continuo", "2"),
        ("For 10 players|For 2 players", "2|10"),
        ("For unspecified number of players", ""),
        ("For voice, piano|Songs", ""),
        ("", ""),
    ],
)
def test_n_players(cell, expected):
    assert n_players_from_categories(cell) == expected


@pytest.mark.parametrize(
    "cell, title, form",
    [
        # IMSLP Quartets holds any four-player work; only original string-quartet forces count
        ("For 2 violins, viola, cello|For 4 players|Quartets", "String Quartet No.1", "string_quartet"),
        ("For 2 violins, viola, cello|For 4 players|Quartets", "Quartet in D major", "string_quartet"),
        ("For 4 players|For violin, viola, cello, piano|Quartets", "Piano Quartet, Op.16", ""),
        ("For 4 players|For horn, 2 trumpets, trombone|Quartets", "Brass Quartet, Op.153", ""),
        ("For 4 voices, organ|For voices with keyboard|Quartets", "O Sacrum Convivium", ""),
        ("For 2 violins, viola, cello (arr)|For 4 players|For flute, violin, viola, cello|Quartets",
         "Flute Quartet", "arrangement"),
        ("For 4 players|For violin, viola, cello, piano|Quartets", "Quartet after a String Quartet",
         "string_quartet"),
        # Songs/Lieder beat an arrangement; song cycles stay song_cycle
        ("For voice, piano|Songs|For guitar (arr)", "Ständchen", "song"),
        ("For voice, piano|Lieder", "Ständchen", "song"),
        ("For voice, piano|Songs", "Dichterliebe, a song cycle", "song_cycle"),
        ("For piano|Pieces|For guitar (arr)", "Romance", "arrangement"),
        ("For piano|Pieces", "Romance", ""),
    ],
)
def test_genre_form(cell, title, form):
    assert map_genre_form(cell, title) == form
