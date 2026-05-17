/* mtg-oracle minimal GUI */
const $ = (id) => document.getElementById(id);

const form = $("f");
const status = $("status");
const result = $("result");
const meta = $("meta");
const report = $("report");
const goBtn = $("go");

// Persist last inputs in localStorage so a refresh doesn't lose your paste
const LS_KEY = "mtg-oracle.v1";
try {
  const saved = JSON.parse(localStorage.getItem(LS_KEY) || "{}");
  if (saved.user) $("user").value = saved.user;
  if (saved.scoring) $("scoring").value = saved.scoring;
  if (saved.replacement_mode) $("replacement_mode").value = saved.replacement_mode;
  if (saved.source) $("source").value = saved.source;
  if (saved.archidekt_user) $("archidekt_user").value = saved.archidekt_user;
  if (saved.decks) $("decks").value = saved.decks;
  if (saved.paste) $("paste").value = saved.paste;
} catch (_) {}

function updateArchidektFieldVisibility() {
  const src = $("source").value;
  $("archidekt-user-label").style.display = (src === "both") ? "flex" : "none";
}
updateArchidektFieldVisibility();
$("source").addEventListener("change", updateArchidektFieldVisibility);

function persist() {
  localStorage.setItem(LS_KEY, JSON.stringify({
    user: $("user").value,
    scoring: $("scoring").value,
    replacement_mode: $("replacement_mode").value,
    source: $("source").value,
    archidekt_user: $("archidekt_user").value,
    decks: $("decks").value,
    paste: $("paste").value,
  }));
}
["user", "scoring", "replacement_mode", "source", "archidekt_user", "decks", "paste"].forEach((id) => $(id).addEventListener("input", persist));

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const user = $("user").value.trim();
  const paste = $("paste").value.trim();
  const scoring = $("scoring").value;
  const replacement_mode = $("replacement_mode").value;
  const source = $("source").value;
  const archidekt_user = $("archidekt_user").value.trim();
  const decksRaw = $("decks").value.trim();
  const decks = decksRaw ? decksRaw.split(",").map((s) => s.trim()).filter(Boolean) : [];

  if (!user || !paste) return;

  goBtn.disabled = true;
  status.className = "";
  status.textContent = "Fetching decks and scoring…";
  result.hidden = true;

  const started = performance.now();
  try {
    const res = await fetch("/api/match", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user, paste, scoring, decks, replacement_mode, source, archidekt_user }),
    });
    const data = await res.json();
    if (!data.ok) {
      status.className = "err";
      status.textContent = "Error: " + (data.error || res.statusText);
      return;
    }

    meta.innerHTML =
      `<span><b>${data.purchases}</b> cards</span>` +
      `<span><b>${data.decks_analyzed}</b> decks</span>` +
      `<span>source <b>${data.source || "moxfield"}</b></span>` +
      `<span>scoring <b>${data.scoring}</b></span>` +
      `<span>replacements <b>${data.replacement_mode || "auto"}</b></span>` +
      `<span><b>${data.elapsed_sec}s</b> total</span>`;

    if (typeof marked === "undefined") {
      // Hard fallback: render as preformatted text so the report is still readable.
      report.innerHTML = "";
      const pre = document.createElement("pre");
      pre.style.whiteSpace = "pre-wrap";
      pre.textContent = data.markdown;
      report.appendChild(pre);
    } else {
      report.innerHTML = marked.parse(data.markdown, { gfm: true, breaks: false });
    }
    // Open deck links in a new tab
    report.querySelectorAll("a[href]").forEach((a) => {
      if (a.href.includes("moxfield.com") || a.href.includes("archidekt.com")) {
        a.target = "_blank";
        a.rel = "noopener noreferrer";
      }
    });

    result.hidden = false;
    status.className = "ok";
    status.textContent = `Done in ${((performance.now() - started) / 1000).toFixed(1)}s`;
  } catch (err) {
    status.className = "err";
    status.textContent = "Network error: " + err.message;
  } finally {
    goBtn.disabled = false;
  }
});
