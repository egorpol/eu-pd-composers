const DATA_BASE = new URL("./data/", import.meta.url);

const state = {
  manifest: null,
  composers: [],
  worksByComposer: {},
  selectedId: null,
  detailForce: "",
};

const el = {
  disclaimer: document.getElementById("disclaimer"),
  meta: document.getElementById("meta"),
  dumpLabel: document.getElementById("dumpLabel"),
  rows: document.getElementById("rows"),
  detail: document.getElementById("detail"),
  q: document.getElementById("q"),
  eu: document.getElementById("eu"),
  scope: document.getElementById("scope"),
  force: document.getElementById("force"),
  cit: document.getElementById("cit"),
  style: document.getElementById("style"),
  sort: document.getElementById("sort"),
  hasWorks: document.getElementById("hasWorks"),
};

function selectedValues(select) {
  return [...select.selectedOptions].map((o) => o.value);
}

function fillSelect(select, values, { selected = [] } = {}) {
  select.innerHTML = "";
  for (const v of values) {
    const opt = document.createElement("option");
    opt.value = v;
    opt.textContent = v;
    if (selected.includes(v)) opt.selected = true;
    select.appendChild(opt);
  }
}

function matchesQuery(c, q) {
  if (!q) return true;
  const hay = [c.name, c.sort, ...(c.aliases || [])].join(" ").toLowerCase();
  return hay.includes(q);
}

function filteredComposers() {
  const q = el.q.value.trim().toLowerCase();
  const eu = new Set(selectedValues(el.eu));
  const scope = new Set(selectedValues(el.scope));
  const force = new Set(selectedValues(el.force));
  const cit = new Set(selectedValues(el.cit));
  const style = new Set(selectedValues(el.style));
  const onlyWorks = el.hasWorks.checked;

  let rows = state.composers.filter((c) => {
    if (!matchesQuery(c, q)) return false;
    if (eu.size && !eu.has(c.eu)) return false;
    if (scope.size && !scope.has(c.scope)) return false;
    if (onlyWorks && !(c.works_n > 0)) return false;
    if (force.size && !c.forces.some((f) => force.has(f))) return false;
    if (cit.size && !c.cit.some((x) => cit.has(x))) return false;
    if (style.size && !c.styles.some((s) => style.has(s))) return false;
    return true;
  });

  const sort = el.sort.value;
  rows.sort((a, b) => {
    if (sort === "name") return (a.sort || a.name).localeCompare(b.sort || b.name);
    if (sort === "eu_year") {
      const ay = a.eu_year === "" || a.eu_year == null ? 99999 : Number(a.eu_year);
      const by = b.eu_year === "" || b.eu_year == null ? 99999 : Number(b.eu_year);
      return ay - by;
    }
    if (sort === "works") return (b.works_n || 0) - (a.works_n || 0);
    return (Number(b.views) || 0) - (Number(a.views) || 0);
  });
  return rows;
}

function renderRows() {
  const rows = filteredComposers();
  el.meta.textContent = `${rows.length.toLocaleString()} composers shown · dump ${state.manifest.dump_id}`;
  el.rows.innerHTML = "";
  const frag = document.createDocumentFragment();
  for (const c of rows) {
    const tr = document.createElement("tr");
    tr.dataset.id = c.id;
    if (c.id === state.selectedId) tr.classList.add("active");
    const years = [c.birth, c.death].filter((x) => x !== "" && x != null).join("–");
    tr.innerHTML = `
      <td>
        <span class="name-cell">${escapeHtml(c.name)}</span>
        <span class="sub">${escapeHtml(years)}${c.scope && c.scope !== "classical_core" ? " · " + escapeHtml(c.scope) : ""}</span>
      </td>
      <td><span class="badge ${escapeHtml(c.eu || "")}">${escapeHtml(c.eu || "—")}</span></td>
      <td>${c.works_n || 0}</td>
      <td>${formatViews(c.views)}</td>`;
    tr.addEventListener("click", () => selectComposer(c.id));
    frag.appendChild(tr);
  }
  el.rows.appendChild(frag);
}

function formatViews(v) {
  const n = Number(v) || 0;
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(0) + "k";
  return String(n);
}

function escapeHtml(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function selectComposer(id) {
  state.selectedId = id;
  state.detailForce = "";
  renderRows();
  renderDetail();
}

function renderDetail() {
  const c = state.composers.find((x) => x.id === state.selectedId);
  if (!c) {
    el.detail.innerHTML = `<p class="detail-empty">Select a composer to see IMSLP works.</p>`;
    return;
  }
  const works = state.worksByComposer[c.id] || [];
  const forces = [...new Set(works.map((w) => w.f).filter(Boolean))].sort();
  const force = state.detailForce;
  const shown = force ? works.filter((w) => w.f === force) : works;

  const links = [];
  if (c.wiki) links.push(`<a href="${escapeHtml(c.wiki)}" target="_blank" rel="noopener">Wikipedia</a>`);
  if (c.wikidata) links.push(`<a href="${escapeHtml(c.wikidata)}" target="_blank" rel="noopener">Wikidata</a>`);
  if (c.imslp) links.push(`<a href="${escapeHtml(c.imslp)}" target="_blank" rel="noopener">IMSLP</a>`);

  const years = [c.birth, c.death].filter((x) => x !== "" && x != null).join(" – ");
  const euBit =
    c.eu_year !== "" && c.eu_year != null
      ? ` · heuristic EU PD year ${escapeHtml(c.eu_year)}`
      : "";

  el.detail.innerHTML = `
    <h2>${escapeHtml(c.name)}</h2>
    <p class="years">${escapeHtml(years)} · <span class="badge ${escapeHtml(c.eu)}">${escapeHtml(c.eu)}</span>${euBit}</p>
    <div class="detail-links">${links.join("") || "<span>No external links</span>"}</div>
    <p class="force-filter">
      Works (${shown.length}/${works.length})
      <select id="detailForce">
        <option value="">all forces</option>
        ${forces.map((f) => `<option value="${escapeHtml(f)}" ${f === force ? "selected" : ""}>${escapeHtml(f)}</option>`).join("")}
      </select>
    </p>
    <ul class="works-list">
      ${
        shown.length
          ? shown
              .map(
                (w) => `<li>
            <a href="${escapeHtml(w.u)}" target="_blank" rel="noopener">${escapeHtml(w.t || "Work")}</a>
            <span class="src">${escapeHtml(w.f)}${w.s ? " · " + escapeHtml(w.s) : ""}</span>
          </li>`
              )
              .join("")
          : "<li>No IMSLP works in this dump for the current filter.</li>"
      }
    </ul>`;

  const sel = document.getElementById("detailForce");
  if (sel) {
    sel.addEventListener("change", () => {
      state.detailForce = sel.value;
      renderDetail();
    });
  }
}

async function load() {
  el.meta.textContent = "Loading dump…";
  const [manifest, composers, worksByComposer] = await Promise.all([
    fetch(new URL("manifest.json", DATA_BASE)).then((r) => r.json()),
    fetch(new URL("composers.json", DATA_BASE)).then((r) => r.json()),
    fetch(new URL("works_by_composer.json", DATA_BASE)).then((r) => r.json()),
  ]);
  state.manifest = manifest;
  state.composers = composers;
  state.worksByComposer = worksByComposer;

  el.disclaimer.textContent = manifest.disclaimer;
  el.dumpLabel.textContent = `dump ${manifest.dump_id}`;

  const facets = manifest.facets || {};
  fillSelect(el.eu, facets.eu_pd_status || [], { selected: ["pd"] });
  // Default: classical_core only (hide film/popular skew)
  const scopes = facets.scope_class || [];
  fillSelect(el.scope, scopes, {
    selected: scopes.includes("classical_core") ? ["classical_core"] : scopes,
  });
  fillSelect(el.force, facets.force_family || []);
  fillSelect(el.cit, facets.citizenship_iso || []);
  fillSelect(el.style, facets.style_tags || []);

  for (const node of [el.q, el.eu, el.scope, el.force, el.cit, el.style, el.sort, el.hasWorks]) {
    node.addEventListener("input", renderRows);
    node.addEventListener("change", renderRows);
  }

  renderRows();
}

load().catch((err) => {
  console.error(err);
  el.meta.textContent = "Failed to load viewer data. Serve this folder over HTTP (GitHub Pages or a local static server).";
});
