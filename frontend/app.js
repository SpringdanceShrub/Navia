const map = L.map("map", {
  zoomControl: false
}).setView([13.6929, -89.2182], 12);

L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "&copy; OpenStreetMap contributors"
}).addTo(map);

L.control.zoom({ position: "bottomright" }).addTo(map);

const els = {
  searchInput: document.getElementById("searchInput"),
  searchResults: document.getElementById("searchResults"),
  clearSearch: document.getElementById("clearSearch"),
  startInput: document.getElementById("startInput"),
  endInput: document.getElementById("endInput"),
  pickStart: document.getElementById("pickStart"),
  pickEnd: document.getElementById("pickEnd"),
  selectionHint: document.getElementById("selectionHint"),
  dayUp: document.getElementById("dayUp"),
  dayDown: document.getElementById("dayDown"),
  dayValue: document.getElementById("dayValue"),
  hourUp: document.getElementById("hourUp"),
  hourDown: document.getElementById("hourDown"),
  minuteUp: document.getElementById("minuteUp"),
  minuteDown: document.getElementById("minuteDown"),
  hourValue: document.getElementById("hourValue"),
  minuteValue: document.getElementById("minuteValue"),
  ampmToggle: document.getElementById("ampmToggle"),
  calculateBtn: document.getElementById("calculateBtn"),
  routesList: document.getElementById("routesList"),
  routeCount: document.getElementById("routeCount"),
  resetBtn: document.getElementById("resetBtn"),
  statusPill: document.getElementById("statusPill"),
  statusText: document.getElementById("statusText")
};

const days = ["Domingo", "Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado"];

let dayIndex = new Date().getDay();
let activeSelection = null;
let startPoint = null;
let endPoint = null;
let startMarker = null;
let endMarker = null;
let routeLayers = [];
let hour = 10;
let minute = 35;
let ampm = "AM";
let searchTimer = null;
let currentRoutes = [];

function setStatus(text) {
  if (!text) {
    els.statusPill.classList.add("hidden");
    return;
  }

  els.statusText.textContent = text;
  els.statusPill.classList.remove("hidden");
}

function markerIcon(kind) {
  const label = kind === "start" ? "I" : "F";

  return L.divIcon({
    className: "",
    html: `<div class="custom-marker ${kind}"><span>${label}</span></div>`,
    iconSize: [28, 28],
    iconAnchor: [8, 26]
  });
}

function setSelectionMode(kind) {
  activeSelection = kind;
  els.pickStart.classList.toggle("active", kind === "start");
  els.pickEnd.classList.toggle("active", kind === "end");
  els.pickStart.setAttribute("aria-pressed", String(kind === "start"));
  els.pickEnd.setAttribute("aria-pressed", String(kind === "end"));
  els.selectionHint.classList.add("active");

  if (kind === "start") {
    els.selectionHint.textContent = "Haz clic en el mapa para elegir el inicio.";
    setStatus("Seleccionando inicio");
  } else {
    els.selectionHint.textContent = "Haz clic en el mapa para elegir el destino.";
    setStatus("Seleccionando destino");
  }
}

function clearSelectionMode() {
  activeSelection = null;
  els.pickStart.classList.remove("active");
  els.pickEnd.classList.remove("active");
  els.pickStart.setAttribute("aria-pressed", "false");
  els.pickEnd.setAttribute("aria-pressed", "false");
  els.selectionHint.classList.remove("active");

  if (startPoint && endPoint) {
    els.selectionHint.textContent = "Inicio y destino listos.";
    setStatus("Inicio y destino listos");
  } else {
    els.selectionHint.textContent = "Elige inicio y destino desde el mapa.";
    setStatus("");
  }
}

function setPoint(kind, latlng, label = null) {
  const point = L.latLng(latlng);
  const text = label || `${point.lat.toFixed(5)}, ${point.lng.toFixed(5)}`;

  if (kind === "start") {
    startPoint = point;

    if (startMarker) {
      map.removeLayer(startMarker);
    }

    startMarker = L.marker(point, {
      icon: markerIcon("start")
    }).addTo(map);

    els.startInput.value = text;
  } else {
    endPoint = point;

    if (endMarker) {
      map.removeLayer(endMarker);
    }

    endMarker = L.marker(point, {
      icon: markerIcon("end")
    }).addTo(map);

    els.endInput.value = text;
  }

  clearRoutes();
  clearSelectionMode();
  updateReadyState();
}

map.on("click", event => {
  if (!activeSelection) return;
  setPoint(activeSelection, event.latlng);
});

els.pickStart.addEventListener("click", () => setSelectionMode("start"));
els.pickEnd.addEventListener("click", () => setSelectionMode("end"));

function renderDeparture() {
  els.dayValue.textContent = days[dayIndex];
  els.hourValue.textContent = String(hour).padStart(2, "0");
  els.minuteValue.textContent = String(minute).padStart(2, "0");
  els.ampmToggle.textContent = ampm;
}

function changeDay(delta) {
  dayIndex = (dayIndex + delta + days.length) % days.length;
  renderDeparture();
}

function changeHour(delta) {
  hour += delta;
  if (hour > 12) hour = 1;
  if (hour < 1) hour = 12;
  renderDeparture();
}

function changeMinute(delta) {
  minute += delta;

  if (minute >= 60) {
    minute = 0;
    changeHour(1);
    return;
  }

  if (minute < 0) {
    minute = 55;
    changeHour(-1);
    return;
  }

  renderDeparture();
}

els.dayUp.addEventListener("click", () => changeDay(1));
els.dayDown.addEventListener("click", () => changeDay(-1));
els.hourUp.addEventListener("click", () => changeHour(1));
els.hourDown.addEventListener("click", () => changeHour(-1));
els.minuteUp.addEventListener("click", () => changeMinute(5));
els.minuteDown.addEventListener("click", () => changeMinute(-5));

els.ampmToggle.addEventListener("click", () => {
  ampm = ampm === "AM" ? "PM" : "AM";
  renderDeparture();
});

function updateReadyState() {
  els.calculateBtn.disabled = !(startPoint && endPoint);
}

function clearRoutes() {
  routeLayers.forEach(layer => map.removeLayer(layer));
  routeLayers = [];
  currentRoutes = [];

  els.routeCount.textContent = "0";
  els.routesList.innerHTML =
    '<div class="empty-route">Selecciona inicio y fin para calcular rutas.</div>';
}

function resetAll() {
  if (startMarker) map.removeLayer(startMarker);
  if (endMarker) map.removeLayer(endMarker);

  startPoint = null;
  endPoint = null;
  startMarker = null;
  endMarker = null;

  els.startInput.value = "";
  els.endInput.value = "";
  els.calculateBtn.disabled = true;

  clearRoutes();
  clearSelectionMode();
}

els.resetBtn.addEventListener("click", resetAll);

function selectRoute(index) {
  routeLayers.forEach((layer, i) => {
    layer.setStyle({
      weight: i === index ? 7 : 5,
      opacity: i === index ? .95 : .3
    });

    if (i === index) {
      layer.bringToFront();
    }
  });

  document.querySelectorAll(".route-card").forEach((card, i) => {
    card.classList.toggle("active", i === index);
  });
}

function renderRoutes(routes) {
  clearRoutes();
  currentRoutes = routes;
  els.routeCount.textContent = String(routes.length);
  els.routesList.innerHTML = "";

  const colors = ["#516d58", "#819187", "#a7afa9"];

  routes.forEach((route, index) => {
    const latlngs = route.geometry.map(coord => [coord[1], coord[0]]);

    const line = L.polyline(latlngs, {
      color: colors[index] || "#819187",
      weight: index === 0 ? 7 : 5,
      opacity: index === 0 ? .95 : .35,
      lineCap: "round",
      lineJoin: "round"
    }).addTo(map);

    routeLayers.push(line);

    const card = document.createElement("div");
    card.className = `route-card ${index === 0 ? "active" : ""}`;
    card.innerHTML = `
      <div class="route-top">
        <span class="route-name">${route.name || `Ruta ${index + 1}`}</span>
        <span class="route-time">${route.minutes ?? "--"} min</span>
      </div>
      <div class="route-bottom">
        <span>${typeof route.distance_km === "number" ? route.distance_km.toFixed(1) : "--"} km</span>
        <span class="route-tag">${route.tag || ""}</span>
      </div>
    `;

    card.addEventListener("click", () => selectRoute(index));
    els.routesList.appendChild(card);
  });

  if (routeLayers.length) {
    const layers = [...routeLayers];

    if (startMarker) layers.push(startMarker);
    if (endMarker) layers.push(endMarker);

    map.fitBounds(L.featureGroup(layers).getBounds(), {
      paddingTopLeft: [70, 70],
      paddingBottomRight: [360, 70]
    });

    selectRoute(0);
    setStatus(`${routes.length} ruta${routes.length === 1 ? "" : "s"} calculada${routes.length === 1 ? "" : "s"}`);
  }
}

els.calculateBtn.addEventListener("click", async () => {
  if (!startPoint || !endPoint) return;

  els.calculateBtn.disabled = true;
  els.calculateBtn.textContent = "Calculando...";
  setStatus("Calculando rutas");

  try {
    const response = await fetch("/route", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        start: [startPoint.lng, startPoint.lat],
        end: [endPoint.lng, endPoint.lat],
        departure: {
          day: days[dayIndex],
          day_index: dayIndex,
          hour,
          minute,
          ampm
        }
      })
    });

    if (!response.ok) throw new Error();

    const data = await response.json();
    renderRoutes(data.routes || []);
  } catch {
    els.routesList.innerHTML =
      '<div class="empty-route">No se pudieron calcular las rutas.</div>';
    setStatus("No se pudieron calcular las rutas");
  } finally {
    els.calculateBtn.disabled = false;
    els.calculateBtn.textContent = "Calcular rutas";
  }
});

async function searchPlace() {
  const query = els.searchInput.value.trim();

  if (query.length < 2) {
    els.searchResults.classList.add("hidden");
    return;
  }

  els.searchResults.innerHTML =
    '<div class="search-result"><div class="search-result-icon">⌕</div><div><strong>Buscando...</strong><span>Red vial local</span></div></div>';

  els.searchResults.classList.remove("hidden");

  try {
    const response = await fetch(`/search?q=${encodeURIComponent(query)}`);

    if (!response.ok) throw new Error();

    const results = await response.json();
    els.searchResults.innerHTML = "";

    if (!results.length) {
      els.searchResults.innerHTML =
        '<div class="search-result"><div class="search-result-icon">⌕</div><div><strong>Sin resultados</strong><span>No hay coincidencias razonables</span></div></div>';
      return;
    }

    results.forEach(item => {
      const row = document.createElement("div");
      row.className = "search-result";

      row.innerHTML = `
        <div class="search-result-icon">⌖</div>
        <div>
          <strong>${escapeHtml(item.name)}</strong>
          <span>${escapeHtml(item.label || "El Salvador")}</span>
        </div>
        <div class="search-score">${item.score ? Math.round(item.score) + "%" : ""}</div>
      `;

      row.addEventListener("click", () => {
        map.flyTo([Number(item.lat), Number(item.lon)], 16, {
          duration: .65
        });

        if (activeSelection) {
          setPoint(
            activeSelection,
            [Number(item.lat), Number(item.lon)],
            item.label || item.name
          );
        }

        els.searchInput.value = item.name;
        els.clearSearch.disabled = false;
        els.searchResults.classList.add("hidden");
      });

      els.searchResults.appendChild(row);
    });
  } catch {
    els.searchResults.innerHTML =
      '<div class="search-result"><div class="search-result-icon">!</div><div><strong>Error de búsqueda</strong><span>Revisa roads.gpkg</span></div></div>';
  }
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

els.searchInput.addEventListener("input", () => {
  els.clearSearch.disabled = !els.searchInput.value;
  clearTimeout(searchTimer);
  searchTimer = setTimeout(searchPlace, 220);
});

els.clearSearch.addEventListener("click", () => {
  clearTimeout(searchTimer);
  els.searchInput.value = "";
  els.clearSearch.disabled = true;
  els.searchResults.innerHTML = "";
  els.searchResults.classList.add("hidden");
  els.searchInput.focus();
});

document.addEventListener("click", event => {
  if (!event.target.closest(".search-shell")) {
    els.searchResults.classList.add("hidden");
  }
});

renderDeparture();
