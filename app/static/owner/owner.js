(() => {
  "use strict";

  const POLL_MS = 5000;
  // Map starts here before any delivery exists. Change to your shop's location.
  const DEFAULT_CENTER = [13.0827, 80.2707];

  const $ = (id) => document.getElementById(id);
  const show = (el, on) => el.classList.toggle("hidden", !on);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtTime = (iso) => iso
    ? new Date(iso).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" })
    : "";
  const minsAgo = (iso) => Math.floor((Date.now() - new Date(iso).getTime()) / 60000);

  const state = { staff: [], dash: null, pollTimer: null, tab: "dashboard" };

  function toast(msg) {
    $("toast").textContent = msg;
    show($("toast"), true);
    clearTimeout(toast.t);
    toast.t = setTimeout(() => show($("toast"), false), 2500);
  }

  async function api(path, opts = {}) {
    const res = await fetch(path, {
      credentials: "same-origin",
      ...opts,
      headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    });
    let body = null;
    try { body = await res.json(); } catch (_) { /* no body */ }
    if (res.status === 401 && path !== "/api/owner/login") {
      showLogin();
      throw new Error("Please log in");
    }
    if (!res.ok) {
      let msg = (body && body.detail) || `Request failed (${res.status})`;
      if (body && body.errors) msg += ": " + body.errors.map((e) => e.field).join(", ");
      throw new Error(msg);
    }
    return body;
  }

  async function copy(text) {
    try { await navigator.clipboard.writeText(text); toast("Link copied"); }
    catch (_) { window.prompt("Copy this link:", text); }
  }

  // wa.me needs the number in international format, digits only. Indian 10-digit numbers get 91.
  function waNumber(phone) {
    let d = String(phone).replace(/\D/g, "");
    if (d.length === 11 && d.startsWith("0")) d = d.slice(1);
    if (d.length === 10) d = "91" + d;
    return d;
  }

  function customerWhatsappUrl(d) {
    const text = `Hello ${d.customer_name}, your order${d.item_desc ? ` (${d.item_desc})` : ""} will be delivered today. ` +
      `Track the delivery live here: ${d.track_url}`;
    return `https://wa.me/${waNumber(d.customer_phone)}?text=${encodeURIComponent(text)}`;
  }

  function staffWhatsappUrl(s) {
    const text = `Hi ${s.name}, open this link to see your deliveries for today: ${s.link}`;
    return `https://wa.me/${waNumber(s.phone)}?text=${encodeURIComponent(text)}`;
  }

  // ---------------------------------------------------------------- auth + tabs
  function showLogin() {
    stopPolling();
    show($("appView"), false);
    show($("loginView"), true);
    $("loginPassword").focus();
  }

  async function showApp() {
    show($("loginView"), false);
    show($("appView"), true);
    await loadStaff();
    switchTab(location.hash.replace("#", "") || "dashboard");
  }

  $("togglePassword").addEventListener("click", () => {
    const input = $("loginPassword");
    const hidden = input.type === "password";
    input.type = hidden ? "text" : "password";
    $("togglePassword").textContent = hidden ? "Hide" : "Show";
    input.focus();
  });

  $("loginForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    show($("loginError"), false);
    $("loginSubmit").disabled = true;
    $("loginSubmit").textContent = "Logging in...";
    try {
      await api("/api/owner/login", { method: "POST", body: JSON.stringify({ password: $("loginPassword").value }) });
      $("loginPassword").value = "";
      showApp();
    } catch (e) {
      $("loginError").textContent = e.message === "Wrong password" ? "That password is not correct." : e.message;
      show($("loginError"), true);
    } finally {
      $("loginSubmit").disabled = false;
      $("loginSubmit").textContent = "Log in";
    }
  });

  $("logoutBtn").addEventListener("click", async () => {
    await api("/api/owner/logout", { method: "POST" }).catch(() => {});
    showLogin();
  });

  function switchTab(tab) {
    if (!["dashboard", "new", "staff"].includes(tab)) tab = "dashboard";
    state.tab = tab;
    history.replaceState(null, "", `#${tab}`);
    document.querySelectorAll(".tab-panel").forEach((p) => show(p, p.id === `tab-${tab}`));
    document.querySelectorAll(".tab").forEach((b) => {
      b.classList.toggle("bg-white", b.dataset.tab === tab);
      b.classList.toggle("text-slate-900", b.dataset.tab === tab);
    });
    if (tab === "dashboard") { initDashMap(); startPolling(); setTimeout(() => dashMap.invalidateSize(), 50); }
    else stopPolling();
    if (tab === "new") { initPickMap(); fillStaffSelect(); setTimeout(() => pickMap.invalidateSize(), 50); }
    if (tab === "staff") renderStaffList();
  }

  document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => switchTab(b.dataset.tab)));

  // ---------------------------------------------------------------- dashboard
  let dashMap, dashLayer, dashFitted = false;

  function initDashMap() {
    if (dashMap) return;
    dashMap = L.map("dashMap").setView(DEFAULT_CENTER, 12);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19, attribution: "&copy; OpenStreetMap contributors",
    }).addTo(dashMap);
    dashLayer = L.layerGroup().addTo(dashMap);
  }

  function startPolling() {
    stopPolling();
    const tick = async () => {
      try { await loadDashboard(); } catch (_) { /* keep polling */ }
      state.pollTimer = setTimeout(tick, POLL_MS);
    };
    tick();
  }

  function stopPolling() {
    clearTimeout(state.pollTimer);
    state.pollTimer = null;
  }

  async function loadDashboard() {
    const dash = await api("/api/dashboard");
    state.dash = dash;
    renderCounts(dash);
    renderAlerts(dash);
    renderStaffStatus(dash);
    renderDeliveries(dash);
    renderDashMap(dash);
    $("updatedAt").textContent = fmtTime(dash.now);
  }

  const STATUS = {
    assigned: ["Assigned", "bg-slate-200 text-slate-800"],
    on_the_way: ["On the way", "bg-blue-100 text-blue-800"],
    arrived: ["Arrived", "bg-amber-100 text-amber-800"],
    delivered: ["Delivered", "bg-green-100 text-green-800"],
    cancelled: ["Cancelled", "bg-red-100 text-red-700"],
  };
  const badge = (s) => `<span class="text-xs font-semibold rounded-full px-2 py-0.5 ${STATUS[s][1]}">${STATUS[s][0]}</span>`;
  const FLAG = {
    delayed: ["Delayed", "bg-red-600 text-white"],
    signal_lost: ["Signal lost", "bg-amber-500 text-white"],
    idle: ["Idle", "bg-slate-300 text-slate-800"],
  };
  const flagChips = (flags) => flags.map((f) =>
    `<span class="text-[11px] font-bold rounded px-1.5 py-0.5 ${FLAG[f][1]}">${FLAG[f][0]}</span>`).join(" ");
  const rowTint = (flags) => flags.includes("delayed") ? "bg-red-50"
    : flags.includes("signal_lost") ? "bg-amber-50" : flags.includes("idle") ? "bg-slate-50" : "";

  function renderCounts(dash) {
    const c = dash.counts;
    const tiles = [
      ["Assigned", c.assigned], ["On the way", c.on_the_way], ["Arrived", c.arrived],
      ["Delivered", c.delivered], ["Cancelled", c.cancelled],
    ];
    $("counts").innerHTML = tiles.map(([l, n], i) => `<div class="bg-white rounded-xl shadow px-2 py-2 ${i === 4 ? "hidden sm:block" : ""}">
      <div class="text-2xl font-bold">${n}</div><div class="text-xs text-slate-500">${l}</div></div>`).join("");
  }

  function deliveryAlertText(d) {
    const out = [];
    if (d.flags.includes("delayed")) {
      out.push(d.status === "arrived"
        ? `waiting at the customer for ${minsAgo(d.arrived_at)} min`
        : `on the way for ${minsAgo(d.started_at)} min`);
    }
    if (d.flags.includes("signal_lost")) {
      out.push(d.last_ping_at ? `no location since ${fmtTime(d.last_ping_at)}` : "no location received since start");
    }
    return out.join(", ");
  }

  function renderAlerts(dash) {
    const items = [];
    for (const d of dash.deliveries) {
      if (!d.flags.length) continue;
      const tone = d.flags.includes("delayed") ? "bg-red-600" : "bg-amber-500";
      items.push(`<div class="${tone} text-white rounded-xl px-3 py-2 text-sm">
        <b>${esc(d.staff_name || "Unassigned")}</b> → ${esc(d.customer_name)}: ${esc(deliveryAlertText(d))}</div>`);
    }
    const idle = dash.staff.filter((s) => s.flags.includes("idle"));
    if (idle.length) {
      items.push(`<div class="bg-slate-200 text-slate-800 rounded-xl px-3 py-2 text-sm">
        <b>Idle:</b> ${idle.map((s) => `${esc(s.name)}${s.pending_count ? ` (${s.pending_count} not started)` : ""}`).join(", ")}</div>`);
    }
    $("alerts").innerHTML = items.join("");
  }

  function renderStaffStatus(dash) {
    if (!dash.staff.length) {
      $("staffStatus").innerHTML = `<div class="text-sm text-slate-500 py-4">No staff yet. Add them on the Staff tab.</div>`;
      return;
    }
    $("staffStatus").innerHTML = dash.staff.map((s) => {
      const a = s.active_delivery;
      const where = a
        ? `${STATUS[a.status][0]} → ${esc(a.customer_name)}${a.eta_minutes ? `, ~${a.eta_minutes} min` : ""}`
        : `${s.pending_count} to do · ${s.done_today} done`;
      const ping = s.last_ping_at ? `last location ${fmtTime(s.last_ping_at)}` : "";
      return `<div class="py-2 px-2 rounded-lg ${rowTint(s.flags)}">
        <div class="flex items-center gap-2"><b class="mr-auto">${esc(s.name)}</b>${flagChips(s.flags)}</div>
        <div class="text-xs text-slate-600">${where}</div>
        ${ping ? `<div class="text-[11px] text-slate-400">${ping}</div>` : ""}
      </div>`;
    }).join("");
  }

  function renderDeliveries(dash) {
    const rows = dash.deliveries;
    if (!rows.length) {
      $("deliveryTable").innerHTML = `<div class="text-sm text-slate-500 p-4">No deliveries today. Create one with “+ New”.</div>`;
      return;
    }
    const order = { on_the_way: 0, arrived: 1, assigned: 2, delivered: 3, cancelled: 4 };
    const sorted = [...rows].sort((a, b) =>
      (b.flags.length > 0) - (a.flags.length > 0) || order[a.status] - order[b.status] || a.id - b.id);
    const staffOpts = (sel) => state.staff.map((s) =>
      `<option value="${s.id}" ${s.id === sel ? "selected" : ""}>${esc(s.name)}</option>`).join("");

    $("deliveryTable").innerHTML = sorted.map((d) => {
      const open = d.status !== "delivered" && d.status !== "cancelled";
      const times = [
        `created ${fmtTime(d.created_at)}`,
        d.started_at && `started ${fmtTime(d.started_at)}`,
        d.arrived_at && `arrived ${fmtTime(d.arrived_at)}`,
        d.delivered_at && `delivered ${fmtTime(d.delivered_at)}`,
      ].filter(Boolean).join(" · ");
      return `<div class="p-3 grid gap-2 md:grid-cols-[1.4fr_1fr_1.2fr] md:items-center ${rowTint(d.flags)}" data-id="${d.id}">
        <div>
          <div class="flex flex-wrap items-center gap-2"><b>${esc(d.customer_name)}</b>${badge(d.status)}${flagChips(d.flags)}</div>
          <div class="text-xs text-slate-600">${esc(d.address)}</div>
          <div class="text-xs text-slate-500">${esc(d.item_desc)}</div>
        </div>
        <div class="text-xs text-slate-600">
          <div>${esc(d.staff_name || "-")}${d.eta_minutes ? ` · ETA ~${d.eta_minutes} min` : ""}</div>
          <div class="text-slate-400">${times}</div>
        </div>
        <div class="flex flex-wrap gap-1 md:justify-end text-xs">
          <button data-act="copy" class="px-2 py-1 rounded bg-slate-200">Copy link</button>
          ${open ? `<a href="${esc(customerWhatsappUrl(d))}" target="_blank" rel="noopener" class="px-2 py-1 rounded bg-green-600 text-white">WhatsApp</a>
          <select data-act="reassign" class="px-1 py-1 rounded border bg-white" title="Reassign">${staffOpts(d.staff_id)}</select>
          <button data-act="cancel" class="px-2 py-1 rounded bg-red-100 text-red-700">Cancel</button>` : ""}
        </div>
      </div>`;
    }).join("");
  }

  $("deliveryTable").addEventListener("click", async (ev) => {
    const btn = ev.target.closest("button[data-act]");
    if (!btn) return;
    const id = Number(btn.closest("[data-id]").dataset.id);
    const d = state.dash.deliveries.find((x) => x.id === id);
    if (btn.dataset.act === "copy") copy(d.track_url);
    if (btn.dataset.act === "cancel") {
      if (!confirm(`Cancel the delivery for ${d.customer_name}?`)) return;
      try { await api(`/api/deliveries/${id}`, { method: "PATCH", body: JSON.stringify({ status: "cancelled" }) }); toast("Cancelled"); }
      catch (e) { toast(e.message); }
      loadDashboard();
    }
  });

  $("deliveryTable").addEventListener("change", async (ev) => {
    const sel = ev.target.closest("select[data-act=reassign]");
    if (!sel) return;
    const id = Number(sel.closest("[data-id]").dataset.id);
    const name = sel.options[sel.selectedIndex].text;
    if (!confirm(`Reassign this delivery to ${name}? It goes back to "Assigned" until they press Start.`)) {
      loadDashboard();
      return;
    }
    try { await api(`/api/deliveries/${id}`, { method: "PATCH", body: JSON.stringify({ staff_id: Number(sel.value) }) }); toast(`Reassigned to ${name}`); }
    catch (e) { toast(e.message); }
    loadDashboard();
  });

  // Pause re-rendering while the owner is using a dropdown, so it does not close under them.
  $("deliveryTable").addEventListener("focusin", (ev) => { if (ev.target.tagName === "SELECT") stopPolling(); });
  $("deliveryTable").addEventListener("focusout", (ev) => { if (ev.target.tagName === "SELECT" && state.tab === "dashboard") startPolling(); });

  const pinIcon = (cls, text, size = 30) => L.divIcon({
    className: "", html: `<div class="pin ${cls}">${esc(text)}</div>`, iconSize: [size, size], iconAnchor: [size / 2, size / 2],
  });

  function renderDashMap(dash) {
    dashLayer.clearLayers();
    const pts = [];
    for (const d of dash.deliveries) {
      if (d.status !== "on_the_way" && d.status !== "arrived") continue;
      const dest = [d.dest_lat, d.dest_lng];
      L.marker(dest, { icon: pinIcon("pin-dest", "🏠", 22) }).bindTooltip(esc(d.customer_name)).addTo(dashLayer);
      pts.push(dest);
      if (d.route && d.route.length > 1) {
        L.polyline(d.route, { color: "#1d4ed8", weight: 4, opacity: 0.6 }).addTo(dashLayer);
      } else if (d.last_lat != null) {
        L.polyline([[d.last_lat, d.last_lng], dest], { color: "#64748b", weight: 2, dashArray: "4 6" }).addTo(dashLayer);
      }
    }
    for (const s of dash.staff) {
      if (s.last_lat == null) continue;
      const p = [s.last_lat, s.last_lng];
      const cls = s.flags.includes("delayed") ? "pin-delayed" : s.flags.includes("signal_lost") ? "pin-signal" : "pin-ok";
      L.marker(p, { icon: pinIcon(cls, s.name.charAt(0)), zIndexOffset: 1000 })
        .bindTooltip(esc(s.name), { permanent: true, direction: "right", offset: [14, 0], className: "staff-label" })
        .addTo(dashLayer);
      pts.push(p);
    }
    if (pts.length && !dashFitted) {
      dashMap.fitBounds(L.latLngBounds(pts), { padding: [40, 40], maxZoom: 15 });
      dashFitted = true;
    }
  }

  // ---------------------------------------------------------------- new delivery
  let pickMap, pickMarker, dest = null;

  function initPickMap() {
    if (pickMap) return;
    pickMap = L.map("pickMap").setView(DEFAULT_CENTER, 12);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19, attribution: "&copy; OpenStreetMap contributors",
    }).addTo(pickMap);
    pickMap.on("click", (ev) => setDest(ev.latlng.lat, ev.latlng.lng, false));
  }

  function setDest(lat, lng, pan = true) {
    dest = { lat: Number(lat.toFixed(6)), lng: Number(lng.toFixed(6)) };
    if (!pickMarker) {
      pickMarker = L.marker([dest.lat, dest.lng], { draggable: true }).addTo(pickMap);
      pickMarker.on("dragend", () => { const p = pickMarker.getLatLng(); setDest(p.lat, p.lng, false); });
    } else {
      pickMarker.setLatLng([dest.lat, dest.lng]);
    }
    if (pan) pickMap.setView([dest.lat, dest.lng], 16);
    $("destLabel").textContent = `${dest.lat}, ${dest.lng}`;
  }

  // Pulls coordinates out of the common Google Maps link formats.
  function parseLatLng(text) {
    const s = decodeURIComponent(String(text).trim());
    const num = "(-?\\d{1,3}(?:\\.\\d+)?)";
    const patterns = [
      new RegExp(`!3d${num}!4d${num}`),                        // exact place pin
      new RegExp(`[?&](?:q|query|ll|destination|daddr|center)=(?:loc:)?${num}\\s*,\\s*${num}`),
      new RegExp(`/place/${num}\\s*,\\s*\\+?${num}`),
      new RegExp(`@${num},${num}`),                            // map view centre
      new RegExp(`^${num}\\s*,\\s*${num}$`),                    // plain "lat, lng"
    ];
    for (const re of patterns) {
      const m = s.match(re);
      if (m) {
        const lat = parseFloat(m[1]), lng = parseFloat(m[2]);
        if (Math.abs(lat) <= 90 && Math.abs(lng) <= 180) return { lat, lng };
      }
    }
    return null;
  }

  $("gmapsInput").addEventListener("input", () => {
    const v = $("gmapsInput").value.trim();
    if (!v) { $("gmapsMsg").textContent = "Or tap the map to set the destination."; return; }
    const p = parseLatLng(v);
    if (p) {
      setDest(p.lat, p.lng);
      $("gmapsMsg").textContent = "Location found. Drag the pin to adjust if needed.";
      $("gmapsMsg").className = "text-xs text-green-700";
    } else if (/goo\.gl|maps\.app/.test(v)) {
      $("gmapsMsg").textContent = "Short links cannot be read. Open the link in a browser, then copy the full address-bar link, or tap the map.";
      $("gmapsMsg").className = "text-xs text-amber-700";
    } else {
      $("gmapsMsg").textContent = "No coordinates found in that text. Tap the map instead.";
      $("gmapsMsg").className = "text-xs text-amber-700";
    }
  });

  function fillStaffSelect() {
    const sel = $("staffSelect");
    const cur = sel.value;
    sel.innerHTML = `<option value="">Choose staff...</option>` +
      state.staff.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join("");
    sel.value = cur;
  }

  $("newForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    show($("newError"), false);
    if (!dest) {
      $("newError").textContent = "Set the destination: paste a Google Maps link or tap the map.";
      show($("newError"), true);
      return;
    }
    const f = new FormData($("newForm"));
    const body = {
      customer_name: f.get("customer_name").trim(),
      customer_phone: f.get("customer_phone").trim(),
      address: f.get("address").trim(),
      item_desc: f.get("item_desc").trim(),
      staff_id: Number(f.get("staff_id")),
      dest_lat: dest.lat,
      dest_lng: dest.lng,
    };
    $("newSubmit").disabled = true;
    try {
      const d = await api("/api/deliveries", { method: "POST", body: JSON.stringify(body) });
      $("doneStaff").textContent = d.staff_name;
      $("doneCustomer").textContent = d.customer_name;
      $("doneLink").textContent = d.track_url;
      $("doneWhatsapp").href = customerWhatsappUrl(d);
      $("doneCopy").onclick = () => copy(d.track_url);
      show($("newForm"), false);
      show($("newDone"), true);
    } catch (e) {
      $("newError").textContent = e.message;
      show($("newError"), true);
    } finally {
      $("newSubmit").disabled = false;
    }
  });

  $("doneAnother").addEventListener("click", () => {
    $("newForm").reset();
    dest = null;
    if (pickMarker) { pickMap.removeLayer(pickMarker); pickMarker = null; }
    $("destLabel").textContent = "not set";
    $("gmapsMsg").textContent = "Or tap the map to set the destination.";
    $("gmapsMsg").className = "text-xs text-slate-500";
    show($("newDone"), false);
    show($("newForm"), true);
    setTimeout(() => pickMap.invalidateSize(), 50);
  });

  // ---------------------------------------------------------------- staff
  async function loadStaff() {
    state.staff = await api("/api/staff");
  }

  async function renderStaffList() {
    const all = await api(`/api/staff?include_inactive=${$("showInactive").checked}`);
    if (!all.length) {
      $("staffList").innerHTML = `<div class="p-4 text-sm text-slate-500">No staff yet.</div>`;
      return;
    }
    $("staffList").innerHTML = all.map((s) => `<div class="p-3 space-y-2 ${s.active ? "" : "opacity-50"}" data-id="${s.id}">
      <div class="flex items-center gap-2">
        <div class="mr-auto"><b>${esc(s.name)}</b> <span class="text-sm text-slate-500">${esc(s.phone)}</span>
          ${s.active ? "" : `<span class="text-xs text-red-700">deactivated</span>`}</div>
        <button data-act="edit" class="text-xs px-2 py-1 rounded bg-slate-200">Edit</button>
        ${s.active
          ? `<button data-act="deactivate" class="text-xs px-2 py-1 rounded bg-red-100 text-red-700">Deactivate</button>`
          : `<button data-act="activate" class="text-xs px-2 py-1 rounded bg-green-100 text-green-800">Activate</button>`}
      </div>
      ${s.active ? `<div class="flex flex-wrap gap-2 text-xs">
        <button data-act="copylink" class="px-2 py-1 rounded bg-slate-200">Copy staff link</button>
        <a href="${esc(staffWhatsappUrl(s))}" target="_blank" rel="noopener" class="px-2 py-1 rounded bg-green-600 text-white">Send link on WhatsApp</a>
      </div>` : ""}
    </div>`).join("");
    $("staffList")._data = all;
  }

  $("showInactive").addEventListener("change", renderStaffList);

  $("staffList").addEventListener("click", async (ev) => {
    const btn = ev.target.closest("button[data-act]");
    if (!btn) return;
    const id = Number(btn.closest("[data-id]").dataset.id);
    const s = $("staffList")._data.find((x) => x.id === id);
    try {
      if (btn.dataset.act === "copylink") return copy(s.link);
      if (btn.dataset.act === "edit") {
        const name = prompt("Name", s.name);
        if (name === null) return;
        const phone = prompt("Phone", s.phone);
        if (phone === null) return;
        await api(`/api/staff/${id}`, { method: "PATCH", body: JSON.stringify({ name: name.trim(), phone: phone.trim() }) });
      }
      if (btn.dataset.act === "deactivate") {
        if (!confirm(`Deactivate ${s.name}? Their staff link stops working.`)) return;
        await api(`/api/staff/${id}`, { method: "DELETE" });
      }
      if (btn.dataset.act === "activate") {
        await api(`/api/staff/${id}`, { method: "PATCH", body: JSON.stringify({ active: true }) });
      }
      await loadStaff();
      renderStaffList();
    } catch (e) { toast(e.message); }
  });

  $("staffForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    show($("staffError"), false);
    const f = new FormData($("staffForm"));
    try {
      await api("/api/staff", { method: "POST", body: JSON.stringify({ name: f.get("name").trim(), phone: f.get("phone").trim() }) });
      $("staffForm").reset();
      await loadStaff();
      renderStaffList();
      toast("Staff added");
    } catch (e) {
      $("staffError").textContent = e.message;
      show($("staffError"), true);
    }
  });

  // ---------------------------------------------------------------- boot
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && state.tab === "dashboard" && !$("appView").classList.contains("hidden")) startPolling();
  });

  api("/api/owner/me").then(showApp).catch(() => showLogin());
})();
