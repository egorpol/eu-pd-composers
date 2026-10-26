import pandas as pd
import pytest

import imslp_licences as il

PAGE = """{{#fte:imslppage

| *****AUDIO***** =
===Recordings===
====For Guitar (Lopes)====
{{#fte:imslpaudio
|File Name 1=PMLP1-a.mp3
|File Description 1=Complete Performance
|Copyright=Creative Commons Attribution 3.0
|Misc. Notes=320 kbps
}}

| *****FILES***** =
===Full Scores===
{{#fte:imslpfile
|File Name 1=PMLP1-score.pdf
|File Name 2=PMLP1-score.mscz
|File Description 1=Complete Score
|Editor={{FE}}
|Image Type=Typeset
|Uploader=[[User:X|X]]
|Publisher Information={{P|Self||Berlin|2020|||}}<br>
reprinted 2021
|Copyright=Creative Commons Attribution-ShareAlike 4.0
|Misc. Notes=
}}
====Arrangements====
{{#fte:imslpfile
|File Name 1=PMLP1-arr.pdf
|Image Type=Normal Scan
|Copyright=Public Domain
|Misc. Notes=
}}
|Copyright Info={{HMBref|1882|242}}
}}"""


def test_iter_blocks_reads_kind_section_and_nested_templates():
    blocks = il.iter_blocks(PAGE)
    assert [b["kind"] for b in blocks] == ["audio", "score", "score"]
    assert [b["section"] for b in blocks] == ["For Guitar (Lopes)", "Full Scores", "Arrangements"]
    score = blocks[1]["fields"]
    assert score["Copyright"] == "Creative Commons Attribution-ShareAlike 4.0"
    assert score["Publisher Information"] == "{{P|Self||Berlin|2020|||}}<br> reprinted 2021"
    assert "Copyright Info" not in blocks[2]["fields"]


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Public Domain", "PD"),
        ("Public Domain (dedicated)", "PD-dedicated"),
        ("Creative Commons Attribution Non-commercial No Derivatives 3.0", "CC-BY-NC-ND-3.0"),
        ("Creative Commons Zero 1.0", "CC0-1.0"),
        ("Performance Restricted Attribution-NonCommercial-NoDerivs 3.0", "LicenseRef-IMSLP-PR-BY-NC-ND-3.0"),
        ("Choral Public Domain Library License v4", "LicenseRef-CPDL-4"),
        ("EFF Open Audio License", "LicenseRef-EFF-OAL"),
        ("Possibly [[public domain]] in EU and other 70 pma territories.{{RoST}}", "PD-note"),
        ("{{USA-C-URAA-RoST}}", "PD-note"),
        ("All rights reserved", "unparsed"),
        ("", ""),
    ],
)
def test_licence_id(raw, expected):
    assert il.licence_id(raw) == expected


def test_open_licence_excludes_plain_pd_and_performance_restricted():
    assert il.is_open_licence("CC-BY-NC-ND-3.0") and il.is_open_licence("PD-dedicated")
    assert not il.is_open_licence("PD") and not il.is_open_licence("LicenseRef-IMSLP-PR-BY-NC-ND-3.0")


def test_block_rows_and_work_summary():
    work = {"work_id": "W (X)", "composer_id": "Q1"}
    rows = il.block_rows(work, {"pageid": 5, "wikitext": PAGE, "revid": 3})
    assert [r["licence"] for r in rows] == ["CC-BY-3.0", "CC-BY-SA-4.0", "PD"]
    assert rows[1]["formats"] == "pdf|musescore" and rows[1]["n_files"] == 2 and rows[0]["formats"] == "audio"

    summary = il.work_summary(pd.DataFrame(rows)).iloc[0]
    assert summary["n_score_blocks"] == 2 and summary["n_open_score_blocks"] == 1
    assert summary["score_licences"] == "CC-BY-SA-4.0|PD"
    assert summary["open_score_licences"] == "CC-BY-SA-4.0"
    assert summary["open_score_formats"] == "musescore|pdf" and bool(summary["open_score_typeset"])
