/* HomeBase front-end.
 *
 * CSP-clean: no inline anything, no CDN. ALL feed-derived strings go through textContent
 * or createElement — never innerHTML — so a malicious feed item is inert (AC-PRIV-7).
 * Link hrefs are scheme-checked (http/https only); javascript:/data: are dropped.
 * No feed media is ever loaded (no <img> from feed data) -> no third-party browser fetch.
 * Each card renders in its own try/catch so one bad card can't blank the page.
 */
"use strict";

const POLL_MS = 60000;   // re-read the LOCAL cache (never hits upstream)
const TICK_MS = 30000;   // re-derive relative timestamps

let lastState = null;

// ---- tiny DOM helpers ----------------------------------------------------------------
function el(tag, cls, txt) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (txt != null) n.textContent = String(txt);
  return n;
}
function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

function safeHref(url) {
  if (typeof url !== "string") return null;
  return /^https?:\/\//i.test(url.trim()) ? url.trim() : null;
}

function relTime(iso) {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (isNaN(t)) return "—";
  const mins = Math.round((Date.now() - t) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return mins + " min ago";
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return hrs + " hr ago";
  const days = Math.round(hrs / 24);
  return days + " day" + (days === 1 ? "" : "s") + " ago";
}

function fmtNum(n) {
  if (typeof n !== "number" || isNaN(n)) return "—";
  return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

// ---- top-level render ----------------------------------------------------------------
function render(state) {
  lastState = state;
  renderBanner(state.warnings || []);
  renderHealth(state.health || {});
  const grid = document.getElementById("cards");
  clear(grid);
  for (const card of state.config.cards) {
    let node;
    try {
      node = renderCard(card, (state.cards || {})[card.id] || {});
    } catch (e) {
      node = el("section", "card");
      node.appendChild(el("h2", null, card.title || card.id));
      node.appendChild(el("div", "err", "This card failed to render."));
    }
    grid.appendChild(node);
  }
}

function renderBanner(warnings) {
  const b = document.getElementById("banner");
  if (warnings && warnings.length) {
    b.textContent = warnings.join("  •  ");
    b.hidden = false;
  } else {
    b.hidden = true;
  }
}

function renderHealth(h) {
  const node = document.getElementById("health");
  clear(node);
  const dot = el("span", "dot");
  const total = h.total_sources || 0;
  const healthy = h.healthy_sources || 0;
  if (healthy < total) dot.style.background = healthy === 0 ? "var(--bad)" : "var(--warn)";
  node.appendChild(dot);
  const txt = "server OK · " + healthy + "/" + total + " sources · last refresh " + relTime(h.last_refresh);
  node.appendChild(document.createTextNode(txt));
}

function statePill(rt) {
  const s = rt.state || "loading";
  const labels = { fresh: "live", stale: "stale", dead: "needs attention",
                   "no-data-yet": "no data yet", loading: "loading…" };
  const p = el("span", "pill " + s, labels[s] || s);
  return p;
}

function cardShell(card, rt) {
  const sec = el("section", "card");
  const h = el("h2", null, card.title || card.id);
  h.appendChild(statePill(rt));
  sec.appendChild(h);
  const meta = el("div", "meta");
  const dataTime = rt.as_of || rt.fetched_at;
  meta.textContent = "as of " + relTime(dataTime);
  meta.dataset.asof = dataTime || "";
  sec.appendChild(meta);
  if (rt.state === "stale" || rt.state === "dead") {
    const w = el("div", "note", rt.state === "dead"
      ? "Source unavailable for a while — showing last known data."
      : "Showing last known data (refresh failed).");
    sec.appendChild(w);
  }
  return sec;
}

// ---- card types ----------------------------------------------------------------------
function renderCard(card, rt) {
  const sec = cardShell(card, rt);
  const p = rt.payload;
  if (!p) {
    sec.appendChild(el("div", "note",
      rt.state === "no-data-yet" ? "No data yet — first fetch pending." : "Loading…"));
    return sec;
  }
  if (p.kind === "team") renderTeam(sec, p);
  else if (p.kind === "markets") renderMarkets(sec, p);
  else if (p.kind === "headlines") renderHeadlines(sec, p, []);
  return sec;
}

function gameLine(g, label) {
  const d = el("div", "game");
  d.appendChild(el("span", "label", label + " "));
  if (g.status === "offseason") { d.appendChild(document.createTextNode("Offseason")); return d; }
  const when = el("span", "when", g.start_local || "");
  d.appendChild(el("div", null, g.opponent || ""));
  if (g.status === "postponed") d.appendChild(el("span", "pill stale", "Postponed"));
  if (g.score) d.appendChild(el("div", "score", g.score));
  else d.appendChild(when);
  if (g.status === "live") d.appendChild(el("span", "pill fresh", "LIVE"));
  return d;
}

function renderTeam(sec, p) {
  if (p.status === "offseason") {
    sec.appendChild(el("div", "note", p.note || "Offseason"));
  }
  if (p.next_game) sec.appendChild(gameLine(p.next_game, "Next"));
  if (p.last_game) sec.appendChild(gameLine(p.last_game, "Last"));
  // news
  if (p.news_state === "off-pending-consent") {
    sec.appendChild(el("div", "note", "Team news off — enable in Settings (routes via Google)."));
  } else if (p.news_state === "unavailable") {
    sec.appendChild(el("div", "note", "Team news source unavailable."));
  } else if (p.news && p.news.length) {
    sec.appendChild(el("div", "label", "News"));
    for (const item of p.news.slice(0, 4)) sec.appendChild(newsLine(item));
  }
}

function renderMarkets(sec, p) {
  if (!p.rows || !p.rows.length) { sec.appendChild(el("div", "note", "No symbols configured.")); return; }
  for (const r of p.rows) {
    const row = el("div", "row");
    row.appendChild(el("span", "sym", r.display || r.symbol));
    if (r.error) {
      row.appendChild(el("span", "err", r.error));
      sec.appendChild(row);
      continue;
    }
    const right = el("span", "px");
    right.appendChild(document.createTextNode(fmtNum(r.price)));
    if (typeof r.pct === "number") {
      const cls = r.pct > 0 ? "up" : (r.pct < 0 ? "down" : "flat");
      const sign = r.pct > 0 ? "+" : "";
      right.appendChild(el("span", cls, "  " + sign + r.pct.toFixed(2) + "%"));
    }
    const qt = r.market_state && r.market_state.indexOf("closed") === 0
      ? "prior close" : (r.quote_type || "");
    if (qt) right.appendChild(el("span", "qt", qt));
    row.appendChild(right);
    sec.appendChild(row);
  }
}

function renderHeadlines(sec, p, _notes) {
  if (p.items && p.items.length) {
    for (const item of p.items.slice(0, 10)) sec.appendChild(newsLine(item));
  } else {
    sec.appendChild(el("div", "note", "No headlines."));
  }
  for (const note of (p.notes || [])) sec.appendChild(el("div", "note", note));
}

function newsLine(item) {
  const d = el("div", "news");
  const href = safeHref(item.url);
  const titleText = item.title || "(untitled)";
  if (href) {
    const a = el("a", null, titleText);
    a.href = href;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    d.appendChild(a);
  } else {
    d.appendChild(el("div", null, titleText)); // unsafe/missing URL -> plain text, inert
  }
  const src = el("div", "src");
  src.appendChild(document.createTextNode(item.source || ""));
  if (item.via_aggregator && item.badge) {
    src.appendChild(document.createTextNode(" "));
    src.appendChild(el("span", "badge", item.badge));
  }
  if (!item.published_at) {
    src.appendChild(document.createTextNode(" · time unknown"));
  } else {
    src.appendChild(document.createTextNode(" · " + relTime(item.published_at)));
  }
  d.appendChild(src);
  return d;
}

// ---- ticker + polling ----------------------------------------------------------------
function tickRelativeTimes() {
  document.querySelectorAll(".meta").forEach((m) => {
    if (m.dataset.asof !== undefined) m.textContent = "as of " + relTime(m.dataset.asof || null);
  });
}

async function poll() {
  try {
    const res = await fetch("/api/state", { headers: { "Accept": "application/json" } });
    if (res.ok) render(await res.json());
  } catch (e) { /* server momentarily down; keep last render */ }
}

async function doRefresh() {
  const btn = document.getElementById("refresh");
  btn.disabled = true;
  btn.textContent = "Refreshing…";
  try {
    await fetch("/api/refresh", { method: "POST", headers: { "Content-Type": "application/json" } });
    await poll();
  } catch (e) { /* ignore */ }
  btn.disabled = false;
  btn.textContent = "Refresh";
}

// ---- settings ------------------------------------------------------------------------
let working = null;

function openSettings() {
  if (!lastState) return;
  working = structuredClone(lastState.config);
  const body = document.getElementById("settings-body");
  clear(body);

  body.appendChild(labelledText("Refresh every (minutes, min 5)", working.refresh_default_minutes,
    (v) => { const n = parseInt(v, 10); if (!isNaN(n)) working.refresh_default_minutes = Math.max(5, n); }));

  for (const card of working.cards) {
    const box = el("div", "setting");
    box.appendChild(el("strong", null, card.title || card.id));
    box.appendChild(el("span", "desc", " — " + card.type + " (" + card.source + ")"));

    if (card.type === "markets") {
      const syms = (card.params.symbols || []).join(", ");
      box.appendChild(labelledText("Symbols (comma-separated)", syms,
        (v) => { card.params.symbols = v.split(",").map((s) => s.trim().toUpperCase()).filter(Boolean); }));
      box.appendChild(sourceSelect(card, ["stooq", "yahoo"]));
    }
    if (card.type === "team" && card.params.news) {
      box.appendChild(consentToggle(card.params.news, "Team news"));
    }
    if (card.type === "headlines") {
      for (const feed of (card.params.feeds || [])) box.appendChild(consentToggle(feed, feed.name));
    }
    body.appendChild(box);
  }

  const note = el("p", "privacy-note");
  note.textContent = "Privacy: HomeBase runs only on this machine and binds the loopback interface — "
    + "nothing else on your network can read it. News sources badged \"via Google\" route that search "
    + "through Google; toggle them off to stop that. Everything else is fetched directly. No accounts, "
    + "no trackers, no data leaves except the source fetches you see here.";
  body.appendChild(note);

  document.getElementById("settings").showModal();
}

function labelledText(labelText, value, onInput) {
  const wrap = el("div", "setting");
  const lab = el("label");
  lab.appendChild(document.createTextNode(labelText));
  const input = el("input");
  input.type = "text";
  input.value = value == null ? "" : String(value);
  input.addEventListener("input", () => onInput(input.value));
  lab.appendChild(input);
  wrap.appendChild(lab);
  return wrap;
}

function consentToggle(src, name) {
  const wrap = el("div", "setting");
  const lab = el("label");
  const cb = el("input");
  cb.type = "checkbox";
  cb.checked = !!src.enabled;
  cb.addEventListener("change", () => { src.enabled = cb.checked; });
  lab.appendChild(cb);
  lab.appendChild(document.createTextNode(" " + name + (src.badge ? " (" + src.badge + ")" : "")));
  wrap.appendChild(lab);
  if (src.mode === "aggregator") {
    wrap.appendChild(el("div", "desc", "Routes your query through Google. Off = no data sent to Google."));
  }
  return wrap;
}

function sourceSelect(card, options) {
  const wrap = el("div", "setting");
  const lab = el("label");
  lab.appendChild(document.createTextNode("Data source "));
  const sel = el("select");
  for (const o of options) {
    const opt = el("option", null, o);
    opt.value = o;
    if (o === card.source) opt.selected = true;
    sel.appendChild(opt);
  }
  sel.addEventListener("change", () => { card.source = sel.value; });
  lab.appendChild(sel);
  wrap.appendChild(lab);
  return wrap;
}

async function saveSettings() {
  try {
    const res = await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(working),
    });
    if (res.ok) { document.getElementById("settings").close(); await poll(); }
  } catch (e) { /* ignore */ }
}

async function resetSettings() {
  try {
    await fetch("/api/config/reset", { method: "POST", headers: { "Content-Type": "application/json" } });
    document.getElementById("settings").close();
    await poll();
  } catch (e) { /* ignore */ }
}

// ---- wire-up -------------------------------------------------------------------------
function init() {
  document.getElementById("refresh").addEventListener("click", doRefresh);
  document.getElementById("open-settings").addEventListener("click", openSettings);
  document.getElementById("settings-cancel").addEventListener("click",
    () => document.getElementById("settings").close());
  document.getElementById("settings-save").addEventListener("click", saveSettings);
  document.getElementById("settings-reset").addEventListener("click", resetSettings);

  poll();
  setInterval(poll, POLL_MS);
  setInterval(tickRelativeTimes, TICK_MS);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
  window.addEventListener("focus", poll);
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
else init();
