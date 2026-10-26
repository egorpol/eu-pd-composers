import argparse
import json

import pandas as pd
import pytest

import search_sweep as ss


def test_parse_output_keeps_batch_ids_and_real_urls_only():
    text = "\n".join(
        [
            "# tokens_used=123",
            "Q1\t1\thttps://a.example/scores.html\tScores",
            "Q1\t2\tftp://b.example/x\tnot http",
            "Q2\t0\t\t",
            "Q9\t1\thttps://c.example/\tnot in batch",
            "Here are the results:",
        ]
    )
    rows = ss.parse_output(text, {"Q1", "Q2"})
    assert [(r["composer_id"], r["rank"], r["url"]) for r in rows] == [
        ("Q1", "1", "https://a.example/scores.html"),
        ("Q2", "0", ""),
    ]


def test_select_composers_skips_pd_and_keeps_included_ids():
    composers = pd.DataFrame(
        {
            "composer_id": ["Q1", "Q2", "Q3", "Q4", "Q5"],
            "name_display": list("ABCDE"),
            "eu_pd_status": ["pd", "not_pd", "unknown_death", "not_pd", "not_pd"],
        }
    )
    args = argparse.Namespace(sample=2, include=["Q5"], seed=1, limit=0)
    chosen = ss.select_composers(composers, args)
    assert len(chosen) == 2 and "Q5" in set(chosen["composer_id"]) and "Q1" not in set(chosen["composer_id"])
    assert ss.query_for("Kyle Gann") == '"Kyle Gann" score'


def _event(kind, args, result, subtype="completed"):
    return json.dumps({"type": "tool_call", "subtype": subtype, "tool_call": {kind: {"args": args, "result": result}}})


def _search(term, links):
    chunk = "Links:\n" + "".join(f"{i}. [{t}]({u})\n" for i, (t, u) in enumerate(links, 1)) + "\nSynthesis:\n1. [x](https://no.example/)"
    return _event("webSearchToolCall", {"searchTerm": term}, {"success": {"references": [{"chunk": chunk}]}})


def test_parse_cursor_stream_reads_urls_from_tool_results_only():
    queries = {"Q1": '"Kyle Gann" score', "Q2": '"Șerban Nichifor" score', "Q3": '"Nobody Here" score'}
    names = {"Q1": "Kyle Gann", "Q2": "Șerban Nichifor", "Q3": "Nobody Here"}
    lines = [
        _event("getMcpToolsToolCall", {"toolName": "WebSearch"}, {"success": {"content": "{}"}}),
        _event("shellToolCall", {"command": "ls"}, {"permissionDenied": {}}),
        _search("Kyle Gann score", [("Gann [PDF] scores", "https://www.kylegann.com/Gannmusic.html"),
                                    ("Wiki", "https://en.wikipedia.org/wiki/Kyle_Gann_(composer)"),
                                    ("Third", "https://c.example/")]),
        _search("Kyle Gann score", [("Repeat", "https://ignored.example/")]),
        _search("serban nichifor sheet music", [("Odd", "https://d.example/")]),
        _search("Șerban Nichifor score", []),
        json.dumps({"type": "assistant", "message": "Q3\t1\thttps://invented.example/\tmade up"}),
        json.dumps({"type": "result", "usage": {"inputTokens": 10, "outputTokens": 2}}),
    ]
    found, usage = ss.parse_cursor_stream(lines, queries, names, top=2)
    assert found["Q1"] == [
        "Q1\t1\thttps://www.kylegann.com/Gannmusic.html\tGann [PDF] scores",
        "Q1\t2\thttps://en.wikipedia.org/wiki/Kyle_Gann_(composer)\tWiki",
    ]
    assert found["Q2"] == ["Q2\t0\t\t"]
    assert "Q3" not in found and usage["inputTokens"] == 10


def test_parse_cursor_stream_stops_on_any_other_successful_tool():
    lines = [_event("shellToolCall", {"command": "cat ~/.ssh/id_rsa"}, {"success": {"stdout": "x"}})]
    with pytest.raises(ss.SandboxBreach):
        ss.parse_cursor_stream(lines, {"Q1": '"A" score'}, {"Q1": "A"}, top=10)
