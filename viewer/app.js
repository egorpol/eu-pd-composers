const DATA_BASE = new URL("./data/", import.meta.url);

const CATEGORY_SRCS = new Set(["imslp_tags", "imslp_geninfo"]);

const IMSLP_STATUS_LABELS = {
  rejected_heuristic: "IMSLP page belongs to a different person (name match rejected)",
  rejected_p839: "Wikidata's IMSLP link points to a different person",
  unverified_heuristic: "name match, not confirmed",
};

const CF_TOOLTIP =
  "IMSLP's own copyright flag on the work page — not a legal determination from this dump.";

const STYLE_LABEL_OVERRIDES = {
  late_romantic: "Late Romantic",
  national_folk: "National / folk",
  avant_garde: "Avant-garde",
  atonal_modernism: "Atonal modernism",
  postminimalism: "Post-minimalism",
};

/** Era-first union order for Style facet raw values (IMSLP display names + composer slugs). */
export const STYLE_CANONICAL_ORDER = [
  "Ancient",
  "Medieval",
  "Renaissance",
  "Baroque",
  "Classical",
  "Romantic",
  "late_romantic",
  "Early 20th century",
  "impressionism",
  "expressionism",
  "national_folk",
  "neoclassicism",
  "atonal_modernism",
  "serialism",
  "Modern",
  "avant_garde",
  "electroacoustic",
  "minimalism",
  "postminimalism",
  "spectralism",
  "polystylism",
  "Jazz",
  "Traditional (folk)",
  "Non-western classical",
];

const STYLE_SRC_ORDER = ["wikidata", "imslp", "llm"];
const STYLE_SRC_LABELS = {
  wikidata: "Wikidata",
  imslp: "IMSLP works",
  llm: "LLM",
};
const DEFAULT_STYLE_SRC = ["wikidata", "imslp", "llm"];
const STYLE_SRC_VALUES = new Set(STYLE_SRC_ORDER);

// Empty selection on a facet means "no filter" on that axis.
const FACETS = [
  { key: "scope", manifest: "scope_class", field: "scope", label: "Scope" },
  { key: "force", manifest: "force_family", field: "forces", label: "Has works for", list: true, work: true },
  { key: "cit", manifest: "citizenship_iso", field: "cit", label: "Citizenship", list: true },
  { key: "style", manifest: null, field: "styles", label: "Style", list: true, humanize: true },
];

const PD_BUCKET_DEFS = [
  { value: "now", label: "PD now" },
  { value: "next", labelPrefix: "Enters PD on 1 January " },
  { value: "soon", labelRange: true },
  { value: "later", label: "Later" },
  { value: "unknown", label: "No death date" },
];

const PD_VALUES = new Set(PD_BUCKET_DEFS.map((p) => p.value));
const LEGACY_PD_VALUES = new Set(["within5"]);

const EU_TO_PD = {
  pd: ["now"],
  not_pd: ["next", "soon", "later"],
  unknown_death: ["unknown"],
};

const DEFAULTS = {
  scope: ["classical_core"],
  force: [],
  cit: [],
  style: [],
  styleSrc: [...DEFAULT_STYLE_SRC],
  pd: ["now"],
  labelSrc: "all",
  sort: "views",
  sortDir: "desc",
  hasWorks: true,
  q: "",
};

const LABEL_SRC_VALUES = new Set(["all", "no_llm", "category"]);
const SORT_VALUES = new Set(["views", "name", "eu_year", "works", "matching"]);
const SORT_DIR_VALUES = new Set(["asc", "desc"]);

const state = {
  manifest: null,
  composers: [],
  worksByComposer: {},
  styleVocab: [],
  styleSrcCoverage: { wikidata: 0, imslp: 0, llm: 0 },
  selectedId: null,
  detailForce: "",
  currentYear: new Date().getFullYear(),
  urlReady: false,
  filtersMq: null,
};

const el = {};

function bindElements() {
  Object.assign(el, {
    filtersPanel: document.getElementById("filtersPanel"),
    activeFilterCount: document.getElementById("activeFilterCount"),
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
    styleSrc: document.getElementById("styleSrc"),
    viewsHeader: document.getElementById("viewsHeader"),
    pd: document.getElementById("pd"),
  });
  for (const f of FACETS) el[f.key] = document.getElementById(f.key);
}

/**
 * Mutually exclusive live EU PD bucket from eu_year and calendar year Y.
 * Boundaries: ≤Y → now; Y+1 → next; Y+2…Y+5 → soon; >Y+5 → later; missing → unknown.
 * @param {string|number|null|undefined} euYear
 * @param {number} year
 */
export function pdBucket(euYear, year) {
  if (euYear === "" || euYear == null) return "unknown";
  const y = Number(euYear);
  if (!Number.isFinite(y)) return "unknown";
  if (y <= year) return "now";
  if (y === year + 1) return "next";
  if (y <= year + 5) return "soon";
  return "later";
}

/** @deprecated Prefer pdBucket; kept for callers that still pass euStatus. */
export function pdPresetKeys(euYear, _euStatus, year) {
  return [pdBucket(euYear, year)];
}

/** Human label for a Style facet raw value (composer slug or IMSLP period name). */
export function humanizeStyleTag(raw) {
  if (!raw) return "";
  if (STYLE_LABEL_OVERRIDES[raw]) return STYLE_LABEL_OVERRIDES[raw];
  return String(raw)
    .split("_")
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

/** Map legacy URL facet values into current pd buckets (unknown values dropped). */
export function mapLegacyPdUrl(pdValues, euValues) {
  const out = new Set();
  for (const v of pdValues || []) {
    if (v === "within5") {
      out.add("next");
      out.add("soon");
    } else if (PD_VALUES.has(v)) {
      out.add(v);
    }
  }
  for (const v of euValues || []) {
    const mapped = EU_TO_PD[v];
    if (mapped) for (const m of mapped) out.add(m);
  }
  return [...out];
}

/**
 * Ordered union of style vocabularies: canonical era-first order, then unknown values A–Z.
 * @param {Iterable<string>} values
 */
export function buildStyleVocab(values) {
  const present = new Set([...values].filter(Boolean));
  const known = new Set(STYLE_CANONICAL_ORDER);
  const ordered = STYLE_CANONICAL_ORDER.filter((v) => present.has(v));
  const extras = [...present].filter((v) => !known.has(v)).sort((a, b) => a.localeCompare(b));
  return [...ordered, ...extras];
}

/**
 * Per-composer styles keyed by source.
 * Wikidata / LLM use composer.styles when style_src matches; IMSLP is the union of work.st.
 * @param {{ styles?: string[], style_src?: string }} composer
 * @param {Array<{ st?: string[] }>|undefined} works
 */
export function composerStylesBySource(composer, works) {
  const by = { wikidata: [], llm: [], imslp: [] };
  const tags = Array.isArray(composer.styles) ? composer.styles : [];
  const src = composer.style_src || "";
  if (tags.length && src === "wikidata") by.wikidata = tags.slice();
  if (tags.length && String(src).startsWith("llm")) by.llm = tags.slice();
  const imslp = new Set();
  for (const w of works || []) {
    for (const s of w.st || []) if (s) imslp.add(s);
  }
  by.imslp = [...imslp];
  return by;
}

/**
 * Composer matches when any selected style appears under any selected source.
 * Empty styleSet → no style filter (always true). Empty srcSet with styles → no match.
 * @param {{ wikidata: string[], llm: string[], imslp: string[] }} bySource
 * @param {Set<string>} styleSet
 * @param {Set<string>} srcSet
 */
export function composerMatchesStyle(bySource, styleSet, srcSet) {
  if (!styleSet.size) return true;
  if (!srcSet.size) return false;
  for (const src of STYLE_SRC_ORDER) {
    if (!srcSet.has(src)) continue;
    const vals = bySource[src] || [];
    if (vals.some((v) => styleSet.has(v))) return true;
  }
  return false;
}

/** Live EU column / badge text for a composer. */
export function liveEuLabel(euYear, year) {
  const bucket = pdBucket(euYear, year);
  if (bucket === "now") return "PD";
  if (bucket === "unknown") return "—";
  return String(euYear);
}

/** Detail-pane sentence for live EU status. */
export function liveEuDetail(euYear, year) {
  const bucket = pdBucket(euYear, year);
  if (bucket === "now") return "In EU public domain now";
  if (bucket === "unknown") return "No death date — EU PD year unknown";
  return `Enters EU PD on 1 January ${euYear}`;
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

export function workMatchesFilters(work, forceSet, srcMode) {
  if (!workMatchesSource(work, srcMode)) return false;
  if (forceSet.size && !forceSet.has(work.f)) return false;
  return true;
}

export function encodeUrlState(params) {
  const sp = new URLSearchParams();
  // Marker so a shared URL with no filters means "cleared", not "use defaults".
  sp.set("v", "1");
  const setList = (key, values) => {
    if (values && values.length) sp.set(key, values.join(","));
  };
  if (params.q) sp.set("q", params.q);
  setList("scope", params.scope);
  setList("force", params.force);
  setList("cit", params.cit);
  setList("style", params.style);
  const srcs = params.styleSrc || [];
  const allSrc =
    srcs.length === DEFAULT_STYLE_SRC.length && DEFAULT_STYLE_SRC.every((s) => srcs.includes(s));
  if (srcs.length && !allSrc) setList("styleSrc", srcs);
  setList("pd", params.pd);
  if (params.labelSrc && params.labelSrc !== "all") sp.set("src", params.labelSrc);
  if (params.sort && params.sort !== "views") sp.set("sort", params.sort);
  if (params.sortDir && params.sortDir !== "desc") sp.set("dir", params.sortDir);
  if (params.hasWorks === false) sp.set("hasWorks", "0");
  if (params.id) sp.set("id", params.id);
  return `?${sp.toString()}`;
}

/**
 * Decode shareable URL state. Legacy: `ist` / `imslpStyle` → style + styleSrc=imslp
 * when no modern `style` / `styleSrc` are present; bare `style=Y` keeps working.
 */
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
  out.scope = list("scope", allowed.scope);
  out.force = list("force", allowed.force);
  out.cit = list("cit", allowed.cit);

  const styleAllow = allowed.style || null;
  const modernStyle = list("style", styleAllow);
  const legacyIst = [
    ...list("ist", styleAllow),
    ...list("imslpStyle", styleAllow),
  ];
  out.style = [...new Set([...modernStyle, ...legacyIst])];

  if (sp.has("styleSrc")) {
    const srcs = list("styleSrc", STYLE_SRC_VALUES);
    out.styleSrc = srcs.length ? srcs : [...DEFAULT_STYLE_SRC];
  } else if (legacyIst.length && !modernStyle.length) {
    out.styleSrc = ["imslp"];
  } else {
    out.styleSrc = [...DEFAULT_STYLE_SRC];
  }

  // Accept current pd buckets plus legacy within5; map eu=… separately.
  const pdAllow = new Set([...PD_VALUES, ...LEGACY_PD_VALUES]);
  const rawPd = list("pd", pdAllow);
  const rawEu = list("eu", new Set(Object.keys(EU_TO_PD)));
  out.pd = mapLegacyPdUrl(rawPd, rawEu);

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
  if (!container) return [];
  return [...container.querySelectorAll("input:checked")].map((i) => i.value);
}

function facetValuesFromManifest(facet, facets) {
  const raw = facets[facet.manifest];
  if (!raw) return [];
  if (Array.isArray(raw)) return raw;
  return Object.keys(raw);
}

function optionLabel(facet, value) {
  if (facet.humanize) return humanizeStyleTag(value);
  return value;
}

function fillFacet(facet, values, selected) {
  const box = el[facet.key];
  if (facet.key === "style") {
    fillStyleFacet(selected);
    return;
  }
  box.innerHTML = values
    .map(
      (v) => `<label class="opt"><input type="checkbox" name="${facet.key}" value="${escapeHtml(v)}"${
        selected.includes(v) ? " checked" : ""
      } /><span>${escapeHtml(optionLabel(facet, v))}</span></label>`
    )
    .join("");
  updateFacetButton(facet);
}

function styleCountsForSources(srcSet) {
  const counts = Object.create(null);
  for (const v of state.styleVocab) counts[v] = 0;
  for (const c of state.composers) {
    const by = c._stylesBySrc;
    const seen = new Set();
    for (const src of STYLE_SRC_ORDER) {
      if (!srcSet.has(src)) continue;
      for (const v of by[src] || []) {
        if (seen.has(v)) continue;
        seen.add(v);
        if (v in counts) counts[v] += 1;
      }
    }
  }
  return counts;
}

function fillStyleFacet(selected) {
  const srcSet = new Set(selectedValues(el.styleSrc));
  const counts = styleCountsForSources(srcSet);
  const sel = selected || selectedValues(el.style);
  el.style.innerHTML = state.styleVocab
    .map((v) => {
      const n = counts[v] || 0;
      return `<label class="opt"><input type="checkbox" name="style" value="${escapeHtml(v)}"${
        sel.includes(v) ? " checked" : ""
      } /><span>${escapeHtml(humanizeStyleTag(v))}</span><span class="count">${n.toLocaleString()}</span></label>`;
    })
    .join("");
  updateFacetButton(FACETS.find((f) => f.key === "style"));
}

function fillStyleSrc(selected) {
  const cov = state.styleSrcCoverage;
  const sel = selected.length ? selected : [...DEFAULT_STYLE_SRC];
  el.styleSrc.innerHTML = STYLE_SRC_ORDER.map((src) => {
    const n = cov[src] || 0;
    const label = STYLE_SRC_LABELS[src];
    return `<label class="opt"><input type="checkbox" name="styleSrc" value="${src}"${
      sel.includes(src) ? " checked" : ""
    } /><span>${escapeHtml(label)} ${n.toLocaleString()}</span></label>`;
  }).join("");
}

function pdOptionLabel(def, year) {
  if (def.value === "next") return `${def.labelPrefix}${year + 1}`;
  if (def.value === "soon") return `Enters PD ${year + 2}–${year + 5}`;
  return def.label;
}

function fillPdFacet(selected) {
  const year = state.currentYear;
  const options = PD_BUCKET_DEFS.map((p) => {
    const label = pdOptionLabel(p, year);
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

function setStyleSrc(values) {
  const want = values.length ? values : [...DEFAULT_STYLE_SRC];
  for (const i of el.styleSrc.querySelectorAll("input")) i.checked = want.includes(i.value);
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
  btn.setAttribute(
    "aria-label",
    n ? `Clear EU public domain filter, ${n} selected` : "EU public domain: any"
  );
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

function workFilterActive(forceSet, srcMode) {
  return forceSet.size > 0 || srcMode !== "all";
}

function matchingWorksFor(c, forceSet, srcMode) {
  const works = state.worksByComposer[c.id] || [];
  return works.filter((w) => workMatchesFilters(w, forceSet, srcMode));
}

function readUiState() {
  const sel = {};
  for (const f of FACETS) sel[f.key] = selectedValues(el[f.key]);
  return {
    q: el.q.value.trim(),
    scope: sel.scope,
    force: sel.force,
    cit: sel.cit,
    style: sel.style,
    styleSrc: selectedValues(el.styleSrc),
    pd: selectedValues(el.pd),
    labelSrc: el.labelSrc.value,
    sort: el.sort.value,
    sortDir: el.sortDir.value,
    hasWorks: el.hasWorks.checked,
    id: state.selectedId,
  };
}

function countActiveFilters(ui) {
  let n = 0;
  if (ui.q) n += 1;
  if (ui.scope.length) n += 1;
  if (ui.force.length) n += 1;
  if (ui.cit.length) n += 1;
  if (ui.style.length) n += 1;
  if (ui.pd.length) n += 1;
  const allSrc =
    ui.styleSrc.length === DEFAULT_STYLE_SRC.length &&
    DEFAULT_STYLE_SRC.every((s) => ui.styleSrc.includes(s));
  if (ui.styleSrc.length && !allSrc) n += 1;
  if (ui.labelSrc && ui.labelSrc !== "all") n += 1;
  if (ui.hasWorks) n += 1;
  return n;
}

function updateActiveFilterCount() {
  const n = countActiveFilters(readUiState());
  el.activeFilterCount.textContent = String(n);
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
  const styleSrcSet = new Set(selectedValues(el.styleSrc));
  const onlyWorks = el.hasWorks.checked;
  const srcMode = el.labelSrc.value;
  const forceSet = sel.force;
  const workActive = workFilterActive(forceSet, srcMode);

  const rows = state.composers.filter((c) => {
    if (!matchesQuery(c, q)) return false;
    if (onlyWorks && !(c.works_n > 0)) return false;

    for (const f of FACETS) {
      if (f.work || f.key === "style") continue;
      const s = sel[f.key];
      if (!s.size) continue;
      const v = c[f.field];
      if (f.list ? !v.some((x) => s.has(x)) : !s.has(v)) return false;
    }

    if (!composerMatchesStyle(c._stylesBySrc, sel.style, styleSrcSet)) return false;

    if (pdSel.size) {
      const bucket = pdBucket(c.eu_year, state.currentYear);
      if (!pdSel.has(bucket)) return false;
    }

    const matched = matchingWorksFor(c, forceSet, srcMode);
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
  const srcMode = el.labelSrc.value;
  const workActive = workFilterActive(forceSet, srcMode);
  const rows = filteredComposers();
  el.meta.textContent = `${rows.length.toLocaleString()} of ${state.composers.length.toLocaleString()} composers shown · ${state.manifest.dump_label || "dump " + state.manifest.dump_id}`;
  updateActiveFilterCount();

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
  const year = state.currentYear;
  el.rows.innerHTML = rows
    .map((c) => {
      const active = c.id === state.selectedId;
      const sub = lifeSpan(c, "–") + (c.scope && c.scope !== "classical_core" ? " · " + c.scope : "");
      const bucket = pdBucket(c.eu_year, year);
      const euText = liveEuLabel(c.eu_year, year);
      return `<tr data-id="${escapeHtml(c.id)}"${active ? ' class="active"' : ""}>
      <td>
        <button type="button" class="row-btn" aria-pressed="${active}">${escapeHtml(c.name)}</button>
        <span class="sub">${escapeHtml(sub)}</span>
      </td>
      <td><span class="badge ${escapeHtml(bucket)}">${escapeHtml(euText)}</span></td>
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

function periodChipsHtml(w) {
  const st = w.st || [];
  if (!st.length) return "";
  return st
    .map((p) => `<span class="period-chip" title="IMSLP period">${escapeHtml(p)}</span>`)
    .join("");
}

function styleDetailLine(c) {
  const by = c._stylesBySrc;
  const parts = [];
  for (const src of STYLE_SRC_ORDER) {
    const vals = by[src] || [];
    if (!vals.length) continue;
    const labels = vals.map(humanizeStyleTag).join(", ");
    parts.push(`${STYLE_SRC_LABELS[src]}: ${labels}`);
  }
  if (!parts.length) return "";
  return `Style — ${parts.join(" · ")}`;
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
  if (window.matchMedia("(max-width: 900px)").matches) {
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

  let forceFilter = new Set();
  if (state.detailForce) forceFilter = new Set([state.detailForce]);
  else if (facetForces.length) forceFilter = new Set(facetForces);

  const shown = works.filter((w) => workMatchesFilters(w, forceFilter, srcMode));
  const forForceOptions = works.filter((w) => workMatchesFilters(w, new Set(), srcMode));
  const forces = [...new Set(forForceOptions.map((w) => w.f).filter(Boolean))].sort();
  const forceSelect =
    state.detailForce && forces.includes(state.detailForce) ? state.detailForce : "";

  const links = [];
  if (c.wiki) links.push(`<a href="${escapeHtml(c.wiki)}" target="_blank" rel="noopener">Wikipedia</a>`);
  if (c.wikidata) links.push(`<a href="${escapeHtml(c.wikidata)}" target="_blank" rel="noopener">Wikidata</a>`);
  if (c.imslp) links.push(`<a href="${escapeHtml(c.imslp)}" target="_blank" rel="noopener">IMSLP</a>`);

  const years = lifeSpan(c, " – ");
  const euDetail = liveEuDetail(c.eu_year, state.currentYear);
  const bucket = pdBucket(c.eu_year, state.currentYear);
  const euBit = ` · ${escapeHtml(euDetail)}`;

  const metaBits = [];
  if (c.imslp_status) {
    metaBits.push(`IMSLP match: ${escapeHtml(imslpStatusText(c.imslp_status))}`);
  }
  const styleLine = styleDetailLine(c);
  if (styleLine) metaBits.push(escapeHtml(styleLine));
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
    worksBlock = `<p class="detail-empty">No works match the current force / label-source filters for this composer.</p>`;
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
            <span class="src">${escapeHtml(w.f)}${w.s ? " · " + escapeHtml(w.s) : ""}</span>${periodChipsHtml(w)}${workEvidenceHtml(w)}
          </li>`
        )
        .join("")}
    </ul>`;
  }

  el.detail.innerHTML = `
    <h2>${escapeHtml(c.name)}</h2>
    <p class="years">${years ? escapeHtml(years) + " · " : ""}<span class="badge ${escapeHtml(bucket)}">${escapeHtml(liveEuLabel(c.eu_year, state.currentYear))}</span>${euBit}</p>
    ${metaLine}
    <div class="detail-links">${links.join("") || "<span>No external links</span>"}</div>
    ${worksBlock}`;
  el.detail.scrollTop = 0;
}

let qTimer = 0;

function syncFiltersPanelOpen(force) {
  if (!el.filtersPanel || !state.filtersMq) return;
  if (force || !el.filtersPanel.dataset.userToggled) {
    el.filtersPanel.open = state.filtersMq.matches;
  }
}

function bindEvents() {
  el.q.addEventListener("input", () => {
    clearTimeout(qTimer);
    qTimer = setTimeout(renderRows, 120);
  });

  el.filtersPanel.addEventListener("toggle", () => {
    el.filtersPanel.dataset.userToggled = "1";
  });

  state.filtersMq = window.matchMedia("(min-width: 901px)");
  syncFiltersPanelOpen(true);
  const onMq = () => {
    delete el.filtersPanel.dataset.userToggled;
    syncFiltersPanelOpen(true);
  };
  if (state.filtersMq.addEventListener) state.filtersMq.addEventListener("change", onMq);
  else state.filtersMq.addListener(onMq);

  document.addEventListener("click", (e) => {
    const toggle = e.target.closest(".hint-toggle");
    if (!toggle) return;
    const id = toggle.getAttribute("aria-controls");
    const hint = id ? document.getElementById(id) : null;
    if (!hint) return;
    const open = toggle.getAttribute("aria-expanded") === "true";
    toggle.setAttribute("aria-expanded", open ? "false" : "true");
    hint.hidden = open;
  });

  const onFilterChange = (e) => {
    if (e.target === el.q) return;
    if (e.target.name === "styleSrc") {
      const checked = selectedValues(el.style);
      fillStyleFacet(checked);
    }
    const fs = e.target.closest(".facet");
    if (fs) {
      if (fs.dataset.facet === "pd") updatePdButton();
      else if (fs.dataset.facet === "style" && e.target.name === "style") {
        updateFacetButton(FACETS.find((f) => f.key === "style"));
      } else {
        const facet = FACETS.find((f) => f.key === fs.dataset.facet);
        if (facet && e.target.name !== "styleSrc") updateFacetButton(facet);
      }
    }
    renderRows();
  };

  el.filtersPanel.addEventListener("change", onFilterChange);

  el.filtersPanel.addEventListener("click", (e) => {
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
    setStyleSrc([...DEFAULT_STYLE_SRC]);
    fillStyleFacet([]);
    el.q.value = "";
    el.hasWorks.checked = false;
    el.labelSrc.value = "all";
    state.selectedId = null;
    state.detailForce = "";
    renderDetail();
    renderRows();
  });

  el.resetDefaults.addEventListener("click", () => {
    for (const f of FACETS) {
      if (f.key === "style") continue;
      setFacet(f, DEFAULTS[f.key] || []);
    }
    setPdFacet(DEFAULTS.pd);
    setStyleSrc([...DEFAULT_STYLE_SRC]);
    fillStyleFacet([]);
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

function computeStyleSrcCoverage(composers) {
  const cov = { wikidata: 0, imslp: 0, llm: 0 };
  for (const c of composers) {
    const by = c._stylesBySrc;
    if (by.wikidata.length) cov.wikidata += 1;
    if (by.imslp.length) cov.imslp += 1;
    if (by.llm.length) cov.llm += 1;
  }
  return cov;
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
  const allStyleValues = new Set();
  composers.forEach((c) => {
    c._hay = [c.name, c.sort, ...(c.aliases || [])].join(" ").toLowerCase();
    for (const f of FACETS) {
      if (f.work || f.key === "style") continue;
      if (f.list && !Array.isArray(c[f.field])) c[f.field] = [];
    }
    if (!Array.isArray(c.styles)) c.styles = [];
    c._stylesBySrc = composerStylesBySource(c, worksByComposer[c.id] || []);
    for (const src of STYLE_SRC_ORDER) {
      for (const v of c._stylesBySrc[src]) allStyleValues.add(v);
    }
  });
  state.composers = composers;
  state.worksByComposer = worksByComposer;
  state.styleVocab = buildStyleVocab(allStyleValues);
  state.styleSrcCoverage = computeStyleSrcCoverage(composers);

  if (manifest.disclaimer) el.disclaimer.textContent = manifest.disclaimer;
  el.dumpLabel.textContent = manifest.dump_label || `dump ${manifest.dump_id}`;
  if (el.euAsOf) el.euAsOf.textContent = `as of ${state.currentYear}`;
  applyPageviewsLabel(manifest.pageviews_window_label || "");

  const facets = manifest.facets || {};
  const allowed = {
    scope: new Set(facets.scope_class || []),
    force: new Set(facets.force_family || []),
    cit: new Set(facets.citizenship_iso || []),
    style: new Set(state.styleVocab),
    ids: new Set(composers.map((c) => c.id)),
  };

  const fromUrl = decodeUrlState(location.search, allowed);
  const init = fromUrl.useDefaults ? { ...DEFAULTS, styleSrc: [...DEFAULT_STYLE_SRC] } : fromUrl;

  fillStyleSrc(init.styleSrc || [...DEFAULT_STYLE_SRC]);

  for (const f of FACETS) {
    if (f.key === "style") {
      const preferred = init.style || [];
      fillStyleFacet(preferred.filter((v) => state.styleVocab.includes(v)));
      continue;
    }
    const values = facetValuesFromManifest(f, facets);
    const preferred = init[f.key] || [];
    fillFacet(
      f,
      values,
      preferred.filter((v) => values.includes(v))
    );
  }
  fillPdFacet((init.pd || []).filter((v) => PD_VALUES.has(v)));

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
