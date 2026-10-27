// node cdp_run.mjs WS_URL NAME=URL...  → prints {name: report} once every page has written #mocklog (or timed out).
const [wsUrl, ...pairs] = process.argv.slice(2);
const ws = new WebSocket(wsUrl);
let nextId = 0;
const waiting = new Map();
ws.onmessage = ev => {
  const msg = JSON.parse(ev.data);
  if (msg.id && waiting.has(msg.id)) { waiting.get(msg.id)(msg); waiting.delete(msg.id); }
};
const send = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
  const id = ++nextId;
  waiting.set(id, m => m.error ? reject(new Error(method + ": " + m.error.message)) : resolve(m.result));
  ws.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
});
await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
async function one(name, url) {
  // A fresh browser context per scenario: its own localStorage, like a new browser.
  const { browserContextId } = await send("Target.createBrowserContext");
  const { targetId } = await send("Target.createTarget", { url, browserContextId });
  const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
  const until = Date.now() + 40000;
  while (Date.now() < until) {
    const r = await send("Runtime.evaluate", { expression: "document.getElementById('mocklog')?.textContent ?? null", returnByValue: true }, sessionId);
    if (r.result?.value) return [name, JSON.parse(r.result.value)];
    await new Promise(r => setTimeout(r, 500));
  }
  return [name, { error: "timed out waiting for #mocklog" }];
}
const results = await Promise.all(pairs.map(p => { const i = p.indexOf("="); return one(p.slice(0, i), p.slice(i + 1)); }));
console.log(JSON.stringify(Object.fromEntries(results)));
ws.close();
process.exit(0);
