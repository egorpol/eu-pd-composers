"""Viewer random pick: pool, draw and the shareable `work=` URL value, without a browser."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is needed for viewer tests")
def test_pick_pool_draw_and_url(tmp_path):
    app = tmp_path / "app.mjs"
    app.write_text((Path(__file__).resolve().parents[1] / "viewer/app.js").read_text())
    code = f"""
        import assert from 'node:assert/strict';
        import {{ pickPool, pickRandom, workKey, encodeUrlState, decodeUrlState }} from {json.dumps(app.as_uri())};
        const cs = [{{id: 'a', views: 900}}, {{id: 'b', views: 10}}, {{id: 'c', views: null}},
                    {{id: 'd', views: 500}}, {{id: 'e', views: 20}}];

        // Anyone keeps every match; lesser-known keeps the less-viewed half, rounded up, missing views as 0.
        assert.equal(pickPool(cs, 'any'), cs);
        assert.deepEqual(pickPool(cs, 'lesser').map((c) => c.id), ['c', 'b', 'e']);
        assert.deepEqual(pickPool([cs[0]], 'lesser').map((c) => c.id), ['a']);
        assert.deepEqual(pickPool([], 'lesser'), []);

        // The draw skips ids already seen until every one was picked, then starts over.
        assert.equal(pickRandom([], new Set()), null);
        assert.equal(pickRandom(cs, null, () => 0.99).id, 'e');
        const seen = new Set(['a', 'b', 'c', 'd']);
        assert.equal(pickRandom(cs, seen, () => 0).id, 'e');
        seen.add('e');
        assert.equal(pickRandom(cs, seen, () => 0).id, 'a');
        assert.equal(seen.size, 0);

        // The work key is the IMSLP page name; it travels in the URL only with a composer id.
        const w = {{u: 'https://imslp.org/wiki/Sonata%2C_Op.1_%28X%2C_Y%29'}};
        assert.equal(workKey(w), 'Sonata%2C_Op.1_%28X%2C_Y%29');
        const qs = encodeUrlState({{id: 'Q1', work: workKey(w)}});
        const back = decodeUrlState(qs, {{ids: new Set(['Q1'])}});
        assert.equal(back.id, 'Q1');
        assert.equal(back.work, workKey(w));
        assert.doesNotMatch(encodeUrlState({{work: 'x'}}), /work=/);
        assert.equal(decodeUrlState('?v=1&work=x', {{ids: new Set()}}).work, undefined);
    """
    subprocess.run(["node", "--input-type=module", "-e", code], check=True, capture_output=True, text=True)
