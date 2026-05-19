/* mtg-oracle dashboard GUI */
const $ = (id) => document.getElementById(id);

const form = $("f");
const statusEl = $("status");
const goBtn = $("go");
const resultsWrap = $("results-wrap");

const MODES = ["upgrades", "radar", "playability"];
const TAB_LABELS = { upgrades: "Upgrades & Cuts", radar: "Deck Radar", playability: "Playability" };

// Primary modes (radio-select). Radar always runs alongside.
const PRIMARY_MODES = ["upgrades", "playability"];

function getPrimaryMode() {
  const r = document.querySelector('input[name="primary_mode"]:checked');
  return r ? r.value : "upgrades";
}

/* ---------------- tab bar ---------------- */

// Per-mode tab state: "idle" | "loading" | "ready" | "error"
const tabState = { upgrades: "idle", radar: "idle", playability: "idle" };
let activeTab = null;

function tabButton(mode) { return document.querySelector(`.tab[data-tab="${mode}"]`); }
function tabPanel(mode) { return $("result_" + mode); }

function setTabState(mode, state) {
  tabState[mode] = state;
  const btn = tabButton(mode);
  const status = $("tabstatus_" + mode);
  if (!btn) return;
  btn.classList.remove("is-loading", "is-ready", "is-error");
  if (state === "idle") {
    btn.disabled = true;
    btn.setAttribute("aria-selected", "false");
    if (status) status.textContent = "";
  } else if (state === "loading") {
    btn.disabled = true;
    btn.classList.add("is-loading");
    if (status) status.textContent = "…";
  } else if (state === "ready") {
    btn.disabled = false;
    btn.classList.add("is-ready");
    if (status) status.textContent = "";
  } else if (state === "error") {
    btn.disabled = false;
    btn.classList.add("is-error");
    if (status) status.textContent = "!";
  }
}

// Charts created while their panel was hidden have canvas width=0; resize them
// after the panel becomes visible. Renderers register charts here.
const panelCharts = { upgrades: [], radar: [], playability: [] };
function registerChart(mode, chart) {
  if (panelCharts[mode]) panelCharts[mode].push(chart);
}
function clearPanelCharts(mode) {
  if (!panelCharts[mode]) return;
  panelCharts[mode].forEach((c) => { try { c.destroy(); } catch (_) {} });
  panelCharts[mode] = [];
}

function activateTab(mode) {
  if (tabState[mode] === "idle") return;
  activeTab = mode;
  MODES.forEach((m) => {
    const btn = tabButton(m);
    const panel = tabPanel(m);
    const isActive = (m === mode);
    if (btn) {
      btn.classList.toggle("is-active", isActive);
      btn.setAttribute("aria-selected", isActive ? "true" : "false");
    }
    if (panel) panel.hidden = !isActive;
  });
  // Resize any charts whose panel just became visible. Wrap in rAF so the
  // browser has applied the hidden=false style before Chart.js measures.
  requestAnimationFrame(() => {
    (panelCharts[mode] || []).forEach((c) => { try { c.resize(); } catch (_) {} });
  });
}

function resetTabs(runningModes) {
  activeTab = null;
  MODES.forEach((m) => {
    clearPanelCharts(m);
    const panel = tabPanel(m);
    if (panel) panel.hidden = true;
    if (runningModes && runningModes.includes(m)) {
      setTabState(m, "loading");
    } else {
      setTabState(m, "idle");
    }
    const btn = tabButton(m);
    if (btn) {
      btn.classList.remove("is-active");
      // Hide tab button entirely for modes that aren't running this turn.
      // Radar always runs, so its tab is always shown.
      btn.hidden = !runningModes || !runningModes.includes(m);
    }
  });
}

function markTabReady(mode, isError) {
  setTabState(mode, isError ? "error" : "ready");
  // First completed tab auto-activates
  if (!activeTab) activateTab(mode);
}

document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    const mode = btn.dataset.tab;
    if (tabState[mode] === "idle" || tabState[mode] === "loading") return;
    activateTab(mode);
  });
});

// Persisted inputs
const LS_KEY = "mtg-oracle.v2";
try {
  const saved = JSON.parse(localStorage.getItem(LS_KEY) || "{}");
  if (saved.user) $("user").value = saved.user;
  if (saved.scoring) $("scoring").value = saved.scoring;
  if (saved.replacement_mode) $("replacement_mode").value = saved.replacement_mode;
  if (saved.source) $("source").value = saved.source;
  if (saved.archidekt_user) $("archidekt_user").value = saved.archidekt_user;
  if (saved.decks) $("decks").value = saved.decks;
  if (saved.extra_decks) $("extra_decks").value = saved.extra_decks;
  if (saved.paste) $("paste").value = saved.paste;
  if (saved.simulations) $("simulations").value = saved.simulations;
  if (saved.turns_seen) $("turns_seen").value = saved.turns_seen;
  if (saved.primary_mode && PRIMARY_MODES.includes(saved.primary_mode)) {
    const r = document.querySelector(`input[name="primary_mode"][value="${saved.primary_mode}"]`);
    if (r) r.checked = true;
  }
} catch (_) {}

function updateArchidektFieldVisibility() {
  const src = $("source").value;
  $("archidekt-user-label").style.display = (src === "both") ? "flex" : "none";
}
updateArchidektFieldVisibility();
$("source").addEventListener("change", updateArchidektFieldVisibility);

function syncModePanels() {
  const primary = getPrimaryMode();
  $("panel_upgrades").hidden = primary !== "upgrades";
  $("panel_playability").hidden = primary !== "playability";
}
syncModePanels();
document.querySelectorAll('input[name="primary_mode"]').forEach((r) => {
  r.addEventListener("change", () => { syncModePanels(); persist(); });
});

function persist() {
  localStorage.setItem(LS_KEY, JSON.stringify({
    user: $("user").value,
    scoring: $("scoring").value,
    replacement_mode: $("replacement_mode").value,
    source: $("source").value,
    archidekt_user: $("archidekt_user").value,
    decks: $("decks").value,
    extra_decks: $("extra_decks").value,
    paste: $("paste").value,
    simulations: $("simulations").value,
    turns_seen: $("turns_seen").value,
    primary_mode: getPrimaryMode(),
  }));
}
const persistedInputs = ["user", "scoring", "replacement_mode", "source", "archidekt_user", "decks", "extra_decks", "paste", "simulations", "turns_seen"];
persistedInputs.forEach((id) => $(id).addEventListener("input", persist));

$("clear-paste").addEventListener("click", () => {
  $("paste").value = "";
  persist();
  $("paste").focus();
});

$("clear-all").addEventListener("click", () => {
  if (!confirm("Clear every field?")) return;
  ["user", "archidekt_user", "decks", "extra_decks", "paste"].forEach((id) => { $(id).value = ""; });
  $("source").value = "moxfield";
  $("scoring").value = "hybrid";
  $("replacement_mode").value = "auto";
  $("simulations").value = "10000";
  $("turns_seen").value = "3";
  const upgradesRadio = document.querySelector('input[name="primary_mode"][value="upgrades"]');
  if (upgradesRadio) upgradesRadio.checked = true;
  // Also clear any picked decks
  pickedDeckKeys.clear();
  $("deck-picker-body").hidden = true;
  $("deck-picker-status").textContent = "";
  updateArchidektFieldVisibility();
  syncModePanels();
  persist();
  resultsWrap.hidden = true;
  resetTabs();
  statusEl.className = "";
  statusEl.textContent = "";
  $("user").focus();
});

/* ---------------- deck picker ---------------- */

const DECKS_INDEX_LS_PREFIX = "mtg-oracle.deck-index.";
const DECKS_INDEX_TTL_MS = 60 * 60 * 1000; // 1 hour

// Set of "source:publicId" keys the user has checked.
const pickedDeckKeys = new Set();
// Currently displayed list of {source, publicId, name, colors, url}
let currentDeckIndex = [];

function deckKey(d) { return `${d.source}:${d.publicId}`; }

function deckIndexCacheKey() {
  const user = $("user").value.trim();
  const source = $("source").value;
  const archi = $("archidekt_user").value.trim();
  return `${DECKS_INDEX_LS_PREFIX}${source}::${user}::${archi}`;
}

function readDeckIndexCache() {
  try {
    const raw = localStorage.getItem(deckIndexCacheKey());
    if (!raw) return null;
    const obj = JSON.parse(raw);
    if (!obj || !Array.isArray(obj.decks)) return null;
    if (typeof obj.ts !== "number") return null;
    if (Date.now() - obj.ts > DECKS_INDEX_TTL_MS) return null;
    return obj;
  } catch (_) { return null; }
}

function writeDeckIndexCache(decks) {
  try {
    localStorage.setItem(deckIndexCacheKey(), JSON.stringify({ ts: Date.now(), decks }));
  } catch (_) {}
}

function fmtCacheAge(ts) {
  const min = Math.floor((Date.now() - ts) / 60000);
  if (min < 1) return "just now";
  if (min === 1) return "1 min ago";
  if (min < 60) return `${min} min ago`;
  const h = Math.floor(min / 60);
  return h === 1 ? "1 hour ago" : `${h} hours ago`;
}

function renderDeckPicker(decks, opts = {}) {
  currentDeckIndex = decks;
  const body = $("deck-picker-body");
  const list = $("deck-pick-list");
  const cacheHint = $("deck-pick-cache-hint");
  list.innerHTML = "";

  if (!decks.length) {
    body.hidden = false;
    list.innerHTML = '<li class="hint" style="grid-column:1/-1;color:var(--muted)">No decks found for that username.</li>';
    cacheHint.textContent = "";
    return;
  }

  decks.forEach((d) => {
    const key = deckKey(d);
    const li = document.createElement("li");
    const label = document.createElement("label");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.dataset.key = key;
    cb.checked = pickedDeckKeys.has(key);
    cb.addEventListener("change", () => {
      if (cb.checked) pickedDeckKeys.add(key); else pickedDeckKeys.delete(key);
      updateDeckPickerStatus();
    });
    const nameSpan = document.createElement("span");
    nameSpan.className = "deck-pick-name";
    nameSpan.textContent = d.name;
    nameSpan.title = d.name;
    const metaSpan = document.createElement("span");
    metaSpan.className = "deck-pick-meta";
    metaSpan.innerHTML = `<span class="deck-pick-src">${escapeHtml(d.source[0].toUpperCase())}</span>${escapeHtml(d.colors || "C")}`;
    label.appendChild(cb);
    label.appendChild(nameSpan);
    label.appendChild(metaSpan);
    li.appendChild(label);
    list.appendChild(li);
  });

  body.hidden = false;
  if (opts.cacheTs) cacheHint.textContent = `cached · ${fmtCacheAge(opts.cacheTs)}`;
  else cacheHint.textContent = "";
  updateDeckPickerStatus();
}

function updateDeckPickerStatus() {
  const total = currentDeckIndex.length;
  const picked = currentDeckIndex.filter((d) => pickedDeckKeys.has(deckKey(d))).length;
  $("deck-picker-status").textContent = total
    ? `${picked}/${total} selected`
    : "";
}

async function loadDecks({ force = false } = {}) {
  const user = $("user").value.trim();
  if (!user) {
    $("deck-picker-status").className = "err";
    $("deck-picker-status").textContent = "Enter a username first.";
    setTimeout(() => { $("deck-picker-status").className = "hint"; $("deck-picker-status").textContent = ""; }, 2500);
    return;
  }

  if (!force) {
    const cached = readDeckIndexCache();
    if (cached) {
      renderDeckPicker(cached.decks, { cacheTs: cached.ts });
      return;
    }
  }

  const btn = $("load-decks");
  const status = $("deck-picker-status");
  btn.disabled = true;
  status.className = "hint";
  status.textContent = "Loading\u2026";
  try {
    const res = await fetch("/api/decks_index", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user,
        source: $("source").value,
        archidekt_user: $("archidekt_user").value.trim(),
      }),
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "Failed to load decks");
    writeDeckIndexCache(data.decks);
    renderDeckPicker(data.decks);
  } catch (err) {
    status.className = "err";
    status.textContent = err.message || String(err);
  } finally {
    btn.disabled = false;
  }
}

$("load-decks").addEventListener("click", () => loadDecks());
$("deck-pick-refresh").addEventListener("click", () => loadDecks({ force: true }));
$("deck-pick-all").addEventListener("click", () => {
  currentDeckIndex.forEach((d) => pickedDeckKeys.add(deckKey(d)));
  document.querySelectorAll("#deck-pick-list input[type=checkbox]").forEach((cb) => { cb.checked = true; });
  updateDeckPickerStatus();
});
$("deck-pick-none").addEventListener("click", () => {
  pickedDeckKeys.clear();
  document.querySelectorAll("#deck-pick-list input[type=checkbox]").forEach((cb) => { cb.checked = false; });
  updateDeckPickerStatus();
});

// Auto-render any cached picker on page load (if user is filled in)
if ($("user").value.trim()) {
  const cached = readDeckIndexCache();
  if (cached) renderDeckPicker(cached.decks, { cacheTs: cached.ts });
}

/* ---------------- result renderers ---------------- */

function renderUpgrades(data) {
  const meta = $("meta_upgrades");
  const report = $("report_upgrades");
  const extraBadge = (data.extra_decks_count && data.extra_decks_count > 0)
    ? `<span>extra <b>${data.extra_decks_count}</b></span>` : "";
  meta.innerHTML =
    `<span><b>${data.purchases}</b> cards</span>` +
    `<span><b>${data.decks_analyzed}</b> decks</span>` +
    extraBadge +
    `<span>source <b>${data.source || "moxfield"}</b></span>` +
    `<span>scoring <b>${data.scoring}</b></span>` +
    `<span>replacements <b>${data.replacement_mode || "auto"}</b></span>` +
    `<span><b>${data.elapsed_sec}s</b></span>`;

  if (typeof marked === "undefined") {
    report.innerHTML = "";
    const pre = document.createElement("pre");
    pre.style.whiteSpace = "pre-wrap";
    pre.textContent = data.markdown;
    report.appendChild(pre);
  } else {
    report.innerHTML = marked.parse(data.markdown, { gfm: true, breaks: false });
  }
  report.querySelectorAll("a[href]").forEach((a) => {
    if (a.href.includes("moxfield.com") || a.href.includes("archidekt.com")) {
      a.target = "_blank";
      a.rel = "noopener noreferrer";
    }
  });
  markTabReady("upgrades");
}

// Pick the highest-scoring axis from each family. Tie-break: broader signal
// (more distinct cards matching) wins; then alphabetical axis id for determinism.
function pickFamilyRepresentatives(family, axisScores, axisMatchCounts) {
  const candidates = family.axes.map((a) => ({
    id: a.id,
    score: axisScores[a.id] || 0,
    matches: (axisMatchCounts && axisMatchCounts[a.id]) || 0,
  }));
  candidates.sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score;
    if (b.matches !== a.matches) return b.matches - a.matches;
    return a.id < b.id ? -1 : 1;
  });
  return candidates[0];
}

function renderRadar(data) {
  const meta = $("meta_radar");
  const panels = $("radar_panels");
  panels.innerHTML = "";
  const familyCount = data.families.length;
  meta.innerHTML =
    `<span><b>${data.decks.length}</b> decks</span>` +
    `<span>top axis per family</span>` +
    `<span>${data.axes_total} axes · ${familyCount} families</span>` +
    `<span><b>${data.elapsed_sec}s</b></span>`;

  // Build a quick lookup from axis_id -> family object
  const axisToFamily = {};
  data.families.forEach((f) => {
    f.axes.forEach((a) => { axisToFamily[a.id] = f; });
  });

  data.decks.forEach((d, idx) => {
    // Pick one representative axis per family
    const reps = data.families.map((f) => ({
      family: f,
      pick: pickFamilyRepresentatives(f, d.axis_scores, d.axis_match_counts),
    }));
    const activeReps = reps.filter((r) => r.pick.score > 0);
    const totalSum = Object.values(d.axis_scores).reduce((s, v) => s + v, 0);
    const repSum = activeReps.reduce((s, r) => s + r.pick.score, 0);
    const concentration = totalSum > 0 ? Math.round((repSum / totalSum) * 100) : 0;

    const wrap = document.createElement("div");
    wrap.className = "deck-panel";
    wrap.innerHTML = `
      <h3>${escapeHtml(d.name)} <small class="hint">${d.cards} cards · ${d.color_identity || "C"} · ${activeReps.length}/${familyCount} families active · ${concentration}% of signal in top axes</small></h3>
      <div class="radar-grid" id="radar_${idx}_grid">
        <div class="radar-cell radar-headline">
          <div class="radar-cell-hint">Top axis per family · click a point to drill into that family</div>
          <canvas id="radar_${idx}_top"></canvas>
        </div>
        <div class="radar-cell" id="radar_${idx}_drill_wrap" hidden>
          <h4 id="radar_${idx}_drill_title"></h4>
          <div class="radar-cell-hint" id="radar_${idx}_drill_hint"></div>
          <canvas id="radar_${idx}_drill"></canvas>
        </div>
      </div>
    `;
    panels.appendChild(wrap);

    if (activeReps.length === 0) {
      const canvas = $(`radar_${idx}_top`);
      const ctx = canvas.getContext("2d");
      ctx.fillStyle = "#6c7585";
      ctx.font = "14px ui-sans-serif, system-ui, sans-serif";
      ctx.textAlign = "center";
      ctx.fillText("No axes matched — deck oracle text may be empty.", canvas.width / 2, 40);
      return;
    }

    // Plot all 12 families always; dim labels for zero-score families.
    // Label is just the family name — the axis name shows in the tooltip on hover,
    // which keeps the radial readable at any container width.
    const labels = reps.map((r) => r.family.label);
    const values = reps.map((r) => r.pick.score);
    const labelColors = reps.map((r) => (r.pick.score > 0 ? "#e6e9ef" : "#5a6378"));
    const pointColors = reps.map((r) => (r.pick.score > 0 ? "#7c5cff" : "rgba(124, 92, 255, 0.25)"));
    const pointSizes = reps.map((r) => (r.pick.score > 0 ? 5 : 2));
    const maxVal = Math.max(1, ...values);

    const headlineChart = new Chart($(`radar_${idx}_top`), {
      type: "radar",
      data: {
        labels,
        datasets: [{
          label: d.name,
          data: values,
          backgroundColor: "rgba(124, 92, 255, 0.22)",
          borderColor: "#7c5cff",
          pointBackgroundColor: pointColors,
          pointRadius: pointSizes,
          pointHoverRadius: pointSizes.map((s) => s + 2),
          borderWidth: 2,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        layout: { padding: 28 },
        onClick: (evt, els) => {
          if (!els.length) return;
          const ix = els[0].index;
          const rep = reps[ix];
          if (rep && rep.family) showDrill(idx, rep.family, d.axis_scores, rep.pick.id);
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (c) => {
                const rep = reps[c.dataIndex];
                return `${rep.family.label}: ${c.formattedValue}`;
              },
              afterLabel: (c) => {
                const rep = reps[c.dataIndex];
                return rep.pick.score > 0
                  ? `top axis: ${prettyAxis(rep.pick.id)}`
                  : "no axis active in this family";
              },
            },
          },
        },
        scales: {
          r: {
            angleLines: { color: "#262b3a" },
            grid: { color: "#262b3a" },
            pointLabels: {
              color: labelColors,
              font: { size: 12, weight: "500" },
              padding: 8,
            },
            ticks: { color: "#6c7585", backdropColor: "transparent", maxTicksLimit: 4 },
            suggestedMin: 0,
            suggestedMax: maxVal,
          },
        },
      },
    });
    registerChart("radar", headlineChart);

    function showDrill(panelIdx, family, axisScores, clickedAxisId) {
      const drillWrap = $(`radar_${panelIdx}_drill_wrap`);
      const drillTitle = $(`radar_${panelIdx}_drill_title`);
      const drillHint = $(`radar_${panelIdx}_drill_hint`);
      const drillCanvas = $(`radar_${panelIdx}_drill`);
      drillTitle.textContent = family.label;
      drillHint.textContent = `${family.axes.length} axes in this family · low-scoring axes dimmed`;

      const dLabels = family.axes.map((a) => prettyAxis(a.id));
      const dValues = family.axes.map((a) => axisScores[a.id] || 0);
      const dMax = Math.max(1, ...dValues);
      // Highlight the clicked axis with brighter color and bigger point
      const pointColors = family.axes.map((a) => a.id === clickedAxisId ? "#7c5cff" : "#4ad6c0");
      const pointSizes = family.axes.map((a) => a.id === clickedAxisId ? 7 : 4);
      // Dim labels for axes that scored 0
      const labelColors = family.axes.map((a) => (axisScores[a.id] || 0) > 0 ? "#e6e9ef" : "#5a6378");

      if (drillCanvas._chart) drillCanvas._chart.destroy();
      const drillChart = new Chart(drillCanvas, {
        type: "radar",
        data: {
          labels: dLabels,
          datasets: [{
            label: family.label,
            data: dValues,
            backgroundColor: "rgba(74, 214, 192, 0.18)",
            borderColor: "#4ad6c0",
            pointBackgroundColor: pointColors,
            pointRadius: pointSizes,
            pointHoverRadius: pointSizes.map((s) => s + 2),
            borderWidth: 2,
          }],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { display: false },
            tooltip: { callbacks: { label: (c) => `${c.label}: ${c.formattedValue}` } },
          },
          scales: {
            r: {
              angleLines: { color: "#262b3a" },
              grid: { color: "#262b3a" },
              pointLabels: {
                color: labelColors,
                font: { size: 11 },
              },
              ticks: { color: "#6c7585", backdropColor: "transparent", maxTicksLimit: 4 },
              suggestedMin: 0,
              suggestedMax: dMax,
            },
          },
        },
      });
      drillCanvas._chart = drillChart;
      registerChart("radar", drillChart);
      drillWrap.hidden = false;
      const grid = $(`radar_${panelIdx}_grid`);
      if (grid) grid.classList.add("has-drill");
      // Resize the headline so it re-fits into the now-narrower column.
      requestAnimationFrame(() => { try { headlineChart.resize(); } catch (_) {} });
      drillWrap.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  });

  markTabReady("radar");
}

// Render axis IDs as readable labels: "attacks_trigger" -> "attacks trigger"
function prettyAxis(id) {
  return id.replace(/_/g, " ");
}

/* ---------------- playability rendering (ported from mtg-nomulli) ---------------- */

const PLAY_BADGE_CLASS = ["t1", "t2", "t3", "t4", "t5", "t6"];
const PLAY_LAND_WORDS = ["island", "plains", "forest", "mountain", "swamp", "tower", "pool", "haven", "reach",
  "land", "gate", "grove", "hearth", "moor", "strand", "heath", "mine", "vista", "mesa", "lagoon",
  "headquarters", "fortress", "harbor", "wastes", "beacon", "coast", "expanse"];
const PLAY_MANA_WORDS = ["signet", "talisman", "sol ring", "mox", "bird", "mystic", "pilgrim", "elves",
  "bloom tender", "faeburrow", "caravan", "myr", "stone", "vault", "crypt", "dynamo"];

function playChipClass(name) {
  const n = String(name || "").toLowerCase();
  if (PLAY_LAND_WORDS.some((w) => n.includes(w))) return "is-land";
  if (PLAY_MANA_WORDS.some((w) => n.includes(w))) return "is-mana";
  return "";
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
}

function renderManaSources(detail) {
  const wrap = el("span", "mana-sources-wrap");
  detail.forEach((src, i) => {
    if (i > 0) wrap.appendChild(el("span", "mana-sep", "+"));
    const opts = src.produced_mana || [];
    if (opts.length === 1) {
      wrap.appendChild(el("span", "mana-cost", `{${opts[0]}}`));
    } else {
      const pill = el("span", "mana-xor");
      opts.forEach((c, ci) => {
        if (ci > 0) pill.appendChild(el("span", "xor-sep", "/"));
        pill.appendChild(el("span", "xor-pip" + (c === src.assigned ? " xor-assigned" : ""), c));
      });
      wrap.appendChild(pill);
    }
  });
  return wrap;
}

function renderPlayTurns(turnsList, turns) {
  turns.forEach((t, idx) => {
    const node = el("div", "turn-node");
    if (idx > 0) node.appendChild(el("div", "turn-connector"));
    const row = el("div", "turn-row");
    const badgeClass = PLAY_BADGE_CLASS[idx] || PLAY_BADGE_CLASS[PLAY_BADGE_CLASS.length - 1];
    row.appendChild(el("div", `turn-badge ${badgeClass}`, `T${idx + 1}`));
    const detail = el("div", "turn-detail");
    if (!t.landPlayed && !t.cast) {
      detail.appendChild(el("div", "no-play", "No land, no play"));
    } else {
      if (t.landPlayed) {
        const r = el("div", "detail-row");
        r.appendChild(el("span", "detail-label", "Land"));
        r.appendChild(el("span", "detail-value", t.landPlayed));
        if (t.landTapped) r.appendChild(el("span", "tapped-badge", "Tapped"));
        detail.appendChild(r);
      }
      if (t.manaSourcesDetail && t.manaSourcesDetail.length > 0) {
        const r = el("div", "detail-row");
        r.appendChild(el("span", "detail-label", "Mana"));
        r.appendChild(renderManaSources(t.manaSourcesDetail));
        detail.appendChild(r);
      } else if (t.manaPool && Object.keys(t.manaPool).length > 0) {
        const poolStr = Object.entries(t.manaPool).filter(([, v]) => v > 0).map(([k, v]) => `${v}{${k}}`).join(" ");
        const r = el("div", "detail-row");
        r.appendChild(el("span", "detail-label", "Mana"));
        r.appendChild(el("span", "mana-cost", poolStr));
        detail.appendChild(r);
      }
      if (t.cast) {
        const r = el("div", "detail-row");
        r.appendChild(el("span", "detail-label", "Cast"));
        r.appendChild(el("span", "cast-value", t.cast.name));
        if (t.cast.manaCost) r.appendChild(el("span", "mana-cost", t.cast.manaCost));
        r.appendChild(el("span", "mv-badge", `MV ${t.cast.mv}`));
        detail.appendChild(r);
      } else if (t.landPlayed) {
        const r = el("div", "detail-row");
        r.appendChild(el("span", "detail-label", "Cast"));
        r.appendChild(el("span", "no-play", "No play"));
        detail.appendChild(r);
      }
    }
    row.appendChild(detail);
    node.appendChild(row);
    turnsList.appendChild(node);
  });
}

function renderPlayTrees(container, sequences) {
  container.innerHTML = "";
  if (!sequences || sequences.length === 0) {
    container.appendChild(el("div", "empty-trees", "No example sequences captured."));
    return;
  }
  const scroll = el("div", "trees-scroll");
  sequences.forEach((seq, i) => {
    const card = el("div", "tree-card");
    const strip = el("div", "hand-strip");
    strip.appendChild(el("div", "hand-strip-label", `Hand ${i + 1} \u2014 Opening 7`));
    const chips = el("div", "hand-chips");
    (seq.openingHand || []).forEach((name) => chips.appendChild(el("span", `chip ${playChipClass(name)}`, name)));
    strip.appendChild(chips);
    card.appendChild(strip);
    const turnsList = el("div", "turns-list");
    renderPlayTurns(turnsList, seq.turns || []);
    card.appendChild(turnsList);
    scroll.appendChild(card);
  });
  container.appendChild(scroll);
}

function renderPlayability(data) {
  const meta = $("meta_playability");
  const panels = $("playability_panels");
  panels.innerHTML = "";
  meta.innerHTML =
    `<span><b>${data.decks.length}</b> decks</span>` +
    `<span>sims/deck <b>${data.simulations.toLocaleString()}</b></span>` +
    `<span>turns <b>${data.turns_seen}</b></span>` +
    `<span><b>${data.elapsed_sec}s</b></span>`;

  data.decks.forEach((d) => {
    const result = d.result || {};
    const r = result.results || {};
    const wrap = el("div", "play-deck");

    // Deck heading
    const head = el("h3", "play-deck-heading");
    head.appendChild(document.createTextNode(d.name));
    const sub = el("small", "hint");
    sub.textContent = ` ${result.deckSize || 0} cards \u00b7 ${result.colorIdentity || "C"}`;
    head.appendChild(sub);
    wrap.appendChild(head);

    // 4 stat cards (mirroring mtg-nomulli)
    const stats = el("div", "play-stats-grid");
    const statCards = [
      { eyebrow: "Playable hands", value: `${(r.playableHandsPct ?? 0).toFixed(1)}%`, note: `${result.deckSize || 0} resolved cards.` },
      { eyebrow: "Curve rate", value: `${(r.onOrAboveCurveThroughTurn3Pct ?? 0).toFixed(1)}%`, note: `On or above curve through T${data.turns_seen}.` },
      { eyebrow: "Has a play", value: `${(r.hasPlayableSpellByTurn3Pct ?? 0).toFixed(1)}%`, note: `Castable by T${data.turns_seen}.` },
      { eyebrow: "Average MV", value: Number(result.averageNonlandManaValue || 0).toFixed(2), note: "Across nonlands." },
    ];
    statCards.forEach((s) => {
      const card = el("div", "play-stat");
      card.appendChild(el("div", "eyebrow", s.eyebrow));
      card.appendChild(el("div", "value", s.value));
      card.appendChild(el("div", "note", s.note));
      stats.appendChild(card);
    });
    wrap.appendChild(stats);

    // Profile table
    const missing = result.missing || [];
    const profileRows = [
      ["Deck size", result.deckSize ?? "\u2014"],
      ["Color identity", result.colorIdentity || "C"],
      ["Lands", result.lands ?? "\u2014"],
      ["Tapped lands", result.tappedLands ?? "\u2014"],
      ["Mana permanents", result.manaPermanents ?? "\u2014"],
      ["Simulations", (result.simulations ?? data.simulations).toLocaleString()],
      ["Missing cards", missing.length ? missing.slice(0, 6).join(", ") : "None"],
    ];
    const table = el("table", "play-profile");
    const thead = el("thead");
    const trh = el("tr");
    trh.appendChild(el("th", null, "Metric"));
    trh.appendChild(el("th", null, "Value"));
    thead.appendChild(trh);
    table.appendChild(thead);
    const tbody = el("tbody");
    profileRows.forEach(([k, v]) => {
      const tr = el("tr");
      tr.appendChild(el("td", null, k));
      tr.appendChild(el("td", null, String(v)));
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    wrap.appendChild(table);

    // Example sequences (tree-cards)
    const trees = el("div", "trees-section");
    trees.appendChild(el("h4", "trees-heading", "Example Opening Sequences"));
    trees.appendChild(el("div", "trees-sub", "Sample playable hands \u2014 opening 7, then what happens each turn."));
    const treesContainer = el("div", "trees-container");
    renderPlayTrees(treesContainer, result.exampleSequences || []);
    trees.appendChild(treesContainer);
    wrap.appendChild(trees);

    panels.appendChild(wrap);
  });

  markTabReady("playability");
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

/* ---------------- submit ---------------- */

form.addEventListener("submit", async (e) => {
  e.preventDefault();

  const primary = getPrimaryMode();
  // Radar always runs alongside the primary mode.
  const modes = [primary, "radar"];

  // Build selected_decks array from the picker (source+publicId pairs)
  const selectedDecks = currentDeckIndex
    .filter((d) => pickedDeckKeys.has(deckKey(d)))
    .map((d) => ({ source: d.source, publicId: d.publicId }));

  const shared = {
    user: $("user").value.trim(),
    source: $("source").value,
    archidekt_user: $("archidekt_user").value.trim(),
    decks: $("decks").value.trim() ? $("decks").value.split(",").map((s) => s.trim()).filter(Boolean) : [],
    extra_decks: $("extra_decks").value.trim(),
    selected_decks: selectedDecks,
  };

  const paste = $("paste").value.trim();
  // Playability paste-overrides-account: if a paste is provided in playability
  // mode, we skip account-based deck resolution.
  const playabilityUsesPaste = (primary === "playability") && !!paste;

  // Validation: we need *some* deck source unless playability is using paste.
  const hasAnyDeckSource = shared.user || shared.extra_decks || selectedDecks.length > 0;
  if (!hasAnyDeckSource && !playabilityUsesPaste) {
    statusEl.className = "err";
    statusEl.textContent = "Provide a username, pick decks, paste a list, or add an extra deck URL.";
    return;
  }

  // Upgrades requires a card list
  if (primary === "upgrades" && !paste) {
    statusEl.className = "err";
    statusEl.textContent = "Upgrades & Cuts needs a card list. Paste one or switch modes.";
    return;
  }

  // If playability is using the paste-only path, radar still needs a deck
  // source. If none is present, gracefully degrade by skipping the radar call.
  const skipRadar = playabilityUsesPaste && !hasAnyDeckSource;
  const runningModes = skipRadar ? [primary] : modes;

  goBtn.disabled = true;
  statusEl.className = "";
  const modeLabel = (primary === "upgrades" ? "upgrades" : "playability")
    + (skipRadar ? "" : " + radar");
  statusEl.textContent = `Running ${modeLabel}\u2026`;
  resultsWrap.hidden = false;
  resetTabs(runningModes);

  const started = performance.now();
  const tasks = [];

  if (primary === "upgrades") {
    tasks.push(
      fetch("/api/match", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...shared,
          paste,
          scoring: $("scoring").value,
          replacement_mode: $("replacement_mode").value,
        }),
      }).then((r) => r.json()).then((data) => {
        if (!data.ok) throw new Error(`Upgrades: ${data.error || "failed"}`);
        renderUpgrades(data);
      }).catch((err) => {
        const meta = $("meta_upgrades");
        meta.innerHTML = `<span class="err">${escapeHtml(err.message)}</span>`;
        markTabReady("upgrades", true);
      })
    );
  }

  if (primary === "playability") {
    tasks.push(
      fetch("/api/playability_decks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...shared,
          paste: playabilityUsesPaste ? paste : "",
          simulations: parseInt($("simulations").value, 10),
          turns_seen: parseInt($("turns_seen").value, 10),
        }),
      }).then((r) => r.json()).then((data) => {
        if (!data.ok) throw new Error(`Playability: ${data.error || "failed"}`);
        renderPlayability(data);
      }).catch((err) => {
        const meta = $("meta_playability");
        meta.innerHTML = `<span class="err">${escapeHtml(err.message)}</span>`;
        markTabReady("playability", true);
      })
    );
  }

  if (!skipRadar) {
    tasks.push(
      fetch("/api/radar", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(shared),
      }).then((r) => r.json()).then((data) => {
        if (!data.ok) throw new Error(`Radar: ${data.error || "failed"}`);
        renderRadar(data);
      }).catch((err) => {
        const meta = $("meta_radar");
        meta.innerHTML = `<span class="err">${escapeHtml(err.message)}</span>`;
        markTabReady("radar", true);
      })
    );
  }

  try {
    await Promise.all(tasks);
    statusEl.className = "ok";
    statusEl.textContent = `Done in ${((performance.now() - started) / 1000).toFixed(1)}s`;
  } finally {
    goBtn.disabled = false;
  }
});
