/* mtg-oracle dashboard GUI */
const $ = (id) => document.getElementById(id);

const form = $("f");
const statusEl = $("status");
const goBtn = $("go");
const resultsWrap = $("results-wrap");

const MODES = ["upgrades", "radar", "playability"];

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
  if (saved.modes) {
    MODES.forEach((m) => { $("mode_" + m).checked = !!saved.modes[m]; });
  }
} catch (_) {}

function updateArchidektFieldVisibility() {
  const src = $("source").value;
  $("archidekt-user-label").style.display = (src === "both") ? "flex" : "none";
}
updateArchidektFieldVisibility();
$("source").addEventListener("change", updateArchidektFieldVisibility);

function syncModePanels() {
  $("panel_upgrades").hidden = !$("mode_upgrades").checked;
  $("panel_playability").hidden = !$("mode_playability").checked;
  // radar has no options panel yet
}
syncModePanels();
MODES.forEach((m) => $("mode_" + m).addEventListener("change", () => { syncModePanels(); persist(); }));

function persist() {
  const modes = {};
  MODES.forEach((m) => { modes[m] = $("mode_" + m).checked; });
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
    modes,
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
  $("mode_upgrades").checked = true;
  $("mode_radar").checked = false;
  $("mode_playability").checked = false;
  updateArchidektFieldVisibility();
  syncModePanels();
  persist();
  resultsWrap.hidden = true;
  MODES.forEach((m) => { $("result_" + m).hidden = true; });
  statusEl.className = "";
  statusEl.textContent = "";
  $("user").focus();
});

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
  $("result_upgrades").hidden = false;
}

const TOP_AXES_COUNT = 8;

function renderRadar(data) {
  const meta = $("meta_radar");
  const panels = $("radar_panels");
  panels.innerHTML = "";
  meta.innerHTML =
    `<span><b>${data.decks.length}</b> decks</span>` +
    `<span>top <b>${TOP_AXES_COUNT}</b> axes shown</span>` +
    `<span>${data.axes_total} total · ${data.families.length} families</span>` +
    `<span><b>${data.elapsed_sec}s</b></span>`;

  // Build a quick lookup from axis_id -> family object
  const axisToFamily = {};
  data.families.forEach((f) => {
    f.axes.forEach((a) => { axisToFamily[a.id] = f; });
  });

  data.decks.forEach((d, idx) => {
    // Pick the deck's top N axes across the whole 105
    const ranked = Object.entries(d.axis_scores)
      .filter(([, v]) => v > 0)
      .sort((a, b) => b[1] - a[1]);
    const topAxes = ranked.slice(0, TOP_AXES_COUNT);
    const topSum = topAxes.reduce((s, [, v]) => s + v, 0);
    const totalSum = ranked.reduce((s, [, v]) => s + v, 0);
    const concentration = totalSum > 0 ? Math.round((topSum / totalSum) * 100) : 0;

    const wrap = document.createElement("div");
    wrap.className = "deck-panel";
    wrap.innerHTML = `
      <h3>${escapeHtml(d.name)} <small class="hint">${d.cards} cards · ${d.color_identity || "C"} · ${concentration}% of signal in top ${topAxes.length}</small></h3>
      <div class="radar-grid">
        <div class="radar-cell">
          <div class="radar-cell-hint">Dominant themes · click an axis to see its family</div>
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

    if (topAxes.length === 0) {
      const canvas = $(`radar_${idx}_top`);
      const ctx = canvas.getContext("2d");
      ctx.fillStyle = "#6c7585";
      ctx.font = "14px ui-sans-serif, system-ui, sans-serif";
      ctx.textAlign = "center";
      ctx.fillText("No axes matched — deck oracle text may be empty.", canvas.width / 2, 40);
      return;
    }

    const labels = topAxes.map(([axisId]) => prettyAxis(axisId));
    const values = topAxes.map(([, v]) => v);
    const maxVal = Math.max(1, ...values);

    new Chart($(`radar_${idx}_top`), {
      type: "radar",
      data: {
        labels,
        datasets: [{
          label: d.name,
          data: values,
          backgroundColor: "rgba(124, 92, 255, 0.22)",
          borderColor: "#7c5cff",
          pointBackgroundColor: "#7c5cff",
          pointRadius: 5,
          pointHoverRadius: 7,
          borderWidth: 2,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: true,
        onClick: (evt, els) => {
          if (!els.length) return;
          const ix = els[0].index;
          const axisId = topAxes[ix][0];
          const family = axisToFamily[axisId];
          if (family) showDrill(idx, family, d.axis_scores, axisId);
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (c) => `${c.label}: ${c.formattedValue}`,
              afterLabel: (c) => {
                const axisId = topAxes[c.dataIndex][0];
                const family = axisToFamily[axisId];
                return family ? `family: ${family.label}` : "";
              },
            },
          },
        },
        scales: {
          r: {
            angleLines: { color: "#262b3a" },
            grid: { color: "#262b3a" },
            pointLabels: { color: "#e6e9ef", font: { size: 12, weight: "500" } },
            ticks: { color: "#6c7585", backdropColor: "transparent", maxTicksLimit: 4 },
            suggestedMin: 0,
            suggestedMax: maxVal,
          },
        },
      },
    });

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
      drillCanvas._chart = new Chart(drillCanvas, {
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
          maintainAspectRatio: true,
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
      drillWrap.hidden = false;
      drillWrap.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  });

  $("result_radar").hidden = false;
}

// Render axis IDs as readable labels: "attacks_trigger" -> "attacks trigger"
function prettyAxis(id) {
  return id.replace(/_/g, " ");
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
    const wrap = document.createElement("div");
    wrap.className = "deck-panel";
    const r = d.result.results;
    const sequencesHtml = (d.result.exampleSequences || []).slice(0, 3).map((ex, i) => {
      const hand = (ex.openingHand || []).join(", ");
      const turns = (ex.turns || []).map((t, ti) => `T${ti + 1}: ${(t.played || []).join(", ") || "—"}`).join("<br>");
      return `<details><summary>Example ${i + 1}</summary><p><em>opening:</em> ${escapeHtml(hand)}</p><p>${turns}</p></details>`;
    }).join("");
    const missingHtml = (d.result.missing && d.result.missing.length)
      ? `<p class="hint warn">Missing from Scryfall: ${d.result.missing.map(escapeHtml).join(", ")}</p>` : "";
    wrap.innerHTML = `
      <h3>${escapeHtml(d.name)} <small class="hint">${d.result.deckSize} cards · ${d.result.colorIdentity || "C"}</small></h3>
      <div class="play-stats">
        <div class="stat"><span class="big">${r.playableHandsPct}%</span><span class="lbl">Playable hands</span></div>
        <div class="stat"><span class="big">${r.onOrAboveCurveThroughTurn3Pct}%</span><span class="lbl">On-curve through T${data.turns_seen}</span></div>
        <div class="stat"><span class="big">${r.hasPlayableSpellByTurn3Pct}%</span><span class="lbl">Spell by T${data.turns_seen}</span></div>
      </div>
      <div class="play-meta">
        <span>lands <b>${d.result.lands}</b> <em>(${d.result.tappedLands} tapped)</em></span>
        <span>mana perms <b>${d.result.manaPermanents}</b></span>
        <span>avg nonland MV <b>${d.result.averageNonlandManaValue}</b></span>
      </div>
      ${missingHtml}
      ${sequencesHtml ? `<div class="sequences">${sequencesHtml}</div>` : ""}
    `;
    panels.appendChild(wrap);
  });

  $("result_playability").hidden = false;
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

/* ---------------- submit ---------------- */

form.addEventListener("submit", async (e) => {
  e.preventDefault();

  const modes = MODES.filter((m) => $("mode_" + m).checked);
  if (!modes.length) {
    statusEl.className = "err";
    statusEl.textContent = "Pick at least one analysis mode.";
    return;
  }

  const shared = {
    user: $("user").value.trim(),
    source: $("source").value,
    archidekt_user: $("archidekt_user").value.trim(),
    decks: $("decks").value.trim() ? $("decks").value.split(",").map((s) => s.trim()).filter(Boolean) : [],
    extra_decks: $("extra_decks").value.trim(),
  };
  if (!shared.user && !shared.extra_decks) {
    statusEl.className = "err";
    statusEl.textContent = "Provide a username or at least one extra deck URL/ID.";
    return;
  }

  // Upgrades mode also requires a paste list
  if (modes.includes("upgrades") && !$("paste").value.trim()) {
    statusEl.className = "err";
    statusEl.textContent = "Upgrades & Cuts needs a card list. Paste one or uncheck the mode.";
    return;
  }

  goBtn.disabled = true;
  statusEl.className = "";
  statusEl.textContent = `Running ${modes.join(", ")}…`;
  resultsWrap.hidden = false;
  MODES.forEach((m) => { $("result_" + m).hidden = true; });

  const started = performance.now();
  const tasks = [];

  if (modes.includes("upgrades")) {
    tasks.push(
      fetch("/api/match", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...shared,
          paste: $("paste").value.trim(),
          scoring: $("scoring").value,
          replacement_mode: $("replacement_mode").value,
        }),
      }).then((r) => r.json()).then((data) => {
        if (!data.ok) throw new Error(`Upgrades: ${data.error || "failed"}`);
        renderUpgrades(data);
      }).catch((err) => {
        const meta = $("meta_upgrades");
        meta.innerHTML = `<span class="err">${escapeHtml(err.message)}</span>`;
        $("result_upgrades").hidden = false;
      })
    );
  }

  if (modes.includes("radar")) {
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
        $("result_radar").hidden = false;
      })
    );
  }

  if (modes.includes("playability")) {
    tasks.push(
      fetch("/api/playability_decks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...shared,
          simulations: parseInt($("simulations").value, 10),
          turns_seen: parseInt($("turns_seen").value, 10),
        }),
      }).then((r) => r.json()).then((data) => {
        if (!data.ok) throw new Error(`Playability: ${data.error || "failed"}`);
        renderPlayability(data);
      }).catch((err) => {
        const meta = $("meta_playability");
        meta.innerHTML = `<span class="err">${escapeHtml(err.message)}</span>`;
        $("result_playability").hidden = false;
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
