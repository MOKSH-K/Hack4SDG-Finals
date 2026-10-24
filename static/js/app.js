/* FoodForAll shared front-end helpers: API client, auth, toasts, formatting, icons, charts. */
window.FFA = (() => {
  const TOKEN = "ffa_token";
  const USER = "ffa_user";
  const HOME = { worker: "/store", restaurant: "/restaurant", darkstore: "/darkstore", ngo: "/ngo", admin: "/analytics" };

  const storage = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
    del(k) { try { localStorage.removeItem(k); } catch { /* private mode */ } },
  };

  function user() {
    try { return JSON.parse(storage.get(USER) || "null"); } catch { return null; }
  }

  function signIn(token, u) {
    storage.set(TOKEN, token);
    storage.set(USER, JSON.stringify(u));
  }

  function signOut(redirect = true) {
    storage.del(TOKEN);
    storage.del(USER);
    if (redirect) location.href = "/";
  }

  async function api(path, { method = "GET", body, auth = true } = {}) {
    const headers = { Accept: "application/json" };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const token = storage.get(TOKEN);
    if (auth && token) headers.Authorization = `Bearer ${token}`;
    let res;
    try {
      res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
    } catch {
      throw new Error("Can't reach FoodForAll right now. Check your connection and try again.");
    }
    let data = null;
    try { data = await res.json(); } catch { /* empty body */ }
    if (res.status === 401 && auth && token) {
      signOut(false);
      location.href = `/login?next=${encodeURIComponent(location.pathname)}`;
      throw new Error("Session expired - please sign in again");
    }
    if (!res.ok) {
      // Only show messages the server wrote for people; never raw status text or error objects.
      const detail = data && typeof data.detail === "string" ? data.detail : null;
      const fallback = res.status >= 500 ? "Something went wrong on our side. Please try again in a moment."
        : res.status === 403 ? "You don't have access to that." : "That didn't work. Please try again.";
      const err = new Error(res.status >= 500 ? fallback : detail || fallback);
      err.status = res.status;
      throw err;
    }
    return data;
  }

  /** Redirects away when the visitor lacks a role the page needs. Returns true if allowed. */
  function guard(roles) {
    if (!roles || !roles.length) return true;
    const u = user();
    if (!u || !storage.get(TOKEN)) {
      location.replace(`/login?next=${encodeURIComponent(location.pathname)}`);
      return false;
    }
    if (!roles.includes(u.role) && u.role !== "admin") {
      location.replace(HOME[u.role] || "/");
      return false;
    }
    return true;
  }

  function toast(message, kind = "ok") {
    const host = document.getElementById("toasts");
    if (!host) return alert(message);
    const el = document.createElement("div");
    const tone = { ok: "border-rescue/40 text-rescue", warn: "border-moon/40 text-moon", err: "border-alert/50 text-alert" }[kind];
    el.className = `toast pointer-events-auto rounded-xl border bg-white px-4 py-3 text-sm shadow-lg shadow-[#2d332c]/10 ${tone}`;
    el.setAttribute("role", kind === "err" ? "alert" : "status");
    el.textContent = message;
    host.appendChild(el);
    setTimeout(() => { el.style.opacity = "0"; el.style.transform = "translateY(6px)"; }, 4200);
    setTimeout(() => el.remove(), 4700);
  }

  const fmt = {
    n: (v) => Number(v || 0).toLocaleString("en-IN"),
    money: (v) => "₹" + Number(v || 0).toLocaleString("en-IN", { maximumFractionDigits: 0 }),
    // Server timestamps are naive local (IST) - show their wall-clock digits as-is.
    time: (iso) => (iso ? iso.slice(11, 16) : "—"),
    dateShort: (iso) => new Date(iso + "T00:00:00").toLocaleDateString("en-IN", { day: "numeric", month: "short" }),
    dur(secs) {
      secs = Math.max(0, Math.floor(secs));
      const h = Math.floor(secs / 3600), m = Math.floor((secs % 3600) / 60), s = secs % 60;
      if (h) return `${h}h ${String(m).padStart(2, "0")}m`;
      return `${m}m ${String(s).padStart(2, "0")}s`;
    },
    label: (s) => (s || "").replace(/_/g, " ").toLowerCase().replace(/(^|\s)\S/g, (c) => c.toUpperCase()),
    /** "23:00" -> "11 PM", "03:30" -> "3:30 AM" */
    clock12(hhmm) {
      if (!hhmm) return "";
      const [h, m] = hhmm.split(":").map(Number);
      const suffix = h < 12 ? "AM" : "PM", h12 = h % 12 || 12;
      return m ? `${h12}:${String(m).padStart(2, "0")} ${suffix}` : `${h12} ${suffix}`;
    },
  };

  // Plain-language names for internal states, shared by every page.
  const words = {
    batch: { INCOMING: "On the way to hub", AVAILABLE: "On sale", SOLD_OUT: "Sold out", RELIEF: "Given to NGOs",
             EXPIRED: "Expired", DISCARDED: "Thrown away", REJECTED: "Turned away" },
    donation: { FLAGGED: "Waiting for an NGO", CLAIMED: "Claimed", IN_TRANSIT: "Picked up",
                DISTRIBUTED: "Delivered", EXPIRED: "Not collected in time" },
    storage: { HOT: "Kept hot", CHILLED: "Chilled" },
    diet: { VEG: "Veg", NON_VEG: "Non-veg", VEGAN: "Vegan" },
  };
  /** Food-safety summary for a batch: never show internal tags, just whether it passed and why not. */
  function safety(batch) {
    if (batch.status === "INCOMING") return { ok: null, text: "Checked on arrival" };
    if (batch.haccp.verified) return { ok: true, text: "Safety checked" };
    const failed = batch.haccp.checks.find((c) => !c.ok);
    return { ok: false, text: failed ? failed.check : "Needs attention" };
  }

  /** Seconds left on a safety clock, given when the payload was fetched and the current tick. */
  function remaining(clock, fetchedAt, nowMs) {
    return Math.max(0, clock.seconds_left - (nowMs - fetchedAt) / 1000);
  }

  // ---- Icons: render <i data-lucide="name"> into inline SVG (works inside Alpine templates) ----
  const pascal = (s) => s.replace(/(^|-)([a-z0-9])/g, (_, __, c) => c.toUpperCase());
  function renderIcons(root = document) {
    if (!window.lucide) return;
    root.querySelectorAll("i[data-lucide]").forEach((el) => {
      const node = window.lucide.icons[pascal(el.getAttribute("data-lucide"))];
      if (!node) return;
      const svg = window.lucide.createElement(node);
      svg.setAttribute("class", `lucide ${el.getAttribute("class") || ""}`.trim());
      svg.setAttribute("aria-hidden", "true");
      el.replaceWith(svg);
    });
  }
  let iconTimer = null;
  new MutationObserver(() => {
    clearTimeout(iconTimer);
    iconTimer = setTimeout(() => renderIcons(), 30);
  }).observe(document.documentElement, { childList: true, subtree: true });
  document.addEventListener("DOMContentLoaded", () => renderIcons());

  // ---- Charts: one light theme for every Chart.js instance ----
  // Multi-series charts use the validated light-mode categorical slots in fixed order (blue, orange, aqua, yellow);
  // single-series charts wear the brand green.
  const palette = {
    blue: "#2a78d6", orange: "#eb6834", aqua: "#1baf7a", yellow: "#eda100",
    brand: "#355e3b", olive: "#5b7a2e", surface: "#ffffff",
    good: "#355e3b", warn: "#9a6212", bad: "#b42318",
    grid: "rgba(45,51,44,0.08)", axis: "#5f6158", text: "#2d332c",
  };
  function chartDefaults() {
    if (!window.Chart || chartDefaults.done) return;
    chartDefaults.done = true;
    const C = window.Chart;
    C.defaults.color = palette.axis;
    C.defaults.font.family = "Inter, system-ui, sans-serif";
    C.defaults.font.size = 12;
    C.defaults.borderColor = palette.grid;
    C.defaults.plugins.legend.labels.usePointStyle = true;
    C.defaults.plugins.legend.labels.boxWidth = 8;
    C.defaults.plugins.legend.labels.color = palette.text;
    C.defaults.plugins.tooltip.backgroundColor = "#ffffff";
    C.defaults.plugins.tooltip.borderColor = "#e6e1d3";
    C.defaults.plugins.tooltip.borderWidth = 1;
    C.defaults.plugins.tooltip.padding = 10;
    C.defaults.plugins.tooltip.titleColor = palette.text;
    C.defaults.plugins.tooltip.bodyColor = palette.text;
    C.defaults.elements.line.borderWidth = 2;
    C.defaults.elements.line.tension = 0.3;
    C.defaults.elements.point.radius = 0;
    C.defaults.elements.point.hoverRadius = 5;
    C.defaults.elements.bar.borderRadius = 4;
    C.defaults.maintainAspectRatio = false;
  }

  /** Create or update a chart on a canvas without leaking old instances. */
  const charts = {};
  function chart(id, config) {
    chartDefaults();
    const canvas = document.getElementById(id);
    if (!canvas || !window.Chart) return null;
    if (charts[id]) {
      charts[id].data = config.data;
      if (config.options) charts[id].options = config.options;
      charts[id].update("none");
      return charts[id];
    }
    charts[id] = new window.Chart(canvas, config);
    return charts[id];
  }

  /** Poll a loader every `ms`, pausing while the tab is hidden. */
  function poll(fn, ms) {
    fn();
    return setInterval(() => { if (!document.hidden) fn(); }, ms);
  }

  return { api, user, signIn, signOut, guard, toast, fmt, words, safety, remaining, renderIcons, chart, palette, poll, HOME };
})();

/* Nav-bar user widget: each person only sees the pages they can use. */
const NAV = {
  store: { href: "/store", label: "Night Store" },
  restaurant: { href: "/restaurant", label: "Restaurant Hub" },
  darkstore: { href: "/darkstore", label: "Dark Store" },
  ngo: { href: "/ngo", label: "NGO Portal" },
  impact: { href: "/analytics", label: "SDG Impact" },
};
const NAV_BY_ROLE = {
  visitor: ["store", "impact"],
  worker: ["store", "impact"],
  restaurant: ["restaurant", "impact"],
  darkstore: ["darkstore", "impact"],
  ngo: ["ngo", "impact"],
  admin: ["store", "restaurant", "darkstore", "ngo", "impact"],
};

function navUser() {
  return {
    user: FFA.user(),
    links() { return (NAV_BY_ROLE[this.user?.role] || NAV_BY_ROLE.visitor).map((k) => NAV[k]); },
    active(href) { return location.pathname === href; },
    home() { return this.user ? FFA.HOME[this.user.role] || "/" : "/login"; },
    logout() { FFA.signOut(); },
  };
}
