"""Headless-Brave check of the form's GitHub saving, against a fake api.github.com injected before the form script.

Usage: .venv/bin/python tests/browser/check_form_github.py [--plant]   (--plant flips one expectation)
Needs Brave and Node (its built-in WebSocket drives Brave over the DevTools protocol; Brave's --dump-dom never returns).
Builds the r020 S01 kit (package 1) in a temp folder. Prints PASS/FAIL per check, never item values; exit 1 on a failure.
"""
import argparse, base64, contextlib, io, json, re, shutil, socket, subprocess, sys, tempfile, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import gold_set as gs  # noqa: E402

KIT = Path(tempfile.mkdtemp(prefix="formcheck-kit-"))
with contextlib.redirect_stdout(io.StringIO()):
    gs.run_kit(argparse.Namespace(dump="r020", gold_dir=None, out_dir=str(KIT), packages="1", annotator="S01"))

PLANT = "--plant" in sys.argv
GH = {"repo": "someone/eu-pd-gold-S09", "branch": "main", "dir": "data/gold/r020", "annotator": "S01",
      "guide": "README.html", "rules": "RULES.html"}
KEY = "github_pat_TEST_0123abc"

MOCK = r"""<script>
(() => {
  const cfg = window.__MOCK = __CFG__;
  const log = [];
  let putN = 0;
  const getN = {};
  window.fetch = async (url, opts = {}) => {
    const method = (opts.method || "GET").toUpperCase();
    const entry = { url: String(url), method, auth: (opts.headers || {}).Authorization || null, keepalive: !!opts.keepalive };
    if (opts.body) entry.body = JSON.parse(opts.body);
    log.push(entry);
    const file = String(url).split("?")[0].split("/").pop();
    if (method === "GET") {
      const st = cfg.getStatus || 200;
      if (st !== 200) return new Response('{"message":"no"}', { status: st, headers: cfg.getHeaders || {} });
      const seq = (cfg.getSeq || {})[file];
      let content;
      if (seq) {
        const i = getN[file] || 0;
        content = seq[Math.min(i, seq.length - 1)];
        getN[file] = i + 1;
      } else content = cfg.sheets[file];
      return new Response(JSON.stringify({ sha: "sha0-" + file, content }), { status: 200 });
    }
    const st = (cfg.putStatuses || [])[putN++] || 200;
    entry.status = st;
    if (cfg.putDelay) await new Promise(r => setTimeout(r, cfg.putDelay));
    if (st !== 200) return new Response('{"message":"no"}', { status: st, headers: cfg.putHeaders || {} });
    return new Response(JSON.stringify({ content: { sha: "sha" + putN + "-" + file } }), { status: 200 });
  };
  if (cfg.click) setTimeout(() => { const b = document.getElementById(cfg.click); if (b) b.click(); else window.__noclick = true; }, 1500);
  if (cfg.click2) setTimeout(() => { const b = document.getElementById(cfg.click2.id); if (b) b.click(); else window.__noclick = true; }, cfg.click2.at);
  setTimeout(() => {
    const pre = document.createElement("pre"); pre.id = "mocklog";
    pre.textContent = JSON.stringify({
      log, conn: document.getElementById("conn").textContent,
      banner: document.getElementById("banner").hidden ? "" : document.getElementById("bannerText").textContent,
      href: location.href, ls: Object.keys(localStorage), noclick: !!window.__noclick,
      dirty: Object.keys(localStorage).filter(k => k.startsWith("goldset.dirty.")).map(k => k.split(".").pop()).sort(),
      notes: document.getElementById("t-notes")?.value ?? null,
      pressed: [...document.querySelectorAll('[aria-pressed="true"]')].map(b => b.id),
      links: [...document.querySelectorAll(".top-links a")].map(a => a.getAttribute("href")),
    }).replace(/</g, "\\u003c");
    document.body.append(pre);
  }, cfg.reportAt || 9000);
})();
</script>
"""


def kit_data() -> dict:
    html = (KIT / "gold_form.html").read_text(encoding="utf-8")
    start = html.index('<script id="gold-data" type="application/json">')
    block = html[start:html.index("</script>", start)]
    return json.loads(block[block.index(">") + 1:])


def b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode()


def page(cfg: dict) -> str:
    template = gs.FORM_TEMPLATE.read_text(encoding="utf-8")
    marker = '<script id="gold-data"'
    assert template.count(marker) == 1
    template = template.replace(marker, MOCK.replace("__CFG__", json.dumps(cfg)) + marker)
    return gs.LOCAL_PAGE.format(page=gs.render_form({**kit_data(), "github": GH}, template))


def write_page(name: str, cfg: dict, root: Path) -> None:
    (root / f"{name}.html").write_text(page(cfg), encoding="utf-8")


def run_all(urls: dict[str, str], work: Path) -> dict:
    """One headless Brave, driven over the DevTools protocol (its --dump-dom never returns here)."""
    profile = work / "profile"
    brave = subprocess.Popen(["brave", "--headless", "--disable-gpu", "--no-first-run", f"--user-data-dir={profile}",
                              "--remote-debugging-port=0", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        port_file = profile / "DevToolsActivePort"
        for _ in range(100):
            if port_file.exists() and len(port_file.read_text().split()) == 2:
                break
            time.sleep(0.2)
        port, path = port_file.read_text().split()
        out = subprocess.run(["node", str(Path(__file__).parent / "cdp_run.mjs"), f"ws://127.0.0.1:{port}{path}",
                              *(f"{n}={u}" for n, u in urls.items())], capture_output=True, text=True, timeout=180)
        if out.returncode:
            raise SystemExit("cdp_run failed: " + out.stderr[-600:])
        return json.loads(out.stdout)
    finally:
        brave.terminate()
        brave.wait(timeout=20)


FAILS = 0


def check(name: str, ok: bool, what: str) -> None:
    global FAILS
    FAILS += not ok
    print(("PASS " if ok else "FAIL ") + name + ": " + what)


def main() -> None:
    sheets = {f: (KIT / "data/gold/r020" / f).read_text(encoding="utf-8") for f in ("composers_sheet.tsv", "works_sheet.tsv")}
    blank = {f: b64(t) for f, t in sheets.items()}
    # Load-back: the repository already holds an answer for the first composer.
    head, first, second, *rest = sheets["composers_sheet.tsv"].split("\n")
    cols = head.split("\t"); row = first.split("\t")
    row[cols.index("wikidata_same_person")] = "no"; row[cols.index("notes")] = "given elsewhere"
    answered = {**blank, "composers_sheet.tsv": b64("\n".join([head, "\t".join(row), second, *rest]))}
    # Same shape, but the answer is on the second composer row (conflict_merge / drafts remote).
    row2 = second.split("\t")
    row2[cols.index("wikidata_same_person")] = "no"; row2[cols.index("notes")] = "given elsewhere"
    answered2 = {**blank, "composers_sheet.tsv": b64("\n".join([head, first, "\t".join(row2), *rest]))}
    item1 = first.split("\t")[cols.index("item")]
    # Quotes: the repository sheet as pandas writes it, a note holding quotes for the first composer.
    import pandas as pd
    frame = pd.read_csv(KIT / "data/gold/r020/composers_sheet.tsv", sep="\t", dtype=str, keep_default_na=False)
    frame.loc[0, ["wikidata_same_person", "notes"]] = ["no", 'says "maybe" here']
    buf = io.StringIO(); frame.to_csv(buf, sep="\t", index=False)
    assert '"says ""maybe"" here"' in buf.getvalue()
    quoted = {**blank, "composers_sheet.tsv": b64(buf.getvalue())}
    scenarios = {
        "quotes": ({"sheets": quoted}, "#key=" + KEY),
        "save": ({"sheets": blank, "click": "o-wikidata_same_person-yes"}, "#key=" + KEY),
        "nokey": ({"sheets": blank, "click": "o-wikidata_same_person-yes"}, ""),
        "badkey": ({"sheets": blank, "getStatus": 401, "click": "o-wikidata_same_person-yes"}, "#key=" + KEY),
        "conflict": ({"sheets": blank, "putStatuses": [409], "click": "o-wikidata_same_person-yes"}, "#key=" + KEY),
        "conflict_merge": ({"sheets": blank, "putStatuses": [409],
                            "getSeq": {"composers_sheet.tsv": [blank["composers_sheet.tsv"], answered2["composers_sheet.tsv"]]},
                            "click": "o-wikidata_same_person-yes"}, "#key=" + KEY),
        "loadback": ({"sheets": answered}, "#key=" + KEY),
        "ratelimit": ({"sheets": blank, "putStatuses": [403], "putHeaders": {"x-ratelimit-remaining": "0"},
                       "click": "o-wikidata_same_person-yes"}, "#key=" + KEY),
        "ratelimit2": ({"sheets": blank, "putStatuses": [403], "putHeaders": {"x-ratelimit-remaining": "0"},
                        "click": "o-wikidata_same_person-yes",
                        "click2": {"id": "o-wikidata_same_person-no", "at": 7000}, "reportAt": 14000}, "#key=" + KEY),
        # A slow PUT that succeeds, a second click during it, then a failing PUT: that row must stay marked.
        "editduringput": ({"sheets": blank, "putStatuses": [200, 500], "putDelay": 3000, "click": "o-wikidata_same_person-yes",
                           "click2": {"id": "o-wikidata_same_person-no", "at": 7000}, "reportAt": 16000}, "#key=" + KEY),
        # A slow PUT: the second click lands while the rate-limited PUT is still in flight.
        "ratelimit3": ({"sheets": blank, "putStatuses": [403], "putHeaders": {"x-ratelimit-remaining": "0"}, "putDelay": 3000,
                        "click": "o-wikidata_same_person-yes",
                        "click2": {"id": "o-wikidata_same_person-no", "at": 7000}, "reportAt": 14000}, "#key=" + KEY),
    }
    work = Path(tempfile.mkdtemp(prefix="formcheck-"))
    root = work / "site"; root.mkdir()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
    server = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1", "--directory", str(root)],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.8)
    try:
        for name, (cfg, _) in scenarios.items():
            write_page(name, cfg, root)
        write_page("drafts1", {"sheets": blank, "click": "o-wikidata_same_person-yes"}, root)
        write_page("drafts2", {"sheets": answered2}, root)
        urls = {name: f"http://127.0.0.1:{port}/{name}.html{h}" for name, (_, h) in scenarios.items()}
        urls["drafts"] = f"http://127.0.0.1:{port}/drafts1.html|http://127.0.0.1:{port}/drafts2.html#key={KEY}"
        r = run_all(urls, work)
    finally:
        server.terminate()
        shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(KIT, ignore_errors=True)
    for name, res in r.items():
        if "error" in res:
            check(name, False, res["error"])
    api = f"https://api.github.com/repos/{GH['repo']}/contents/{GH['dir']}/"

    s = r["save"]
    if "error" not in s:
        puts = [e for e in s["log"] if e["method"] == "PUT"]
        gets = [e for e in s["log"] if e["method"] == "GET"]
        check("save", not s["noclick"], "answer button found and clicked")
        check("save", sorted(e["url"].split("?")[0] for e in gets) == [api + "composers_sheet.tsv", api + "works_sheet.tsv"]
              and all("?ref=main" in e["url"] for e in gets), "loads both sheets from main before the first commit")
        check("save", len(puts) == 1 and puts[0]["url"] == api + "composers_sheet.tsv",
              f"exactly one PUT, to composers_sheet.tsv (got {len(puts)}: {[p['url'].split('/')[-1] for p in puts]})")
        check("save", all(e["auth"] == "Bearer " + KEY for e in s["log"]), "every request carries the key as Bearer")
        if puts:
            body = puts[0]["body"]
            check("save", body.get("sha") == "sha0-composers_sheet.tsv" and body.get("branch") == "main",
                  "PUT sends the loaded sha and branch main")
            check("save", body.get("message", "").startswith("S01: "), "commit message names the annotator")
            text = base64.b64decode(body["content"]).decode("utf-8")
            new, old = text.split("\n"), sheets["composers_sheet.tsv"].split("\n")
            check("save", new[0] == old[0], "TSV header identical to the kit sheet's")
            diff = [i for i, (a, b) in enumerate(zip(new, old)) if a != b]
            check("save", len(new) == len(old) and len(diff) == 1, f"exactly one row changed (changed rows: {len(diff)})")
            if len(diff) == 1:
                cells = dict(zip(new[0].split("\t"), new[diff[0]].split("\t")))
                check("save", cells["wikidata_same_person"] == "yes"
                      and sum(bool(cells[f]) for f in gs.answer_fields("composers", new[0].split("\t"))) == 1,
                      "that row holds the clicked answer and nothing else")
        want = "Saved to GitHub" if not PLANT else "Saved to the moon"
        check("save", s["conn"].startswith(want), f"top bar says '{want} …' (got '{s['conn']}')")
        check("save", "#" not in s["href"] and "goldset.key.r020.S01" in s["ls"], "key moved from the URL into localStorage")
        check("save", s["links"] == ["README.html", "RULES.html"], "header links to the guide and rules")
        check("save", s["banner"] == "", "no banner")
        check("save", s["dirty"] == [], f"no row left marked after the commit (got {len(s['dirty'])})")

    s = r["nokey"]
    if "error" not in s:
        check("nokey", s["log"] == [], "no request to GitHub without a key")
        check("nokey", "Open this page from your personal link" in s["banner"], "banner asks for the personal link")
        check("nokey", s["conn"] == "Saving in this browser", f"top bar: '{s['conn']}'")
        check("nokey", "o-wikidata_same_person-yes" in s["pressed"], "the answer is kept in the browser")
        check("nokey", s["dirty"] == ["answers/" + item1], "the answer is marked to send once the key arrives")

    s = r["badkey"]
    if "error" not in s:
        check("badkey", not [e for e in s["log"] if e["method"] == "PUT"], "no PUT after a 401")
        check("badkey", "key in your link doesn't work" in s["banner"], "banner says the key doesn't work")
        check("badkey", s["conn"] == "Not saving to GitHub", f"top bar: '{s['conn']}'")
        check("badkey", "o-wikidata_same_person-yes" in s["pressed"], "the answer is kept in the browser")

    s = r["conflict"]
    if "error" not in s:
        seq = [(e["method"], e["url"].split("?")[0].split("/")[-1], e.get("status")) for e in s["log"]]
        tail = seq[2:]
        check("conflict", tail == [("PUT", "composers_sheet.tsv", 409), ("GET", "composers_sheet.tsv", None),
                                   ("GET", "works_sheet.tsv", None), ("PUT", "composers_sheet.tsv", 200)],
              f"409 → load both → one retry (got {tail})")
        check("conflict", s["conn"].startswith("Saved to GitHub"), f"top bar: '{s['conn']}'")

    s = r["conflict_merge"]
    if "error" not in s:
        puts = [e for e in s["log"] if e["method"] == "PUT"]
        check("conflict_merge", len(puts) == 2 and puts[0].get("status") == 409 and puts[1].get("status") == 200,
              f"one 409 then one 200 PUT (got {len(puts)})")
        if len(puts) >= 2:
            text = base64.b64decode(puts[1]["body"]["content"]).decode("utf-8")
            lines = text.split("\n")
            r1 = dict(zip(lines[0].split("\t"), lines[1].split("\t")))
            r2 = dict(zip(lines[0].split("\t"), lines[2].split("\t")))
            check("conflict_merge", r1.get("wikidata_same_person") == "yes" and r2.get("wikidata_same_person") == "no",
                  "retried PUT keeps local row 1 and remote row 2")
        check("conflict_merge", s["conn"].startswith("Saved to GitHub"), f"top bar: '{s['conn']}'")

    s = r["loadback"]
    if "error" not in s:
        check("loadback", "o-wikidata_same_person-no" in s["pressed"], "an answer from the repository shows in a new browser")
        check("loadback", not [e for e in s["log"] if e["method"] == "PUT"], "nothing committed when nothing changed")
        check("loadback", s["conn"] == "Connected to GitHub", f"top bar: '{s['conn']}'")

    s = r["quotes"]
    if "error" not in s:
        check("quotes", s["notes"] == 'says "maybe" here', f"a quoted note loads unquoted (got {s['notes']!r})")
        check("quotes", not [e for e in s["log"] if e["method"] == "PUT"], "the form writes it back byte for byte (no PUT)")

    s = r["ratelimit"]
    if "error" not in s:
        check("ratelimit", s["banner"] == "", "a rate limit shows no key banner")
        check("ratelimit", s["conn"] == "Not saved to GitHub yet, retrying", f"top bar: '{s['conn']}'")

    s = r["ratelimit2"]
    if "error" not in s:
        puts = [e for e in s["log"] if e["method"] == "PUT"]
        check("ratelimit2", len(puts) == 1, f"exactly 1 PUT (backoff not cut short; got {len(puts)})")
        check("ratelimit2", s["dirty"] == ["answers/" + item1], "the row stays marked after the failed push")

    s = r["ratelimit3"]
    if "error" not in s:
        puts = [e for e in s["log"] if e["method"] == "PUT"]
        check("ratelimit3", len(puts) == 1, f"an edit during a failing PUT doesn't cut the backoff short (got {len(puts)} PUTs)")
        check("ratelimit3", s["dirty"] == ["answers/" + item1], "the row stays marked after the failed push")

    s = r["editduringput"]
    if "error" not in s:
        st = [e.get("status") for e in s["log"] if e["method"] == "PUT"]
        check("editduringput", st == [200, 500], f"one PUT succeeds, the next fails (got {st})")
        check("editduringput", s["dirty"] == ["answers/" + item1], "an edit made during the successful PUT stays marked")

    s = r["drafts"]
    if "error" not in s:
        puts = [e for e in s["log"] if e["method"] == "PUT"]
        check("drafts", len(puts) == 1 and puts[0]["url"] == api + "composers_sheet.tsv",
              f"exactly one PUT to composers_sheet.tsv (got {len(puts)})")
        if puts:
            body = puts[0]["body"]
            check("drafts", item1 in body.get("message", ""), "commit message names row 1's item")
            text = base64.b64decode(body["content"]).decode("utf-8")
            lines = text.split("\n")
            r1 = dict(zip(lines[0].split("\t"), lines[1].split("\t")))
            r2 = dict(zip(lines[0].split("\t"), lines[2].split("\t")))
            check("drafts", r1.get("wikidata_same_person") == "yes" and r2.get("wikidata_same_person") == "no",
                  "PUT holds draft row 1 and remote row 2")
        check("drafts", s["dirty"] == [], f"no row left marked after the commit (got {len(s['dirty'])})")

    print(f"{FAILS} failure(s)")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
