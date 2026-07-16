"use strict";
/* VoiceType dashboard.
   Vanilla JS, no build step, no framework, no CDN — the CSP blocks every external
   host anyway, which is how the "no network" promise is enforced rather than just
   claimed. */

// The token arrives in the URL fragment. Browsers never send a fragment to the
// server, so it can't land in a log or a Referer header. Stash it and strip it
// from the address bar so it doesn't survive in history or a copied link.
const TOKEN = new URLSearchParams(location.hash.slice(1)).get("t")
           || sessionStorage.getItem("vt");
if (TOKEN) {
  sessionStorage.setItem("vt", TOKEN);
  history.replaceState(null, "", location.pathname);
}

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => s.replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

async function api(path, opts = {}) {
  const r = await fetch("/api" + path, {
    ...opts,
    headers: { "X-VoiceType-Token": TOKEN, "Content-Type": "application/json" },
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.status);
  return r.json();
}

let D = null;          // the bootstrap payload
let offset = 0, query = "";

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("on");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove("on"), 1700);
}

/* ── history ─────────────────────────────────────────────────────────────── */

const dayName = (ts) => {
  const d = new Date(ts * 1000), now = new Date();
  const same = (a, b) => a.toDateString() === b.toDateString();
  const y = new Date(now); y.setDate(y.getDate() - 1);
  if (same(d, now)) return "Today";
  if (same(d, y)) return "Yesterday";
  return d.toLocaleDateString(undefined,
    { weekday: "long", month: "long", day: "numeric" });
};

const clock = (ts) => new Date(ts * 1000)
  .toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });

function renderList(entries, append) {
  const list = $("#list");
  if (!append) list.innerHTML = "";
  if (!entries.length && !append) {
    list.innerHTML = query
      ? `<div class="empty">Nothing matches <b>${esc(query)}</b>.</div>`
      : `<div class="empty"><b>Nothing yet.</b><br>Double-tap ${esc(D.modifier)} and start talking.</div>`;
    return;
  }
  let last = append ? list.dataset.lastDay : "";
  let html = "";
  for (const e of entries) {
    const d = dayName(e.ts);
    if (d !== last) { html += `<div class="day">${esc(d)}</div>`; last = d; }
    const bits = [];
    if (e.words) bits.push(e.words + (e.words === 1 ? " word" : " words"));
    if (e.dur) bits.push(e.dur.toFixed(1) + "s");
    if (e.lang) bits.push(e.lang);
    html += `<div class="entry" data-id="${e.id}">
      <div class="t">${clock(e.ts)}</div>
      <div class="x">${esc(e.text)}<div class="meta">${bits.join(" · ")}</div></div>
      <div class="acts">
        <button class="icon copy" title="Copy">⧉</button>
        <button class="icon del" title="Delete">✕</button>
      </div></div>`;
  }
  list.dataset.lastDay = last;
  list.insertAdjacentHTML("beforeend", html);
}

async function loadHistory(append) {
  if (!append) offset = 0;
  const r = await api(`/entries?limit=200&offset=${offset}&q=${encodeURIComponent(query)}`);
  renderList(r.entries, append);
  offset += r.entries.length;
  $("#more").hidden = offset >= r.total;
}

$("#list").addEventListener("click", async (e) => {
  const row = e.target.closest(".entry");
  if (!row) return;
  const id = +row.dataset.id;
  if (e.target.closest(".copy")) {
    // 127.0.0.1 counts as a secure context, so the clipboard API works with no
    // TLS and no server round-trip.
    const text = row.querySelector(".x").firstChild.textContent;
    try { await navigator.clipboard.writeText(text); toast("Copied"); }
    catch { toast("Couldn't copy"); }
  }
  if (e.target.closest(".del")) {
    await api("/entries/" + id, { method: "DELETE" });
    row.remove();
    toast("Deleted");
  }
});

let qt;
$("#q").addEventListener("input", (e) => {
  query = e.target.value.trim();
  clearTimeout(qt);
  qt = setTimeout(() => loadHistory(false), 140);
});
$("#more").addEventListener("click", () => loadHistory(true));

/* ── stats ───────────────────────────────────────────────────────────────── */

function human(sec) {
  if (sec < 60) return Math.round(sec) + "s";
  if (sec < 3600) return Math.round(sec / 60) + " min";
  const h = Math.floor(sec / 3600), m = Math.round((sec % 3600) / 60);
  return m ? `${h}h ${m}m` : `${h}h`;
}

function renderStats(s) {
  const cards = [
    { n: s.words.toLocaleString(), l: "words dictated" },
    { n: s.entries.toLocaleString(), l: "dictations" },
    {
      n: human(s.saved_sec), l: "time saved",
      // Say what this is. It compares your real speaking time against typing the
      // same words at 40 wpm — an estimate, and it should look like one.
      s: s.saved_measured ? `vs typing at ${s.typing_wpm} wpm` : "needs more data",
    },
    { n: s.streak + (s.streak === 1 ? " day" : " days"), l: "streak" },
    { n: s.avg_words, l: "avg words each" },
    { n: human(s.spoken_sec), l: "spent talking" },
  ];
  $("#cards").innerHTML = cards.map((c) =>
    `<div class="card"><div class="n">${esc(String(c.n))}</div>
     <div class="l">${c.l}</div>${c.s ? `<div class="s">${c.s}</div>` : ""}</div>`).join("");

  const days = s.per_day.slice(-60);
  const max = Math.max(1, ...days.map((d) => d.w));
  $("#chart").innerHTML = days.map((d) =>
    `<div class="b" style="height:${Math.max(2, (d.w / max) * 100)}%"
      title="${d.d}: ${d.w} words"></div>`).join("")
    || `<div class="empty">No dictations yet.</div>`;
}

/* ── words ───────────────────────────────────────────────────────────────── */

function renderVocab() {
  $("#vocab").innerHTML = D.vocab.map((t, i) =>
    `<span class="chip">${esc(t)}<button data-i="${i}" title="Remove">✕</button></span>`)
    .join("") || `<span class="hint" style="margin:0">No words yet.</span>`;

  const pairs = Object.entries(D.corrections);
  $("#corr").innerHTML = pairs.map(([w, r]) =>
    `<div class="pair"><span class="w">${esc(w)}</span><span class="arrow">→</span>
     <span class="r">${esc(r)}</span><span class="sp"></span>
     <button class="icon del" data-w="${esc(w)}" title="Remove">✕</button></div>`)
    .join("") || `<p class="hint" style="margin:0">Nothing yet.</p>`;
}

const saveVocab = () =>
  api("/vocab", { method: "POST", body: JSON.stringify({ terms: D.vocab, corrections: D.corrections }) });

$("#vocab-add").addEventListener("submit", async (e) => {
  e.preventDefault();
  const v = $("#vocab-in").value.trim();
  if (!v || D.vocab.some((t) => t.toLowerCase() === v.toLowerCase())) return;
  D.vocab.push(v); $("#vocab-in").value = "";
  renderVocab(); await saveVocab(); toast("Added");
});

$("#vocab").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-i]");
  if (!b) return;
  D.vocab.splice(+b.dataset.i, 1);
  renderVocab(); await saveVocab();
});

$("#corr-add").addEventListener("submit", async (e) => {
  e.preventDefault();
  const w = $("#corr-wrong").value.trim(), r = $("#corr-right").value.trim();
  if (!w || !r) return;
  D.corrections[w] = r;
  $("#corr-wrong").value = $("#corr-right").value = "";
  renderVocab(); await saveVocab(); toast("Added");
});

$("#corr").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-w]");
  if (!b) return;
  delete D.corrections[b.dataset.w];
  renderVocab(); await saveVocab();
});

/* ── snippets ────────────────────────────────────────────────────────────── */

function renderSnips() {
  $("#snips").innerHTML = D.snippets.map((s, i) =>
    `<div class="snip"><div class="top"><span class="trig">${esc(s.trigger)}</span>
     <span class="sp" style="flex:1"></span>
     <button class="icon del" data-i="${i}" title="Remove">✕</button></div>
     <div class="body">${esc(s.text)}</div></div>`)
    .join("") || `<p class="hint" style="margin:0">No snippets yet.</p>`;
}

const saveSnips = () =>
  api("/snippets", { method: "POST", body: JSON.stringify({ items: D.snippets }) });

$("#snip-add").addEventListener("submit", async (e) => {
  e.preventDefault();
  const t = $("#snip-trig").value.trim(), x = $("#snip-text").value;
  if (!t || !x.trim()) return;
  D.snippets.push({ trigger: t, text: x });
  $("#snip-trig").value = $("#snip-text").value = "";
  renderSnips(); await saveSnips(); toast("Saved");
});

$("#snips").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-i]");
  if (!b) return;
  D.snippets.splice(+b.dataset.i, 1);
  renderSnips(); await saveSnips();
});

/* ── settings ────────────────────────────────────────────────────────────── */

function renderSettings() {
  $$("#lang button").forEach((b) =>
    b.setAttribute("aria-pressed", b.dataset.lang === D.settings.lang));
  $("#autostart").checked = !!D.settings.autostart;
  const M = esc(D.modifier);
  $("#keys").innerHTML = [
    [`<kbd>${M}</kbd> <kbd>${M}</kbd>`, "double-tap — start listening; tap once to stop and type"],
    [`<kbd>${M}</kbd>`, "hold — record while held, types when you let go"],
    ["<kbd>Esc</kbd>", "cancel — stop and throw it away"],
    ["<kbd>⌃⌘Z</kbd>", "undo — delete what it just typed"],
    ["<kbd>⌃⌘V</kbd>", "paste your last dictation again"],
  ].map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join("");
}

$("#lang").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-lang]");
  if (!b) return;
  D.settings.lang = b.dataset.lang;
  renderSettings();
  await api("/settings", { method: "POST", body: JSON.stringify({ lang: D.settings.lang }) });
  toast("Language set");
});

$("#autostart").addEventListener("change", async (e) => {
  const on = e.target.checked;
  const r = await api("/settings", { method: "POST", body: JSON.stringify({ autostart: on }) });
  D.settings = r.settings;
  renderSettings();
  toast(on ? "Will start at login" : "Won't start at login");
});

$("#clear").addEventListener("click", async () => {
  if (!confirm("Delete every dictation on this machine? This can't be undone.")) return;
  await api("/entries/clear", { method: "POST", body: JSON.stringify({ confirm: true }) });
  D.stats = await api("/stats");
  renderStats(D.stats);
  await loadHistory(false);
  toast("History deleted");
});

/* ── nav ─────────────────────────────────────────────────────────────────── */

function show(view) {
  $$("button.nav").forEach((b) =>
    b.setAttribute("aria-current", b.dataset.view === view));
  $$("main section").forEach((s) => (s.hidden = s.id !== "v-" + view));
  if (view === "stats") api("/stats").then(renderStats);
}
$$("button.nav").forEach((b) => b.addEventListener("click", () => show(b.dataset.view)));

/* ── boot ────────────────────────────────────────────────────────────────── */

(async () => {
  if (!TOKEN) return;
  try {
    D = await api("/bootstrap");
  } catch (e) {
    $("#boot").innerHTML =
      `<p>Couldn't reach VoiceType.</p><p class="hint">Open the dashboard from the menu again.</p>`;
    return;
  }
  $("#boot").hidden = true;
  $("#app").hidden = false;
  renderList(D.entries.slice(0, 200), false);
  offset = Math.min(200, D.entries.length);
  $("#more").hidden = offset >= D.total;
  renderStats(D.stats);
  renderVocab();
  renderSnips();
  renderSettings();
})();

// No shutdown-on-close. It's tempting — but `pagehide` also fires on RELOAD, so
// hitting refresh would kill the server out from under the page that's reloading,
// and every call after it fails with "bad token" while the app looks alive. The
// server's own 15-minute idle timer cleans up, and it can't be fooled by a reload.
