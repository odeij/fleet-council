/**
 * @fileoverview Fleet dashboard. Fetches /api/state every few seconds and
 * renders summary tiles, one card per machine, the jobs table, and the
 * processes and logs of the selected machine. No dependencies, no build step.
 */

const STATE_POLL_MS = 5000;
const LOG_POLL_MS = 10000;
const LOG_LINES = 80;
const MAX_JOBS = 12;
const MAX_LOG_BUTTONS = 16;
const HOT_TEMP_C = 83;
const WARN_PERCENT = 85;
const CRIT_PERCENT = 95;
const THEME_KEY = 'fleet-theme';

/** Everything the page knows. Rendering is a pure function of this. */
const view = {
  /** @type {?Object} Last /api/state response. */
  data: null,
  /** @type {?string} Machine whose processes and logs are open. */
  selectedHost: null,
  /** @type {?string} Path of the log being tailed. */
  selectedLog: null,
  /** @type {string} Last text fetched for the selected log. */
  logText: '',
  /** @type {number} Timer id of the next log refresh. */
  logTimer: 0,
};

/**
 * @param {string} id
 * @return {!HTMLElement}
 */
const byId = (id) => document.getElementById(id);

// ---------------------------------------------------------------------------
// Formatting
// ---------------------------------------------------------------------------

const HTML_ESCAPES =
    {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', '\'': '&#39;'};

/**
 * Escapes text for use in HTML content and quoted attributes.
 * @param {*} value
 * @return {string}
 */
function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => HTML_ESCAPES[c]);
}

/**
 * @param {number} mib Size in MiB.
 * @return {string} Size in GB, one decimal below 10 GB.
 */
function formatGb(mib) {
  return (mib / 1024).toFixed(mib >= 10240 ? 0 : 1);
}

/**
 * @param {number} seconds
 * @return {string} For example "45 s", "12 min", "1.5 h", "3 d".
 */
function formatDuration(seconds) {
  if (seconds < 60) return `${seconds} s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min`;
  if (seconds < 86400) return `${(seconds / 3600).toFixed(1)} h`;
  return `${Math.round(seconds / 86400)} d`;
}

/**
 * @param {?number} epoch Unix time in seconds.
 * @return {string} For example "12 s ago".
 */
function formatAgo(epoch) {
  if (!epoch) return 'never';
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - epoch));
  return `${formatDuration(seconds)} ago`;
}

/**
 * @param {number} part
 * @param {number} whole
 * @return {number} Rounded percentage; 0 when whole is 0.
 */
function percent(part, whole) {
  return Math.round(100 * part / Math.max(1, whole));
}

/**
 * @param {number} value Percentage.
 * @return {string} Meter class for the value.
 */
function severityClass(value) {
  if (value >= CRIT_PERCENT) return 'crit';
  if (value >= WARN_PERCENT) return 'warn';
  return '';
}

// ---------------------------------------------------------------------------
// Derived state
// ---------------------------------------------------------------------------

/** @return {!Array<string>} Machine names in hosts.conf order. */
function hostNames() {
  const {hosts, rigs} = view.data;
  return hosts.length ? hosts : Object.keys(rigs);
}

/**
 * A machine is online if its last poll worked and is recent.
 * @param {?Object} machine Entry of /api/state "rigs".
 * @return {boolean}
 */
function isOnline(machine) {
  const maxAgeS = 3 * (view.data.poll_interval || 20) + 30;
  return Boolean(machine && machine.online &&
      Date.now() / 1000 - machine.fetched < maxAgeS);
}

/** @return {!Array<!Object>} Every GPU on an online machine. */
function onlineGpus() {
  return hostNames()
      .map((name) => view.data.rigs[name])
      .filter(isOnline)
      .flatMap((machine) => machine.snapshot.gpus || []);
}

/** @return {!Array<!Object>} Every job, tagged with its machine. */
function allJobs() {
  return hostNames().flatMap((name) => {
    const jobs = view.data.rigs[name]?.snapshot?.fleet_jobs || [];
    return jobs.map((job) => ({...job, host: name}));
  });
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

function renderHeader() {
  const {last_poll: lastPoll, poll_interval: interval, demo} = view.data;
  const fresh = lastPoll && Date.now() / 1000 - lastPoll < 3 * interval + 30;
  byId('live').className = fresh ? 'live' : 'live stale';
  byId('live-text').textContent = fresh ?
      `Live · updated ${formatAgo(lastPoll)}` :
      `Stale · last update ${formatAgo(lastPoll)}`;
  byId('demo-badge').innerHTML =
      demo ? '<span class="demo">Demo data</span>' : '';
}

function renderTiles() {
  const gpus = onlineGpus();
  const names = hostNames();
  const totalGpus = names.reduce(
      (sum, name) => sum + (view.data.rigs[name]?.snapshot?.gpus || []).length,
      0);
  const reachable = names.filter((n) => isOnline(view.data.rigs[n])).length;
  const avgUtil = gpus.length ?
      Math.round(gpus.reduce((sum, g) => sum + g.util, 0) / gpus.length) :
      0;
  const memUsed = gpus.reduce((sum, g) => sum + g.mem_used, 0);
  const memTotal = gpus.reduce((sum, g) => sum + g.mem_total, 0);
  const running = allJobs().filter((job) => job.state === 'running').length;

  byId('tiles').innerHTML = [
    tile('GPUs online', gpus.length, ` / ${totalGpus || gpus.length}`,
        `${reachable} of ${names.length} machines reachable`, 'hero'),
    tile('Average utilization', avgUtil, ' %', 'across online GPUs'),
    tile('GPU memory in use', formatGb(memUsed), ` / ${formatGb(memTotal)} GB`,
        `${percent(memUsed, memTotal)} % of fleet VRAM`),
    tile('Running jobs', running, '', 'started with rig run'),
  ].join('');
}

/**
 * @param {string} label
 * @param {number|string} value
 * @param {string} unit Smaller text after the value.
 * @param {string} sub Caption under the value.
 * @param {string=} extraClass
 * @return {string} HTML.
 */
function tile(label, value, unit, sub, extraClass = '') {
  return `<div class="tile ${extraClass}">
      <div class="label">${escapeHtml(label)}</div>
      <div class="value">${escapeHtml(value)}<small>${escapeHtml(unit)}</small></div>
      <div class="sub">${escapeHtml(sub)}</div></div>`;
}

/**
 * Utilization history of one GPU as a sparkline.
 * @param {string} key "<machine>:<gpu index>".
 * @return {string} SVG markup.
 */
function sparkline(key) {
  const samples = view.data.hist?.[key] || [];
  const empty =
      '<svg class="spark" viewBox="0 0 100 34" preserveAspectRatio="none"></svg>';
  if (samples.length < 2) return empty;

  const capacity = view.data.history_len || samples.length;
  const offset = capacity - samples.length;
  const height = 30;
  const x = (i) => ((i + offset) / Math.max(1, capacity - 1)) * 100;
  const y = (util) => 2 + height - (util / 100) * height;
  const points = samples.map(([, util], i) => [x(i), y(util)]);
  const line = points
      .map(([px, py], i) => `${i ? 'L' : 'M'}${px.toFixed(1)} ${py.toFixed(1)}`)
      .join(' ');
  const first = points[0];
  const last = points[points.length - 1];
  const area = `${line} L${last[0]} ${height + 2} L${first[0]} ${height + 2} Z`;
  return `<svg class="spark" viewBox="0 0 100 34" preserveAspectRatio="none"
      data-key="${escapeHtml(key)}" aria-label="Utilization history">
      <path class="area" d="${area}"/>
      <path class="line" d="${line}" vector-effect="non-scaling-stroke"/>
      <rect width="100" height="34" fill="transparent"/></svg>`;
}

/**
 * @param {string} hostName
 * @param {!Object} gpu Entry of a snapshot's "gpus".
 * @return {string} HTML.
 */
function gpuRow(hostName, gpu) {
  const memPercent = percent(gpu.mem_used, gpu.mem_total);
  const hot = gpu.temp >= HOT_TEMP_C;
  return `<div class="gpu">
      <div class="gpu-head"><b>GPU ${gpu.index}</b><span>${escapeHtml(gpu.name)}</span>
        <span class="temp ${hot ? 'hot' : ''}">${gpu.temp} °C${hot ? ' hot' : ''}</span></div>
      <div class="metric"><span class="key">Utilization</span>
        <div class="meter"><i style="width:${gpu.util}%"></i></div>
        <span class="value">${gpu.util} %</span></div>
      <div class="metric"><span class="key">Memory</span>
        <div class="meter"><i class="${severityClass(memPercent)}" style="width:${memPercent}%"></i></div>
        <span class="value">${formatGb(gpu.mem_used)} / ${formatGb(gpu.mem_total)} GB</span></div>
      ${sparkline(`${hostName}:${gpu.index}`)}</div>`;
}

/**
 * @param {string} name
 * @return {string} HTML of one machine card.
 */
function hostCard(name) {
  const machine = view.data.rigs[name];
  const online = isOnline(machine);
  const snapshot = machine?.snapshot;

  const status = online ?
      '<span class="pill ok"><span class="dot"></span>Online</span>' :
      '<span class="pill off"><span class="dot hollow"></span>Offline</span>';
  let meta = escapeHtml(machine?.error || 'Waiting for first poll');
  if (snapshot) {
    const seen = online ? `updated ${formatAgo(machine.fetched)}` :
                          `last seen ${formatAgo(machine.last_online)}`;
    meta = `load ${escapeHtml((snapshot.load || [])[0] ?? '?')} · ` +
        `disk free ${escapeHtml(snapshot.disk_free)} · ${seen}`;
  }
  const gpus = (snapshot?.gpus || []).map((gpu) => gpuRow(name, gpu)).join('');

  const running = (snapshot?.fleet_jobs || [])
      .filter((job) => job.state === 'running')
      .map((job) => job.job);
  let activity = 'Not reachable';
  if (online) {
    if (running.length) {
      activity = `Running <b>${escapeHtml(running.join(', '))}</b>`;
    } else if ((snapshot.processes || []).length) {
      activity = `Busy: <b>${escapeHtml(snapshot.processes[0].name)}</b>`;
    } else {
      activity = 'Idle';
    }
  }

  const selected = view.selectedHost === name ? 'selected' : '';
  return `<article class="host ${selected}" data-host="${escapeHtml(name)}"
      tabindex="0" role="button" aria-pressed="${Boolean(selected)}">
      <div class="top"><span class="name">${escapeHtml(name)}</span>
        ${machine?.hub ? '<span class="hub-tag">hub</span>' : ''}${status}</div>
      <div class="meta">${meta}</div>
      ${gpus || '<div class="no-gpu">No GPU reported</div>'}
      <div class="now">${activity}</div></article>`;
}

/**
 * @param {string} state Job state from the collector.
 * @return {string} HTML.
 */
function jobState(state) {
  if (state === 'running') {
    return '<span class="state run"><span class="dot"></span>Running</span>';
  }
  if (state === 'exit 0') {
    return '<span class="state ok"><span class="dot"></span>Done</span>';
  }
  if (state?.startsWith('exit')) {
    return '<span class="state bad"><span class="dot square"></span>' +
        `Failed (${escapeHtml(state.slice(5))})</span>`;
  }
  return `<span class="state"><span class="dot hollow"></span>${escapeHtml(state)}</span>`;
}

function renderJobs() {
  const rank = (job) => (job.state === 'running' ? 0 : 1);
  const jobs = allJobs()
      .sort((a, b) => rank(a) - rank(b) || a.age_s - b.age_s)
      .slice(0, MAX_JOBS);
  if (!jobs.length) {
    byId('jobs').innerHTML =
        '<div class="empty">No jobs yet. Start one with <code>rig run</code>.</div>';
    return;
  }
  const rows = jobs.map((job) => `<tr>
      <td><b>${escapeHtml(job.job)}</b></td><td>${escapeHtml(job.host)}</td>
      <td>${jobState(job.state)}</td><td><code>${escapeHtml(job.last)}</code></td>
      <td class="num">${formatDuration(job.age_s)} ago</td></tr>`);
  byId('jobs').innerHTML = `<table>
      <tr><th>Job</th><th>Machine</th><th>State</th><th>Latest output</th>
        <th class="num">Updated</th></tr>${rows.join('')}</table>`;
}

function renderDetail() {
  const el = byId('detail');
  const snapshot = view.data.rigs[view.selectedHost]?.snapshot;
  if (!snapshot) {
    el.innerHTML = '';
    return;
  }

  const processes = snapshot.processes || [];
  const processTable = processes.length ?
      `<table><tr><th>Process</th><th class="num">Up</th><th class="num">CPU</th>
        <th class="num">Memory</th></tr>${processes.map((p) => `<tr>
        <td>${escapeHtml(p.name)}${p.n > 1 ? ` <span class="muted">×${p.n}</span>` : ''}</td>
        <td class="num">${escapeHtml(p.etime)}</td>
        <td class="num">${Math.round(p.cpu)} %</td>
        <td class="num">${formatGb(p.rss_mb)} GB</td></tr>`).join('')}</table>` :
      '<div class="empty">No fleet processes on this machine.</div>';

  const logButtons = (snapshot.logs || [])
      .slice(0, MAX_LOG_BUTTONS)
      .map((log) => {
        const label = log.name.replace(/^rig-runs\//, '')
                          .replace(/\/stdout\.log$/, '');
        const selected = view.selectedLog === log.path ? 'selected' : '';
        return `<button type="button" class="${selected}"
            data-log="${escapeHtml(log.path)}">${escapeHtml(label)}</button>`;
      })
      .join('');

  const logBox = view.selectedLog ?
      `<div class="log-box" id="log-box"><pre>${
          escapeHtml(view.logText || 'Loading')}</pre></div>` :
      '';

  el.innerHTML = `<h2>${escapeHtml(view.selectedHost)}: processes and logs</h2>
      <div class="card">${processTable}
        <div class="logs">${logButtons || '<span class="empty">No logs</span>'}</div>
        ${logBox}</div>`;
  scrollLogToEnd();
}

function render() {
  if (!view.data) return;
  renderHeader();
  renderTiles();
  byId('hosts').innerHTML = hostNames().map(hostCard).join('');
  renderJobs();
  renderDetail();
}

// ---------------------------------------------------------------------------
// Interaction
// ---------------------------------------------------------------------------

/** @param {string} name Machine to open, or the open one to close it. */
function selectHost(name) {
  view.selectedHost = view.selectedHost === name ? null : name;
  view.selectedLog = null;
  view.logText = '';
  clearTimeout(view.logTimer);
  render();
  if (view.selectedHost) {
    byId('detail').scrollIntoView({behavior: 'smooth', block: 'start'});
  }
}

/** @param {string} path Log to tail. */
function selectLog(path) {
  view.selectedLog = path;
  view.logText = '';
  renderDetail();
  loadLog();
}

function scrollLogToEnd() {
  const box = document.getElementById('log-box');
  if (box) box.scrollTop = box.scrollHeight;
}

async function loadLog() {
  clearTimeout(view.logTimer);
  const host = view.selectedHost;
  const path = view.selectedLog;
  if (!host || !path) return;
  const query = new URLSearchParams({rig: host, path, n: String(LOG_LINES)});
  try {
    const response = await fetch(`/api/log?${query}`);
    const body = await response.json();
    if (view.selectedHost === host && view.selectedLog === path) {
      view.logText = body.text || body.error || '';
      renderDetail();
    }
  } catch (e) {
    view.logText = 'Could not load the log.';
    renderDetail();
  }
  view.logTimer = setTimeout(loadLog, LOG_POLL_MS);
}

function toggleTheme() {
  const root = document.documentElement;
  const isDark = root.dataset.theme ?
      root.dataset.theme === 'dark' :
      matchMedia('(prefers-color-scheme: dark)').matches;
  root.dataset.theme = isDark ? 'light' : 'dark';
  try {
    localStorage.setItem(THEME_KEY, root.dataset.theme);
  } catch (e) {
    // Storage can be blocked; the choice then lasts for this page only.
  }
}

/** @param {!MouseEvent} event */
function showSparkTooltip(event) {
  const tooltip = byId('tooltip');
  const svg = event.target.closest?.('svg.spark[data-key]');
  const samples = svg && view.data?.hist?.[svg.dataset.key];
  if (!samples?.length) {
    tooltip.style.opacity = '0';
    return;
  }
  const capacity = view.data.history_len || samples.length;
  const box = svg.getBoundingClientRect();
  const slot = Math.round(((event.clientX - box.left) / box.width) *
                          (capacity - 1));
  const index = Math.min(samples.length - 1,
                         Math.max(0, slot - (capacity - samples.length)));
  const [timestamp, util] = samples[index];
  tooltip.textContent = `${util} % utilization · ${formatAgo(timestamp)}`;
  tooltip.style.left = `${event.clientX + 12}px`;
  tooltip.style.top = `${event.clientY - 30}px`;
  tooltip.style.opacity = '1';
}

async function fetchState() {
  try {
    const response = await fetch('/api/state');
    view.data = await response.json();
    render();
  } catch (e) {
    byId('live').className = 'live stale';
    byId('live-text').textContent = 'Server unreachable';
  }
}

async function refreshNow() {
  await fetch('/api/refresh', {method: 'POST'});
  setTimeout(fetchState, 3000);
  setTimeout(fetchState, 10000);
}

function init() {
  byId('theme-button').addEventListener('click', toggleTheme);
  byId('refresh-button').addEventListener('click', refreshNow);

  const hosts = byId('hosts');
  hosts.addEventListener('click', (event) => {
    const card = event.target.closest('[data-host]');
    if (card) selectHost(card.dataset.host);
  });
  hosts.addEventListener('keydown', (event) => {
    const card = event.target.closest('[data-host]');
    if (card && (event.key === 'Enter' || event.key === ' ')) {
      event.preventDefault();
      selectHost(card.dataset.host);
    }
  });
  byId('detail').addEventListener('click', (event) => {
    const button = event.target.closest('[data-log]');
    if (button) selectLog(button.dataset.log);
  });
  document.addEventListener('mousemove', showSparkTooltip);

  fetchState();
  setInterval(fetchState, STATE_POLL_MS);
}

init();
