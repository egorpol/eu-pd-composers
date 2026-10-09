"""Carry-forward precedence and immutable dump outputs."""

import argparse
import json

import pandas as pd
import pytest

import carry_forward as carry
import common


def frames():
    c = pd.DataFrame([
        {"composer_id": "Q1", "style_tags": "", "style_tags_src": "", "dump_date": "new", "raw": " NA "},
        {"composer_id": "Q2", "style_tags": "modern", "style_tags_src": "wikidata", "dump_date": "new", "raw": "001"},
    ])
    w = pd.DataFrame([
        {"composer_id": "Q1", "imslp_pageid": str(i), "force_family": fam, "force_family_src": src,
         "raw": "0001", "instrumentation_raw": instrument}
        for i, fam, src, instrument in (
            (1, "other", "title", ""), (2, "guitar", "imslp_tags", "current"),
            (3, "chamber", "imslp_geninfo", ""), (4, "other", "imslp_geninfo", ""),
            (5, "piano_solo", "title", ""), (6, "other", "imslp_tags", ""),
        )
    ])
    bc = pd.DataFrame([
        {"composer_id": cid, "style_tags": "serialism", "style_tags_src": "llm_grok"}
        for cid in ("Q1", "Q2", "lost")
    ])
    bw = pd.DataFrame([
        {"composer_id": "Q1", "imslp_pageid": str(i), "force_family": "choral", "force_family_src": src,
         "instrumentation_raw": "choir", "piece_style_raw": " NA ", "composition_year": "001900"}
        for i, src in ((1, "llm"), (2, "llm_grok"), (3, "llm"), (4, "imslp_geninfo"),
                       (5, "llm_luna_xhigh"), (6, "llm"), (99, "llm"))
    ])
    return c, w, bc, bw


def test_precedence_fills_rollups_and_lost_decisions():
    c, w, bc, bw = frames()
    out_c, out_w, report = carry.carry_forward(c, w, bc, bw)
    assert out_w.force_family.tolist() == ["choral", "guitar", "chamber", "choral", "choral", "other"]
    assert out_w.force_family_src.tolist() == ["llm", "imslp_tags", "imslp_geninfo", "imslp_geninfo", "llm_luna_xhigh", "imslp_tags"]
    assert out_w.instrumentation_raw.tolist() == ["choir", "current", "choir", "choir", "choir", "choir"]
    assert out_w.composition_year.tolist() == ["001900"] * 6
    assert out_w.piece_style_raw.tolist() == [" NA "] * 6
    assert out_w.raw.tolist() == w.raw.tolist()
    assert out_c.style_tags.tolist() == ["serialism", "modern"]
    assert out_c.raw.tolist() == c.raw.tolist()
    assert out_c.works_count_total.tolist() == [6, 0]
    assert json.loads(out_c.works_count_by_category[0])["choral"] == 3
    assert report["carried_forward"] == {"force_family": 3, "instrumentation_raw": 5,
                                           "piece_style_raw": 6, "composition_year": 6, "style_tags": 1}
    assert report["lost_rows"] == {"works": [["Q1", "99"]], "composers": ["lost"]}
    assert report["lost_decision_counts"]["force_family"] == 1
    assert report["lost_decisions"]["style_tags"] == ["lost"]
    assert list(out_w.columns) == [*w.columns, "piece_style_raw", "composition_year"]


def test_nonapproved_force_is_not_carried():
    c, w, bc, bw = frames()
    bw["force_family_src"] = "title"
    _, out, report = carry.carry_forward(c, w, bc, bw)
    assert out.force_family.tolist() == w.force_family.tolist()
    assert report["carried_forward"]["force_family"] == 0


def test_keys_include_composer_and_duplicates_fail():
    c, w, bc, bw = frames()
    bw["composer_id"] = "Q2"
    _, out, _ = carry.carry_forward(c, w, bc, bw)
    assert out.force_family.tolist() == w.force_family.tolist()
    with pytest.raises(ValueError, match="Duplicate"):
        carry.carry_forward(c, w, bc, pd.concat([bw, bw.iloc[:1]]))


def test_cli_dry_run_and_refuse_overwrite(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "DATA_DIR", tmp_path)
    c, w, bc, bw = frames()
    for dump_id, cf, wf in (("new", c, w), ("base", bc, bw)):
        common.write_tsv_dump(cf, "composers", dump_id)
        common.write_tsv_dump(wf, "works", dump_id)
        common.write_dump_meta(dump_id, {"created_at_utc": "2026-01-01"})
    args = argparse.Namespace(from_dump="new", base="base", to="out", dry_run=True)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    carry.run(args)
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    args.dry_run = False
    carry.run(args)
    written = pd.read_csv(tmp_path / "works_out.tsv", sep="\t", dtype=str, keep_default_na=False)
    assert "dump_date" not in written
    assert written.raw.tolist() == w.raw.tolist()
    assert set(pd.read_csv(tmp_path / "composers_out.tsv", sep="\t").dump_date) == {"out"}
    with pytest.raises(FileExistsError):
        carry.run(args)
