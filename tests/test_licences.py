import pytest

import licences


@pytest.mark.parametrize(
    "url, expected",
    [
        ("http://creativecommons.org/licenses/by-nc/3.0/", "CC-BY-NC-3.0"),
        ("https://creativecommons.org/licenses/by-nc-sa/4.0/deed.en", "CC-BY-NC-SA-4.0"),
        ("https://creativecommons.org/licenses/by-sa/3.0/de/", "CC-BY-SA-3.0-DE"),
        ("https://creativecommons.org/licenses/by-nd-nc/1.0/", "CC-BY-NC-ND-1.0"),
        ("https://creativecommons.org/licenses/by/", "CC-BY"),
        ("https://creativecommons.org/publicdomain/zero/1.0/", "CC0-1.0"),
        ("https://creativecommons.org/publicdomain/mark/1.0/", "CC-PDM-1.0"),
        ("http://i.creativecommons.org/l/by-nc/3.0/88x31.png", "CC-BY-NC-3.0"),
        ("https://licensebuttons.net/l/by-sa/3.0/de/88x31.png", "CC-BY-SA-3.0-DE"),
        ("https://i.creativecommons.org/p/zero/1.0/88x31.png", "CC0-1.0"),
        ("https://creativecommons.org/about/", None),
        ("https://example.org/licenses/by/4.0/", None),
    ],
)
def test_spdx_from_cc_url(url, expected):
    assert licences.spdx_from_cc_url(url) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Creative Commons Attribution-NonCommercial-NoDerivs 4.0", "CC-BY-NC-ND-4.0"),
        ("Creative Commons Attribution Non-commercial Share Alike 3.0", "CC-BY-NC-SA-3.0"),
        ("Creative Commons Attribution 4.0", "CC-BY-4.0"),
        ("Creative Commons Attribution-ShareAlike 4.0", "CC-BY-SA-4.0"),
        ("Creative Commons Zero 1.0", "CC0-1.0"),
        ("Released under CC BY-NC 3.0.", "CC-BY-NC-3.0"),
        ("cc-by-sa", "CC-BY-SA"),
        ("Public Domain", None),
        ("Performance Restricted Attribution-NonCommercial-NoDerivs 3.0", None),
        ("CC Ensemble concert", None),
    ],
)
def test_spdx_from_cc_text(text, expected):
    assert licences.spdx_from_cc_text(text) == expected


def test_is_cc_family():
    assert licences.is_cc_family("CC0-1.0")
    assert licences.is_cc_family("CC-BY-4.0")
    assert not licences.is_cc_family("LicenseRef-IMSLP-PR-BY-NC-ND-3.0")
    assert not licences.is_cc_family("")
