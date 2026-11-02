const tg = window.Telegram ? window.Telegram.WebApp : null;
const INIT = tg ? tg.initData : "";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;
// Jeton de lecture des aperçus (fourni par /api/overview) : la session Telegram ne circule jamais dans les adresses.
let MT = "";
const media = (kind, id) => `/media/${kind}/${id}?mt=${encodeURIComponent(MT)}`;
let poller = null;
const nf = new Intl.NumberFormat("fr-FR");

/* Icônes (tracé Lucide, décoratives) */
const ICONS = {
  pause: '<rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/>',
  play: '<path d="m6 3 14 9-14 9V3z"/>',
  zap: '<path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z"/>',
  send: '<path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  trash: '<path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
  x: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
  external: '<path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
  archive: '<rect x="2" y="3" width="20" height="5" rx="1"/><path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8"/><path d="M10 12h4"/>',
  link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
};
const icon = (name, cls = "w-4 h-4") =>
  `<svg class="${cls}" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name]}</svg>`;

/* Badges de statut (couleur réservée aux statuts, toujours avec un libellé) */
const TONES = {
  success: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20",
  info: "bg-blue-500/10 text-blue-600 dark:text-blue-400 border border-blue-500/20",
  warning: "bg-yellow-500/10 text-yellow-700 dark:text-yellow-400 border border-yellow-500/20",
  error: "bg-red-500/10 text-red-600 dark:text-red-400 border border-red-500/20",
  neutral: "bg-muted text-muted border border-default",
};
const badge = (label, tone = "neutral", pulse = false) =>
  `<span class="inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full whitespace-nowrap ${TONES[tone]}">${pulse ? '<span class="w-1.5 h-1.5 rounded-full bg-current animate-pulse-slow"></span>' : ""}${esc(label)}</span>`;
const STATUS = {
  rendering: ["En cours", "neutral", true], publishing: ["Publication", "neutral", true],
  pending_approval: ["À valider", "warning"], ready: ["Prête", "info"], published: ["Publiée", "success"],
  failed: ["Échec", "error"], discarded: ["Jetée", "neutral"],
  archived: ["Archivée", "neutral"], scheduled: ["Programmée", "info"],
};
const statusBadge = (s) => badge(...(STATUS[s] || [s, "neutral"]));

const BTN_PRIMARY = "inline-flex items-center justify-center gap-2 h-10 px-4 bg-primary text-primary-foreground text-sm font-medium rounded-md hover:opacity-90 transition-opacity disabled:opacity-50";
const BTN_SECONDARY = "inline-flex items-center justify-center gap-2 h-10 px-4 border border-default text-sm font-medium rounded-md hover:bg-muted transition-colors disabled:opacity-50";
const BTN_ICON = "inline-flex items-center justify-center w-10 h-10 rounded-md border border-default hover:bg-muted transition-colors disabled:opacity-50";

/* Thème */
function applyTheme() {
  const dark = tg && tg.colorScheme ? tg.colorScheme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  document.documentElement.classList.toggle("dark", dark);
  try { tg.setHeaderColor(dark ? "#09090b" : "#ffffff"); tg.setBackgroundColor(dark ? "#09090b" : "#ffffff"); } catch (e) {}
}
if (tg) { tg.ready(); tg.expand(); tg.onEvent("themeChanged", applyTheme); }
applyTheme();

function haptic(kind = "light") { try { tg.HapticFeedback.impactOccurred(kind); } catch (e) {} }
let toastTimer;
function toast(msg) {
  const t = $("toast");
  if (!t) return;
  t.textContent = msg;
  t.classList.remove("opacity-0", "translate-y-4");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("opacity-0", "translate-y-4"), 2600);
}

async function api(path, method = "GET") {
  const r = await fetch(path, { method, headers: { "X-Telegram-Init-Data": INIT } });
  if (r.status === 401) {
    showLocked("Session expirée ou non autorisée. Ferme le dashboard puis rouvre-le depuis le bot.");
    throw new Error("Accès refusé");
  }
  if (!r.ok) {
    let msg = r.status === 401 ? "Accès refusé" : `Erreur ${r.status}`;
    try { msg = (await r.json()).detail || msg; } catch (e) {}
    throw new Error(msg);
  }
  return r.json();
}

async function act(path, okMsg) {
  haptic("medium");
  try {
    await api(path, "POST");
    toast(okMsg);
    try { tg.HapticFeedback.notificationOccurred("success"); } catch (e) {}
  } catch (e) {
    toast(e.message);
    try { tg.HapticFeedback.notificationOccurred("error"); } catch (x) {}
  }
  load();
}

/* Graphique : une série (publications/jour), barres fines, infobulle, vue tableau */
let lastSeries = null;
let view = "chart";
function drawChart(series) {
  lastSeries = series;
  const svg = $("chart");
  const W = Math.max(280, $("chartWrap").clientWidth);
  const H = 168, top = 22, bottom = 24, left = 24, right = 14;
  const plotH = H - top - bottom;
  const n = series.labels.length;
  const max = Math.max(1, ...series.published);
  const step = max <= 4 ? 1 : Math.ceil(max / 4);
  const yMax = Math.max(step * Math.ceil(max / step), 1);
  const slot = (W - left - right) / n;
  const bw = Math.min(24, slot * 0.56);
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("width", W);

  const grid = "hsl(var(--border))", ink = "hsl(var(--primary))", muted = "hsl(var(--muted-foreground))";
  let out = "";
  for (let v = 0; v <= yMax; v += step) {
    const y = top + plotH - (v / yMax) * plotH;
    out += `<line x1="${left}" x2="${W - right}" y1="${y}" y2="${y}" stroke="${grid}" stroke-width="1"/>`;
    out += `<text x="0" y="${y + 4}" font-size="11" fill="${muted}">${v}</text>`;
  }
  series.published.forEach((v, i) => {
    const cx = left + slot * i + slot / 2;
    const h = (v / yMax) * plotH;
    const x = cx - bw / 2, y = top + plotH - h, r = Math.min(4, h, bw / 2);
    if (h > 0) {
      out += `<path d="M${x},${top + plotH} V${y + r} Q${x},${y} ${x + r},${y} H${x + bw - r} Q${x + bw},${y} ${x + bw},${y + r} V${top + plotH} Z" fill="${ink}"/>`;
    }
    if (i === n - 1 && v > 0) {
      out += `<text x="${cx}" y="${y - 6}" text-anchor="middle" font-size="11" font-weight="600" fill="hsl(var(--foreground))">${v}</text>`;
    }
    if (i % 2 === (n - 1) % 2) {
      out += `<text x="${cx}" y="${H - 6}" text-anchor="middle" font-size="11" fill="${muted}">${esc(series.labels[i])}</text>`;
    }
    out += `<rect class="hit" data-i="${i}" x="${left + slot * i}" y="${top}" width="${slot}" height="${plotH}" fill="transparent"/>`;
  });
  svg.innerHTML = out;

  const tip = $("chartTip");
  svg.querySelectorAll(".hit").forEach((el) => {
    const show = () => {
      const i = +el.dataset.i;
      const cx = left + slot * i + slot / 2;
      const h = (series.published[i] / yMax) * plotH;
      tip.innerHTML = `<p class="font-medium">${esc(series.labels[i])}</p><p class="text-muted">${series.published[i]} publiée${series.published[i] > 1 ? "s" : ""} · ${series.generated[i]} générée${series.generated[i] > 1 ? "s" : ""}</p>`;
      tip.style.left = `${Math.min(Math.max(cx, 70), W - 70)}px`;
      tip.style.top = `${top + plotH - h - 8}px`;
      tip.classList.remove("hidden");
    };
    el.addEventListener("pointerenter", show);
    el.addEventListener("pointerdown", show);
    el.addEventListener("pointerleave", () => tip.classList.add("hidden"));
  });

  $("chartRows").innerHTML = series.labels.map((d, i) => `
    <tr><td class="px-3 py-2">${esc(d)}</td><td class="px-3 py-2 text-right font-medium">${series.published[i]}</td>
    <td class="px-3 py-2 text-right text-muted">${series.generated[i]}</td></tr>`).reverse().join("");
  const total = series.published.reduce((a, b) => a + b, 0);
  $("chartSub").textContent = `14 jours · ${total} au total`;
}

function setView(v) {
  view = v;
  document.querySelectorAll(".view-btn").forEach((b) => {
    const on = b.dataset.view === v;
    b.setAttribute("aria-selected", on);
    b.className = `view-btn h-9 px-3 rounded font-medium transition-colors ${on ? "bg-card shadow-sm" : "text-muted hover:text-[hsl(var(--foreground))]"}`;
  });
  $("chartWrap").classList.toggle("hidden", v !== "chart");
  $("chartTable").classList.toggle("hidden", v !== "table");
}

function render(d) {
  const now = new Date();
  if (d.media_token) MT = d.media_token;
  $("subtitle").textContent = d.user && d.user.first_name ? `Bonjour ${d.user.first_name} · actif depuis ${d.uptime}` : `Studio · actif depuis ${d.uptime}`;
  $("systemBadge").innerHTML = d.paused ? badge("En pause", "warning") : badge("Actif", "success", true);
  const pb = $("pauseBtn");
  pb.innerHTML = icon(d.paused ? "play" : "pause");
  pb.setAttribute("aria-label", d.paused ? "Relancer le système" : "Mettre le système en pause");
  pb.title = d.paused ? "Relancer" : "Pause globale";

  $("kGen").textContent = d.today.generated;
  $("kPub").textContent = d.today.published;
  $("kPubSub").textContent = `YT ${d.today.youtube} · TT ${d.today.tiktok} · IG ${d.today.instagram || 0}`;
  $("kAppr").textContent = d.pending_approval;
  $("kApprSub").textContent = d.pending_approval ? "en attente de toi" : "rien en attente";
  $("kFail").textContent = d.today.failed;
  $("kFailSub").textContent = d.today.failed ? "à vérifier" : "aucun";
  $("kFailSub").className = `text-xs mt-1 ${d.today.failed ? "text-red-600 dark:text-red-400" : "text-muted"}`;

  drawChart(d.series);
  const q = d.quota;
  $("quota").classList.toggle("hidden", !(q && q.limit));
  if (q && q.limit) {
    const left = Math.max(0, q.limit - q.used);
    $("quotaTxt").textContent = `${nf.format(left)} restants sur ${nf.format(q.limit)}`;
    $("quotaBar").style.width = `${Math.max(1, (left / q.limit) * 100)}%`;
  }

  // À valider
  $("apprSec").classList.toggle("hidden", !d.approvals.length);
  $("apprCount").textContent = d.approvals.length ? `${d.approvals.length} en attente` : "";
  $("approvals").innerHTML = d.approvals.map((v) => `
    <article class="snap-start shrink-0 w-44 rounded-lg border border-default bg-card overflow-hidden">
      <button type="button" class="relative block w-full aspect-[9/16] bg-muted" data-play="${v.id}" aria-label="Lire l'aperçu de ${esc(v.title)}">
        <img loading="lazy" src="${media("thumb", v.id)}" alt="" class="w-full h-full object-cover">
        <span class="absolute inset-0 flex items-center justify-center">
          <span class="w-11 h-11 rounded-full bg-[hsl(var(--background))]/85 backdrop-blur flex items-center justify-center">${icon("play", "w-4 h-4 ml-0.5")}</span>
        </span>
        <span class="absolute right-2 bottom-2 text-xs font-medium px-2 py-0.5 rounded bg-[hsl(var(--background))]/85">${mmss(v.duration)}</span>
      </button>
      <div class="p-3">
        <h3 class="text-sm font-medium leading-snug line-clamp-2">${esc(v.title)}</h3>
        <p class="text-xs text-muted mt-1 truncate">${esc(v.channel)}${v.part ? " · partie " + esc(v.part) : ""}</p>
        <button type="button" class="${BTN_PRIMARY} w-full mt-3" data-act="/api/videos/${v.id}/publish" data-msg="Publication lancée">${icon("send")}Publier</button>
        <div class="flex gap-2 mt-2">
          <button type="button" class="${BTN_SECONDARY} flex-1" data-act="/api/videos/${v.id}/approve" data-msg="Validée — publiée au prochain créneau">Valider</button>
          <button type="button" class="${BTN_ICON}" data-act="/api/videos/${v.id}/archive" data-msg="Vidéo archivée" aria-label="Archiver la vidéo" title="Archiver">${icon("archive")}</button>
          <button type="button" class="${BTN_ICON}" data-act="/api/videos/${v.id}/discard" data-msg="Vidéo jetée" aria-label="Jeter la vidéo" title="Jeter">${icon("trash")}</button>
        </div>
      </div>
    </article>`).join("");

  // Chaînes
  const active = d.channels.filter((c) => c.active).length;
  $("chCount").textContent = `${active} active${active > 1 ? "s" : ""} sur ${d.channels.length}`;
  $("channels").innerHTML = d.channels.length ? d.channels.map((c) => {
    const meta = [`${c.ready} prête${c.ready > 1 ? "s" : ""}`];
    if (c.long_enabled) meta.push(`${c.long_ready} longue${c.long_ready > 1 ? "s" : ""} prête${c.long_ready > 1 ? "s" : ""}`);
    if (c.pending) meta.push(`${c.pending} à valider`);
    if (c.rendering) meta.push(`${c.rendering} en fabrication`);
    meta.push(c.active ? `prochain créneau ${c.next_slot}` : "en pause");
    meta.push(`${c.per_day}/jour`);
    const plat = (name, p) => p.enabled && p.linked
      ? `<span class="inline-flex items-center gap-1.5 text-xs"><span class="w-1.5 h-1.5 rounded-full bg-emerald-500"></span>${name}</span>`
      : `<span class="inline-flex items-center gap-1.5 text-xs text-muted"><span class="w-1.5 h-1.5 rounded-full border border-current"></span>${name} ${p.enabled ? "non lié" : "désactivé"}</span>`;
    return `
    <article class="p-4">
      <div class="flex items-start justify-between gap-4">
        <div class="min-w-0">
          <div class="flex items-center gap-2 flex-wrap">
            <h3 class="font-medium truncate">${esc(c.name)}</h3>
            <span class="text-xs px-2 py-0.5 rounded bg-muted text-muted uppercase tracking-wide">${esc(c.language)}</span>
          </div>
          <p class="text-sm text-muted mt-1">${esc(meta.join(" · "))}</p>
          <div class="flex flex-wrap gap-x-4 gap-y-1 mt-2">${plat("YouTube", c.youtube)}${plat("TikTok", c.tiktok)}${plat("Instagram", c.instagram)}</div>
        </div>
        <label class="switch mt-0.5" title="${c.active ? "Mettre en pause" : "Activer"}">
          <input type="checkbox" ${c.active ? "checked" : ""} data-toggle="${c.id}" aria-label="${c.active ? "Mettre en pause" : "Activer"} ${esc(c.name)}">
          <span class="track"></span><span class="thumb"></span>
        </label>
      </div>
      <div class="flex flex-wrap gap-2 mt-4">
        <button type="button" class="${BTN_SECONDARY}" data-act="/api/channels/${c.id}/generate" data-msg="Génération lancée — suivi dans Telegram">${icon("zap")}${c.long_enabled ? "Short" : "Générer"}</button>
        ${c.long_enabled ? `<button type="button" class="${BTN_SECONDARY}" data-act="/api/channels/${c.id}/generate?kind=horizontal" data-msg="Vidéo longue lancée — suivi dans Telegram">${icon("zap")}Vidéo longue</button>` : ""}
        <button type="button" class="${BTN_SECONDARY}" data-act="/api/channels/${c.id}/publish-next" data-msg="Publication lancée">${icon("send")}Publier</button>
        <button type="button" class="${BTN_SECONDARY}" data-connect="${c.id}">${icon("link")}Comptes</button>
      </div>
    </article>`;
  }).join("") : `<div class="p-8 text-center"><p class="text-sm text-muted">Aucune chaîne pour l'instant. Crée la première depuis le bot avec /new.</p></div>`;

  // En cours
  $("jobsSec").classList.toggle("hidden", !d.jobs.length);
  $("jobCount").textContent = d.jobs.length ? `${d.jobs.length} tâche${d.jobs.length > 1 ? "s" : ""}` : "";
  $("jobs").innerHTML = d.jobs.map((j) => `
    <div class="flex items-center justify-between gap-4 px-4 py-3">
      <div class="flex items-center gap-3 min-w-0">
        <span class="w-2 h-2 rounded-full shrink-0 ${j.status === "running" ? "bg-emerald-500 animate-pulse-slow" : "bg-[hsl(var(--border))]"}"></span>
        <span class="text-sm truncate">${esc(j.label)}</span>
      </div>
      <span class="text-xs text-muted shrink-0">${j.status === "running" ? mmss(j.since) : "en attente"}</span>
    </div>`).join("");

  // Dernières vidéos
  $("recent").innerHTML = d.recent.length ? d.recent.map((v) => {
    const links = v.pubs.map((p) => {
      const name = { youtube: "YouTube", tiktok: "TikTok", instagram: "Instagram" }[p.platform] || p.platform;
      if (p.url) return `<a href="${esc(p.url)}" target="_blank" rel="noopener" class="inline-flex items-center gap-1 text-xs text-muted hover:text-[hsl(var(--foreground))] transition-colors">${name}${icon("external", "w-3 h-3")}</a>`;
      if (p.status === "failed") return `<span class="text-xs text-red-600 dark:text-red-400" title="${esc(p.error)}">${name} : échec</span>`;
      if (p.status === "draft") return `<span class="text-xs text-muted">${name} : brouillon</span>`;
      return `<span class="text-xs text-muted">${name}</span>`;
    }).join('<span class="text-xs text-muted">·</span>');
    return `
    <div class="flex items-center gap-3 px-4 py-3">
      <button type="button" class="w-9 h-16 rounded bg-muted overflow-hidden shrink-0" ${v.has_file ? `data-play="${v.id}" aria-label="Lire ${esc(v.title)}"` : 'tabindex="-1" aria-hidden="true"'}>
        ${v.has_thumb ? `<img loading="lazy" src="${media("thumb", v.id)}" alt="" class="w-full h-full object-cover">` : ""}
      </button>
      <div class="min-w-0 flex-1">
        <p class="text-sm font-medium truncate">${esc(v.title)}</p>
        <p class="text-xs text-muted truncate mt-0.5">${esc(v.channel)}${v.format === "horizontal" ? " · vidéo longue" : ""}${v.part ? " · partie " + esc(v.part) : ""} · ${esc(v.created)}</p>
        ${links ? `<div class="flex items-center gap-2 mt-1">${links}</div>` : ""}
      </div>
      <div class="shrink-0">${statusBadge(v.status)}</div>
    </div>`;
  }).join("") : `<div class="p-8 text-center"><p class="text-sm text-muted">Les vidéos générées apparaîtront ici.</p></div>`;

  $("synced").textContent = `Synchronisé à ${now.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" })}`;
}

async function load() {
  try { render(await api("/api/overview")); }
  catch (e) { toast(e.message); }
}

function openPlayer(id) {
  haptic();
  const v = $("player");
  v.src = media("video", id);
  $("modal").classList.remove("hidden");
  $("modal").classList.add("flex");
  v.play().catch(() => {});
}
function closePlayer() {
  $("player").pause();
  $("modal").classList.add("hidden");
  $("modal").classList.remove("flex");
}

async function openConnect(cid) {
  haptic();
  try {
    const { url } = await api(`/api/channels/${cid}/connect-link`, "POST");
    // Hors de la WebView : Google refuse les connexions OAuth dans les vues intégrées.
    if (tg && tg.openLink) tg.openLink(url, { try_instant_view: false }); else window.open(url, "_blank");
  } catch (e) { toast(e.message); }
}

document.addEventListener("click", (ev) => {
  const c = ev.target.closest("[data-connect]");
  if (c) { openConnect(c.dataset.connect); return; }
  const a = ev.target.closest("[data-act]");
  if (a) { a.disabled = true; act(a.dataset.act, a.dataset.msg); return; }
  const p = ev.target.closest("[data-play]");
  if (p) { openPlayer(p.dataset.play); return; }
  const vb = ev.target.closest(".view-btn");
  if (vb) setView(vb.dataset.view);
});
document.addEventListener("change", (ev) => {
  const t = ev.target.closest("[data-toggle]");
  if (t) act(`/api/channels/${t.dataset.toggle}/toggle`, t.checked ? "Chaîne activée" : "Chaîne en pause");
});
$("closeModal").innerHTML = icon("x");
$("closeModal").addEventListener("click", closePlayer);
$("modal").addEventListener("click", (e) => { if (e.target.id === "modal") closePlayer(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closePlayer(); });
$("pauseBtn").addEventListener("click", () => act("/api/system/pause", "Système mis à jour"));
window.addEventListener("resize", () => { if (lastSeries) drawChart(lastSeries); });
// Miniature absente : on retire l'image (pas de gestionnaire inline, interdit par la politique de sécurité)
document.addEventListener("error", (e) => { if (e.target instanceof HTMLImageElement) e.target.remove(); }, true);
setView("chart");

function showLocked(message) {
  if (poller) { clearInterval(poller); poller = null; }
  document.body.innerHTML = `
    <main class="min-h-screen flex items-center justify-center px-5">
      <div class="max-w-sm p-6 rounded-lg border border-default bg-card text-center">
        <div class="w-10 h-10 rounded-full bg-muted mx-auto mb-4 flex items-center justify-center"><span class="text-xs font-semibold">SF</span></div>
        <h1 class="font-semibold tracking-tight mb-2">StoryForge</h1>
        <p class="text-sm text-muted leading-relaxed">${esc(message)}</p>
      </div>
    </main>`;
}

if (!INIT) {
  showLocked("Ouvre ce dashboard depuis le bot Telegram, avec le bouton « Dashboard ».");
} else {
  load();
  poller = setInterval(load, 15000);
}
