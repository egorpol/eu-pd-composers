"""Mocked tests for the LLM style-labelling pass (no network, no real CLIs)."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

import common
import fetch_wikipedia_leads as wiki_leads
import llm_style_pass as lsp
from style_vocab import ERA_SLUGS, STYLE_SLUGS


def _schema():
    return json.loads(lsp.SCHEMA_PATH.read_text(encoding="utf-8"))


def test_schema_matches_vocab():
    schema = _schema()
    enum = schema["properties"]["labels"]["items"]["properties"]["styles"]["items"]["enum"]
    assert enum == STYLE_SLUGS
    period_enum = schema["properties"]["labels"]["items"]["properties"]["primary_period"]["enum"]
    assert period_enum == ERA_SLUGS + ["unknown"]
    assert schema["required"] == ["labels"]


def test_prompt_contains_no_forbidden_fields():
    records = [
        {
            "composer_id": "Q123",
            "name_display": "Ada Example",
            "birth_year": 1900,
            "death_year": 1950,
            "citizenship_iso": "DE",
        }
    ]
    prompt = lsp.build_prompt(records, condition="closed")
    lsp.assert_prompt_clean(prompt)
    for field in lsp.FORBIDDEN_PROMPT_FIELDS:
        assert field not in prompt
    assert "Ada Example" in prompt
    assert "style-v1" in prompt
    # grounded adds lead only
    g = dict(records[0])
    g["wikipedia_lead"] = "A German composer of piano music."
    gp = lsp.build_prompt([g], condition="grounded")
    lsp.assert_prompt_clean(gp)
    assert "wikipedia_lead" in gp
    assert "piano music" in gp


def test_validate_labels_ok_and_drops_unknown_slugs():
    ids = ["Q1", "Q2"]
    parsed = {
        "labels": [
            {
                "id": "Q1",
                "styles": ["romantic", "not_a_real_slug", "national_folk"],
                "primary_period": "romantic",
                "confidence": "high",
            },
            {
                "id": "Q2",
                "styles": [],
                "primary_period": "unknown",
                "confidence": "low",
            },
        ]
    }
    by_id, stats = lsp.validate_labels(parsed, ids)
    assert by_id["Q1"]["styles"] == ["romantic", "national_folk"]
    assert stats["unknown_slugs_dropped"] == 1
    assert by_id["Q2"]["primary_period"] == "unknown"


def test_validate_labels_rejects_id_mismatch_and_bad_period():
    with pytest.raises(ValueError, match="Id mismatch"):
        lsp.validate_labels(
            {
                "labels": [
                    {
                        "id": "Q1",
                        "styles": [],
                        "primary_period": "unknown",
                        "confidence": "low",
                    }
                ]
            },
            ["Q1", "Q2"],
        )
    with pytest.raises(ValueError, match="primary_period"):
        lsp.validate_labels(
            {
                "labels": [
                    {
                        "id": "Q1",
                        "styles": [],
                        "primary_period": "jazz",
                        "confidence": "low",
                    }
                ]
            },
            ["Q1"],
        )


def test_split_retry_on_failure(monkeypatch):
    calls: list[list[str]] = []

    def fake_backend(prompt, **kwargs):
        # Extract composer ids from the prompt's trailing JSON array is awkward;
        # count calls by looking at "composer_id" occurrences via a side channel.
        raise AssertionError("use call_backend mock")

    def fake_call(prompt, **kwargs):
        # Pull ids from the composers JSON blob at the end of the prompt.
        start = prompt.rfind("Composers JSON:\n")
        blob = prompt[start + len("Composers JSON:\n") :]
        records = json.loads(blob)
        ids = [r["composer_id"] for r in records]
        calls.append(ids)
        if len(ids) > 2:
            raise RuntimeError("too big")
        return {
            "labels": [
                {
                    "id": cid,
                    "styles": ["modern"],
                    "primary_period": "modern",
                    "confidence": "medium",
                }
                for cid in ids
            ]
        }, {"input_tokens": 1}

    monkeypatch.setattr(lsp, "call_backend", fake_call)
    records = [
        {
            "composer_id": f"Q{i}",
            "name_display": f"N{i}",
            "birth_year": 1900 + i,
            "death_year": None,
            "citizenship_iso": "FR",
        }
        for i in range(4)
    ]
    by_id, failed, dropped, usage = lsp.label_batch_with_split(
        records,
        condition="closed",
        backend="codex",
        binary="codex",
        model="m",
        effort="low",
        batch_id="t0",
    )
    assert failed == []
    assert set(by_id) == {"Q0", "Q1", "Q2", "Q3"}
    assert calls[0] == ["Q0", "Q1", "Q2", "Q3"]
    assert sorted(len(c) for c in calls[1:]) == [2, 2]
    assert usage.get("input_tokens") == 2


def test_split_retry_unrecoverable_half(monkeypatch):
    def fake_call(prompt, **kwargs):
        start = prompt.rfind("Composers JSON:\n")
        records = json.loads(prompt[start + len("Composers JSON:\n") :])
        ids = [r["composer_id"] for r in records]
        if "Qbad" in ids:
            raise RuntimeError("bad apple")
        return {
            "labels": [
                {
                    "id": cid,
                    "styles": [],
                    "primary_period": "unknown",
                    "confidence": "low",
                }
                for cid in ids
            ]
        }, {}

    monkeypatch.setattr(lsp, "call_backend", fake_call)
    records = [
        {
            "composer_id": "Qok",
            "name_display": "Ok",
            "birth_year": 1900,
            "death_year": 1980,
            "citizenship_iso": "IT",
        },
        {
            "composer_id": "Qbad",
            "name_display": "Bad",
            "birth_year": 1901,
            "death_year": 1981,
            "citizenship_iso": "IT",
        },
    ]
    by_id, failed, _, _ = lsp.label_batch_with_split(
        records,
        condition="closed",
        backend="codex",
        binary="codex",
        model="m",
        effort="low",
        batch_id="t1",
    )
    # First call (both) fails → split; half with Qbad fails without further split.
    assert "Qok" in by_id
    assert failed == ["Qbad"]


def test_ledger_resume_skip(tmp_path, monkeypatch):
    labels = tmp_path / "style_labels.jsonl"
    monkeypatch.setattr(lsp, "LABELS_PATH", labels)
    monkeypatch.setattr(lsp, "LEDGER_DIR", tmp_path)
    monkeypatch.setattr(lsp, "RUNS_PATH", tmp_path / "runs.jsonl")

    rec = {
        "composer_id": "Q9",
        "name_display": "X",
        "birth_year": 1900,
        "death_year": 1970,
        "citizenship_iso": "PL",
    }
    h = lsp.input_hash(rec)
    row = {
        "composer_id": "Q9",
        "condition": "closed",
        "backend": "codex",
        "model": "gpt-test",
        "effort": "low",
        "prompt_version": lsp.PROMPT_VERSION,
        "input_hash": h,
        "styles": ["romantic"],
        "primary_period": "romantic",
        "confidence": "high",
        "run_id": "r0",
        "batch_id": "b0",
        "created_at": "2026-01-01T00:00:00Z",
    }
    labels.write_text(json.dumps(row) + "\n", encoding="utf-8")
    keys = lsp.load_ledger_keys(labels)
    assert (
        "Q9",
        "closed",
        "gpt-test",
        "low",
        lsp.PROMPT_VERSION,
        h,
    ) in keys
    # Different input_hash must not skip
    rec2 = dict(rec)
    rec2["citizenship_iso"] = "DE"
    assert lsp.input_hash(rec2) != h


def test_scrub_child_env_drops_appimage_leaks(monkeypatch):
    env = {
        "PATH": "/tmp/.mount_CursorApp/usr/bin:/usr/bin:/bin",
        "LD_LIBRARY_PATH": "/tmp/.mount_CursorApp/usr/lib:/usr/lib",
        "HOME": "/home/egor",
        "KEEP": "yes",
    }
    cleaned = lsp.scrub_child_env(env)
    assert cleaned["PATH"] == "/usr/bin:/bin"
    assert "LD_LIBRARY_PATH" not in cleaned
    assert cleaned["KEEP"] == "yes"
    assert cleaned["HOME"] == "/home/egor"


def test_extract_json_tolerates_fence_and_cursor_wrapper():
    obj = {"labels": [{"id": "Q1", "styles": [], "primary_period": "unknown", "confidence": "low"}]}
    fenced = "```json\n" + json.dumps(obj) + "\n```"
    assert lsp.extract_json_object(fenced) == obj
    wrapped = json.dumps({"type": "result", "result": json.dumps(obj)})
    text = lsp.extract_cursor_text(wrapped)
    assert lsp.extract_json_object(text) == obj


def test_truncate_at_sentence():
    short = "Hello. World."
    assert wiki_leads.truncate_at_sentence(short, 1200) == short
    long = ("Sentence one is here. " * 40) + ("Word " * 200)
    out = wiki_leads.truncate_at_sentence(long, 200)
    assert len(out) <= 200
    assert out.endswith(".")


def test_fetch_batch_follows_redirects(monkeypatch):
    class Sess:
        pass

    def fake_request(session, url, params=None, **kwargs):
        return {
            "query": {
                "normalized": [{"from": "Foo_Bar", "to": "Foo Bar"}],
                "redirects": [{"from": "Foo Bar", "to": "Foo Bar (composer)"}],
                "pages": {
                    "1": {
                        "title": "Foo Bar (composer)",
                        "extract": "A composer. More text that goes on.",
                    }
                },
            }
        }

    monkeypatch.setattr(wiki_leads, "request_json", fake_request)
    out = wiki_leads.fetch_batch(Sess(), ["Foo_Bar"])
    assert "composer" in out["Foo_Bar"]


def test_codex_and_cursor_commands_use_scrubbed_env(monkeypatch, tmp_path):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["env"] = kwargs.get("env")
        seen["input"] = kwargs.get("input")
        seen["cwd"] = kwargs.get("cwd")
        # The model must not see the repo (dumps hold the labels under study).
        assert seen["cwd"] and not any(Path(seen["cwd"]).iterdir())
        # Write empty output file for codex -o
        if "-o" in cmd:
            out = Path(cmd[cmd.index("-o") + 1])
            out.write_text(
                json.dumps(
                    {
                        "labels": [
                            {
                                "id": "Q1",
                                "styles": [],
                                "primary_period": "unknown",
                                "confidence": "low",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

        class P:
            returncode = 0
            stdout = json.dumps(
                {
                    "result": json.dumps(
                        {
                            "labels": [
                                {
                                    "id": "Q1",
                                    "styles": [],
                                    "primary_period": "unknown",
                                    "confidence": "low",
                                }
                            ]
                        }
                    )
                }
            )
            stderr = ""

        return P()

    monkeypatch.setattr(lsp.subprocess, "run", fake_run)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/.mount_x/lib")
    monkeypatch.setenv("PATH", "/tmp/.mount_x/bin:/usr/bin")

    raw, _ = lsp.run_codex(
        "prompt",
        binary="codex",
        model="m",
        effort="low",
        schema=lsp.SCHEMA_PATH,
    )
    assert seen["cmd"][0] == "codex"
    assert seen["cmd"][-1] == "-"
    assert "--output-schema" in seen["cmd"]
    assert "--force" not in seen["cmd"]
    assert "LD_LIBRARY_PATH" not in seen["env"]
    assert "/tmp/.mount_" not in seen["env"]["PATH"]
    assert seen["input"] == "prompt"
    assert "labels" in json.loads(raw)

    lsp.run_cursor("prompt2", binary="cursor-agent", model="m")
    assert seen["cmd"][:2] == ["cursor-agent", "-p"]
    assert "--mode" in seen["cmd"] and "ask" in seen["cmd"]
    assert "--force" not in seen["cmd"]
    assert "--trust" in seen["cmd"]
    assert seen["input"] == "prompt2"


def test_parse_cli_usage():
    assert lsp.parse_codex_usage("codex\n{}\ntokens used\n4,798\n") == {"total_tokens": 4798}
    assert lsp.parse_codex_usage("no usage here") == {}
    out = json.dumps(
        {"type": "result", "result": "{}", "usage": {"inputTokens": 14697, "outputTokens": 269, "cacheReadTokens": 0}}
    )
    assert lsp.parse_cursor_usage(out) == {"input_tokens": 14697, "output_tokens": 269, "cache_read_tokens": 0}
    assert lsp.parse_cursor_usage("not json") == {}


def test_prepare_records_skips_missing_grounded_leads(monkeypatch, tmp_path):
    monkeypatch.setattr(common, "CACHE_DIR", tmp_path)
    df = pd.DataFrame(
        [
            {
                "composer_id": "Q1",
                "name_display": "A",
                "birth_year": 1900,
                "death_year": 1980,
                "citizenship_iso": "DE",
                "wikipedia_url": "https://en.wikipedia.org/wiki/A_Composer",
            },
            {
                "composer_id": "Q2",
                "name_display": "B",
                "birth_year": 1910,
                "death_year": 1990,
                "citizenship_iso": "FR",
                "wikipedia_url": "https://en.wikipedia.org/wiki/B_Composer",
            },
        ]
    )
    common.cache_set(
        wiki_leads.CACHE_NS,
        "A Composer",
        {"title": "A Composer", "extract": "Lead text about A.", "chars": 18},
    )
    recs, missing = lsp.prepare_records(df, condition="grounded", ids=None, limit=None)
    assert [r["composer_id"] for r in recs] == ["Q1"]
    assert missing == ["Q2"]
    assert "wikipedia_lead" in recs[0]
