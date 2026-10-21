(() => {
  "use strict";

  const TOKEN = decodeURIComponent(location.pathname.split("/")[2] || "");
  const PING_INTERVAL_MS = 20000;
  const REFRESH_MS = 15000;

  const $ = (id) => document.getElementById(id);
  const state = {
    deliveries: [],
    watchId: null,
    lastPosition: null,
    pingTimer: null,
    wakeLock: null,
    pingFailures: 0,
    confirmFor: null,
    busy: false,
  };

  // ---------------------------------------------------------------- PWA setup
  const manifest = document.createElement("link");
  manifest.rel = "manifest";
  manifest.href = `/s/${encodeURIComponent(TOKEN)}/manifest.json`;
  document.head.appendChild(manifest);
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/s/sw.js", { scope: "/s/" }).catch(() => {});
  }

  // ---------------------------------------------------------------- helpers
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const fmtTime = (iso) => iso
    ? new Date(iso).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Kolkata" })
    : "";

  function show(el, on) { el.classList.toggle("hidden", !on); }

  function showError(msg) {
    $("bannerError").textContent = msg;
    show($("bannerError"), !!msg);
    if (msg) setTimeout(() => show($("bannerError"), false), 6000);
  }

  async function api(path, opts = {}) {
    const res = await fetch(path, {
      ...opts,
      headers: { "Content-Type": "application/json", "X-Staff-Token": TOKEN, ...(opts.headers || {}) },
    });
    let body = null;
    try { body = await res.json(); } catch (_) { /* empty body */ }
    if (!res.ok) {
      const err = new Error((body && body.detail) || `Request failed (${res.status})`);
      err.status = res.status;
      throw err;
    }
    return body;
  }

  // ---------------------------------------------------------------- rendering
  const BADGE = {
    assigned: ["To do", "bg-slate-200 text-slate-800"],
    on_the_way: ["On the way", "bg-blue-100 text-blue-800"],
    arrived: ["Arrived", "bg-amber-100 text-amber-800"],
    delivered: ["Delivered", "bg-green-100 text-green-800"],
    cancelled: ["Cancelled", "bg-red-100 text-red-800"],
  };

  function card(d) {
    const [label, cls] = BADGE[d.status] || [d.status, "bg-slate-200"];
    const done = d.status === "delivered" || d.status === "cancelled";
    const nav = `https://www.google.com/maps/dir/?api=1&destination=${d.dest_lat},${d.dest_lng}&travelmode=driving`;
    let actions = "";
    if (d.status === "assigned") {
      actions = `<button data-act="start" data-id="${d.id}" class="w-full py-4 rounded-xl bg-blue-600 text-white text-lg font-bold active:bg-blue-800">Start trip</button>`;
    } else if (d.status === "on_the_way") {
      actions = `<div class="grid grid-cols-2 gap-3">
        <button data-act="arrive" data-id="${d.id}" class="py-4 rounded-xl bg-amber-500 text-white text-lg font-bold active:bg-amber-700">Arrived</button>
        <button data-act="deliver" data-id="${d.id}" class="py-4 rounded-xl bg-green-600 text-white text-lg font-bold active:bg-green-800">Delivered</button>
      </div>`;
    } else if (d.status === "arrived") {
      actions = `<button data-act="deliver" data-id="${d.id}" class="w-full py-4 rounded-xl bg-green-600 text-white text-lg font-bold active:bg-green-800">Delivered</button>`;
    }
    const timeline = [
      d.started_at && `Started ${fmtTime(d.started_at)}`,
      d.arrived_at && `Arrived ${fmtTime(d.arrived_at)}`,
      d.delivered_at && `Delivered ${fmtTime(d.delivered_at)}`,
    ].filter(Boolean).join(" · ");

    return `<article class="bg-white rounded-2xl shadow p-4 space-y-3 ${done ? "opacity-60" : ""}">
      <div class="flex items-start justify-between gap-2">
        <div>
          <div class="text-xl font-bold">${esc(d.customer_name)}</div>
          <div class="text-slate-600">${esc(d.address)}</div>
        </div>
        <span class="shrink-0 text-xs font-semibold rounded-full px-3 py-1 ${cls}">${label}</span>
      </div>
      ${d.item_desc ? `<div class="text-sm bg-slate-50 rounded-lg px-3 py-2">📦 ${esc(d.item_desc)}</div>` : ""}
      ${timeline ? `<div class="text-xs text-slate-500">${timeline}</div>` : ""}
      ${done ? "" : `<div class="grid grid-cols-2 gap-3">
        <a href="${nav}" target="_blank" rel="noopener" class="text-center py-3 rounded-xl border-2 border-blue-600 text-blue-700 font-semibold">Open in Google Maps</a>
        <a href="tel:${esc(d.customer_phone)}" class="text-center py-3 rounded-xl border-2 border-slate-300 font-semibold">Call customer</a>
      </div>`}
      ${actions}
    </article>`;
  }

  function render() {
    const list = $("list");
    if (!state.deliveries.length) {
      list.innerHTML = `<div class="text-center text-slate-500 py-16">No deliveries assigned for today.</div>`;
      return;
    }
    // Active first, then to-do, then finished.
    const order = { on_the_way: 0, arrived: 1, assigned: 2, delivered: 3, cancelled: 4 };
    const sorted = [...state.deliveries].sort((a, b) => order[a.status] - order[b.status] || a.id - b.id);
    list.innerHTML = sorted.map(card).join("");
  }

  // ---------------------------------------------------------------- data
  async function refresh() {
    try {
      const data = await api("/api/staff/me/deliveries");
      $("staffName").textContent = data.staff_name;
      state.deliveries = data.deliveries;
      render();
      syncTracking();
    } catch (e) {
      if (e.status === 401) {
        $("staffName").textContent = "Link not valid";
        $("list").innerHTML = `<div class="text-center text-red-700 py-16 px-6">This delivery link is not valid any more. Ask the shop for a new link.</div>`;
        stopTracking();
      } else {
        showError("Could not load deliveries. Retrying...");
      }
    }
  }

  async function act(action, id, body) {
    if (state.busy) return;
    state.busy = true;
    try {
      await api(`/api/deliveries/${id}/${action}`, { method: "POST", body: body ? JSON.stringify(body) : undefined });
      await refresh();
      return true;
    } catch (e) {
      showError(e.message);
      await refresh();
      return false;
    } finally {
      state.busy = false;
    }
  }

  // ---------------------------------------------------------------- location
  const onTheWay = () => state.deliveries.filter((d) => d.status === "on_the_way");

  function syncTracking() {
    if (onTheWay().length) startTracking(); else stopTracking();
  }

  function startTracking() {
    show($("trackingPill"), true);
    requestWakeLock();
    if (state.watchId !== null) return;
    if (!("geolocation" in navigator)) {
      show($("bannerGps"), true);
      $("bannerGps").textContent = "This phone or browser cannot share location. Use Chrome on Android.";
      return;
    }
    state.watchId = navigator.geolocation.watchPosition(
      (pos) => {
        const first = !state.lastPosition;
        state.lastPosition = { lat: pos.coords.latitude, lng: pos.coords.longitude };
        show($("bannerGps"), false);
        if (first) sendPings(); // show the customer a position right away
      },
      (err) => {
        if (err.code === err.PERMISSION_DENIED) show($("bannerGps"), true);
      },
      { enableHighAccuracy: true, maximumAge: 10000, timeout: 30000 },
    );
    state.pingTimer = setInterval(sendPings, PING_INTERVAL_MS);
  }

  function stopTracking() {
    show($("trackingPill"), false);
    if (state.watchId !== null) navigator.geolocation.clearWatch(state.watchId);
    state.watchId = null;
    clearInterval(state.pingTimer);
    state.pingTimer = null;
    state.lastPosition = null;
    state.pingFailures = 0;
    show($("bannerPing"), false);
    releaseWakeLock();
  }

  async function sendPings() {
    const pos = state.lastPosition;
    const active = onTheWay();
    if (!pos || !active.length) return;
    let failed = false;
    let stopped = false;
    for (const d of active) {
      try {
        await api(`/api/deliveries/${d.id}/ping`, { method: "POST", body: JSON.stringify(pos) });
      } catch (e) {
        if (e.status === 409 || e.status === 404) stopped = true; // delivery finished or reassigned
        else failed = true;
      }
    }
    state.pingFailures = failed ? state.pingFailures + 1 : 0;
    show($("bannerPing"), state.pingFailures >= 2);
    if (stopped) refresh();
  }

  // ---------------------------------------------------------------- wake lock
  async function requestWakeLock() {
    if (!("wakeLock" in navigator) || state.wakeLock || document.visibilityState !== "visible") return;
    try {
      state.wakeLock = await navigator.wakeLock.request("screen");
      state.wakeLock.addEventListener("release", () => { state.wakeLock = null; });
    } catch (_) {
      state.wakeLock = null; // e.g. battery saver; tracking still works while the screen is on
    }
  }

  function releaseWakeLock() {
    if (state.wakeLock) state.wakeLock.release().catch(() => {});
    state.wakeLock = null;
  }

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      if (onTheWay().length) requestWakeLock();
      refresh();
    }
  });

  // ---------------------------------------------------------------- confirm delivered
  function openConfirm(id) {
    const d = state.deliveries.find((x) => x.id === id);
    state.confirmFor = id;
    $("confirmText").textContent = d ? `${d.customer_name}, ${d.address}` : "";
    show($("confirmModal"), true);
  }

  function closeConfirm() {
    state.confirmFor = null;
    show($("confirmModal"), false);
  }

  $("confirmCancel").addEventListener("click", closeConfirm);
  $("confirmOk").addEventListener("click", async () => {
    const id = state.confirmFor;
    closeConfirm();
    if (id) await act("deliver", id);
  });

  // ---------------------------------------------------------------- buttons
  $("list").addEventListener("click", (ev) => {
    const btn = ev.target.closest("button[data-act]");
    if (!btn) return;
    const id = Number(btn.dataset.id);
    const action = btn.dataset.act;
    if (action === "deliver") return openConfirm(id);
    act(action, id); // start / arrive; tracking starts or stops after the list refreshes
  });

  // ---------------------------------------------------------------- connectivity
  const updateOnline = () => show($("bannerOffline"), !navigator.onLine);
  window.addEventListener("online", () => { updateOnline(); refresh(); });
  window.addEventListener("offline", updateOnline);
  updateOnline();

  refresh();
  setInterval(refresh, REFRESH_MS);
})();
