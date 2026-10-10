"""Offline tests for IMSLP work evidence parsing and remap CLI."""

from __future__ import annotations

import argparse
import json

import pandas as pd
import pytest
import requests

import common
import imslp_work_evidence as evidence
import remap_work_evidence as remap


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail("Work evidence tests must not access the network or real cache")

    monkeypatch.setattr(requests.Session, "request", fail)
    monkeypatch.setattr(common, "cache_get", fail)
    monkeypatch.setattr(common, "cache_set", fail)


def test_every_copyright_mapping_row():
    for category, tokens in evidence.COPYRIGHT_CATEGORY_FLAGS.items():
        result = evidence.work_evidence([category])
        assert result["imslp_copyright_flags"] == "|".join(sorted(tokens))
        assert evidence.unmapped_copyright_categories([category]) == []


def test_worknonpd_usandeu_expands_to_both_tokens():
    result = evidence.work_evidence(["WorkNonPD-USandEU"])
    assert result["imslp_copyright_flags"] == "nonpd_eu|nonpd_us"


def test_copyright_flags_sorted_and_deduplicated():
    result = evidence.work_evidence(
        [
            "WorkNonPD-US",
            "WorkNonPD-EU",
            "WorkNonPD-USandEU",
            "Files PD in the EU due to RoST",
            "WorkNonPD-EU",
        ]
    )
    assert result["imslp_copyright_flags"] == "nonpd_eu|nonpd_us|pd_eu_rost"


def test_style_ordering_and_dedup():
    result = evidence.work_evidence(
        [
            "Romantic style",
            "For piano",
            "Early 20th century style",
            "Romantic style",
            "Modern style",
        ]
    )
    assert result["imslp_style"] == "Romantic|Early 20th century|Modern"


def test_decade_and_century_publication_categories_ignored():
    result = evidence.work_evidence(
        [
            "Works first published in the 19th century",
            "Works first published in the 1910s",
            "Works first published in 1922",
            "Works first published in 1909",
            "Works first published in the 20th century",
        ]
    )
    assert result["imslp_first_published"] == "1909"


def test_multiple_publication_years_take_earliest():
    result = evidence.work_evidence(
        [
            "Works first published in 1940",
            "Works first published in 1912",
            "Works first published in 1925",
        ]
    )
    assert result["imslp_first_published"] == "1912"


def test_no_publication_year_is_empty():
    assert evidence.work_evidence(["Romantic style"])["imslp_first_published"] == ""


def test_librettist_suffix_parsing_order_and_dedup():
    result = evidence.work_evidence(
        [
            "Hugo, Victor/Librettist",
            "Someone/Editor",
            "Hofmannsthal, Hugo von/Librettist",
            "Hugo, Victor/Librettist",
            "Dedicatee person/Dedicatee",
        ]
    )
    assert result["imslp_librettists"] == "Hugo, Victor|Hofmannsthal, Hugo von"


def test_unmapped_copyright_category_reported_but_not_in_cell():
    cats = [
        "WorkNonPD-EU",
        "Some Future NonPD Flag",
        "Items under mystery copyright regime",
        "Files PD somewhere due to RoST",
        "Rostropovich, Mstislav/Dedicatee",
        "Frost, Robert/Librettist",
        "Works Licensed through SACEM",
        "WorkPD-CAonly",
        "Items in the public domain on Mars",
        "WIMA mirror files",
        "Scores published by BMI Canada",
        "Scores from BandMusic PDF",
        "Submission Project",
        "Tag 'fl ob cl bn hpd str'",
        "Wimann, Anna/Editor",
    ]
    result = evidence.work_evidence(cats)
    assert result["imslp_copyright_flags"] == "nonpd_eu"
    assert result["imslp_librettists"] == "Frost, Robert"
    assert evidence.unmapped_copyright_categories(cats) == [
        "Some Future NonPD Flag",
        "Items under mystery copyright regime",
        "Files PD somewhere due to RoST",
        "Works Licensed through SACEM",
        "WorkPD-CAonly",
        "Items in the public domain on Mars",
        "WIMA mirror files",
    ]


def test_rights_basis_categories_map_to_tokens():
    result = evidence.work_evidence([
        "Works not in public domain",
        "WorkPD-USonly",
        "Work-PD-US-notrenewed",
        "PD-US-no notice",
        "WIMA files",
        "Works Licensed through BMI",
        "Works Licensed through GEMA",
    ])
    assert result["imslp_copyright_flags"] == (
        "nonpd_licensed|pd_us_no_notice|pd_us_notrenewed|pd_us_only|pro_licensed|wima"
    )


def test_file_evidence_hosts_and_images():
    files = [
        "Cover scan.jpg",
        "TN-PMLP7-PMLUS1-placeholder.png",
        "PMLP7-PMLUS1-placeholder-score.pdf",
        "PMLP7-PMLASIA2-placeholder-parts.PDF",
        "PMLP7-PMLP9-nested-token.pdf",
        "PMLP7-Plain score.pdf",
        "WIMA.01f5-legacy-name.mid",
        "SIBLEY1802.10954-score.pdf",
    ]
    assert evidence.file_evidence(files) == {"has_files": "true", "imslp_file_hosts": "asia|ca|us"}
    assert evidence.file_host("PMLP7-PMLEU3-new-server.pdf") == "eu"
    assert evidence.file_evidence(["PMLP7-PMLUS1-x.mp3"]) == {"has_files": "true", "imslp_file_hosts": "us"}


@pytest.mark.parametrize("files", [[], ["Cover.jpg", "TN-PMLP7-x.png", "PV-preview.PNG"]])
def test_file_evidence_without_scores(files):
    assert evidence.file_evidence(files) == {"has_files": "false", "imslp_file_hosts": ""}


def test_empty_categories_yield_empty_cells():
    assert evidence.work_evidence([]) == {
        "imslp_style": "",
        "imslp_first_published": "",
        "imslp_copyright_flags": "",
        "imslp_librettists": "",
    }


def _page(categories=None, *, redirect=False, missing=False, pageid=1, title="Work"):
    return {
        "pageid": pageid,
        "title": title,
        "categories": list(categories or []),
        "missing": missing,
        "redirect": redirect,
        "fetched_at": "2026-01-01T00:00:00+00:00",
    }


def _files(images=None, *, redirect=False, missing=False, pageid=1):
    return {
        "pageid": pageid,
        "images": list(images or []),
        "missing": missing,
        "redirect": redirect,
        "fetched_at": "2026-01-01T00:00:00+00:00",
    }


def _work(pageid, *, composer_id="Q1", title="Opera", composition_year="1910"):
    return {
        "work_id": title,
        "composer_id": composer_id,
        "imslp_pageid": str(pageid),
        "imslp_work_url": f"https://imslp.org/wiki/{title}",
        "title": title,
        "imslp_genre_categories": "Operas",
        "force_family": "vocal_stage",
        "force_family_src": "imslp_tags",
        "genre_form": "opera",
        "has_files": "true",
        "fetched_at": "2026-01-01T00:00:00+00:00",
        "instrumentation_raw": "voice",
        "piece_style_raw": "Romantic",
        "composition_year": composition_year,
        "untouched": "keep-me",
    }


def _composer(qid, name, *, eu_pd_status="pd"):
    return {
        "composer_id": qid,
        "name_display": name,
        "eu_pd_status": eu_pd_status,
        "dump_date": "r012",
        "imslp_category": f"Category:{name}",
    }


@pytest.fixture
def source(monkeypatch):
    pages = {
        "10": _page(
            [
                "Romantic style",
                "Early 20th century style",
                "Romantic style",
                "Works first published in the 20th century",
                "Works first published in 1922",
                "Works first published in 1910",
                "WorkNonPD-USandEU",
                "Hugo, Victor/Librettist",
                "Hofmannsthal, Hugo von/Librettist",
                "Hugo, Victor/Librettist",
            ],
            pageid=10,
            title="Full Work",
        ),
        "11": _page(redirect=True, pageid=11, title="Redirected"),
        "12": _page(missing=True, pageid=12, title="Missing"),
        "13": _page(
            [
                "Modern style",
                "WorkNonPD-EU",
                "Some New NonPD Category",
                "Works first published in 1950",
            ],
            pageid=13,
            title="Unmapped Copy",
        ),
    }
    files = {
        "10": _files(["PMLP10-score.pdf", "PMLP10-PMLUS3-placeholder.pdf", "Cover.jpg"], pageid=10),
        "11": _files(redirect=True, pageid=11),
        "12": _files(["Cover.jpg"], pageid=12),
        "13": _files(["PMLP13-PMLASIA1-placeholder.mp3"], pageid=13),
    }
    calls: list[tuple[str, str]] = []

    def cache_get(namespace, key):
        calls.append((namespace, key))
        return {"imslp_page_cats": pages, "imslp_page_files": files}[namespace].get(key)

    monkeypatch.setattr(common, "cache_get", cache_get)
    monkeypatch.setattr(remap, "cache_get", cache_get)

    composers = pd.DataFrame(
        [
            _composer("Q1", "Alive Composer", eu_pd_status="not_pd"),
            _composer("Q2", "PD Composer", eu_pd_status="pd"),
        ]
    )
    works = pd.DataFrame(
        [
            _work(10, composer_id="Q1", title="Full Work"),
            _work(11, composer_id="Q2", title="Redirected"),
            _work(12, composer_id="Q2", title="Missing Page"),
            _work(13, composer_id="Q2", title="Unmapped Copy"),
            {
                **_work(99, composer_id="Q1", title="No Cache"),
                "imslp_pageid": "99",
            },
        ]
    )
    return composers, works, calls


def test_remap_redirect_missing_and_uncached_are_empty(source):
    composers, works, _ = source
    out, report = remap.remap_work_evidence(composers, works)
    assert out.loc[1, "imslp_style"] == ""
    assert out.loc[2, "imslp_style"] == ""
    assert out.loc[4, "imslp_style"] == ""
    assert report["redirect_or_missing_page"] == 2
    assert report["missing_cache"] == 1
    assert report["kept_prior"] == 0


def test_uncached_page_keeps_prior_evidence(source):
    composers, works, calls = source
    works = pd.concat(
        [works, pd.DataFrame([{**_work(20, composer_id="Q1", title="No Page"), "imslp_pageid": ""}])],
        ignore_index=True,
    )
    for col in evidence.EVIDENCE_COLUMNS:
        works[col] = ""
    works.loc[[0, 1, 4, 5], "imslp_style"] = "Baroque"
    works.loc[[4, 5], "imslp_first_published"] = "1700"
    out, report = remap.remap_work_evidence(composers, works)
    # Cached pages (including redirects) are recomputed; only the uncached page keeps its cells.
    assert out.loc[0, "imslp_style"] == "Romantic|Early 20th century"
    assert out.loc[1, "imslp_style"] == ""
    assert out.loc[4, "imslp_style"] == "Baroque"
    assert out.loc[4, "imslp_first_published"] == "1700"
    # A row without a page id has no IMSLP evidence to keep.
    assert out.loc[5, "imslp_style"] == ""
    assert out.loc[5, "imslp_first_published"] == ""
    assert report["missing_cache"] == 1
    assert report["kept_prior"] == 1
    assert report["no_pageid"] == 1
    assert report["style_counts"]["Baroque"] == 1
    assert report["coverage"]["imslp_first_published"]["count"] == 3
    assert ("imslp_page_cats", "") not in calls


def test_remap_fills_evidence_and_reports(source):
    composers, works, calls = source
    out, report = remap.remap_work_evidence(composers, works)
    assert list(out.columns)[list(out.columns).index("composition_year") + 1 :][
        :4
    ] == list(evidence.EVIDENCE_COLUMNS)
    assert out.loc[0, "imslp_style"] == "Romantic|Early 20th century"
    assert out.loc[0, "imslp_first_published"] == "1910"
    assert out.loc[0, "imslp_copyright_flags"] == "nonpd_eu|nonpd_us"
    assert out.loc[0, "imslp_librettists"] == "Hugo, Victor|Hofmannsthal, Hugo von"
    assert out.loc[3, "imslp_copyright_flags"] == "nonpd_eu"
    assert report["unmapped_copyright_categories"] == {"Some New NonPD Category": 1}
    assert report["coverage"]["imslp_style"]["count"] == 2
    assert report["copyright_token_counts"]["nonpd_eu"] == 2
    assert report["distinct_librettists"] == 2
    assert report["eu_pd_status_x_nonpd_eu"]["pd × nonpd_eu"] == 1
    assert report["pd_composer_nonpd_eu_examples"][0]["title"] == "Unmapped Copy"
    assert [key for ns, key in calls if ns == "imslp_page_cats"] == ["10", "11", "12", "13", "99"]
    assert [key for ns, key in calls if ns == "imslp_page_files"] == ["10", "11", "12", "13", "99"]


def test_remap_fills_file_columns(source):
    composers, works, _ = source
    works = pd.concat(
        [works, pd.DataFrame([{**_work(20, composer_id="Q1", title="No Page"), "imslp_pageid": ""}])],
        ignore_index=True,
    )
    works["imslp_file_hosts"] = "stale"
    out, report = remap.remap_work_evidence(composers, works)
    columns = list(out.columns)
    assert columns[columns.index("has_files") + 1] == "imslp_file_hosts"
    assert columns.count("imslp_file_hosts") == 1
    assert out["has_files"].tolist() == ["true", "", "false", "true", "true", ""]
    # Uncached page 99 keeps its prior cells; a row without a page id has none.
    assert out["imslp_file_hosts"].tolist() == ["ca|us", "", "", "asia", "stale", ""]
    assert report["has_files_counts"] == {"(empty)": 2, "false": 1, "true": 3}
    assert report["file_host_counts"] == {"asia": 1, "ca": 1, "stale": 1, "us": 1}
    assert report["unknown_file_hosts"] == ["stale"]
    assert report["score_files_total"] == 3
    assert report["files_missing_cache"] == 1
    assert report["files_kept_prior"] == 1
    assert report["files_redirect_or_missing_page"] == 1


def test_file_columns_added_when_absent(source):
    composers, works, _ = source
    works = works.drop(columns=["has_files"])
    out, _ = remap.remap_work_evidence(composers, works)
    assert list(out.columns)[-2:] == ["has_files", "imslp_file_hosts"]
    assert out.loc[4, "has_files"] == ""


def test_untouched_columns_round_trip(source):
    composers, works, _ = source
    original = works.copy(deep=True)
    out, _ = remap.remap_work_evidence(composers, works)
    pd.testing.assert_frame_equal(works, original)
    for col in works.columns:
        if col != "has_files":
            assert out[col].tolist() == works[col].tolist()


def test_recomputes_existing_evidence_columns(source):
    composers, works, _ = source
    works = works.copy()
    works["imslp_style"] = "stale"
    works["imslp_first_published"] = "1800"
    works["imslp_copyright_flags"] = "stale"
    works["imslp_librettists"] = "stale"
    out, _ = remap.remap_work_evidence(composers, works)
    assert out.loc[0, "imslp_style"] == "Romantic|Early 20th century"
    assert list(out.columns).count("imslp_style") == 1
    assert list(out.columns)[
        list(out.columns).index("composition_year") + 1
    ] == "imslp_style"


def test_columns_append_when_composition_year_absent(source):
    composers, works, _ = source
    works = works.drop(columns=["composition_year"])
    out, _ = remap.remap_work_evidence(composers, works)
    assert list(out.columns)[-4:] == list(evidence.EVIDENCE_COLUMNS)


@pytest.fixture
def dump_files(tmp_path, monkeypatch, source):
    composers, works, calls = source
    monkeypatch.setattr(common, "DATA_DIR", tmp_path)
    composers.to_csv(tmp_path / "composers_r012.tsv", sep="\t", index=False)
    works.to_csv(tmp_path / "works_r012.tsv", sep="\t", index=False)
    (tmp_path / "dump_meta_r012.json").write_text(
        json.dumps({"created_at_utc": "2026-01-01T00:00:00+00:00", "pd_reference_year": 2026,
                    "enrichment": "pipeline_derive"})
    )
    return tmp_path, composers, works, calls


def args(*, dry_run=False):
    return argparse.Namespace(from_dump="r012", to="r999", dry_run=dry_run)


def test_dry_run_report_content_without_writes(dump_files, capsys):
    directory, _, _, _ = dump_files
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    remap.run(args(dry_run=True))
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == before
    output = capsys.readouterr().out
    assert "Work evidence remap r012 → r999 (offline)" in output
    assert "imslp_style: 2" in output
    assert "nonpd_eu: 2" in output
    assert "Some New NonPD Category: 1" in output
    assert "Distinct librettists: 2" in output
    assert "pd × nonpd_eu: 1" in output
    assert "Unmapped Copy — PD Composer (Q2)" in output
    assert "Missing cache entries: 1" in output
    assert "Redirect / missing pages: 2" in output
    assert "Missing file cache entries: 1 (prior file cells kept: 1)" in output
    assert "  us: 1" in output


def test_cli_round_trip_untouched_columns_and_meta(dump_files):
    directory, composers, works, _ = dump_files
    source_composer_bytes = (directory / "composers_r012.tsv").read_bytes()
    remap.run(args())
    assert (directory / "composers_r012.tsv").read_bytes() == source_composer_bytes

    written_works = pd.read_csv(
        directory / "works_r999.tsv", sep="\t", dtype=str, keep_default_na=False,
    )
    for col in works.columns:
        if col != "has_files":
            assert written_works[col].tolist() == works[col].tolist()
    assert written_works.loc[0, "imslp_style"] == "Romantic|Early 20th century"
    assert written_works.loc[0, "untouched"] == "keep-me"

    written_composers = pd.read_csv(
        directory / "composers_r999.tsv", sep="\t", dtype=str, keep_default_na=False,
    )
    assert written_composers["dump_date"].tolist() == ["r999", "r999"]
    assert written_composers["name_display"].tolist() == composers["name_display"].tolist()
    assert "dump_date" not in written_works.columns

    meta = json.loads((directory / "dump_meta_r999.json").read_text())
    assert meta["derived_from_dump_id"] == "r012"
    assert meta["enrichment"] == "remap_work_evidence"
    assert meta["row_counts"] == {"composers": 2, "works": 5}
    assert meta["coverage"]["imslp_librettists"]["count"] == 1
    assert meta["unmapped_copyright_categories"] == {"Some New NonPD Category": 1}
    assert meta["pd_composer_nonpd_eu_examples"][0]["composer"] == "PD Composer"
    assert meta["pd_reference_year"] == 2026
    assert meta["created_at_utc"] != "2026-01-01T00:00:00+00:00"
    assert meta["work_columns"] == list(written_works.columns)
    assert meta["has_files_counts"] == {"(empty)": 1, "false": 1, "true": 3}


@pytest.mark.parametrize("filename", [
    "composers_r999.tsv", "works_r999.tsv", "dump_meta_r999.json",
])
def test_writer_refuses_overwrite(dump_files, filename):
    directory, _, _, _ = dump_files
    (directory / filename).write_text("sentinel")
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        remap.run(args())
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == before
