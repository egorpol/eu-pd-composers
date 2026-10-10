"""Viewer source matching and compact style detail text, without a browser."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest

from apply_llm_styles import policy_metadata


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is needed for viewer tests")
def test_consensus_facet_and_detail_line(tmp_path):
    app = tmp_path / "app.mjs"
    app.write_text((Path(__file__).resolve().parents[1] / "viewer/app.js").read_text())
    code = f"""
        import assert from 'node:assert/strict';
        import {{ composerStylesBySource, composerMatchesStyle, buildStyleVocab, styleDetailLine }} from {json.dumps(app.as_uri())};
        const policy = {json.dumps(policy_metadata())};
        const c = {{styles: ['serialism'], style_src: 'wikidata', ls: ['impressionism'],
                    lp: 'Early 20th century', lv: [['impressionism', 'neoclassicism'], ['impressionism']]}};
        const by = composerStylesBySource(c, [{{st: ['Romantic']}}]);
        assert.deepEqual(by, {{wikidata: ['serialism'], llm: ['impressionism'], imslp: ['Romantic']}});
        assert.equal(composerMatchesStyle(by, new Set(['neoclassicism']), new Set(['llm'])), false);
        assert.equal(composerMatchesStyle(by, new Set(['impressionism']), new Set(['llm'])), true);
        assert.equal(styleDetailLine(c, policy), 'Style — Wikidata: Serialism · LLM: Impressionism (GPT-6.1 Sol: Impressionism, Neoclassicism · Grok 4.7: Impressionism); period: Early 20th century');
        assert.equal(styleDetailLine({{lv: [['impressionism'], []]}}, policy), 'Style — LLM: no consensus (GPT-6.1 Sol: Impressionism · Grok 4.7: —)');
        assert.equal(styleDetailLine({{lv: [null, []]}}, policy), 'Style — LLM: incomplete labelling (Grok 4.7: —)');
        assert.equal(styleDetailLine({{styles: [], style_src: ''}}, policy), '');
        assert.deepEqual(composerStylesBySource({{styles: ['modern'], style_src: 'llm_luna_xhigh'}}, []).llm, []);
        const aligned = composerStylesBySource({{ls: ['Romantic']}}, [{{st: ['Romantic']}}]);
        assert.deepEqual(buildStyleVocab([...aligned.llm, ...aligned.imslp]), ['Romantic']);
        assert.equal(composerMatchesStyle(aligned, new Set(['Romantic']), new Set(['llm'])), true);
        assert.equal(composerMatchesStyle(aligned, new Set(['Romantic']), new Set(['imslp'])), true);
    """
    subprocess.run(["node", "--input-type=module", "-e", code], check=True, capture_output=True, text=True)


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is needed for viewer tests")
def test_work_evidence_badges(tmp_path):
    app = tmp_path / "app.mjs"
    app.write_text((Path(__file__).resolve().parents[1] / "viewer/app.js").read_text())
    code = f"""
        import assert from 'node:assert/strict';
        import {{ workEvidenceHtml }} from {json.dumps(app.as_uri())};
        const labels = (w) => [...workEvidenceHtml(w).matchAll(/>([^<]+)<\\/span>/g)].map((m) => m[1]);
        assert.deepEqual(labels({{}}), []);
        assert.deepEqual(labels({{cf: ['pd_us_notrenewed', 'pd_us_only', 'nonpd_eu']}}),
                         ['IMSLP: not PD in EU', 'IMSLP: PD in US only']);
        assert.deepEqual(labels({{cf: ['permission_granted', 'wima', 'pd_eu_rost']}}),
                         ['IMSLP: in copyright, hosted by permission', 'IMSLP: WIMA files']);
        assert.deepEqual(labels({{hf: false}}), ['IMSLP: no files']);
        assert.deepEqual(labels({{fh: ['asia', 'ca', 'us', 'eu'], fp: 1950}}),
                         ['Files: life+50 server', 'Files: US server', 'pub. 1950']);
        assert.match(workEvidenceHtml({{cf: ['wima']}}), /class="ev-badge info"/);
        assert.match(workEvidenceHtml({{cf: ['pro_licensed']}}), /class="ev-badge" title="Hosted under/);
    """
    subprocess.run(["node", "--input-type=module", "-e", code], check=True, capture_output=True, text=True)
