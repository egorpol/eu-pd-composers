"""Offline style policy replay, provenance and immutable outputs."""

import argparse
import hashlib
import json

import pandas as pd
import pytest

import apply_llm_styles as styles
import common


def label(cid="Q1", labeller=0, tags=None, period="modern", **changes):
    backend, model, effort, _ = styles.POLICY_LABELLERS[labeller]
    row = {"composer_id": cid, "backend": backend, "model": model, "effort": effort,
           "condition": styles.POLICY_CONDITION, "prompt_version": styles.POLICY_PROMPT_VERSION,
           "styles": tags if tags is not None else ["modern"], "primary_period": period,
           "created_at": "2026-10-10T00:00:00Z", "input_hash": "original"}
    row.update(changes)
    return row


def ledger(tmp_path, rows):
    path = tmp_path / "labels.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def composers(*ids):
    return pd.DataFrame([{"composer_id": cid, "style_tags": "", "style_tags_src": "",
                          "dump_date": "r014", "raw": " NA 001 "} for cid in ids])


def test_consensus_order_period_votes_and_column_order(tmp_path):
    path = ledger(tmp_path, [
        label(tags=["neoclassicism", "impressionism", "modern"]),
        label(labeller=1, tags=["impressionism", "neoclassicism"]),
    ])
    source = composers("Q1")
    out, counts = styles.apply_llm_styles(source, styles.load_ledger(path))
    assert out.llm_style_tags.tolist() == ["neoclassicism|impressionism"]
    assert out.llm_style_period.tolist() == ["modern"]
    assert out.llm_style_votes.tolist() == [
        "gpt-6.1-sol@low=neoclassicism|impressionism|modern;grok-4.7-low=impressionism|neoclassicism",
    ]
    assert out.llm_style_src.tolist() == [styles.POLICY_ID]
    assert counts["consensus_nonempty"] == counts["period_nonempty"] == 1
    assert list(out.columns) == [*source.columns[:3], *styles.LLM_STYLE_COLUMNS, *source.columns[3:]]
    pd.testing.assert_frame_equal(out[source.columns], source)


def test_abstain_missing_disagreement_and_unknown_period(tmp_path):
    rows = [label("abstain"), label("abstain", 1, []),
            label("missing"), label("cursor_only", 1, []),
            label("disagree"), label("disagree", 1, ["romantic"], "romantic"),
            label("unknown", period="unknown"), label("unknown", 1, period="unknown")]
    out, counts = styles.apply_llm_styles(
        composers("abstain", "missing", "cursor_only", "neither", "disagree", "unknown"),
        styles.load_ledger(ledger(tmp_path, rows)),
    )
    indexed = out.set_index("composer_id")
    assert indexed.loc["abstain", "llm_style_tags"] == ""
    assert indexed.loc["abstain", "llm_style_votes"] == "gpt-6.1-sol@low=modern;grok-4.7-low="
    assert indexed.loc["abstain", "llm_style_src"] == styles.POLICY_ID
    assert indexed.loc["missing", "llm_style_votes"] == "gpt-6.1-sol@low=modern"
    assert indexed.loc["cursor_only", "llm_style_votes"] == "grok-4.7-low="
    assert indexed.loc["neither", list(styles.LLM_STYLE_COLUMNS)].tolist() == [""] * 4
    assert indexed.loc[["missing", "cursor_only"], "llm_style_src"].tolist() == ["", ""]
    assert indexed.loc["disagree", "llm_style_period"] == ""
    assert indexed.loc["unknown", "llm_style_period"] == ""
    assert counts == {"consensus_nonempty": 1, "both_labelled_no_consensus": 2, "missing_labellers": 3,
                      "period_nonempty": 1, "both_labelled": 3, "legacy_retired": 0}


def test_latest_timestamp_then_line_wins_across_input_hashes(tmp_path):
    rows = [label(tags=["romantic"], created_at="2026-10-09T23:00:00Z"),
            label(tags=["modern"], created_at="2026-10-10T00:01:00+00:00", input_hash="changed"),
            label(tags=["baroque"], created_at="2026-10-09T23:59:00Z"),
            label(tags=["impressionism"], created_at="2026-10-10T00:01:00Z"),
            label(labeller=1, tags=["impressionism"])]
    out, _ = styles.apply_llm_styles(composers("Q1"), styles.load_ledger(ledger(tmp_path, rows)))
    assert out.llm_style_tags.tolist() == ["impressionism"]


@pytest.mark.parametrize("change", [
    {"condition": "closed"}, {"prompt_version": "style-v1"}, {"effort": "medium"},
    {"backend": "cursor"}, {"model": "another-model"},
])
def test_nonpolicy_rows_ignored_even_if_newer(tmp_path, change):
    rows = [label(), label(labeller=1),
            label(tags=["romantic"], created_at="2026-10-11T00:00:00Z", **change)]
    out, _ = styles.apply_llm_styles(composers("Q1"), styles.load_ledger(ledger(tmp_path, rows)))
    assert out.llm_style_tags.tolist() == ["modern"]


def test_legacy_retirement_and_idempotency(tmp_path):
    source = composers("llm", "llm_old", "wikidata", "empty")
    source["style_tags"] = ["serialism", "romantic", "impressionism", ""]
    source["style_tags_src"] = ["llm", "llm_luna_xhigh", "wikidata", ""]
    selected = styles.load_ledger(ledger(tmp_path, [label("llm"), label("llm", 1)]))
    out, counts = styles.apply_llm_styles(source, selected)
    again, second_counts = styles.apply_llm_styles(out, selected)
    assert out.style_tags.tolist() == ["", "", "impressionism", ""]
    assert out.style_tags_src.tolist() == ["", "", "wikidata", ""]
    pd.testing.assert_frame_equal(out, again)
    assert counts["legacy_retired"] == 2
    assert second_counts["legacy_retired"] == 0


def test_cli_meta_dry_run_immutability_and_string_values(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "DATA_DIR", tmp_path)
    c = composers("Q1")
    w = pd.DataFrame([{"composer_id": "Q1", "work_id": "001", "title": "NA", "raw": " 001 "}])
    common.write_tsv_dump(c, "composers", "r014")
    common.write_tsv_dump(w, "works", "r014")
    common.write_dump_meta("r014", {"pd_reference_year": 2024, "row_counts": {"composers": 1, "works": 1}})
    path = ledger(tmp_path, [label(), label(labeller=1)])
    args = argparse.Namespace(from_dump="r014", to="r015", ledger=path, dry_run=True)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    styles.run(args)
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    args.dry_run = False
    styles.run(args)
    meta = json.loads(common.dump_meta_path("r015").read_text())
    assert meta["llm_styles"]["ledger_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert meta["llm_styles"]["policy"] == styles.policy_metadata()
    assert meta["llm_styles"]["counts"]["consensus_nonempty"] == 1
    assert meta["llm_styles"]["ledger_path"] == str(path)  # outside the repo: kept as given
    assert meta["schema_version"] == 3 and meta["pd_reference_year"] == 2024
    assert common.dump_tsv_path("works", "r015").read_bytes() == common.dump_tsv_path("works", "r014").read_bytes()
    written = pd.read_csv(common.dump_tsv_path("composers", "r015"), sep="\t", dtype=str, keep_default_na=False)
    assert written.raw.tolist() == c.raw.tolist()
    assert written.dump_date.tolist() == ["r015"]
    with pytest.raises(FileExistsError):
        styles.run(args)


@pytest.mark.parametrize("has_dump_date", [True, False])
def test_cli_stamps_existing_composer_dump_date_only(tmp_path, monkeypatch, has_dump_date):
    monkeypatch.setattr(common, "DATA_DIR", tmp_path)
    c = composers("Q1")
    if not has_dump_date:
        c = c.drop(columns="dump_date")
    w = pd.DataFrame([{"composer_id": "Q1", "work_id": "001", "dump_date": "r014"}])
    common.write_tsv_dump(c, "composers", "r014")
    common.write_tsv_dump(w, "works", "r014")
    common.write_dump_meta("r014", {"row_counts": {"composers": 1, "works": 1}})
    path = ledger(tmp_path, [])
    styles.run(argparse.Namespace(from_dump="r014", to="r998", ledger=path, dry_run=False))
    written = pd.read_csv(common.dump_tsv_path("composers", "r998"), sep="\t", dtype=str, keep_default_na=False)
    if has_dump_date:
        assert written.dump_date.tolist() == ["r998"]
    else:
        assert "dump_date" not in written
    assert common.dump_tsv_path("works", "r998").read_bytes() == common.dump_tsv_path("works", "r014").read_bytes()


def test_meta_ledger_path_is_repo_relative():
    assert styles.meta_path_label(styles.default_ledger_path()) == "data/llm_ledger/style_labels.jsonl"
