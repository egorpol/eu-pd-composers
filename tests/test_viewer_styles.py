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
        import {{ composerStylesBySource, composerMatchesStyle, buildStyleVocab, styleDetailLine, llmDetailHtml }} from {json.dumps(app.as_uri())};
        const policy = {json.dumps(policy_metadata())};
        const c = {{styles: ['serialism'], style_src: 'wikidata', ls: ['impressionism'], lp: 'Early 20th century',
                    lv: [['impressionism', 'neoclassicism'], ['impressionism'], ['neoclassicism', 'modern']],
                    lpv: ['Early 20th century', 'Early 20th century', '']}};
        const by = composerStylesBySource(c, [{{st: ['Romantic']}}]);
        assert.deepEqual(by, {{wikidata: ['serialism'], llm: ['impressionism'], imslp: ['Romantic']}});
        assert.equal(composerMatchesStyle(by, new Set(['neoclassicism']), new Set(['llm'])), false);
        assert.equal(composerMatchesStyle(by, new Set(['impressionism']), new Set(['llm'])), true);
        assert.equal(styleDetailLine({{...c, _stylesBySrc: by}}), 'Style — Wikidata: Serialism · IMSLP works: Romantic');
        assert.equal(styleDetailLine({{styles: [], style_src: '', ls: ['modern']}}), '');
        const html = llmDetailHtml(c, 'consensus', policy).replace(/\\s+/g, ' ');
        assert.match(html, /LLM style tags \\(GPT-6\\.1 Sol and Grok 4\\.7 agree\\): <strong>Impressionism<\\/strong> · period: Early 20th century/);
        assert.match(html, /GPT-6\\.1 Sol<\\/span> Impressionism, Neoclassicism · period: Early 20th century/);
        assert.match(html, /Claude Haiku 5\\.5<\\/span> Neoclassicism, Modern · period: unknown/);
        assert.match(llmDetailHtml(c, '2of3', policy), /At least 2 of 3 models agree\\): <strong>Impressionism, Neoclassicism<\\/strong><\\/p>/);
        const sparse = llmDetailHtml({{lv: [null, [], null]}}, 'all', policy);
        assert.match(sparse, /<strong>none<\\/strong>/);
        assert.match(sparse, /GPT-6\\.1 Sol<\\/span> not labelled/);
        assert.match(sparse, /Grok 4\\.7<\\/span> no style tags<\\/li>/);
        assert.equal(llmDetailHtml({{styles: []}}, 'consensus', policy), '');
        assert.deepEqual(composerStylesBySource({{styles: ['modern'], style_src: 'llm_luna_xhigh'}}, []).llm, []);
        const aligned = composerStylesBySource({{ls: ['Romantic']}}, [{{st: ['Romantic']}}]);
        assert.deepEqual(buildStyleVocab([...aligned.llm, ...aligned.imslp]), ['Romantic']);
        assert.equal(composerMatchesStyle(aligned, new Set(['Romantic']), new Set(['llm'])), true);
        assert.equal(composerMatchesStyle(aligned, new Set(['Romantic']), new Set(['imslp'])), true);
    """
    subprocess.run(["node", "--input-type=module", "-e", code], check=True, capture_output=True, text=True)


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is needed for viewer tests")
def test_llm_rules_tags_and_url_state(tmp_path):
    app = tmp_path / "app.mjs"
    app.write_text((Path(__file__).resolve().parents[1] / "viewer/app.js").read_text())
    code = f"""
        import assert from 'node:assert/strict';
        import {{ llmRules, llmTags, composerStylesBySource, encodeUrlState, decodeUrlState }} from {json.dumps(app.as_uri())};
        const policy = {json.dumps(policy_metadata())};
        assert.deepEqual(llmRules(policy).map((r) => r.value),
                         ['consensus', '2of3', 'all', 'any', 'sol', 'grok', 'haiku']);
        assert.deepEqual(llmRules(policy).map((r) => r.label),
                         ['Sol + Grok agree', '2 of 3 agree', 'All 3 agree', 'Any model', 'Sol only', 'Grok only', 'Haiku only']);
        assert.equal(llmRules(policy).at(-1).title, 'Claude Haiku 5.5 only');
        const old = {{labellers: [{{key: 'x', name: 'X'}}, {{key: 'y', name: 'Y'}}]}};
        assert.deepEqual(llmRules(old).map((r) => r.value), ['consensus', 'any', 'm1', 'm2']);
        assert.equal(llmRules(old)[0].label, 'X + Y agree');
        assert.equal(llmRules(old)[0].title, 'X and Y agree');
        const c = {{ls: ['impressionism'], lv: [['impressionism', 'neoclassicism'], ['impressionism'], ['neoclassicism', 'modern']]}};
        const tags = (rule, x = c) => llmTags(x, rule, policy);
        assert.deepEqual(tags('consensus'), ['impressionism']);
        assert.deepEqual(tags('2of3'), ['impressionism', 'neoclassicism']);
        assert.deepEqual(tags('all'), []);
        assert.deepEqual(tags('any'), ['impressionism', 'neoclassicism', 'modern']);
        assert.deepEqual(tags('haiku'), ['neoclassicism', 'modern']);
        assert.deepEqual(tags('bogus'), []);
        assert.deepEqual(tags('all', {{lv: [['a'], ['a', 'a'], ['a']]}}), ['a']);
        assert.deepEqual(tags('all', {{lv: [['a'], ['a'], null]}}), []);
        assert.deepEqual(tags('2of3', {{lv: [['a'], ['a'], null]}}), ['a']);
        assert.deepEqual(tags('grok', {{lv: [['a']]}}), []);
        assert.deepEqual(composerStylesBySource(c, [], 'haiku', policy).llm, ['neoclassicism', 'modern']);
        assert.match(encodeUrlState({{llmRule: 'haiku'}}), /[?&]llm=haiku(&|$)/);
        assert.doesNotMatch(encodeUrlState({{llmRule: 'consensus'}}), /llm=/);
        const allowed = {{llmRules: new Set(llmRules(policy).map((r) => r.value))}};
        assert.equal(decodeUrlState('?v=1&llm=haiku', allowed).llmRule, 'haiku');
        assert.equal(decodeUrlState('?v=1&llm=bogus', allowed).llmRule, 'consensus');
        assert.equal(decodeUrlState('?v=1', allowed).llmRule, 'consensus');
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
