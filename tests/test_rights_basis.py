"""Rights-basis report: basis precedence, death bands and host groups (offline)."""

import pandas as pd

import rights_basis as rb


def test_basis_first_match_wins_and_us_warning_is_not_a_basis():
    assert rb.rights_basis("nonpd_eu|nonpd_licensed|wima") == "eu_warning"
    assert rb.rights_basis("pd_us_notrenewed|pd_us_only") == "us_only"
    assert rb.rights_basis("pro_licensed|wima") == "licensed"
    assert rb.rights_basis("pd_us_no_notice") == "us_routes"
    assert rb.rights_basis("nonpd_us") == "none"
    assert rb.rights_basis("") == "none"


def test_death_bands_follow_reference_year_and_canada_cutoff():
    assert [rb.death_band(y, 2026) for y in ("1955", "1956", "1971", "1972", "1999", "2000", "")] == [
        "≤ 1955", "1956–1971", "1956–1971", "1972–1999", "1972–1999", "2000+", "unknown"]
    assert rb.band_order(2027) == ["≤ 1956", "1957–1971", "1972–1999", "2000+", "unknown"]


def test_host_groups():
    assert rb.host_group("true", "asia|ca|us") == "asia (life+50 server)"
    assert rb.host_group("true", "ca|us") == "us (US-only server)"
    assert rb.host_group("true", "ca") == "ca (main server only)"
    assert rb.host_group("false", "") == "no files"
    assert rb.host_group("", "") == "unknown"


def test_analyse_counts_unflagged_not_yet_pd_works():
    composers = pd.DataFrame({"composer_id": ["A", "B", "C"], "death_year": ["1940", "1965", "1973"]})
    works = pd.DataFrame({
        "composer_id": ["A", "A", "B", "B", "C"],
        "imslp_copyright_flags": ["", "nonpd_eu", "nonpd_eu", "", ""],
        "has_files": ["true", "true", "true", "true", "true"],
        "imslp_file_hosts": ["ca", "ca", "ca", "ca", "asia"],
    })
    report = rb.analyse(composers, works, 2026)
    assert report["basis_by_band"]["≤ 1955"]["eu_warning"] == 1
    assert report["eu_warning_share"]["1956–1971"] == {"warned": 1, "works": 2}
    assert report["unflagged_not_pd"] == 2
    assert report["unflagged_by_band_host"] == {"1956–1971 · ca (main server only)": 1,
                                                 "1972–1999 · asia (life+50 server)": 1}
    assert "| EU warning (PD in Canada) | 1 | 1 | 0 | 0 | 0 |" in rb.to_markdown(report, "r999")
