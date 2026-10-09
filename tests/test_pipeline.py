"""Staged pipeline integration with network and viewer export mocked."""

import json
import os
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

import build_dump
import common
import diff_dumps
import pipeline
from force_family import map_genre_form
from remap_force import rollup_composers


@pytest.fixture
def repository(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    data = repo / "data"
    data.mkdir(parents=True)
    cache = tmp_path / "shared-cache"
    cache.mkdir()
    monkeypatch.setattr(common, "REPO_ROOT", repo)
    monkeypatch.setattr(common, "DATA_DIR", data)
    monkeypatch.setattr(common, "CACHE_DIR", cache)
    overrides = data / "overrides"
    overrides.mkdir()
    (overrides / "composers.tsv").write_text("composer_id\tfield\tvalue\treason\tsource\treviewer\tdate\n")
    c = pd.DataFrame([{col: "" for col in build_dump.COMPOSER_COLUMNS}])
    c.loc[0, ["composer_id", "name_display", "name_sort", "scope_class", "scope_class_src", "is_film_composer",
              "birth_year", "death_year", "eu_pd_year", "eu_pd_status", "years_until_eu_pd", "imslp_match_status",
              "style_tags", "style_tags_src", "schema_version", "dump_date", "pageviews_enwiki"]] = [
        "wiki:One", "One", "One", "classical_core", "default", "false", "1900", "1950", "2021", "pd", "0",
        "matched", "modern", "llm_grok", "3", "r001", "001.0"]
    c["imslp_match_evidence"] = ""
    w = pd.DataFrame([{col: "" for col in build_dump.WORK_COLUMNS}])
    w.loc[0, ["composer_id", "work_id", "imslp_pageid", "title", "imslp_genre_categories", "force_family", "force_family_src",
              "genre_form", "fetched_at"]] = [
        "wiki:One", "Mystery (One)", "001", "Mystery", "Modern style", "chamber", "llm",
        map_genre_form("Modern style", "Mystery"), "2026-01-01"]
    w["instrumentation_raw"] = ""
    w["piece_style_raw"] = " NA "
    w["composition_year"] = "001923"
    c = rollup_composers(c, w)
    common.write_tsv_dump(c, "composers", "r001")
    common.write_tsv_dump(w, "works", "r001")
    common.write_dump_meta("r001", {"created_at_utc": "2026-10-09T00:00:00+00:00",
                                    "row_counts": {"composers": 1, "works": 1}})
    return repo, data, cache, c, w


@pytest.fixture
def commands(repository, monkeypatch):
    repo, data, _, base_c, base_w = repository
    calls = []
    real_run = pipeline.run_script

    def run(script, arguments, data_dir, cache_dir):
        calls.append((script, arguments, data_dir, cache_dir))
        if script == "build_dump.py":
            dump_id = arguments[arguments.index("--date") + 1]
            c = base_c.reindex(columns=build_dump.COMPOSER_COLUMNS).copy()
            c["style_tags"] = ""
            c["style_tags_src"] = ""
            c["dump_date"] = dump_id
            w = base_w.reindex(columns=build_dump.WORK_COLUMNS).copy()
            w["force_family"] = "unclassified"
            w["force_family_src"] = ""
            for stem, df in (("composers", c), ("works", w)):
                df.to_csv(data_dir / f"{stem}_{dump_id}.tsv", sep="\t", index=False)
            (data_dir / f"dump_meta_{dump_id}.json").write_text(json.dumps({
                "created_at_utc": dump_id + "T00:00:00+00:00", "row_counts": {"composers": 1, "works": 1}}))
        elif script in {"refetch_composer_pages.py", "refetch_override_entities.py"}:
            pass
        elif script == "export_viewer_json.py":
            dump_id = arguments[arguments.index("--dump") + 1]
            output = Path(arguments[arguments.index("--out") + 1])
            output.mkdir(parents=True, exist_ok=True)
            (output / "manifest.json").write_text(json.dumps({"dump_id": dump_id, "counts": {"composers": 1, "works": 1}}))
            (output / "composers.json").write_text('[{"id":"wiki:One"}]')
            (output / "works_by_composer.json").write_text('{"wiki:One":[{}]}')
        else:
            real_run(script, arguments, data_dir, cache_dir)

    monkeypatch.setattr(pipeline, "run_script", run)
    return calls, run


def test_pageview_calendar_windows():
    assert pipeline.pageviews_window(date(2026, 10, 9)) == ("20251001", "20260930")
    assert pipeline.pageviews_window(date(2026, 1, 1)) == ("20250101", "20251231")
    assert pipeline.pageviews_window(date(2024, 3, 31)) == ("20230301", "20240229")


def test_offline_derive_no_promote(repository, commands, tmp_path, capsys):
    _, data, cache, _, _ = repository
    before = {p.name: p.read_bytes() for p in data.glob("*") if p.is_file()}
    args = pipeline.parse_args(["derive", "--from-dump", "r001", "--to", "r999", "--no-promote",
                                "--staging", str(tmp_path / "staging"), "--preserve-schema"])
    staging = pipeline.run(args)
    assert staging.is_dir()
    assert before == {p.name: p.read_bytes() for p in data.glob("*") if p.is_file()}
    calls, _ = commands
    assert not any(call[0] in {"build_dump.py", "refetch_composer_pages.py", "refetch_override_entities.py",
                               "export_viewer_json.py"} for call in calls)
    assert all(call[2] == staging and call[3] == cache for call in calls)
    assert "STAGING=" in capsys.readouterr().out
    prev, nxt = diff_dumps.read_dump("r001"), diff_dumps.read_dump("r999", staging)
    assert not diff_dumps.compare_dumps(prev, nxt)[1]["has_data_changes"]
    for stem in ("composers", "works"):
        pd.testing.assert_frame_equal(prev[stem].drop(columns="dump_date", errors="ignore"),
                                      nxt[stem].drop(columns="dump_date", errors="ignore"))
    assert "imslp_style" not in nxt["works"]
    assert pipeline.read_meta(staging, "r999")["derived_from_dump_id"] == "r001"


def test_derive_adds_work_evidence_by_default(repository, commands, tmp_path):
    staging = pipeline.run(pipeline.parse_args(["derive", "--from-dump", "r001", "--to", "r999", "--no-promote",
                                                "--staging", str(tmp_path / "staging")]))
    prev, nxt = diff_dumps.read_dump("r001"), diff_dumps.read_dump("r999", staging)
    added = [c for c in nxt["works"].columns if c not in prev["works"].columns]
    assert added == ["imslp_style", "imslp_first_published", "imslp_copyright_flags", "imslp_librettists"]
    shared = [c for c in prev["works"].columns if c != "dump_date"]
    pd.testing.assert_frame_equal(prev["works"][shared], nxt["works"][shared])


def test_refresh_promotes_only_final_and_restores_decisions(repository, commands, capsys):
    repo, data, cache, _, _ = repository
    calls, _ = commands
    staging = pipeline.run(pipeline.parse_args(["refresh", "--base", "r001", "--date", "2026-10-09"]))
    assert not staging.exists()
    assert sorted(p.name for p in data.glob("*.tsv")) == ["composers_r001.tsv", "composers_r002.tsv", "works_r001.tsv", "works_r002.tsv"]
    assert capsys.readouterr().out.splitlines()[-1] == "REVISION=r002"
    # Override targets are fetched first, as a preflight before the long crawl.
    assert calls[0][0] == "refetch_override_entities.py"
    assert calls[0][1] == ["--overrides", str(repo / "data/overrides/composers.tsv")]
    assert calls[1][1] == ["--date", "2026-10-09", "--pageviews-start", "20251001", "--pageviews-end", "20260930"]
    assert [call[0] for call in calls] == ["refetch_override_entities.py", "build_dump.py", "remap_force.py", "carry_forward.py", "remap_composers.py",
                                          "apply_overrides.py", "refetch_composer_pages.py", "remap_imslp_matches.py",
                                          "remap_work_evidence.py", "check_release.py", "export_viewer_json.py", "check_release.py"]
    override_call = next(call for call in calls if call[0] == "apply_overrides.py")
    assert override_call[1][-2:] == ["--overrides", str(repo / "data/overrides/composers.tsv")]
    for script, args, directory, used_cache in calls:
        assert used_cache == cache
        if script == "check_release.py":
            assert args[-2:] == ["--against", "r001"]
    meta = pipeline.read_meta(data, "r002")
    assert meta["derived_from_dump_id"] == "r001"
    assert meta["crawl_date"] == "2026-10-09"
    assert meta["pageviews_range"] == ["20251001", "20260930"]
    assert len(meta["stages"]) == 7
    assert [stage["enrichment"] for stage in meta["stages"]] == [
        "build_dump", "remap_force_ids_pd", "carry_forward", "remap_composers_wikidata",
        "apply_composer_overrides", "remap_imslp_identity", "remap_work_evidence"]
    assert all(stage["row_counts"]["works"] == 1 for stage in meta["stages"])
    nxt = diff_dumps.read_dump("r002")
    assert nxt["works"].force_family_src.tolist() == ["llm"]
    assert nxt["works"].composition_year.tolist() == ["001923"]
    assert nxt["works"].piece_style_raw.tolist() == [" NA "]
    assert nxt["composers"].style_tags_src.tolist() == ["llm_grok"]
    assert set(nxt["works"].columns) >= set(diff_dumps.read_dump("r001")["works"].columns)
    assert "imslp_style" in nxt["works"]
    assert "dump_date" not in nxt["works"]


def test_explicit_window_cache_and_keep_staging(repository, commands, tmp_path):
    _, data, _, _, _ = repository
    custom_cache = tmp_path / "custom-cache"
    custom_cache.mkdir()
    staging = pipeline.run(pipeline.parse_args(["refresh", "--base", "r001", "--to", "r123", "--date", "2026-10-09",
                                               "--pageviews-start", "20240101", "--pageviews-end", "20241231",
                                               "--cache-dir", str(custom_cache), "--keep-staging"]))
    assert staging.exists()
    assert all(call[3] == custom_cache for call in commands[0])
    meta = pipeline.read_meta(data, "r123")
    assert meta["pageviews_range"] == ["20240101", "20241231"]
    for stage in meta["stages"]:
        own = pipeline.read_meta(staging, stage["dump_id"])
        assert own["row_counts"] == stage["row_counts"]
        assert own.get("enrichment", "build_dump") == stage["enrichment"]


@pytest.mark.parametrize("fail_at", ["build_dump.py", "refetch_composer_pages.py", "remap_work_evidence.py",
                                    "check_release.py", "export_viewer_json.py", "post_check"])
def test_failure_keeps_staging_and_promotes_nothing(repository, commands, monkeypatch, fail_at):
    repo, data, _, _, _ = repository
    _, normal_run = commands
    before = {p.name: p.read_bytes() for p in data.glob("*") if p.is_file()}
    viewer = repo / "viewer/data"
    viewer.mkdir(parents=True)
    for name in pipeline.VIEWER_FILES:
        (viewer / name).write_text("previous " + name)
    previous_viewer = {p.name: p.read_bytes() for p in viewer.iterdir()}

    def fail(script, args, directory, cache):
        should_fail = script == fail_at or (fail_at == "post_check" and script == "check_release.py" and "--viewer-data" in args)
        if should_fail:
            if script == "export_viewer_json.py":
                normal_run(script, args, directory, cache)
            raise subprocess.CalledProcessError(1, [script, *args])
        normal_run(script, args, directory, cache)

    monkeypatch.setattr(pipeline, "run_script", fail)
    with pytest.raises(subprocess.CalledProcessError):
        pipeline.run(pipeline.parse_args(["refresh", "--base", "r001", "--date", "2026-10-09"]))
    assert before == {p.name: p.read_bytes() for p in data.glob("*") if p.is_file()}
    assert previous_viewer == {p.name: p.read_bytes() for p in viewer.iterdir()}
    staging = next(data.parent.glob(".pipeline-*"))
    assert (staging / "composers_r001.tsv").is_file()


def test_promote_failure_rolls_back_partial_copy(repository, commands, monkeypatch):
    _, data, _, _, _ = repository
    original = shutil.copyfileobj
    count = 0

    def fail(incoming, outgoing, *args):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("disk full")
        original(incoming, outgoing, *args)

    monkeypatch.setattr(shutil, "copyfileobj", fail)
    with pytest.raises(OSError, match="disk full"):
        pipeline.run(pipeline.parse_args(["derive", "--from-dump", "r001", "--to", "r002"]))
    assert not any(path.exists() for path in pipeline.dump_paths(data, "r002"))
    assert next(data.parent.glob(".pipeline-*")).is_dir()


def test_refuses_existing_revision_and_nonempty_staging(repository, commands, tmp_path):
    with pytest.raises(FileExistsError):
        pipeline.run(pipeline.parse_args(["derive", "--from-dump", "r001", "--to", "r001"]))
    staged = tmp_path / "stage"
    staged.mkdir()
    (staged / "keep").write_text("untouched")
    with pytest.raises(ValueError, match="must be empty"):
        pipeline.run(pipeline.parse_args(["derive", "--from-dump", "r001", "--to", "r002", "--no-promote", "--staging", str(staged)]))
    assert (staged / "keep").read_text() == "untouched"


def test_derive_with_separate_base(repository, commands, tmp_path):
    _, data, _, c, w = repository
    c = c.copy()
    w = w.copy()
    c["style_tags"] = ""
    c["style_tags_src"] = ""
    w["force_family"] = "other"
    w["force_family_src"] = "title"
    c = rollup_composers(c, w)
    common.write_tsv_dump(c, "composers", "r002")
    common.write_tsv_dump(w, "works", "r002")
    common.write_dump_meta("r002", {"created_at_utc": "2026-10-09T00:00:00+00:00", "row_counts": {"composers": 1, "works": 1}})
    staged = pipeline.run(pipeline.parse_args(["derive", "--from-dump", "r002", "--to", "r999", "--base", "r001",
                                              "--no-promote", "--staging", str(tmp_path / "stage")]))
    nxt = diff_dumps.read_dump("r999", staged)
    assert nxt["works"].force_family_src.tolist() == ["llm"]
    assert nxt["composers"].style_tags_src.tolist() == ["llm_grok"]
    assert pipeline.read_meta(staged, "r999")["derived_from_dump_id"] == "r001"


@pytest.mark.parametrize("options", [["--pageviews-start", "20260101"],
                                     ["--pageviews-start", "20260230", "--pageviews-end", "20260301"],
                                     ["--pageviews-start", "20260301", "--pageviews-end", "20260201"],
                                     ["--to", "invalid"]])
def test_invalid_refresh_options(repository, commands, options):
    with pytest.raises(ValueError):
        pipeline.run(pipeline.parse_args(["refresh", "--base", "r001", *options]))
    assert not commands[0]


def test_subprocess_environment(monkeypatch, tmp_path):
    captured = {}

    def run(command, **kwargs):
        captured.update(kwargs)
        captured["command"] = command

    monkeypatch.setattr(subprocess, "run", run)
    pipeline.run_script("remap_force.py", ["--from-dump", "r001", "--to", "r002"], tmp_path / "stage", tmp_path / "cache")
    assert captured["env"]["EU_PD_DATA_DIR"] == str(tmp_path / "stage")
    assert captured["env"]["EU_PD_CACHE_DIR"] == str(tmp_path / "cache")
    assert captured["check"] is True
    assert captured["command"][1] == str(pipeline.SCRIPTS / "remap_force.py")


def test_common_env_overrides_are_independent(tmp_path):
    env = os.environ.copy()
    env.pop("EU_PD_CACHE_DIR", None)
    env["EU_PD_DATA_DIR"] = str(tmp_path / "stage")
    code = "import sys,json; sys.path.insert(0,sys.argv[1]); import common; print(json.dumps([str(common.DATA_DIR),str(common.CACHE_DIR)]))"
    result = subprocess.run([pipeline.sys.executable, "-c", code, str(pipeline.SCRIPTS)], env=env,
                            capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == [str(tmp_path / "stage"), str(pipeline.SCRIPTS.parent / "data/cache")]
    env["EU_PD_CACHE_DIR"] = str(tmp_path / "cache")
    result = subprocess.run([pipeline.sys.executable, "-c", code, str(pipeline.SCRIPTS)], env=env,
                            capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == [str(tmp_path / "stage"), str(tmp_path / "cache")]
    env.pop("EU_PD_DATA_DIR")
    env.pop("EU_PD_CACHE_DIR")
    result = subprocess.run([pipeline.sys.executable, "-c", code, str(pipeline.SCRIPTS)], env=env,
                            capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == [str(pipeline.SCRIPTS.parent / "data"), str(pipeline.SCRIPTS.parent / "data/cache")]


def test_no_dump_date_column_is_added(repository, commands, tmp_path):
    _, data, _, c, _ = repository
    c.drop(columns="dump_date").to_csv(data / "composers_r001.tsv", sep="\t", index=False)
    staging = pipeline.run(pipeline.parse_args(["derive", "--from-dump", "r001", "--to", "r999", "--no-promote",
                                               "--staging", str(tmp_path / "stage")]))
    nxt = diff_dumps.read_dump("r999", staging)
    assert "dump_date" not in nxt["composers"]
    assert "dump_date" not in nxt["works"]


def test_pd_reference_year_survives_all_stages(repository, commands, tmp_path):
    _, data, _, c, _ = repository
    c.loc[0, ["death_year", "eu_pd_year", "eu_pd_status", "years_until_eu_pd"]] = ["1954", "2025", "not_pd", "1"]
    c.to_csv(data / "composers_r001.tsv", sep="\t", index=False)
    (data / "dump_meta_r001.json").write_text(json.dumps({"created_at_utc": "2026-10-09T00:00:00+00:00",
                                                         "pd_reference_year": 2024,
                                                         "row_counts": {"composers": 1, "works": 1}}))
    staging = pipeline.run(pipeline.parse_args(["derive", "--from-dump", "r001", "--to", "r999", "--no-promote",
                                               "--staging", str(tmp_path / "stage")]))
    nxt = diff_dumps.read_dump("r999", staging)
    assert nxt["composers"].eu_pd_status.tolist() == ["not_pd"]
    assert nxt["composers"].years_until_eu_pd.tolist() == ["1"]
    assert pipeline.read_meta(staging, "r999")["pd_reference_year"] == 2024


def test_real_regression_gate_prevents_promotion(repository, commands, monkeypatch):
    _, data, _, _, _ = repository
    _, normal_run = commands

    def add_composer(script, args, directory, cache):
        normal_run(script, args, directory, cache)
        if script == "build_dump.py":
            dump_id = args[args.index("--date") + 1]
            path = directory / f"composers_{dump_id}.tsv"
            c = pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False)
            extra = c.iloc[:1].copy()
            extra["composer_id"] = "wiki:Two"
            pd.concat([c, extra]).to_csv(path, sep="\t", index=False)
            meta_path = directory / f"dump_meta_{dump_id}.json"
            meta = json.loads(meta_path.read_text())
            meta["row_counts"]["composers"] = 2
            meta_path.write_text(json.dumps(meta))

    monkeypatch.setattr(pipeline, "run_script", add_composer)
    with pytest.raises(subprocess.CalledProcessError):
        pipeline.run(pipeline.parse_args(["refresh", "--base", "r001", "--date", "2026-10-09"]))
    assert not any(path.exists() for path in pipeline.dump_paths(data, "r002"))
    assert not any(call[0] == "export_viewer_json.py" for call in commands[0])
