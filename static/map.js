/* ============================================================
   ATMOS map + data orchestration
   ============================================================ */

(function () {
'use strict';

const { renderDashboard, renderInsights, setInsightsStatus, setStatus, setLoading, showToast } = window.ATMOS;

/* ---------------- map ---------------- */

const map = L.map('map', { zoomControl: true }).setView([30.0, 70.0], 5);

// Free community OpenStreetMap tile servers: no API key, no account. Both were
// checked to serve real tiles even when the browser strips the Referer header.
// (CARTO watermarks street zooms with "API KEY REQUIRED", and
// tile.openstreetmap.org returns an "Access blocked" image without a Referer.)
const TILES = {
  primary: {
    name: 'OpenStreetMap Germany',
    url: 'https://tile.openstreetmap.de/{z}/{x}/{y}.png',
    opts: {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      maxZoom: 18,
    },
  },
  fallback: {
    name: 'OpenStreetMap Humanitarian',
    url: 'https://{s}.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png',
    opts: {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors, '
        + 'Tiles style by <a href="https://www.hotosm.org/">Humanitarian OpenStreetMap Team</a> '
        + 'hosted by <a href="https://openstreetmap.fr/">OpenStreetMap France</a>',
      subdomains: 'abc',
      maxZoom: 19,
    },
  },
};

let baseLayer = L.tileLayer(TILES.primary.url, TILES.primary.opts).addTo(map);
let switchedTiles = false;

// Fires only on real load failures (network or HTTP errors). A server that
// answers 200 with a watermark image can't be detected from the browser.
baseLayer.on('tileerror', () => {
  if (switchedTiles) return;
  switchedTiles = true;
  console.warn(`${TILES.primary.name} tiles unavailable — falling back to ${TILES.fallback.name}.`);
  map.removeLayer(baseLayer);
  baseLayer = L.tileLayer(TILES.fallback.url, TILES.fallback.opts).addTo(map);
});

// Leaflet mis-measures a container that was sized after init (or was hidden),
// which shows up as a blank or part-drawn map.
// Default invalidateSize keeps the view centred when the container resizes,
// so rotating a device doesn't slide the region of interest off-screen.
function fixMapSize() { map.invalidateSize(); }

// The header wraps onto more lines on narrow screens; expose its live height
// so the CSS can size the map to exactly the viewport space that's left.
const header = document.querySelector('.app-header');
function syncHeaderHeight() {
  document.documentElement.style.setProperty('--header-h', `${header.offsetHeight}px`);
}
syncHeaderHeight();

if ('ResizeObserver' in window) {
  // Catches every size change — rotation, split-screen, layout breakpoints,
  // the address bar collapsing — not just window resize events.
  new ResizeObserver(() => requestAnimationFrame(fixMapSize)).observe(document.getElementById('map'));
  new ResizeObserver(syncHeaderHeight).observe(header);
} else {
  window.addEventListener('resize', () => { syncHeaderHeight(); fixMapSize(); });
}
window.addEventListener('load', fixMapSize);
setTimeout(fixMapSize, 200);

const drawnItems = new L.FeatureGroup().addTo(map);

map.addControl(new L.Control.Draw({
  draw: {
    polygon: { shapeOptions: { color: '#3987e5', weight: 2, fillOpacity: 0.12 } },
    rectangle: { shapeOptions: { color: '#3987e5', weight: 2, fillOpacity: 0.12 } },
    polyline: false,
    circle: false,
    marker: false,
    circlemarker: false,
  },
  edit: { featureGroup: drawnItems },
}));

/* ---------------- state ---------------- */

const state = { coords: null, startDate: '', endDate: '' };
let inFlight = null;

// Identifies a set of inputs, so we can tell when on-screen results are stale.
const inputsKey = () => JSON.stringify([state.coords, state.startDate, state.endDate]);
let shownKey = null;   // inputs behind the results currently displayed

const els = {
  start: document.getElementById('startDate'),
  end: document.getElementById('endDate'),
  run: document.getElementById('run-btn'),
  roi: document.getElementById('roi-status'),
};

/* ---------------- dates ---------------- */

const iso = (d) => d.toISOString().slice(0, 10);

function setRange(months) {
  const end = new Date();
  const start = new Date();
  start.setMonth(start.getMonth() - months);
  els.start.value = iso(start);
  els.end.value = iso(end);
  syncDates();
}

function syncDates() {
  state.startDate = els.start.value;
  state.endDate = els.end.value;
  // Keep the two inputs from crossing over.
  els.start.max = state.endDate || iso(new Date());
  els.end.min = state.startDate || '';
  noteInputsChanged();
}

els.start.addEventListener('change', syncDates);
els.end.addEventListener('change', syncDates);

document.querySelectorAll('.preset').forEach((btn) => {
  btn.addEventListener('click', () => setRange(Number(btn.dataset.months)));
});

els.end.max = iso(new Date());
setRange(6);

/* ---------------- region ---------------- */

function describeArea(layer) {
  try {
    const ring = layer.getLatLngs()[0];
    const m2 = L.GeometryUtil.geodesicArea(ring);
    const km2 = m2 / 1e6;
    return km2 >= 1 ? `${km2.toLocaleString('en', { maximumFractionDigits: 0 })} km²`
                    : `${Math.round(m2).toLocaleString('en')} m²`;
  } catch {
    return 'region selected';
  }
}

function refreshRunButton() {
  els.run.disabled = !(state.coords && state.startDate && state.endDate) || Boolean(inFlight);
}

/**
 * Any input change only updates the UI. Queries run exclusively from the
 * Submit button, so drawing or editing a region never hits the backend.
 */
function noteInputsChanged() {
  refreshRunButton();
  if (inFlight) return;   // keep the busy indicator for the submitted query

  if (!state.coords) {
    setStatus('idle', 'Awaiting region');
  } else if (!shownKey) {
    setStatus('idle', 'Region ready — press Submit');
  } else if (shownKey !== inputsKey()) {
    setStatus('idle', 'Inputs changed — press Submit to update');
  } else {
    setStatus('ready', 'Results up to date');
  }
}

map.on(L.Draw.Event.CREATED, (e) => {
  drawnItems.clearLayers();
  drawnItems.addLayer(e.layer);
  state.coords = e.layer.toGeoJSON().geometry.coordinates;

  els.roi.textContent = `ROI · ${describeArea(e.layer)}`;
  els.roi.dataset.active = 'true';
  noteInputsChanged();
});

map.on(L.Draw.Event.EDITED, (e) => {
  e.layers.eachLayer((layer) => {
    state.coords = layer.toGeoJSON().geometry.coordinates;
    els.roi.textContent = `ROI · ${describeArea(layer)}`;
  });
  noteInputsChanged();
});

map.on(L.Draw.Event.DELETED, () => {
  state.coords = null;
  els.roi.textContent = 'No region selected';
  els.roi.dataset.active = 'false';
  noteInputsChanged();
});

els.run.addEventListener('click', runAnalysis);

/* ---------------- data ---------------- */

/**
 * ML insights run on the rows already on screen, in the background: the
 * charts are usable immediately and the insights fill in when ready.
 */
async function runInsights(rows, key) {
  setInsightsStatus('busy', 'Analysing…');
  try {
    const response = await fetch('/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rows }),
    });
    const insights = await response.json();
    if (!response.ok || insights?.error) {
      throw new Error(insights?.error || `Analysis failed (${response.status})`);
    }
    if (key !== shownKey) return;   // newer results replaced these rows
    renderInsights(insights);
  } catch (err) {
    if (key !== shownKey) return;
    console.error(err);
    setInsightsStatus('error', 'Analysis failed');
  }
}

async function runAnalysis() {
  if (!state.coords) {
    showToast('Draw a region on the map first.');
    return;
  }
  if (!state.startDate || !state.endDate) {
    showToast('Select both a start and an end date.');
    return;
  }
  if (state.startDate >= state.endDate) {
    showToast('The start date must fall before the end date.');
    return;
  }

  // Supersede any request still running — the newest selection wins.
  inFlight?.abort();
  const controller = new AbortController();
  inFlight = controller;
  const submittedKey = inputsKey();

  setLoading(true);
  setStatus('busy', 'Querying Earth Engine…');
  refreshRunButton();

  try {
    const response = await fetch('/get_data', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        coords: state.coords,
        startDate: state.startDate,
        endDate: state.endDate,
      }),
      signal: controller.signal,
    });

    const payload = await response.json();
    if (!response.ok || payload?.error) {
      throw new Error(payload?.error || `Request failed (${response.status})`);
    }
    if (!Array.isArray(payload) || payload.length === 0) {
      setStatus('ready', 'No data for this range');
      showToast('No imagery was available for that region and date range.');
      return;
    }

    renderDashboard(payload);
    shownKey = submittedKey;
    if (submittedKey === inputsKey()) {
      setStatus('ready', `${payload.length} month${payload.length === 1 ? '' : 's'} analysed`);
    } else {
      // The region or dates were changed while this query was running.
      setStatus('idle', 'Inputs changed — press Submit to update');
    }
    runInsights(payload, submittedKey);
  } catch (err) {
    if (err.name === 'AbortError') return;   // superseded, not a failure
    console.error(err);
    setStatus('error', 'Analysis failed');
    showToast(err.message || 'The analysis request failed.');
  } finally {
    if (inFlight === controller) {
      inFlight = null;
      setLoading(false);
      refreshRunButton();
    }
  }
}

})();
