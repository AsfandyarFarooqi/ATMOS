/* ============================================================
   ATMOS chart layer
   Hues are the validated dark steps for the #1b222d card
   surface. The blue/red diverging pair used by the balance
   chart clears CVD (dE 19.2 protan) and normal-vision (29.0)
   separation. Metrics are faceted into separate titled cards
   rather than competing for hue inside one plot.
   ============================================================ */

/* Wrapped in an IIFE: plain <script> tags share one global lexical scope, so
   top-level declarations here would collide with map.js. Only window.ATMOS
   is exported. */
(function () {
'use strict';

const CHART_THEME = {
  surface:  '#1b222d',
  blue:     '#3987e5',
  orange:   '#d95926',
  aqua:     '#199e70',
  red:      '#e66767',

  ink:      '#e9eff7',
  ink2:     '#a7b6c7',
  muted:    '#7d8ea0',
  grid:     'rgba(255,255,255,0.06)',
  axis:     'rgba(255,255,255,0.14)',
};

// Recessive chrome everywhere; series color never leaks into text.
Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
Chart.defaults.font.size = 11;
Chart.defaults.color = CHART_THEME.muted;
Chart.defaults.animation.duration = 480;
Chart.defaults.animation.easing = 'easeOutQuart';

const charts = {};

/* ---------------- formatting ---------------- */

const compact = new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 2 });
const precise = new Intl.NumberFormat('en', { maximumFractionDigits: 3 });
// Stat tiles: 3 significant figures (24.2 °C, 0.575 idx); exact values are in the table.
const tile = new Intl.NumberFormat('en', { maximumSignificantDigits: 3 });

function fmt(value, unit = '', { small = false } = {}) {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  const n = Number(value);
  const body = small || Math.abs(n) < 1000 ? precise.format(n) : compact.format(n);
  return unit ? `${body} ${unit}` : body;
}

/* ---------------- plugins ---------------- */

/** Vertical crosshair under the hovered point (line/area charts). */
const crosshair = {
  id: 'crosshair',
  afterDatasetsDraw(chart, _args, opts) {
    if (opts?.enabled === false) return;
    const active = chart.tooltip?.getActiveElements?.();
    if (!active?.length) return;
    const { ctx, chartArea } = chart;
    ctx.save();
    ctx.beginPath();
    ctx.moveTo(active[0].element.x, chartArea.top);
    ctx.lineTo(active[0].element.x, chartArea.bottom);
    ctx.lineWidth = 1;
    ctx.strokeStyle = 'rgba(255,255,255,0.22)';
    ctx.stroke();
    ctx.restore();
  },
};

/**
 * Selective direct label — marks only the peak, never every point.
 * Skipped when the peak sits too near an edge to render cleanly.
 */
const peakLabel = {
  id: 'peakLabel',
  afterDatasetsDraw(chart, _args, opts) {
    if (opts?.enabled === false) return;
    const meta = chart.getDatasetMeta(0);
    const data = chart.data.datasets[0].data;
    // Skipped on narrow charts, where the label would crowd the plot.
    if (!meta?.data?.length || data.length < 3 || chart.width < 300) return;

    let peak = -1, best = -Infinity;
    data.forEach((v, i) => {
      if (v !== null && v !== undefined && Math.abs(v) > best) { best = Math.abs(v); peak = i; }
    });
    if (peak < 0 || !meta.data[peak]) return;

    const point = meta.data[peak];
    const { ctx, chartArea } = chart;
    const text = fmt(data[peak], opts?.unit ?? '');

    ctx.save();
    ctx.font = `600 11px ${Chart.defaults.font.family}`;
    const width = ctx.measureText(text).width + 12;
    let x = point.x - width / 2;
    x = Math.max(chartArea.left, Math.min(x, chartArea.right - width));
    const y = Math.max(chartArea.top + 2, point.y - 26);

    ctx.fillStyle = 'rgba(13,16,20,0.88)';
    ctx.strokeStyle = 'rgba(255,255,255,0.12)';
    ctx.beginPath();
    ctx.roundRect(x, y, width, 19, 5);
    ctx.fill();
    ctx.stroke();

    ctx.fillStyle = CHART_THEME.ink;   // text token, not the series color
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(text, x + width / 2, y + 10);
    ctx.restore();
  },
};

Chart.register(crosshair, peakLabel);

/* ---------------- shared options ---------------- */

function verticalFill(ctx, area, hex) {
  if (!area) return hexToRgba(hex, 0.18);
  const g = ctx.createLinearGradient(0, area.top, 0, area.bottom);
  g.addColorStop(0, hexToRgba(hex, 0.32));
  g.addColorStop(1, hexToRgba(hex, 0.01));
  return g;
}

function hexToRgba(hex, alpha) {
  const h = hex.replace('#', '');
  const int = parseInt(h, 16);
  return `rgba(${(int >> 16) & 255}, ${(int >> 8) & 255}, ${int & 255}, ${alpha})`;
}

function baseOptions({ yLabel, unit = '', small = false }) {
  return {
    responsive: true,
    maintainAspectRatio: false,
    resizeDelay: 60,   // debounce redraws while a window is dragged or rotated
    layout: { padding: { top: 22, right: 6, bottom: 2, left: 2 } },
    // Generous hit area so you never have to land dead-centre on a point.
    interaction: { mode: 'index', intersect: false, axis: 'x' },
    plugins: {
      legend: { display: false },   // single series — the card title names it
      tooltip: {
        backgroundColor: 'rgba(13,16,20,0.95)',
        borderColor: 'rgba(255,255,255,0.12)',
        borderWidth: 1,
        titleColor: CHART_THEME.ink,
        bodyColor: CHART_THEME.ink2,
        padding: 10,
        cornerRadius: 8,
        displayColors: false,
        callbacks: {
          label: (c) => fmt(c.parsed.y, unit, { small }),
        },
      },
    },
    scales: {
      x: {
        border: { color: CHART_THEME.axis },
        grid: { display: false },
        ticks: {
          color: CHART_THEME.muted,
          maxRotation: 0,
          autoSkipPadding: 14,
          font: (c) => ({ size: c.chart.width < 360 ? 10 : 11 }),  // tighter on small charts
        },
      },
      y: {
        title: { display: !!yLabel, text: yLabel, color: CHART_THEME.muted, font: { size: 10 } },
        border: { display: false },
        // Solid hairlines, one shade off the surface — never dashed.
        grid: { color: CHART_THEME.grid, lineWidth: 1, drawTicks: false },
        ticks: {
          color: CHART_THEME.muted,
          padding: 8,
          callback: (v) => (small ? precise.format(v) : compact.format(v)),
        },
      },
    },
  };
}

function destroy(id) {
  if (charts[id]) { charts[id].destroy(); delete charts[id]; }
}

/* ---------------- renderers ---------------- */

/** Diverging bars: blue above zero (sink), red below (source). */
function renderBalanceChart(labels, values) {
  const id = 'cBalance-chart';
  destroy(id);
  const colorFor = (v) => (v >= 0 ? CHART_THEME.blue : CHART_THEME.red);

  charts[id] = new Chart(document.getElementById(id), {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        label: 'Net carbon balance',
        data: values,
        backgroundColor: values.map((v) => hexToRgba(colorFor(v), 0.82)),
        hoverBackgroundColor: values.map((v) => colorFor(v)),
        borderRadius: 4,           // rounded data-ends, anchored to the baseline
        borderSkipped: false,
        barPercentage: 0.72,       // the gap between bars is surface, not a border
        categoryPercentage: 0.86,
      }],
    },
    options: {
      ...baseOptions({ yLabel: 'CO₂ (kg)', unit: 'kg' }),
      plugins: {
        ...baseOptions({ yLabel: 'CO₂ (kg)', unit: 'kg' }).plugins,
        peakLabel: { enabled: false },
        tooltip: {
          ...baseOptions({ unit: 'kg' }).plugins.tooltip,
          callbacks: {
            label: (c) => `${fmt(c.parsed.y, 'kg CO₂')} · ${c.parsed.y >= 0 ? 'net sink' : 'net source'}`,
          },
        },
      },
    },
  });
}

/** Filled area line — the default for a single continuous series. */
function renderAreaChart(id, labels, values, hex, { yLabel, unit = '', small = false }) {
  destroy(id);
  const canvas = document.getElementById(id);
  if (!canvas) return;

  charts[id] = new Chart(canvas, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        data: values,
        borderColor: hex,
        borderWidth: 2,                       // thin marks
        tension: 0.35,
        fill: true,
        backgroundColor: (c) => verticalFill(c.chart.ctx, c.chart.chartArea, hex),
        pointRadius: 0,
        pointHoverRadius: 5,                  // >= 8px diameter on hover
        pointBackgroundColor: hex,
        pointBorderColor: CHART_THEME.surface,
        pointBorderWidth: 2,                  // 2px surface ring
        pointHitRadius: 24,                   // forgiving hit target
        spanGaps: false,
      }],
    },
    options: {
      ...baseOptions({ yLabel, unit, small }),
      plugins: { ...baseOptions({ yLabel, unit, small }).plugins, peakLabel: { unit } },
    },
  });
}

/** Bars for discrete accumulated totals (precipitation). */
function renderBarChart(id, labels, values, hex, { yLabel, unit = '', small = false }) {
  destroy(id);
  const canvas = document.getElementById(id);
  if (!canvas) return;

  charts[id] = new Chart(canvas, {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        data: values,
        backgroundColor: hexToRgba(hex, 0.72),
        hoverBackgroundColor: hex,
        borderRadius: 4,
        borderSkipped: false,
        barPercentage: 0.72,
        categoryPercentage: 0.86,
      }],
    },
    options: {
      ...baseOptions({ yLabel, unit, small }),
      plugins: { ...baseOptions({ yLabel, unit, small }).plugins, peakLabel: { enabled: false } },
    },
  });
}

/* ---------------- stat tiles ---------------- */

function mean(values) {
  const clean = values.filter((v) => v !== null && v !== undefined && !Number.isNaN(v));
  return clean.length ? clean.reduce((a, b) => a + b, 0) / clean.length : null;
}

function sum(values) {
  const clean = values.filter((v) => v !== null && v !== undefined && !Number.isNaN(v));
  return clean.length ? clean.reduce((a, b) => a + b, 0) : null;
}

function setStat(id, value, unit, meta, dir) {
  const valueEl = document.getElementById(`stat-${id}`);
  const metaEl = document.getElementById(`stat-${id}-meta`);
  if (!valueEl) return;

  const text = value === null ? '' : (Math.abs(value) < 1000 ? tile.format(value) : compact.format(value));
  valueEl.innerHTML = value === null ? '—' : `${text}<span class="stat__unit">${unit}</span>`;
  if (metaEl) {
    metaEl.textContent = meta;
    if (dir) metaEl.dataset.dir = dir; else delete metaEl.dataset.dir;
  }
}

function renderStats(rows) {
  const months = `${rows.length} month${rows.length === 1 ? '' : 's'}`;

  // Mean, not sum: the balance includes the AGB *stock*, and adding a stock
  // up across months would count the same biomass once per month.
  const balance = mean(rows.map((r) => r.Carbon_Balance_kg));
  const temp = mean(rows.map((r) => r.Temperature_C));
  const precep = sum(rows.map((r) => r.Precipitation_mm));   // monthly totals add up
  const uvai = mean(rows.map((r) => r.UVAI_index));

  setStat('balance', balance, 'kg CO₂', balance === null ? 'No data' : `Mean per month across ${months}`);
  setStat('temp', temp, '°C', `Mean across ${months}`);
  setStat('precep', precep, 'mm', `Total across ${months}`);
  setStat('uvai', uvai, 'idx', `Mean across ${months} · dimensionless`);
}

/* ---------------- table view ---------------- */

function renderTable(rows) {
  const body = document.getElementById('monthly-summary-body');
  if (!body) return;

  if (!rows.length) {
    body.innerHTML = '<tr><td class="empty-row" colspan="7">No data for this region and range.</td></tr>';
    return;
  }

  body.innerHTML = rows.map((r) => {
    const balance = r.Carbon_Balance_kg;
    const cls = balance === null || balance === undefined ? '' : (balance >= 0 ? 'pos' : 'neg');
    return `<tr data-month="${r.month}">
      <td>${r.month}</td>
      <td>${fmt(r.AGB_CO2_kg)}</td>
      <td>${fmt(r.GPP_CO2_kg)}</td>
      <td>${fmt(r.UVAI_index, '', { small: true })}</td>
      <td class="${cls}">${fmt(balance)}</td>
      <td>${fmt(r.Temperature_C, '', { small: true })}</td>
      <td>${fmt(r.Precipitation_mm, '', { small: true })}</td>
    </tr>`;
  }).join('');
}

/* ---------------- orchestration ---------------- */

function renderDashboard(rows) {
  const labels = rows.map((r) => r.month);

  renderStats(rows);
  renderBalanceChart(labels, rows.map((r) => r.Carbon_Balance_kg));

  renderAreaChart('agb-chart', labels, rows.map((r) => r.AGB_CO2_kg),
    CHART_THEME.blue, { yLabel: 'CO₂ (kg)', unit: 'kg' });

  renderAreaChart('gpp-chart', labels, rows.map((r) => r.GPP_CO2_kg),
    CHART_THEME.blue, { yLabel: 'CO₂ (kg)', unit: 'kg' });

  renderAreaChart('temp-chart', labels, rows.map((r) => r.Temperature_C),
    CHART_THEME.orange, { yLabel: '°C', unit: '°C', small: true });

  renderBarChart('precep-chart', labels, rows.map((r) => r.Precipitation_mm),
    CHART_THEME.blue, { yLabel: 'mm', unit: 'mm', small: true });

  renderAreaChart('uvai-chart', labels, rows.map((r) => r.UVAI_index),
    CHART_THEME.aqua, { yLabel: 'index', unit: '', small: true });

  renderTable(rows);

  document.getElementById('empty-state').hidden = true;
  document.getElementById('charts').hidden = false;
}

/* ---------------- ML insights ---------------- */

const FINDING_ICON = { forecast: '⟶', trend: '↗', anomaly: '⚠', driver: '◎', regime: '◐', data: 'ℹ' };
// Regime scatter is an all-pairs context: only the first three validated slots.
const REGIME_COLORS = [CHART_THEME.blue, CHART_THEME.orange, CHART_THEME.aqua];
const FORECAST_COLOR = {
  GPP_CO2_kg: CHART_THEME.blue,
  Temperature_C: CHART_THEME.orange,
  Precipitation_mm: CHART_THEME.blue,
};

let insightsData = null;
let forecastMetric = 'GPP_CO2_kg';

const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
const escapeHtml = (s) => Array.from(String(s), (ch) => ESC[ch] || ch).join('');

function setInsightsStatus(state, text) {
  const badge = document.getElementById('insights-meta');
  const card = document.querySelector('.card--insights');
  const list = document.getElementById('findings');
  if (!badge || !card || !list) return;

  badge.dataset.state = state;
  badge.textContent = text;
  card.dataset.busy = String(state === 'busy');   // dims, never blanks, old findings

  if (state === 'busy' && !list.children.length) {
    list.innerHTML = '<li class="finding finding--skeleton"></li>'.repeat(3);
  }
  if (state === 'error' && (!list.children.length || list.querySelector('.finding--skeleton'))) {
    list.innerHTML = '<li class="finding"><span class="finding__icon" aria-hidden="true">!</span>'
      + '<span class="finding__title">Insights unavailable</span>'
      + '<span class="finding__text">The charts below are unaffected. Press Submit to try again.</span></li>';
  }
}

function renderFindings(findings) {
  const list = document.getElementById('findings');
  if (!findings.length) {
    list.innerHTML = '<li class="finding"><span class="finding__icon" aria-hidden="true">ℹ</span>'
      + '<span class="finding__title">Nothing notable</span>'
      + '<span class="finding__text">No reliable pattern was found in this period.</span></li>';
    return;
  }
  list.innerHTML = findings.map((f) => `
    <li class="finding">
      <span class="finding__icon" aria-hidden="true">${FINDING_ICON[f.kind] || '•'}</span>
      <span class="finding__title">${escapeHtml(f.title)}
        <span class="conf" data-level="${escapeHtml(f.confidence)}">${escapeHtml(f.confidence)} confidence</span>
      </span>
      <span class="finding__text">${escapeHtml(f.text)}</span>
    </li>`).join('');
}

/** Observed line, dashed forecast joined to the last observation, 95% band. */
function renderForecast(metric) {
  const id = 'forecast-chart';
  const note = document.getElementById('forecast-note');
  const fc = insightsData?.forecasts?.[metric];
  destroy(id);

  document.querySelectorAll('#forecast-toggle button').forEach((btn) => {
    btn.disabled = !insightsData?.forecasts?.[btn.dataset.metric];
    btn.setAttribute('aria-selected', String(btn.dataset.metric === metric));
  });
  if (!fc) {
    note.textContent = 'Not enough data to forecast this metric (needs at least 4 months).';
    return;
  }

  const hex = FORECAST_COLOR[metric];
  const small = metric !== 'GPP_CO2_kg';
  const unit = fc.unit;
  const n = fc.history.length;
  const last = fc.history[n - 1].value;
  const lead = Array(n - 1).fill(null);
  const tail = Array(fc.forecast.length).fill(null);
  const base = baseOptions({ yLabel: unit || 'index', unit, small });

  charts[id] = new Chart(document.getElementById(id), {
    type: 'line',
    data: {
      labels: [...fc.history.map((h) => h.month), ...fc.forecast.map((f) => f.month)],
      datasets: [
        { label: 'upper', data: [...lead, last, ...fc.forecast.map((f) => f.upper)],
          borderWidth: 0, pointRadius: 0, pointHitRadius: 0, fill: false },
        { label: '95% interval', data: [...lead, last, ...fc.forecast.map((f) => f.lower)],
          borderWidth: 0, pointRadius: 0, pointHitRadius: 0, fill: '-1', backgroundColor: hexToRgba(hex, 0.2) },
        { label: 'Observed', data: [...fc.history.map((h) => h.value), ...tail],
          borderColor: hex, borderWidth: 2, tension: 0.3, pointRadius: 0, pointHoverRadius: 5,
          pointBackgroundColor: hex, pointBorderColor: CHART_THEME.surface, pointBorderWidth: 2, pointHitRadius: 24 },
        { label: 'Forecast', data: [...lead, last, ...fc.forecast.map((f) => f.mean)],
          borderColor: hex, borderWidth: 2, borderDash: [6, 4], tension: 0.3,
          pointRadius: (c) => (c.dataIndex >= n ? 4 : 0), pointHoverRadius: 6,
          pointBackgroundColor: CHART_THEME.surface, pointBorderColor: hex, pointBorderWidth: 2, pointHitRadius: 24 },
      ],
    },
    options: {
      ...base,
      plugins: {
        ...base.plugins,
        peakLabel: { enabled: false },
        tooltip: {
          ...base.plugins.tooltip,
          // Hide the helper series and the duplicated join point.
          filter: (item) => item.raw !== null && item.dataset.label !== 'upper'
            && (item.dataset.label === 'Observed' || item.dataIndex >= n),
          callbacks: {
            label: (c) => {
              if (c.dataset.label === '95% interval') {
                const up = c.chart.data.datasets[0].data[c.dataIndex];
                return '95% interval: ' + fmt(c.parsed.y, unit, { small }) + ' – ' + fmt(up, unit, { small });
              }
              return c.dataset.label + ': ' + fmt(c.parsed.y, unit, { small });
            },
          },
        },
      },
    },
  });

  const bt = fc.backtest;
  note.textContent = bt
    ? 'Held-out check: forecasting ' + bt.month + ' without seeing it gave ' + fmt(bt.predicted, unit, { small })
      + ' against an actual ' + fmt(bt.actual, unit, { small }) + ' — '
      + (bt.within_interval ? 'inside' : 'outside') + ' its 95% interval.'
    : 'Gaussian-process regression; the band widens the further ahead it looks.';
}

document.getElementById('forecast-toggle')?.addEventListener('click', (e) => {
  const btn = e.target.closest('button[data-metric]');
  if (!btn || btn.disabled) return;
  forecastMetric = btn.dataset.metric;
  renderForecast(forecastMetric);
});

/** Diverging horizontal bars: blue raises GPP uptake, red lowers it. */
function renderDrivers(dr) {
  const id = 'drivers-chart';
  const note = document.getElementById('drivers-note');
  destroy(id);
  if (!dr) {
    note.textContent = 'Needs at least 6 months with both GPP and climate data.';
    return;
  }

  const items = dr.drivers;
  const values = items.map((d) => d.effect_sd);
  const colorFor = (v) => (v >= 0 ? CHART_THEME.blue : CHART_THEME.red);
  const base = baseOptions({});

  charts[id] = new Chart(document.getElementById(id), {
    type: 'bar',
    data: {
      labels: items.map((d) => d.label),
      datasets: [{
        data: values,
        backgroundColor: values.map((v) => hexToRgba(colorFor(v), 0.82)),
        hoverBackgroundColor: values.map(colorFor),
        borderRadius: 4,
        borderSkipped: false,
        barPercentage: 0.62,
        categoryPercentage: 0.9,
      }],
    },
    options: {
      ...base,
      indexAxis: 'y',
      interaction: { mode: 'nearest', intersect: false, axis: 'y' },
      plugins: {
        ...base.plugins,
        crosshair: { enabled: false },
        peakLabel: { enabled: false },
        tooltip: {
          ...base.plugins.tooltip,
          callbacks: {
            label: (c) => {
              const d = items[c.dataIndex];
              const rho = d.spearman_rho === null ? '—' : d.spearman_rho.toFixed(2);
              return [(c.parsed.x >= 0 ? '+' : '') + c.parsed.x.toFixed(2) + ' SD per SD', 'Spearman ρ = ' + rho];
            },
          },
        },
      },
      scales: {
        x: {
          suggestedMin: -1,
          suggestedMax: 1,
          border: { display: false },
          grid: { color: (c) => (c.tick.value === 0 ? CHART_THEME.axis : CHART_THEME.grid), drawTicks: false },
          ticks: { color: CHART_THEME.muted, padding: 6 },
          title: { display: true, text: 'Effect on GPP uptake (SD)', color: CHART_THEME.muted, font: { size: 10 } },
        },
        y: { border: { color: CHART_THEME.axis }, grid: { display: false }, ticks: { color: CHART_THEME.ink2 } },
      },
    },
  });

  let text = 'Leave-one-out R² = ' + dr.r2_loo.toFixed(2) + ' (' + dr.reliability + ', ' + dr.n
    + ' months): how well climate predicts months the model never saw.';
  if (dr.collinear.length) {
    text += ' ' + dr.collinear.map((pair) => pair.join(' & ')).join('; ')
      + ' move together here, so how the effect is split between them is uncertain.';
  }
  note.textContent = text;
}

/** Temperature × precipitation scatter, one validated hue per regime. */
function renderRegimes(rg) {
  const id = 'regimes-chart';
  const note = document.getElementById('regimes-note');
  const legend = document.getElementById('regimes-legend');
  destroy(id);
  legend.innerHTML = '';

  if (!rg) {
    note.textContent = 'Needs at least 6 months with temperature and precipitation.';
    return;
  }
  if (!rg.found) {
    note.textContent = rg.silhouette === null
      ? 'Temperature or rainfall barely varied, so there are no regimes to find.'
      : 'No distinct regimes: the best grouping scored a silhouette of ' + rg.silhouette.toFixed(2) + ' (needs ≥ 0.25).';
    return;
  }

  legend.innerHTML = rg.groups.map((g) => '<span class="legend__item">'
    + '<span class="legend__swatch" style="border-radius:50%;background:' + REGIME_COLORS[g.id] + '"></span>'
    + escapeHtml(g.label) + ' · ' + g.months.length + ' mo</span>').join('');

  const base = baseOptions({ yLabel: '°C', unit: '°C', small: true });
  charts[id] = new Chart(document.getElementById(id), {
    type: 'scatter',
    data: {
      datasets: rg.groups.map((g) => ({
        label: g.label,
        data: rg.points.filter((p) => p.regime === g.id)
          .map((p) => ({ x: p.precipitation_mm, y: p.temperature_c, month: p.month })),
        backgroundColor: REGIME_COLORS[g.id],
        borderColor: CHART_THEME.surface,   // 2px surface ring where markers overlap
        borderWidth: 2,
        pointRadius: 6,
        pointHoverRadius: 8,
        pointHitRadius: 14,
      })),
    },
    options: {
      ...base,
      interaction: { mode: 'nearest', intersect: false },
      plugins: {
        ...base.plugins,
        crosshair: { enabled: false },
        peakLabel: { enabled: false },
        tooltip: {
          ...base.plugins.tooltip,
          callbacks: {
            title: (items) => items[0]?.raw.month,
            label: (c) => [c.dataset.label, fmt(c.parsed.y, '°C', { small: true }) + ' · ' + fmt(c.parsed.x, 'mm', { small: true })],
          },
        },
      },
      scales: {
        x: {
          type: 'linear',
          title: { display: true, text: 'Precipitation (mm)', color: CHART_THEME.muted, font: { size: 10 } },
          border: { color: CHART_THEME.axis },
          grid: { color: CHART_THEME.grid, drawTicks: false },
          ticks: { color: CHART_THEME.muted, padding: 6 },
        },
        y: base.scales.y,
      },
    },
  });

  const uptake = rg.groups.filter((g) => g.mean_gpp_kg !== null)
    .map((g) => g.label + ': ' + fmt(g.mean_gpp_kg, 'kg CO₂') + ' mean uptake').join(' · ');
  note.textContent = 'Silhouette ' + rg.silhouette.toFixed(2) + ' (1 = perfectly separated). ' + uptake;
}

function markAnomalies(an) {
  document.querySelectorAll('#monthly-summary-body tr[data-anomaly]').forEach((tr) => {
    tr.removeAttribute('data-anomaly');
    tr.removeAttribute('title');
  });
  (an?.months || []).forEach((a) => {
    const tr = document.querySelector('#monthly-summary-body tr[data-month="' + a.month + '"]');
    if (!tr) return;
    tr.dataset.anomaly = a.metric;
    tr.title = 'Anomaly: ' + a.label + ' unusually ' + a.direction + ' (modified z = ' + a.modified_z.toFixed(1) + ')';
  });
}

function renderInsights(data) {
  insightsData = data;
  const findings = data.findings || [];
  const n = data.n_months;

  renderFindings(findings);
  setInsightsStatus('ready', findings.length + ' finding' + (findings.length === 1 ? '' : 's')
    + ' · ' + n + ' month' + (n === 1 ? '' : 's'));

  const skipped = document.getElementById('insights-skipped');
  const skips = data.skipped || [];
  skipped.hidden = !skips.length;
  skipped.textContent = skips.length
    ? 'Skipped: ' + skips.map((s) => s.analysis + ' (' + s.reason + ')').join('; ') + '.'
    : '';

  if (!data.forecasts?.[forecastMetric]) {
    forecastMetric = Object.keys(data.forecasts || {})[0] || 'GPP_CO2_kg';
  }
  renderForecast(forecastMetric);
  renderDrivers(data.drivers);
  renderRegimes(data.regimes);
  markAnomalies(data.anomalies);
}

/* ---------------- UI helpers ---------------- */

function setStatus(state, text) {
  const el = document.getElementById('app-status');
  if (!el) return;
  el.dataset.state = state;
  document.getElementById('app-status-text').textContent = text;
}

function setLoading(isLoading) {
  document.getElementById('dashboard').dataset.loading = String(isLoading);
}

let toastTimer;
function showToast(message) {
  const el = document.getElementById('toast');
  if (!el) return;
  el.textContent = message;
  el.dataset.show = 'true';
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.dataset.show = 'false'; }, 6000);
}

window.ATMOS = { renderDashboard, renderInsights, setInsightsStatus, setStatus, setLoading, showToast };

})();
