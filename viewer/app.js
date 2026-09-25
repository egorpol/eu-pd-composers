const DATA_BASE = new URL("./data/", import.meta.url);

// Empty selection on a facet means "no filter" on that axis.
const FACETS = [
  { key: "eu", manifest: "eu_pd_status", field: "eu", label: "EU PD status" },
  { key: "scope", manifest: "scope_class", field: "scope", label: "Scope" },
  { key: "force", manifest: "force_family", field: "forces", label: "Has works for", list: true },
  { key: "cit", manifest: "citizenship_iso", field: "cit", label: "Citizenship", list: true },
  { key: "style", manifest: "style_tags", field: "styles", label: "Style", list: true },
];

const DEFAULTS = { eu: ["pd"], scope: ["classical_core"], force: [], cit: [], style: [] };

const state = {
  manifest: null,
  composers: [],
  worksByComposer: {},
  selectedKey: null,
  detailForce: "",
};

const el = {
  controls: document.querySelector(".controls"),
  disclaimer: document.getElementById("disclaimer"),
  meta: document.getElementById("meta"),
  announce: document.getElementById("announce"),
  dumpLabel: document.getElementById("dumpLabel"),
  rows: document.getElementById("rows"),
  detail: document.getElementById("detail"),
  q: document.getElementById("q"),
  sort: document.getElementById("sort"),
  sortDir: document.getElementById("sortDir"),
  hasWorks: document.getElementById("hasWorks"),
  clearAll: document.getElementById("clearAll"),
  resetDefaults: document.getElementById("resetDefaults"),
};
for (const f of FACETS) el[f.key] = document.getElementById(f.key);

function selectedValues(container) {
  return [...container.querySelectorAll("input:checked")].map((i) => i.value);
}

function fillFacet(facet, values, selected) {
  const box = el[facet.key];
  box.innerHTML = values
    .map(
      (v) => `<label class="opt"><input type="checkbox" name="${facet.key}" value="${escapeHtml(v)}"${
        selected.includes(v) ? " checked" : ""
      } /><span>${escapeHtml(v)}</span></label>`
    )
    .join("");
  updateFacetButton(facet);
}

function setFacet(facet, values) {
  for (const i of el[facet.key].querySelectorAll("input")) i.checked = values.includes(i.value);
  updateFacetButton(facet);
}

function updateFacetButton(facet) {
  const btn = el[facet.key].closest(".facet").querySelector(".facet-clear");
  const n = selectedValues(el[facet.key]).length;
  btn.disabled = n === 0;
  btn.textContent = n ? `Clear (${n})` : "Any";
  btn.setAttribute("aria-label", n ? `Clear ${facet.label} filter, ${n} selected` : `${facet.label}: any`);
}

function matchesQuery(c, q) {
  if (!q) return true;
  return c._hay.includes(q);
}

function lifeSpan(c, sep) {
  const b = c.birth !== "" && c.birth != null ? String(c.birth) : "";
  const d = c.death !== "" && c.death != null ? String(c.death) : "";
  if (b && d) return `${b}${sep}${d}`;
  if (b) return c.eu === "living" ? `b. ${b}` : `b. ${b}, death unknown`;
  if (d) return `d. ${d}`;
  return "";
}

function filteredComposers() {
  const q = el.q.value.trim().toLowerCase();
  const sel = {};
  for (const f of FACETS) sel[f.key] = new Set(selectedValues(el[f.key]));
  const onlyWorks = el.hasWorks.checked;

  const rows = state.composers.filter((c) => {
    if (!matchesQuery(c, q)) return false;
    if (onlyWorks && !(c.works_n > 0)) return false;
    for (const f of FACETS) {
      const s = sel[f.key];
      if (!s.size) continue;
      const v = c[f.field];
      if (f.list ? !v.some((x) => s.has(x)) : !s.has(v)) return false;
    }
    return true;
  });

  const sort = el.sort.value;
  const asc = el.sortDir.value === "asc";
  const byName = (a, b) => (a.sort || a.name).localeCompare(b.sort || b.name);
  rows.sort((a, b) => {
    let d = 0;
    if (sort === "name") d = byName(a, b);
    else if (sort === "eu_year") {
      const ay = a.eu_year === "" || a.eu_year == null ? 99999 : Number(a.eu_year);
      const by = b.eu_year === "" || b.eu_year == null ? 99999 : Number(b.eu_year);
      d = ay - by;
    } else if (sort === "works") d = (a.works_n || 0) - (b.works_n || 0);
    else d = (Number(a.views) || 0) - (Number(b.views) || 0);
    if (!asc) d = -d;
    return d || byName(a, b);
  });
  return rows;
}

function renderRows() {
  const rows = filteredComposers();
  el.meta.textContent = `${rows.length.toLocaleString()} of ${state.composers.length.toLocaleString()} composers shown · ${state.manifest.dump_label || "dump " + state.manifest.dump_id}`;

  // Keep the detail pane aligned with the current filter set.
  if (state.selectedKey != null) {
    const stillVisible = rows.some((c) => c._k === state.selectedKey);
    if (!stillVisible) {
      state.selectedKey = null;
      state.detailForce = "";
      el.announce.textContent = "";
      renderDetail();
    } else {
      syncDetailForceFromFacet();
      renderDetail();
    }
  }

  if (!rows.length) {
    el.rows.innerHTML = `<tr class="empty"><td colspan="4">No composers match these filters. Try “Any” on a facet, or clear the search.</td></tr>`;
    return;
  }
  el.rows.innerHTML = rows
    .map((c) => {
      const active = c._k === state.selectedKey;
      const sub = lifeSpan(c, "–") + (c.scope && c.scope !== "classical_core" ? " · " + c.scope : "");
      return `<tr data-k="${c._k}"${active ? ' class="active"' : ""}>
      <td>
        <button type="button" class="row-btn" aria-pressed="${active}">${escapeHtml(c.name)}</button>
        <span class="sub">${escapeHtml(sub)}</span>
      </td>
      <td><span class="badge ${escapeHtml(c.eu || "")}">${escapeHtml(c.eu || "—")}</span></td>
      <td class="num">${c.works_n || 0}</td>
      <td class="num">${formatViews(c.views)}</td></tr>`;
    })
    .join("");
}

/** Mirror "Has works for" into the detail works list when a composer is open. */
function syncDetailForceFromFacet() {
  if (state.selectedKey == null) return;
  const c = state.composers[state.selectedKey];
  const works = state.worksByComposer[c.id] || [];
  const mainForce = selectedValues(el.force);
  if (mainForce.length === 1 && works.some((w) => w.f === mainForce[0])) {
    state.detailForce = mainForce[0];
  } else if (mainForce.length === 0) {
    state.detailForce = "";
  } else {
    // Multiple force facets: detail dropdown stays on "all"; list filters via facet set in renderDetail.
    state.detailForce = "";
  }
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

function selectComposer(k) {
  const prev = el.rows.querySelector("tr.active");
  if (prev) {
    prev.classList.remove("active");
    prev.querySelector(".row-btn")?.setAttribute("aria-pressed", "false");
  }
  const tr = el.rows.querySelector(`tr[data-k="${k}"]`);
  if (tr) {
    tr.classList.add("active");
    tr.querySelector(".row-btn")?.setAttribute("aria-pressed", "true");
  }

  state.selectedKey = k;
  const c = state.composers[k];
  const works = state.worksByComposer[c.id] || [];
  syncDetailForceFromFacet();
  renderDetail();
  el.announce.textContent = `${c.name}: ${works.length} IMSLP works`;
  if (window.matchMedia("(max-width: 860px)").matches) {
    el.detail.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

function renderDetail() {
  const c = state.composers[state.selectedKey];
  if (!c) {
    el.detail.innerHTML = `<p class="detail-empty">Select a composer to see IMSLP works.</p>`;
    return;
  }
  const works = state.worksByComposer[c.id] || [];
  const forces = [...new Set(works.map((w) => w.f).filter(Boolean))].sort();
  const facetForces = selectedValues(el.force);
  let shown;
  if (state.detailForce) {
    shown = works.filter((w) => w.f === state.detailForce);
  } else if (facetForces.length > 1) {
    // Several "Has works for" chips: show the union in the detail list.
    const want = new Set(facetForces);
    shown = works.filter((w) => want.has(w.f));
  } else {
    shown = works;
  }
  const forceSelect =
    state.detailForce && forces.includes(state.detailForce) ? state.detailForce : "";

  const links = [];
  if (c.wiki) links.push(`<a href="${escapeHtml(c.wiki)}" target="_blank" rel="noopener">Wikipedia</a>`);
  if (c.wikidata) links.push(`<a href="${escapeHtml(c.wikidata)}" target="_blank" rel="noopener">Wikidata</a>`);
  if (c.imslp) links.push(`<a href="${escapeHtml(c.imslp)}" target="_blank" rel="noopener">IMSLP</a>`);

  const years = lifeSpan(c, " – ");
  const euBit =
    c.eu_year !== "" && c.eu_year != null
      ? ` · heuristic EU PD year ${escapeHtml(c.eu_year)}`
      : "";

  let worksBlock;
  if (!works.length) {
    worksBlock = `<p class="detail-empty">No IMSLP works linked in this dump${
      c.imslp ? " — the IMSLP category page may still list scores" : ""
    }.</p>`;
  } else if (!shown.length) {
    worksBlock = `<p class="detail-empty">No works match the current force filter for this composer.</p>`;
  } else {
    worksBlock = `
    <p class="force-filter">
      <label for="detailForce">Works (${shown.length}/${works.length})</label>
      <select id="detailForce">
        <option value="">all forces</option>
        ${forces.map((f) => `<option value="${escapeHtml(f)}" ${f === forceSelect ? "selected" : ""}>${escapeHtml(f)}</option>`).join("")}
      </select>
    </p>
    <ul class="works-list">
      ${shown
        .map(
          (w) => `<li>
            <a href="${escapeHtml(w.u)}" target="_blank" rel="noopener">${escapeHtml(w.t || "Work")}</a>
            <span class="src">${escapeHtml(w.f)}${w.s ? " · " + escapeHtml(w.s) : ""}</span>
          </li>`
        )
        .join("")}
    </ul>`;
  }

  el.detail.innerHTML = `
    <h2>${escapeHtml(c.name)}</h2>
    <p class="years">${years ? escapeHtml(years) + " · " : ""}<span class="badge ${escapeHtml(c.eu)}">${escapeHtml(c.eu)}</span>${euBit}</p>
    <div class="detail-links">${links.join("") || "<span>No external links</span>"}</div>
    ${worksBlock}`;
  el.detail.scrollTop = 0;
}

let qTimer = 0;

function bindEvents() {
  el.q.addEventListener("input", () => {
    clearTimeout(qTimer);
    qTimer = setTimeout(renderRows, 120);
  });

  el.controls.addEventListener("change", (e) => {
    if (e.target === el.q) return;
    const fs = e.target.closest(".facet");
    if (fs) updateFacetButton(FACETS.find((f) => f.key === fs.dataset.facet));
    renderRows();
  });

  el.controls.addEventListener("click", (e) => {
    const btn = e.target.closest(".facet-clear");
    if (!btn) return;
    const facet = FACETS.find((f) => f.key === btn.closest(".facet").dataset.facet);
    setFacet(facet, []);
    el[facet.key].querySelector("input")?.focus();
    renderRows();
  });

  el.clearAll.addEventListener("click", () => {
    for (const f of FACETS) setFacet(f, []);
    el.q.value = "";
    el.hasWorks.checked = false;
    renderRows();
  });

  el.resetDefaults.addEventListener("click", () => {
    for (const f of FACETS) setFacet(f, DEFAULTS[f.key]);
    el.q.value = "";
    el.hasWorks.checked = true;
    el.sort.value = "views";
    el.sortDir.value = "desc";
    renderRows();
  });

  el.rows.addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-k]");
    if (tr) selectComposer(Number(tr.dataset.k));
  });

  el.detail.addEventListener("change", (e) => {
    if (e.target.id !== "detailForce") return;
    state.detailForce = e.target.value;
    renderDetail();
    document.getElementById("detailForce")?.focus();
  });
}

async function fetchJson(name) {
  const r = await fetch(new URL(name, DATA_BASE));
  if (!r.ok) throw new Error(`${name}: HTTP ${r.status}`);
  return r.json();
}

async function load() {
  el.meta.textContent = "Loading dump…";
  const manifest = await fetchJson("manifest.json");
  const files = manifest.files || {};
  const [composers, worksByComposer] = await Promise.all([
    fetchJson(files.composers || "composers.json"),
    fetchJson(files.works_by_composer || "works_by_composer.json"),
  ]);
  state.manifest = manifest;
  // Wikidata ids are not unique in the dump (duos, duplicate wiki titles), so rows are keyed by index.
  composers.forEach((c, i) => {
    c._k = i;
    c._hay = [c.name, c.sort, ...(c.aliases || [])].join(" ").toLowerCase();
    for (const f of FACETS) if (f.list && !Array.isArray(c[f.field])) c[f.field] = [];
  });
  state.composers = composers;
  state.worksByComposer = worksByComposer;

  if (manifest.disclaimer) el.disclaimer.textContent = manifest.disclaimer;
  el.dumpLabel.textContent = manifest.dump_label || `dump ${manifest.dump_id}`;

  const facets = manifest.facets || {};
  for (const f of FACETS) {
    const values = facets[f.manifest] || [];
    fillFacet(f, values, DEFAULTS[f.key].filter((v) => values.includes(v)));
  }

  bindEvents();
  renderRows();
}

load().catch((err) => {
  console.error(err);
  el.meta.textContent = "Failed to load viewer data. Serve this folder over HTTP (GitHub Pages or a local static server).";
});
