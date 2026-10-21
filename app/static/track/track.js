(() => {
  "use strict";

  const TOKEN = decodeURIComponent(location.pathname.split("/")[2] || "");
  const POLL_MS = 5000;
  const $ = (id) => document.getElementById(id);
  const show = (el, on) => el.classList.toggle("hidden", !on);

  const fmtTime = (iso) => iso
    ? new Date(iso).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" })
    : "";

  let map, homeMarker, staffMarker, routeLine, timer;
  let fitted = false;

  const icon = (cls, emoji) => L.divIcon({
    className: "", html: `<div class="pin ${cls}">${emoji}</div>`, iconSize: [36, 36], iconAnchor: [18, 18],
  });

  function initMap(dest) {
    map = L.map("map", { zoomControl: false, attributionControl: true }).setView([dest.lat, dest.lng], 14);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19, attribution: "&copy; OpenStreetMap contributors",
    }).addTo(map);
    homeMarker = L.marker([dest.lat, dest.lng], { icon: icon("pin-home", "🏠") }).addTo(map);
  }

  // Glide the staff pin to its new position instead of jumping.
  function moveMarker(marker, to, ms = 1200) {
    const from = marker.getLatLng();
    const start = performance.now();
    const step = (now) => {
      const t = Math.min(1, (now - start) / ms);
      marker.setLatLng([from.lat + (to[0] - from.lat) * t, from.lng + (to[1] - from.lng) * t]);
      if (t < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  function updateMap(data) {
    const dest = [data.destination.lat, data.destination.lng];
    const pos = data.last_position;
    if (!pos) {
      if (staffMarker) { map.removeLayer(staffMarker); staffMarker = null; }
      if (routeLine) { map.removeLayer(routeLine); routeLine = null; }
      return;
    }
    const p = [pos.lat, pos.lng];
    const road = data.route && data.route.length > 1;
    const line = road ? data.route : [p, dest];
    if (!staffMarker) {
      staffMarker = L.marker(p, { icon: icon("pin-staff", "🛵"), zIndexOffset: 1000 }).addTo(map);
    } else {
      moveMarker(staffMarker, p);
    }
    if (routeLine) map.removeLayer(routeLine);
    routeLine = L.polyline(line, road
      ? { color: "#1d4ed8", weight: 5, opacity: 0.75 }
      : { color: "#1d4ed8", weight: 3, dashArray: "6 8", opacity: 0.7 }).addTo(map);
    const bounds = L.latLngBounds(line);
    if (!fitted || !map.getBounds().contains(bounds)) {
      map.fitBounds(bounds, { padding: [50, 50], maxZoom: 16 });
      fitted = true;
    }
  }

  function render(data) {
    show($("content"), true);
    $("item").textContent = data.item_desc || "Your order";
    $("statusText").textContent = data.status === "delivered"
      ? `Delivered at ${data.delivered_at_local}`
      : data.status_text;

    const sub = {
      assigned: "We will show the delivery person on the map once they set off.",
      on_the_way: data.eta_minutes ? "Arrival time is an estimate and may change with traffic." : "Waiting for the first location update...",
      arrived: "Please meet the delivery person at your door.",
      delivered: "Thank you for shopping with us.",
      cancelled: "Please contact the shop if you have questions.",
    }[data.status] || "";
    $("statusSub").textContent = sub;

    show($("staffBox"), !!data.staff);
    if (data.staff) {
      $("staffName").textContent = data.staff.first_name;
      $("callBtn").href = `tel:${data.staff.phone}`;
    }

    $("address").textContent = `Delivering to: ${data.address}`;

    const ts = data.timestamps;
    const items = [
      ["Order placed", ts.created_at],
      ["Left the shop", ts.started_at],
      ["Arrived", ts.arrived_at],
      ["Delivered", ts.delivered_at],
    ].filter(([, t]) => t);
    $("timeline").innerHTML = "";
    for (const [label, t] of items) {
      const li = document.createElement("li");
      li.textContent = `${fmtTime(t)}  ${label}`;
      $("timeline").appendChild(li);
    }

    // Tell the customer if the location is old (e.g. the rider lost signal).
    const stale = data.status === "on_the_way" && data.last_position &&
      (Date.now() - new Date(data.last_position.recorded_at).getTime()) > 3 * 60 * 1000;
    show($("stale"), !!stale);
    if (stale) $("stale").textContent = `Location last updated at ${fmtTime(data.last_position.recorded_at)}.`;

    if (!map) initMap(data.destination);
    updateMap(data);
  }

  async function poll() {
    try {
      const res = await fetch(`/api/track/${encodeURIComponent(TOKEN)}`, { cache: "no-store" });
      if (res.status === 404) {
        show($("notFound"), true);
        show($("content"), false);
        return; // stop polling
      }
      if (res.ok) {
        const data = await res.json();
        render(data);
        if (data.status === "delivered" || data.status === "cancelled") return; // nothing more will change
      }
    } catch (_) { /* network blip: try again */ }
    timer = setTimeout(poll, POLL_MS);
  }

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && timer) { clearTimeout(timer); poll(); }
  });

  poll();
})();
