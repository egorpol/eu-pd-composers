const DATA_BASE = new URL("./data/", import.meta.url);

const CATEGORY_SRCS = new Set(["imslp_tags", "imslp_geninfo"]);

const IMSLP_STATUS_LABELS = {
  rejected_heuristic: "IMSLP page belongs to a different person (name match rejected)",
  rejected_p839: "Wikidata's IMSLP link points to a different person",
  unverified_heuristic: "name match, not confirmed",
};

const CF_TOOLTIP =
  "IMSLP's own copyright flag on the work page — not a legal determination from this dump.";

// Empty selection on a facet means "no filter" on that axis.
const FACETS = [
  { key: "eu", manifest: "eu_pd_status", field: "eu", label: "EU PD status" },
  { key: "scope", manifest: "scope_class", field: "scope", label: "Scope" },
  { key: "force", manifest: "force_family", field: "forces", label: "Has works for", list: true, work: true },
  { key: "imslpStyle", manifest: "imslp_style", field: "st", label: "IMSLP style", list: true, work: true },
  { key: "cit", manifest: "citizenship_iso", field: "cit", label: "Citizenship", list: true },
  { key: "style", manifest: "style_tags", field: "styles", label: "Style", list: true },
];

const PD_PRESET_DEFS = [
  { value: "now", label: "PD now" },
  { value: "next", labelPrefix: "Enters PD on 1 January " },
  { value: "within5", label: "Within 5 years" },
];

const DEFAULTS = {
  eu: ["pd"],
  scope: ["classical_core"],
  force: [],
  imslpStyle: [],
  cit: [],
  style: [],
  pd: [],
  labelSrc: "all",
  sort: "views",
  sortDir: "desc",
  hasWorks: true,
  q: "",
};

const LABEL_SRC_VALUES = new Set(["all", "no_llm", "category"]);
const SORT_VALUES = new Set(["views", "name", "eu_year", "works", "matching"]);
const SORT_DIR_VALUES = new Set(["asc", "desc"]);
const PD_VALUES = new Set(PD_PRESET_DEFS.map((p) => p.value));

const state = {
  manifest: null,
  composers: [],
  worksByComposer: {},
  selectedId: null,
  detailForce: "",
  currentYear: new Date().getFullYear(),
  urlReady: false,
};

const el = {};

function bindElements() {
  Object.assign(el, {
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
    labelSrc: document.getElementById("labelSrc"),
    clearAll: document.getElementById("clearAll"),
    resetDefaults: document.getElementById("resetDefaults"),
    euAsOf: document.getElementById("euAsOf"),
    viewsHeader: document.getElementById("viewsHeader"),
    pd: document.getElementById("pd"),
  });
  for (const f of FACETS) el[f.key] = document.getElementById(f.key);
}

/** @param {number} year */
export function pdPresetKeys(euYear, euStatus, year) {
  if (euStatus === "unknown_death" || euYear === "" || euYear == null) return [];
  const y = Number(euYear);
  if (!Number.isFinite(y)) return [];
  const keys = [];
  if (y <= year) keys.push("now");
  else {
    if (y === year + 1) keys.push("next");
    if (y <= year + 5) keys.push("within5");
  }
  return keys;
}

/** Missing/unknown sort last in both directions. */
export function compareNullable(a, b, asc) {
  const aMiss = a === "" || a == null || (typeof a === "number" && Number.isNaN(a));
  const bMiss = b === "" || b == null || (typeof b === "number" && Number.isNaN(b));
  if (aMiss && bMiss) return 0;
  if (aMiss) return 1;
  if (bMiss) return -1;
  const d = Number(a) - Number(b);
  return asc ? d : -d;
}

export function workMatchesSource(work, srcMode) {
  if (!srcMode || srcMode === "all") return true;
  const s = work.s || "";
  if (srcMode === "no_llm") return !s.startsWith("llm");
  if (srcMode === "category") return CATEGORY_SRCS.has(s);
  return true;
}

export function workMatchesFilters(work, forceSet, styleSet, srcMode) {
  if (!workMatchesSource(work, srcMode)) return false;
  if (forceSet.size && !forceSet.has(work.f)) return false;
  if (styleSet.size) {
    const st = work.st || [];
    if (!st.some((x) => styleSet.has(x))) return false;
  }
  return true;
}

export function encodeUrlState(params) {
  const sp = new URLSearchParams();
  // Marker so a shared URL with no eu/scope means "cleared", not "use defaults".
  sp.set("v", "1");
  const setList = (key, values) => {
    if (values && values.length) sp.set(key, values.join(","));
  };
  if (params.q) sp.set("q", params.q);
  setList("eu", params.eu);
  setList("scope", params.scope);
  setList("force", params.force);
  setList("ist", params.imslpStyle);
  setList("cit", params.cit);
  setList("style", params.style);
  setList("pd", params.pd);
  if (params.labelSrc && params.labelSrc !== "all") sp.set("src", params.labelSrc);
  if (params.sort && params.sort !== "views") sp.set("sort", params.sort);
  if (params.sortDir && params.sortDir !== "desc") sp.set("dir", params.sortDir);
  if (params.hasWorks === false) sp.set("hasWorks", "0");
  if (params.id) sp.set("id", params.id);
  return `?${sp.toString()}`;
}

export function decodeUrlState(search, allowed) {
  const sp = new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);
  const list = (key, allow) => {
    const raw = sp.get(key);
    if (!raw) return [];
    return raw
      .split(",")
      .map((s) => s.trim())
      .filter((s) => s && (!allow || allow.has(s)));
  };
  // No state marker → caller should apply DEFAULTS (first visit / bare URL).
  if (!sp.has("v")) return { useDefaults: true };

  const out = { useDefaults: false };
  out.q = sp.has("q") ? sp.get("q") || "" : "";
  out.eu = list("eu", allowed.eu);
  out.scope = list("scope", allowed.scope);
  out.force = list("force", allowed.force);
  out.imslpStyle = list("ist", allowed.imslpStyle);
  out.cit = list("cit", allowed.cit);
  out.style = list("style", allowed.style);
  out.pd = list("pd", PD_VALUES);
  out.labelSrc = "all";
  if (sp.has("src")) {
    const src = sp.get("src");
    if (LABEL_SRC_VALUES.has(src)) out.labelSrc = src;
  }
  out.sort = "views";
  if (sp.has("sort")) {
    const sort = sp.get("sort");
    if (SORT_VALUES.has(sort)) out.sort = sort;
  }
  out.sortDir = "desc";
  if (sp.has("dir")) {
    const dir = sp.get("dir");
    if (SORT_DIR_VALUES.has(dir)) out.sortDir = dir;
  }
  out.hasWorks = true;
  if (sp.has("hasWorks")) {
    const hw = sp.get("hasWorks");
    if (hw === "0" || hw === "false") out.hasWorks = false;
    else if (hw === "1" || hw === "true") out.hasWorks = true;
  }
  if (sp.has("id")) {
    const id = sp.get("id");
    if (id && (!allowed.ids || allowed.ids.has(id))) out.id = id;
  }
  return out;
}

function selectedValues(container) {
  return [...container.querySelectorAll("input:checked")].map((i) => i.value);
}

function facetValuesFromManifest(facet, facets) {
  const raw = facets[facet.manifest];
  if (!raw) return [];
  if (Array.isArray(raw)) return raw;
  // imslp_style vocabulary is { name: count }
  return Object.keys(raw);
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

function fillPdFacet(selected) {
  const year = state.currentYear;
  const options = PD_PRESET_DEFS.map((p) => {
    const label =
      p.value === "next" ? `${p.labelPrefix}${year + 1}` : p.label;
    return `<label class="opt"><input type="checkbox" name="pd" value="${p.value}"${
      selected.includes(p.value) ? " checked" : ""
    } /><span>${escapeHtml(label)}</span></label>`;
  });
  el.pd.innerHTML = options.join("");
  updatePdButton();
}

function setFacet(facet, values) {
  for (const i of el[facet.key].querySelectorAll("input")) i.checked = values.includes(i.value);
  updateFacetButton(facet);
}

function setPdFacet(values) {
  for (const i of el.pd.querySelectorAll("input")) i.checked = values.includes(i.value);
  updatePdButton();
}

function updateFacetButton(facet) {
  const btn = el[facet.key].closest(".facet").querySelector(".facet-clear");
  const n = selectedValues(el[facet.key]).length;
  btn.disabled = n === 0;
  btn.textContent = n ? `Clear (${n})` : "Any";
  btn.setAttribute("aria-label", n ? `Clear ${facet.label} filter, ${n} selected` : `${facet.label}: any`);
}

function updatePdButton() {
  const btn = el.pd.closest(".facet").querySelector(".facet-clear");
  const n = selectedValues(el.pd).length;
  btn.disabled = n === 0;
  btn.textContent = n ? `Clear (${n})` : "Any";
  btn.setAttribute("aria-label", n ? `Clear PD preset filter, ${n} selected` : "PD preset: any");
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
  if (c.eu === "unknown_death") return "death unknown";
  return "";
}

function workFilterActive(forceSet, styleSet, srcMode) {
  return forceSet.size > 0 || styleSet.size > 0 || srcMode !== "all";
}

function matchingWorksFor(c, forceSet, styleSet, srcMode) {
  const works = state.worksByComposer[c.id] || [];
  return works.filter((w) => workMatchesFilters(w, forceSet, styleSet, srcMode));
}

function readUiState() {
  const sel = {};
  for (const f of FACETS) sel[f.key] = selectedValues(el[f.key]);
  return {
    q: el.q.value.trim(),
    eu: sel.eu,
    scope: sel.scope,
    force: sel.force,
    imslpStyle: sel.imslpStyle,
    cit: sel.cit,
    style: sel.style,
    pd: selectedValues(el.pd),
    labelSrc: el.labelSrc.value,
    sort: el.sort.value,
    sortDir: el.sortDir.value,
    hasWorks: el.hasWorks.checked,
    id: state.selectedId,
  };
}

function pushUrlState() {
  if (!state.urlReady) return;
  const qs = encodeUrlState(readUiState());
  const url = `${location.pathname}${qs}${location.hash || ""}`;
  history.replaceState(null, "", url);
}

function filteredComposers() {
  const q = el.q.value.trim().toLowerCase();
  const sel = {};
  for (const f of FACETS) sel[f.key] = new Set(selectedValues(el[f.key]));
  const pdSel = new Set(selectedValues(el.pd));
  const onlyWorks = el.hasWorks.checked;
  const srcMode = el.labelSrc.value;
  const forceSet = sel.force;
  const imslpStyleSet = sel.imslpStyle;
  const workActive = workFilterActive(forceSet, imslpStyleSet, srcMode);

  const rows = state.composers.filter((c) => {
    if (!matchesQuery(c, q)) return false;
    if (onlyWorks && !(c.works_n > 0)) return false;

    for (const f of FACETS) {
      if (f.work) continue;
      const s = sel[f.key];
      if (!s.size) continue;
      const v = c[f.field];
      if (f.list ? !v.some((x) => s.has(x)) : !s.has(v)) return false;
    }

    if (pdSel.size) {
      const keys = pdPresetKeys(c.eu_year, c.eu, state.currentYear);
      if (![...pdSel].some((k) => keys.includes(k))) return false;
    }

    const matched = matchingWorksFor(c, forceSet, imslpStyleSet, srcMode);
    c._match_n = matched.length;
    if (workActive && matched.length === 0) return false;
    return true;
  });

  const sort = el.sort.value;
  const asc = el.sortDir.value === "asc";
  const byName = (a, b) => (a.sort || a.name).localeCompare(b.sort || b.name);
  rows.sort((a, b) => {
    if (sort === "name") {
      const d = byName(a, b);
      return asc ? d : -d;
    }
    let d;
    if (sort === "eu_year") d = compareNullable(a.eu_year, b.eu_year, asc);
    else if (sort === "works") d = compareNullable(a.works_n || 0, b.works_n || 0, asc);
    else if (sort === "matching") d = compareNullable(a._match_n || 0, b._match_n || 0, asc);
    else d = compareNullable(a.views, b.views, asc);
    return d || byName(a, b);
  });
  return rows;
}

function formatWorksCell(c, workActive) {
  const total = c.works_n || 0;
  if (!workActive) return String(total);
  const n = c._match_n || 0;
  return `<span class="match-n">${n}</span><span class="match-sep">/</span>${total}`;
}

function renderRows() {
  const forceSet = new Set(selectedValues(el.force));
  const imslpStyleSet = new Set(selectedValues(el.imslpStyle));
  const srcMode = el.labelSrc.value;
  const workActive = workFilterActive(forceSet, imslpStyleSet, srcMode);
  const rows = filteredComposers();
  el.meta.textContent = `${rows.length.toLocaleString()} of ${state.composers.length.toLocaleString()} composers shown · ${state.manifest.dump_label || "dump " + state.manifest.dump_id}`;

  if (state.selectedId != null) {
    const stillVisible = rows.some((c) => c.id === state.selectedId);
    if (!stillVisible) {
      state.selectedId = null;
      state.detailForce = "";
      el.announce.textContent = "";
      renderDetail();
    } else {
      syncDetailForceFromFacet();
      renderDetail();
    }
  }

  pushUrlState();

  if (!rows.length) {
    el.rows.innerHTML = `<tr class="empty"><td colspan="4">No composers match these filters. Try “Any” on a facet, or clear the search.</td></tr>`;
    return;
  }
  el.rows.innerHTML = rows
    .map((c) => {
      const active = c.id === state.selectedId;
      const sub = lifeSpan(c, "–") + (c.scope && c.scope !== "classical_core" ? " · " + c.scope : "");
      return `<tr data-id="${escapeHtml(c.id)}"${active ? ' class="active"' : ""}>
      <td>
        <button type="button" class="row-btn" aria-pressed="${active}">${escapeHtml(c.name)}</button>
        <span class="sub">${escapeHtml(sub)}</span>
      </td>
      <td><span class="badge ${escapeHtml(c.eu || "")}">${escapeHtml(c.eu || "—")}</span></td>
      <td class="num">${formatWorksCell(c, workActive)}</td>
      <td class="num">${formatViews(c.views)}</td></tr>`;
    })
    .join("");
}

/** Mirror "Has works for" into the detail works list when a composer is open. */
function syncDetailForceFromFacet() {
  if (state.selectedId == null) return;
  const c = state.composers.find((x) => x.id === state.selectedId);
  if (!c) return;
  const works = state.worksByComposer[c.id] || [];
  const mainForce = selectedValues(el.force);
  if (mainForce.length === 1 && works.some((w) => w.f === mainForce[0])) {
    state.detailForce = mainForce[0];
  } else if (mainForce.length === 0) {
    state.detailForce = "";
  } else {
    state.detailForce = "";
  }
}

function formatViews(v) {
  if (v === "" || v == null) return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
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

function imslpStatusText(status) {
  if (!status) return "";
  return IMSLP_STATUS_LABELS[status] || status;
}

function workEvidenceHtml(w) {
  const bits = [];
  const cf = w.cf || [];
  if (cf.includes("nonpd_eu")) {
    bits.push(
      `<span class="ev-badge" title="${escapeHtml(CF_TOOLTIP)}">IMSLP: not PD in EU</span>`
    );
  }
  if (cf.includes("nonpd_us")) {
    bits.push(
      `<span class="ev-badge" title="${escapeHtml(CF_TOOLTIP)}">IMSLP: not PD in US</span>`
    );
  }
  if (w.fp != null && w.fp !== "") {
    bits.push(`<span class="pub-year">pub. ${escapeHtml(w.fp)}</span>`);
  }
  return bits.length ? ` ${bits.join(" ")}` : "";
}

function selectComposer(id) {
  const prev = el.rows.querySelector("tr.active");
  if (prev) {
    prev.classList.remove("active");
    prev.querySelector(".row-btn")?.setAttribute("aria-pressed", "false");
  }
  const tr = el.rows.querySelector(`tr[data-id="${CSS.escape(id)}"]`);
  if (tr) {
    tr.classList.add("active");
    tr.querySelector(".row-btn")?.setAttribute("aria-pressed", "true");
  }

  state.selectedId = id;
  const c = state.composers.find((x) => x.id === id);
  if (!c) return;
  const works = state.worksByComposer[c.id] || [];
  syncDetailForceFromFacet();
  renderDetail();
  pushUrlState();
  el.announce.textContent = `${c.name}: ${works.length} IMSLP works`;
  if (window.matchMedia("(max-width: 860px)").matches) {
    el.detail.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

function renderDetail() {
  const c = state.composers.find((x) => x.id === state.selectedId);
  if (!c) {
    el.detail.innerHTML = `<p class="detail-empty">Select a composer to see IMSLP works.</p>`;
    return;
  }
  const works = state.worksByComposer[c.id] || [];
  const srcMode = el.labelSrc.value;
  const facetForces = selectedValues(el.force);
  const imslpStyleSet = new Set(selectedValues(el.imslpStyle));

  let forceFilter = new Set();
  if (state.detailForce) forceFilter = new Set([state.detailForce]);
  else if (facetForces.length) forceFilter = new Set(facetForces);

  const shown = works.filter((w) =>
    workMatchesFilters(w, forceFilter, imslpStyleSet, srcMode)
  );
  const forForceOptions = works.filter((w) =>
    workMatchesFilters(w, new Set(), imslpStyleSet, srcMode)
  );
  const forces = [...new Set(forForceOptions.map((w) => w.f).filter(Boolean))].sort();
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

  const metaBits = [];
  if (c.imslp_status) {
    metaBits.push(`IMSLP match: ${escapeHtml(imslpStatusText(c.imslp_status))}`);
  }
  if (c.styles && c.styles.length) {
    const src = c.style_src ? ` (${escapeHtml(c.style_src)})` : "";
    metaBits.push(`styles: ${escapeHtml(c.styles.join(", "))}${src}`);
  }
  if (c.film) metaBits.push("also film composer");
  const metaLine = metaBits.length
    ? `<p class="detail-meta">${metaBits.join(" · ")}</p>`
    : "";

  let worksBlock;
  if (!works.length) {
    worksBlock = `<p class="detail-empty">No IMSLP works linked in this dump${
      c.imslp ? " — the IMSLP category page may still list scores" : ""
    }.</p>`;
  } else if (!shown.length) {
    worksBlock = `<p class="detail-empty">No works match the current force / style / label-source filters for this composer.</p>`;
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
            <span class="src">${escapeHtml(w.f)}${w.s ? " · " + escapeHtml(w.s) : ""}</span>${workEvidenceHtml(w)}
          </li>`
        )
        .join("")}
    </ul>`;
  }

  el.detail.innerHTML = `
    <h2>${escapeHtml(c.name)}</h2>
    <p class="years">${years ? escapeHtml(years) + " · " : ""}<span class="badge ${escapeHtml(c.eu)}">${escapeHtml(c.eu)}</span>${euBit}</p>
    ${metaLine}
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
    if (fs) {
      if (fs.dataset.facet === "pd") updatePdButton();
      else {
        const facet = FACETS.find((f) => f.key === fs.dataset.facet);
        if (facet) updateFacetButton(facet);
      }
    }
    renderRows();
  });

  el.controls.addEventListener("click", (e) => {
    const btn = e.target.closest(".facet-clear");
    if (!btn) return;
    const facetEl = btn.closest(".facet");
    if (facetEl.dataset.facet === "pd") {
      setPdFacet([]);
      el.pd.querySelector("input")?.focus();
    } else {
      const facet = FACETS.find((f) => f.key === facetEl.dataset.facet);
      setFacet(facet, []);
      el[facet.key].querySelector("input")?.focus();
    }
    renderRows();
  });

  el.clearAll.addEventListener("click", () => {
    for (const f of FACETS) setFacet(f, []);
    setPdFacet([]);
    el.q.value = "";
    el.hasWorks.checked = false;
    el.labelSrc.value = "all";
    state.selectedId = null;
    state.detailForce = "";
    renderDetail();
    renderRows();
  });

  el.resetDefaults.addEventListener("click", () => {
    for (const f of FACETS) setFacet(f, DEFAULTS[f.key] || []);
    setPdFacet(DEFAULTS.pd);
    el.q.value = "";
    el.hasWorks.checked = true;
    el.labelSrc.value = "all";
    el.sort.value = "views";
    el.sortDir.value = "desc";
    state.selectedId = null;
    state.detailForce = "";
    renderDetail();
    renderRows();
  });

  el.rows.addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-id]");
    if (tr) selectComposer(tr.dataset.id);
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

function applyPageviewsLabel(label) {
  const text = label ? `Views (${label})` : "Views";
  el.viewsHeader.textContent = text;
  const opt = el.sort.querySelector('option[value="views"]');
  if (opt) opt.textContent = label ? `Pageviews (${label})` : "Pageviews";
}

async function load() {
  bindElements();
  el.meta.textContent = "Loading dump…";
  const manifest = await fetchJson("manifest.json");
  const files = manifest.files || {};
  const [composers, worksByComposer] = await Promise.all([
    fetchJson(files.composers || "composers.json"),
    fetchJson(files.works_by_composer || "works_by_composer.json"),
  ]);
  state.manifest = manifest;
  composers.forEach((c) => {
    c._hay = [c.name, c.sort, ...(c.aliases || [])].join(" ").toLowerCase();
    for (const f of FACETS) {
      if (f.work) continue;
      if (f.list && !Array.isArray(c[f.field])) c[f.field] = [];
    }
  });
  state.composers = composers;
  state.worksByComposer = worksByComposer;

  if (manifest.disclaimer) el.disclaimer.textContent = manifest.disclaimer;
  el.dumpLabel.textContent = manifest.dump_label || `dump ${manifest.dump_id}`;
  el.euAsOf.textContent = `(as of ${state.currentYear})`;
  applyPageviewsLabel(manifest.pageviews_window_label || "");

  const facets = manifest.facets || {};
  const allowed = {
    eu: new Set(facets.eu_pd_status || []),
    scope: new Set(facets.scope_class || []),
    force: new Set(facets.force_family || []),
    imslpStyle: new Set(facetValuesFromManifest({ manifest: "imslp_style" }, facets)),
    cit: new Set(facets.citizenship_iso || []),
    style: new Set(facets.style_tags || []),
    ids: new Set(composers.map((c) => c.id)),
  };

  const fromUrl = decodeUrlState(location.search, allowed);
  const init = fromUrl.useDefaults ? { ...DEFAULTS } : fromUrl;

  for (const f of FACETS) {
    const values = facetValuesFromManifest(f, facets);
    const preferred = init[f.key] || [];
    fillFacet(
      f,
      values,
      preferred.filter((v) => values.includes(v))
    );
  }
  fillPdFacet(init.pd || []);

  el.q.value = init.q || "";
  el.labelSrc.value = init.labelSrc || "all";
  el.sort.value = init.sort || "views";
  el.sortDir.value = init.sortDir || "desc";
  el.hasWorks.checked = init.hasWorks !== false;

  bindEvents();
  state.urlReady = true;
  renderRows();

  if (init.id && allowed.ids.has(init.id)) {
    selectComposer(init.id);
  }
}

if (typeof document !== "undefined") {
  load().catch((err) => {
    console.error(err);
    if (el.meta) {
      el.meta.textContent =
        "Failed to load viewer data. Serve this folder over HTTP (GitHub Pages or a local static server).";
    }
  });
}
