/* Factotum UI (phase 1). Talks only to /api. Pending changes live in this tab until Apply. */
(function () {
  'use strict';
  const PLAUD = 'https://web.plaud.ai/file/';
  const NOTION = 'https://app.notion.com/p/';
  const TABS = ['today', 'timeline', 'projects', 'review', 'chat', 'log', 'settings'];

  let data = { tasks: [], projects: [], today: new Date().toISOString().slice(0, 10), top_cap: 7 };
  let review = null;        // /api/review payload
  let settings = null;      // /api/settings payload
  // Settings changed but not saved yet: key → the value captured when its control changed; '#accounts' → true
  // when the accounts table changed. A save sends only these, so a value nobody touched stays the file's.
  const settingsDirty = new Map();
  let connectors = null;    // /api/connectors payload (Settings → Connections)
  let staged = new Map();   // task id -> {op, fields, label}
  let lastBatch = null;
  let chat = { threads: null, current: null, messages: [], live: null, busy: false, place: null };   // place: the key of the conversation in place whose turn runs (stage 6)
  let lastTap = { id: null, at: 0 };   // for the hand-rolled double-click on rows
  let railTap = { id: null, timer: null };   // a Top tile waits a moment for a second tap before it acts
  // What is being typed, kept outside the DOM: every render() rebuilds the panel (a chat turn's status
  // line re-renders it every second), and a long prompt must survive that with its caret.
  const draft = { cmd: '', ask: '', askFor: null, pick: '', outcome: '', askToday: '', askTask: '', askTaskFor: null,
                  newLine: '', newName: '',      // the New task line; the phone sheet's "What needs doing" (stage 6)
                  findQ: '', picks: {},      // Find any task's box; the shared task picker's boxes by slot (stage 6)
                  edit: {},      // the editor's typed text by task id: {name?, context?, who?} until it is staged (stage 6)
                  step: {} };    // an Add a step line's text by task id (stage 6)
  let asking = null;        // {id, name} of the task whose outcome the toast asks (ripples (stage 5): the outcome question)
  try { draft.cmd = sessionStorage.getItem('factotum.cmd') || ''; sessionStorage.removeItem('factotum.cmd'); } catch (_) {}   // kept by reloadKeepingDraft
  const TOUCH = () => window.matchMedia('(hover: none)').matches;
  let logView = 'all', logQuiet = false, logLimit = 60, logOpen = new Set();
  try { const v = localStorage.getItem('factotum.logview'); if (['timeline', 'changes', 'runs', 'all'].includes(v)) logView = v; logQuiet = localStorage.getItem('factotum.logquiet') === '1'; } catch (_) {}
  let linkView = 'all';     // Today's and Timeline's All / Linked / Ready now (links (stage 4b): filters and the Linked view)
  try { const v = localStorage.getItem('factotum.linkview'); if (['all', 'linked', 'ready'].includes(v)) linkView = v; } catch (_) {}
  let summary = null;       // /api/summary payload: the Today summary, what changed since, the refresh state
  let sumTimer = null;
  // Which sections are folded, per tab ("today:week", "timeline:later", "projects:<name>"); kept in
  // this browser so the layout survives a reload and the next session.
  let folds = {};
  try { folds = JSON.parse(localStorage.getItem('factotum.folds') || '{}'); } catch (_) { folds = {}; }
  if (!folds || typeof folds !== 'object' || Array.isArray(folds)) folds = {};
  const folded = (key, dflt) => Object.prototype.hasOwnProperty.call(folds, key) ? !!folds[key] : !!dflt;
  function setFold(key, shut) { folds[key] = !!shut; try { localStorage.setItem('factotum.folds', JSON.stringify(folds)); } catch (_) {} }
  let ui = { tab: 'today', focus: null, focusAt: null, sel: new Set(), open: new Set(), reviewIdx: 0, laterOpen: false, datePick: false, trayOpen: false, trayOff: new Set(), reviewPaused: false, rvNote: { id: null, text: '' }, remOpen: null, dueOpen: null, sinceOpen: null,
             railOpen: !window.matchMedia('(max-width: 860px)').matches, chatDrawer: false,
             page: null, pageScroll: 0, pageMore: false, pageLater: false,      // the task page: its id, the list's scroll, a phone's More and Later open
             bulkMore: false,      // the phone's short bar shows its second line (back to false when nothing is selected)
             askTask: null,        // the opened task whose conversation shows (stage 6; one at a time)
             newOpen: false,       // the New task line is open (stage 6; a desktop's list tabs)
             sheet: null,          // the phone's New task sheet when open: {due, pick, project, effort, links, hit, linking} (stage 6)
             findOpen: false,      // Find any task is open (stage 6)
             edWho: {},            // the editor's "is for" / "waiting on" choice by task id, kept only while its field is empty (stage 6)
             linkFor: null, linkHit: null,      // the opened task whose Link a task is open, and the task its picker found (stage 6)
             moreFor: null, mergeFor: null,     // the opened task whose More is open, and whose Merge into another picker shows (stage 6)
             bulkLink: false, bulkSwap: false }; // two selected rows: the Link line is open, and its two tasks are swapped (stage 6)
  try { ui.tab = parseAddress().tab; } catch (_) {}
  try { const r = localStorage.getItem('factotum.rail'); if (r === '0' || r === '1') ui.railOpen = r === '1'; } catch (_) {}
  try { const s = JSON.parse(localStorage.getItem('factotum.staged') || '[]'); staged = new Map(s); } catch (_) {}
  for (const [k, v] of staged) { if (!v || typeof v !== 'object') staged.delete(k); else if (v.task_id == null && v.op !== 'create') v.task_id = typeof k === 'number' ? k : parseInt(k, 10); }  // trays saved before entries carried task_id (a new task has none)

  const TODAY = () => new Date(data.today + 'T12:00:00');
  const PHONE = () => window.matchMedia('(max-width: 700px)').matches;
  const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const byId = id => data.tasks.find(t => t.id === id);
  // An expired sign-in answers an /api request with a redirect to the login page on another origin; with
  // redirect: 'manual' the page sees it as an opaqueredirect. A 401 or 403 counts the same (/api sends
  // none of the three). A restarting server (502) or no network (fetch rejects) is not a sign-out.
  const expired = r => r.type === 'opaqueredirect' || r.status === 401 || r.status === 403;
  // The build stamp the page loaded with; every /api response carries the server's. When they differ a
  // newer app is on the server, and the next return to the front reloads (onReturn).
  const BUILD = (document.querySelector('meta[name="factotum-build"]') || {}).content || '';
  let newBuild = false;
  function noteBuild(r) { const b = r.headers.get('X-Factotum-Build') || ''; if (BUILD && b && b !== BUILD) newBuild = true; }
  // A fetch that rejects (no network, a refused connection) is marked `network` so a caller can tell it
  // from a fault in the page; its message is unchanged.
  const netFail = e => { if (e && typeof e === 'object') e.network = true; throw e; };
  const api = (path, opts) => fetch(path, Object.assign({ headers: { 'Content-Type': 'application/json' }, redirect: 'manual' }, opts)).catch(netFail).then(r => {
    noteBuild(r);
    if (expired(r)) { signedOut(); throw new Error('signed out'); }
    if (!r.ok) return r.json().catch(() => null).then(j => {
      // The message stays the status (what every toast shows); a refusal's own words ride along as `detail`.
      const e = new Error(r.status); e.status = r.status;
      if (j && typeof j.detail === 'string') e.detail = j.detail;
      if (j && j.factotum_refused === true) e.factotumRefused = true;      // Factotum's own refusal (a push address it does not post to)
      throw e;
    });
    return r.json();
  });

  // A task as it will be after Apply: its staged decision and date, and its links with the staged link
  // changes and suggestion verdicts drawn in (overlayRel), and the editor's staged edits (stage 6). Never mutates `data`.
  function view(t) {
    const st = staged.get(t.id), due = staged.get(t.id + ':due'), rel = overlayRel(t);
    const edits = EDIT_FIELDS.map(f => staged.get(editKey(t.id, f))).filter(Boolean);
    const merged = [...staged.values()].some(s => s.op === 'merge' && s.task_id === t.id);
    if (!st && !due && !rel && !merged && !edits.length) return t;
    const v = Object.assign({}, t, due ? due.fields : {}, ...edits.map(e => e.fields), st ? st.fields : {});
    if (rel) v.rel = rel;
    if (merged) v.status = 'dropped';      // a staged merge: the duplicate leaves the lists
    if (!st) return v;
    if (st.op === 'done') v.status = 'done';
    if (st.op === 'drop') v.status = 'dropped';
    if (st.op === 'pin_top') v.top_rank = v.top_rank || 99;
    if (st.op === 'unpin') v.top_rank = null;
    if (st.op === 'longterm') v.longterm = 1;
    return v;
  }
  const openOnes = () => data.tasks.map(view).filter(t => t.status === 'open');
  const topList = () => openOnes().filter(x => x.top_rank).sort((a, b) => a.top_rank - b.top_rank);
  let topPos = new Map();   // task id -> its place in the Top list, rebuilt on every render
  const isoPlus = (n) => { const d = TODAY(); d.setDate(d.getDate() + n); return d.toISOString().slice(0, 10); };
  const nextFri = () => { const d = TODAY(); const delta = (5 - d.getDay() + 7) % 7 || 7; d.setDate(d.getDate() + delta); return d.toISOString().slice(0, 10); };

  function fmtDue(due) {
    if (!due) return null;
    const dt = new Date(due.length > 10 ? due : due + 'T12:00:00');
    const days = Math.round((startOfDay(dt) - startOfDay(TODAY())) / 86400000);
    const time = due.length > 10 ? ' ' + dt.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : '';
    let label;
    if (days < -1) label = `${-days}d overdue`; else if (days === -1) label = 'yesterday';
    else if (days === 0) label = 'today' + time; else if (days === 1) label = 'tomorrow' + time;
    else if (days < 7) label = dt.toLocaleDateString([], { weekday: 'short' }) + time;
    else label = dt.toLocaleDateString([], { month: 'short', day: 'numeric' });
    return { label, days };
  }
  function startOfDay(d) { const x = new Date(d); x.setHours(0, 0, 0, 0); return x; }
  function fmtWhen(iso) { const d = new Date(iso); return d.toLocaleDateString([], { weekday: 'short' }) + ' ' + d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }); }
  function daysTo(iso) { return Math.round((startOfDay(new Date(iso + 'T12:00:00')) - startOfDay(TODAY())) / 86400000); }

  // ---------- addresses (stage 1) ----------
  // Today is /, every other tab /<tab>, a task /t/<id>. A tab change is a history entry, so the
  // browser's Back and Forward move between tabs; an old /#<tab> link or /today is rewritten in place.
  function addressOf(tab) { return tab === 'today' ? '/' : '/' + tab; }
  function parseAddress() {
    const p = location.pathname;
    const t = /^\/t\/(\d+)$/.exec(p);
    if (t) return { tab: 'today', task: +t[1], legacy: false };
    if (p === '/new') return { tab: 'today', task: null, legacy: false, add: true };
    if (p === '/') { const h = location.hash.replace('#', ''); return TABS.includes(h) ? { tab: h, task: null, legacy: true } : { tab: 'today', task: null, legacy: false }; }
    const name = p.slice(1);
    return { tab: TABS.includes(name) ? name : 'today', task: null, legacy: false };
  }
  // A task entry carries { page: true, from }: it was put there by the app, so a list address already lies
  // beneath it. A reload or self-update on a phone task page sees the mark and adds nothing under it.
  // `from` is the task page this one was opened from (the trail: the page's back button reads
  // "‹ Task-<from>"); kept in the entry, so it survives a reload.
  function setAddress(path, replace, from = null) {
    if (location.pathname + location.hash === path) return;
    const state = /^\/t\//.test(path) ? { page: true, from } : null;
    if (replace) history.replaceState(state, '', path); else history.pushState(state, '', path);
  }
  function go(tab) { ui.tab = tab; ui.laterOpen = false; ui.page = null; setAddress(addressOf(tab)); render(); }      // a tab covers a desktop's task page
  // Once after the first load (no event), and on every popstate (with it). On a desktop a task address
  // opens the task's row where a list shows it and leaves that tab's address in place of /t/<id>; a task
  // no list shows (closed, merged, missing) opens as the task page and keeps /t/<id>. On a phone it
  // opens the task page. A task page opened straight at /t/<id> (a reminder) gets / put beneath it, so
  // Back lands on Today instead of leaving the app.
  function applyAddress(ev) {
    const a = parseAddress();
    if (a.task !== null && PHONE()) {
      if (!ev && !(history.state && history.state.page)) { setAddress('/', true); setAddress('/t/' + a.task); }
      openTask(a.task, false); return;
    }
    if (a.task !== null) {
      if (jumpTo(a.task, true, true) === 'row') setAddress(addressOf(ui.tab), true);
      else if (!ev && !(history.state && history.state.page)) { setAddress('/', true); setAddress('/t/' + a.task); }      // as on a phone: Back lands on Today
      return;
    }
    // /new (the long-press "New task"): Today with New task open. / REPLACES /new, so a reload opens Today plainly
    // and Back leaves the app as from /. Signed out, this never runs and /new stays for the reload after Sign in again.
    if (a.add) { ui.tab = 'today'; ui.laterOpen = false; ui.page = null; setAddress('/', true); render(); openNew(); return; }
    ui.tab = a.tab; ui.laterOpen = false;
    setAddress(addressOf(a.tab), true);      // a legacy /#<tab> or /today becomes the tab's path; otherwise nothing
    if (ui.page !== null) { closeTask(); return; }      // Back from the task page
    render();
  }

  // ---------- notifications (stage 2) ----------
  // The service worker (/sw.js) only shows notifications and follows a tap on one; it never serves the app.
  let workerError = null;      // why registering the worker failed, when it did (a string)
  function registerWorker() {
    if (!('serviceWorker' in navigator)) return Promise.resolve(null);
    return navigator.serviceWorker.register('/sw.js', { scope: '/', updateViaCache: 'none' })
      .catch(e => { workerError = String((e && e.message) || e); return null; });
  }
  // A tap on a notification while the app is open: the worker posts the address and this window follows
  // it as Back and Forward do (a phone opens the task page, a desktop the task's row, /review the tab).
  function followPath(path) {
    if (!/^\/(t\/\d+|[a-z]+)?$/.test(path)) return;
    setAddress(path); applyAddress({ type: 'worker' });
  }
  if ('serviceWorker' in navigator) navigator.serviceWorker.addEventListener('message', e => { if (e.data && e.data.type === 'open' && typeof e.data.path === 'string') followPath(e.data.path); });

  // Settings → Notifications: this device's subscription and the devices Factotum sends to.
  let pushInfo = null;      // the GET /api/push answer: the public key (or why there is none), the provider, the devices
  let pushDev = { sub: null, busy: false, reason: '', ours: false };   // this browser's PushSubscription, a call in flight, a refusal from the last attempt (ours: Factotum's own, not the push service's)
  let pushLoading = null;   // the loadPush() in flight, so a render during it does not start another
  const isIOS = () => /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  const installed = () => window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
  // An iPhone shows notifications only from the app added to the Home Screen.
  function pushSupport() {
    if (isIOS() && !installed()) return 'install';
    if (!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) return 'unsupported';
    return 'ok';
  }
  // The name the devices table shows for this browser ("Chrome on Windows").
  function deviceLabel() {
    const ua = navigator.userAgent;
    if (/iPhone|iPod/.test(ua)) return 'iPhone';
    if (/iPad/.test(ua) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1)) return 'iPad';
    const browser = /Edg\//.test(ua) ? 'Edge' : /Firefox\//.test(ua) ? 'Firefox' : /Chrome\//.test(ua) ? 'Chrome' : /Safari\//.test(ua) ? 'Safari' : '';
    const os = /Android/.test(ua) ? 'Android' : /Windows/.test(ua) ? 'Windows' : /CrOS/.test(ua) ? 'ChromeOS' : /Mac OS X|Macintosh/.test(ua) ? 'Mac' : /Linux/.test(ua) ? 'Linux' : '';
    return browser && os ? `${browser} on ${os}` : 'This browser';
  }
  function b64uBytes(s) {
    const t = String(s || '').replace(/-/g, '+').replace(/_/g, '/');
    return Uint8Array.from(atob(t + '==='.slice((t.length + 3) % 4)), c => c.charCodeAt(0));
  }
  // A subscription made with another server key (the key file was made again) can never be sent to.
  function sameKey(sub, publicKey) {
    const k = sub && sub.options && sub.options.applicationServerKey;
    if (!k || !publicKey) return false;
    const a = new Uint8Array(k), b = b64uBytes(publicKey);
    return a.length === b.length && a.every((x, i) => x === b[i]);
  }
  // Never rejects: start-up chains it after the lists, and a failure there must not replace the panel.
  function loadPush() {
    if (pushLoading) return pushLoading;
    pushLoading = (async () => {
      try { pushInfo = await api('/api/push'); }
      catch (e) {
        if (e && e.message === 'signed out') return;
        pushInfo = { public_key: null, error: `the page could not ask it (${(e && e.message) || e})`, provider: null, devices: [] };
      }
      try {
        // Only with a registration: serviceWorker.ready would otherwise wait forever.
        if (pushSupport() === 'ok' && await navigator.serviceWorker.getRegistration('/'))
          pushDev.sub = await (await navigator.serviceWorker.ready).pushManager.getSubscription();
      } catch (_) { pushDev.sub = null; }
    })().finally(() => {
      pushLoading = null;
      renderRemind();
      // Signed out leaves pushInfo null; rendering Settings then would only ask again, and again.
      if (pushInfo !== null && ui.tab === 'settings' && ui.page === null) render();
    });
    return pushLoading;
  }
  const thisRow = () => (pushInfo && pushDev.sub && (pushInfo.devices || []).find(d => d.endpoint === pushDev.sub.endpoint)) || null;
  const failedLast = d => !!(d && d.last_failure_at && (!d.last_success_at || d.last_failure_at > d.last_success_at));
  function pushView() {
    const row = thisRow(), v = (state, reason) => ({ state, reason: reason || '', row });
    if (pushDev.busy) return v('working');
    const sup = pushSupport(); if (sup !== 'ok') return v(sup);
    if (!pushInfo) return v('loading');
    if (pushInfo.error) return v('server', pushInfo.error);
    if (Notification.permission === 'denied') return v('denied');
    if (pushDev.reason) return v(pushDev.ours ? 'notsent' : 'refused', pushDev.reason);
    if (failedLast(row)) return v(row.factotum_refused ? 'notsent' : 'refused', row.last_error);
    if (!pushDev.sub || !sameKey(pushDev.sub, pushInfo.public_key) || !row) return v('off');
    return v('on');
  }
  // The review nudge has somewhere to go: ntfy can send, or the app has a device to send to.
  function reviewPushOn() {
    const e = settings && settings.effective; if (!e) return false;
    const p = ['app', 'both'].includes(e['notify.provider']) ? e['notify.provider'] : 'ntfy';
    const ntfy = p !== 'app' && e['notify.ntfy.enabled'] && e['notify.ntfy.topic'];
    const app = p !== 'ntfy' && pushInfo && (pushInfo.devices || []).length > 0;
    return !!(ntfy || app);
  }
  function renderRemind() {
    const eff = settings && settings.effective;
    document.getElementById('remind').innerHTML = eff && eff['notify.review_time'] && reviewPushOn()
      ? `<span>Review at</span><span class="pill">${esc(eff['notify.review_time'])}</span>` : '';
  }
  function pushDeviceHtml() {
    const { state, reason } = pushView();
    const on = (label, dis) => `<button class="btn primary" id="push-on" ${dis ? 'disabled' : ''}>${label}</button>`;
    const p = settings && settings.effective ? settings.effective['notify.provider'] : 'ntfy';
    const control = {
      loading: 'Checking this device…',
      unsupported: '<b>This browser cannot show notifications from Factotum.</b> Use Chrome, Edge or Firefox on a computer or an Android phone, or the installed app on an iPhone.',
      install: '<b>On an iPhone, notifications work only in the installed app.</b> In Safari tap Share, then Add to Home Screen, and open Factotum from the Home Screen.',
      denied: '<b>This device does not allow notifications from Factotum.</b> Allow them in the browser\'s site settings (on an iPhone: Settings › Notifications › Factotum), then come back here.',
      server: `<b>Factotum cannot send notifications right now:</b> ${esc(reason)}`,
      off: `<div class="acts">${on('Turn on notifications on this device')}</div><p class="hint">Reminders and the evening review nudge will appear here and open the task they are about.</p>`,
      working: `<div class="acts">${on('Turning on…', true)}</div>`,
      on: `<b>Notifications are on for this device.</b><div class="acts"><button class="btn" id="push-test">Send a test</button><button class="btn" id="push-off">Turn off</button></div>`
        + (['app', 'both'].includes(p) ? '' : '<p class="hint">Reminders go to ntfy only at the moment. Choose The app or Both in "Send reminders to" to get them here.</p>'),
      refused: `<b>The push service refused:</b> ${esc(reason)}<div class="acts">${on('Try again')}${pushDev.sub ? '<button class="btn" id="push-off">Turn off</button>' : ''}</div>`,
      notsent: `<b>Factotum does not send to that address:</b> ${esc(String(reason).replace(/^not sent: /, ''))}<div class="acts">${on('Try again')}${pushDev.sub ? '<button class="btn" id="push-off">Turn off</button>' : ''}</div>`,
    }[state];
    return `<div class="field" id="push-device" data-state="${state}"><label>This device</label><div class="push-ctl">${control}</div></div>`;
  }
  const fmtStamp = iso => { if (!iso) return '—'; const d = new Date(iso); return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) + ' ' + d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }); };
  function pushDevicesHtml() {
    const list = (pushInfo && pushInfo.devices) || [];
    if (!list.length) return '<p class="hint">No device has notifications on yet.</p>';
    const mine = pushDev.sub ? pushDev.sub.endpoint : null;
    // data-l names a cell when a phone stacks each device into a block (app.css); a failure cell with none stays out there
    return `<div class="tablewrap"><table class="devices"><tr class="hd"><td class="code">device</td><td class="code">added</td><td class="code">last sent</td><td class="code">last failure</td><td></td></tr>${list.map(d => `<tr>
      <td>${esc(d.label)}${d.endpoint === mine ? ' (this device)' : ''}</td><td data-l="added">${esc((d.created_at || '').slice(0, 10))}</td><td data-l="last sent">${esc(fmtStamp(d.last_success_at))}</td>
      <td data-l="last failure" ${failedLast(d) ? '' : 'class="nofail"'}>${failedLast(d) ? esc(d.last_error || '') : '—'}</td><td>${d.endpoint === mine ? '' : `<button class="btn small" data-push-remove="${d.id}">Remove</button>`}</td></tr>`).join('')}</table></div>`;
  }
  // The permission is asked first, before any await: iOS asks only from inside the tap.
  async function pushOn() {
    const asked = Notification.permission === 'granted' ? Promise.resolve('granted') : Notification.requestPermission();
    pushDev.busy = true; pushDev.reason = ''; pushDev.ours = false; render();
    try {
      const perm = await asked;
      if (perm === 'denied') return;      // the render below shows the denied state
      if (perm !== 'granted') { toast('Notifications were not allowed'); return; }
      if (!(await navigator.serviceWorker.getRegistration('/')) && !(await registerWorker())) { pushDev.reason = workerError || 'the notification helper did not start'; return; }
      const reg = await navigator.serviceWorker.ready;
      let sub = await reg.pushManager.getSubscription();
      if (sub && !sameKey(sub, pushInfo.public_key)) { try { await sub.unsubscribe(); } catch (_) {} }
      try { sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64uBytes(pushInfo.public_key) }); }
      catch (e) { pushDev.reason = (e && (e.message || e.name)) || String(e); return; }
      pushDev.sub = sub;
      try { pushInfo = await api('/api/push/subscribe', { method: 'POST', body: JSON.stringify(Object.assign(sub.toJSON(), { label: deviceLabel(), origin: location.origin })) }); }
      catch (e) { if (e.message !== 'signed out') { pushDev.ours = !!e.factotumRefused; pushDev.reason = (e.factotumRefused ? '' : 'Factotum did not take the subscription: ') + (e.detail || e.message); } return; }
      toast('Notifications are on for this device');
    } catch (e) {
      pushDev.reason = (e && (e.message || e.name)) || String(e);
    } finally {
      pushDev.busy = false; render();
    }
  }
  async function pushTest() {
    if (!pushDev.sub) return;
    try {
      const out = await api('/api/push/test', { method: 'POST', body: JSON.stringify({ endpoint: pushDev.sub.endpoint }) });
      if (out.ok) toast('Test sent · it should appear in a few seconds');
      else if (out.removed) { pushDev.reason = 'it no longer knows this device. Turn notifications on again.'; pushDev.ours = false; }
      else { pushDev.reason = out.error || 'no reason given'; pushDev.ours = !!out.factotum_refused; }
    } catch (e) { if (e.message !== 'signed out') toast('Test not sent: ' + e.message); }
    await loadPush();
  }
  async function pushOff() {
    const sub = pushDev.sub; if (!sub) return;
    try { pushInfo = await api('/api/push/unsubscribe', { method: 'POST', body: JSON.stringify({ endpoint: sub.endpoint }) }); }
    catch (e) { if (e.message !== 'signed out') toast('Not turned off: ' + e.message); return; }
    try { await sub.unsubscribe(); } catch (_) {}
    pushDev.sub = null; pushDev.reason = ''; pushDev.ours = false;
    toast('Notifications are off for this device'); render();
  }
  async function pushRemove(id) {
    try { pushInfo = await api('/api/push/unsubscribe', { method: 'POST', body: JSON.stringify({ id }) }); }
    catch (e) { if (e.message !== 'signed out') toast('Not removed: ' + e.message); return; }
    toast('Device removed'); render();
  }

  // ---------- app extras (stage 8) ----------
  // The number beside Review and on the installed app's icon. Before Review is loaded it is the server's
  // count (GET /api/review/count, read in load()); while Review is loaded, that page's own number (it leaves
  // out what the tray already decided). The icon always gets what the tab shows.
  let reviewCount = null;
  let badgeShown;            // what the icon was last given (undefined: nothing yet), so a redraw calls nothing
  // null: not known yet (no Review page and no count has arrived), and the icon is then left as it is.
  function reviewShown() {
    if (!review) return reviewCount === null ? null : (reviewCount || 0);
    const cards = review.suggestions || [];
    return review.total || cards.length ? review.total + cards.filter(x => !x.created).length : 0;
  }
  // Off (notify.app.badge), signed out, or 0: cleared. Before Settings has loaded, or while the count is not known: nothing.
  function syncBadge() {
    const out = !document.getElementById('signedout').hidden;
    const n = out ? 0 : !settings ? null : !settings.effective['notify.app.badge'] ? 0 : reviewShown();
    if (n === null || n === badgeShown) return;
    badgeShown = n;
    try {
      const p = n > 0 ? (typeof navigator.setAppBadge === 'function' ? navigator.setAppBadge(n) : null)
                      : (typeof navigator.clearAppBadge === 'function' ? navigator.clearAppBadge() : null);
      if (p && typeof p.catch === 'function') p.catch(() => {});
    } catch (_) {}
  }

  async function loadCount() {
    const excl = [...staged.keys()].filter(k => typeof k === 'number').join(',');      // as loadReview sends it
    try { reviewCount = (await api('/api/review/count' + (excl ? '?exclude=' + excl : ''))).count; } catch (_) {}      // a failure keeps the last count; a sign-out shows its own screen
  }
  // The number beside Review and on the icon, without a redraw (render() and a return while something is typed).
  function showCount() { const rv = reviewShown(); document.getElementById('n-review').textContent = rv || ''; syncBadge(); }

  // ---------- loading ----------
  let pending = [];           // batches a skill staged (/api/proposals): Apply or Dismiss, above every tab
  async function load() {
    const was = sumBusy();
    data = await api('/api/tasks?status=open');
    find.cache.clear(); if (find.done) find.done.clear(); find.found = null;      // a search answered before this load may be out of date
    detailGen++; detailCache.forEach(d => { d.stale = true; });      // an opened task keeps what it shows until its new answer arrives
    try { pending = (await api('/api/proposals')).proposals; } catch (_) { pending = []; }
    try { summary = await api('/api/summary'); } catch (_) { summary = null; }
    await loadCount();
    document.getElementById('today-label').textContent = '· ' + TODAY().toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
    render();        // what is being typed survives it (the drafts)
    watchSummary();
    if (was && !sumBusy()) refreshEnded();      // this load, not the poll, saw the refresh finish
  }
  function refreshEnded() {
    const last = ((summary || {}).refresh || {}).last || {};
    logData = null; if (ui.tab !== 'review') review = null;      // a Review session in progress keeps its page
    toast(last.ok ? 'Refreshed · ' + (last.line || '') : 'Refresh failed · ' + (last.line || ''));
  }
  // Something is being typed: a draft or a Review note, an open reminder or due-date form (their values
  // live only in the DOM), a chat turn running, or focus in a box of the panel (Settings, among others).
  // Focus in an empty draft-backed box does not count: its text lives in `draft` and render() puts the
  // focus and caret back, so a reload of the lists loses nothing (a box left focused and empty would
  // otherwise keep the lists stale on every return). render() uses the same list, through isDraftBox.
  const DRAFT_BOXES = ['cmd', 'rv-ask', 'chat-pick', 'rv-note', 'today-ask', 'task-ask', 'new-line', 'find-q', 'pick-link', 'pick-merge', 'pick-sheet', 'new-name'];
  // A box that can be closed with its text kept (the New task line, the phone sheet, an opened task's conversation,
  // Find) counts only while it is drawn: text kept out of sight must not keep the lists stale on every return.
  const drawn = id => !!document.getElementById(id);
  function typingNow() {
    const shown = [drawn('new-line') ? draft.newLine : '', drawn('new-name') ? draft.newName : '', drawn('task-ask') ? draft.askTask : '', ui.findOpen ? draft.findQ : ''];
    if ([draft.cmd, draft.ask, draft.pick, draft.outcome, draft.askToday, ...shown, ...Object.values(draft.picks), ui.rvNote.text].some(s => (s || '').trim())) return true;
    if (Object.keys(draft.edit).some(id => edTyped(+id))) return true;      // an editor field typed in and not staged yet
    if (Object.values(draft.step).some(s => (s || '').trim())) return true;      // an Add a step line
    if (ui.remOpen !== null || ui.dueOpen !== null || chat.busy) return true;
    const a = document.activeElement;
    return !!(a && a.matches('input, textarea, select') && a.closest('#panel') && !isDraftBox(a.id));
  }
  // The app came back to the front. A newer build on the server reloads the page, or only says
  // "Update ready" while something is typed or staged; otherwise the lists are reloaded, unless
  // something is typed, when only the summary is polled so the box being typed in is not redrawn.
  function onReturn() {
    if (document.hidden || !document.getElementById('signedout').hidden) return;
    if (newBuild) {
      if (typingNow() || staged.size) document.getElementById('update').hidden = false;
      else location.reload();
      return;
    }
    // Off Review, a Review page loaded earlier is let go, so the number beside Review and on the icon is the
    // server's fresh count again (as at Refresh's end); a Review session in progress keeps its page. The icon is
    // set again even when the number did not change: a push may have put another number there in the meantime.
    // Before the typing check: with something typed only the count is asked for and shown, nothing is redrawn.
    if (ui.tab !== 'review') review = null;
    badgeShown = undefined;
    if (typingNow()) { if (ui.tab === 'today') { clearTimeout(sumTimer); pollSummary(); } loadCount().then(showCount); return; }
    if (ui.tab === 'log') logData = null;
    // The Today box's conversation is asked for again, so one gone quiet for 12 hours is let go in an app that
    // stays open (the server's rule, chat.place_thread). A closed one stays closed; one with a turn running stays.
    const tp = places.get('today:');
    if (tp && tp.loaded && !tp.live && !tp.loading && !tp.fresh) tp.loaded = false;
    // A failed reload keeps what is on screen: a sign-out shows its own screen and no network is not a
    // fault. Anything else (a server error, a bug in a redraw) is logged rather than lost.
    load().then(loadPush).catch(e => { if (!(e && (e.message === 'signed out' || e.network))) console.error('Reloading on return failed:', e); });
  }
  document.getElementById('update').onclick = () => reloadKeepingDraft();      // the tray is in localStorage: it survives

  // ---------- the Today summary (SPEC §8.4) ----------
  // The planner's verdict, kept by the server with what has changed since. Python decides "stale";
  // this only shows it. Refresh asks the scheduler for the sweeps and the planner, then polls.
  const sumBusy = () => !!(summary && summary.refresh && summary.refresh.state !== 'idle');
  function summaryHtml() {
    // With no summary, the box shows only when something will write one: Refresh, the morning planner
    // (inside the brief) or the automatic rewrite. With all three off it would promise what never comes.
    const writer = !summary ? '' : summary.can_refresh ? 'Refresh writes one.' : summary.planner ? 'The morning planner writes one.' : summary.auto ? 'One is written automatically.' : '';
    if (!summary || (!summary.summary && (!openOnes().length || !writer))) return '<section class="summary" id="summary" hidden></section>';
    const s = summary.summary, rf = summary.refresh || {}, busy = sumBusy(), stale = s ? s.stale || [] : [];
    const when = s && s.at ? new Date(s.at).toLocaleString([], s.day === data.today ? { hour: 'numeric', minute: '2-digit' } : { weekday: 'short', hour: 'numeric', minute: '2-digit' }) : '';
    const btn = summary.can_refresh ? `<button class="btn small" id="sum-refresh" ${busy ? 'disabled' : ''} title="${sweepsSaid() ? `Sweeps ${sweepsSaid()} now, then runs the planner on what they found.` : 'Runs the planner now.'} About a minute.">${busy ? '<span class="spin"></span>Refreshing…' : '↻ Refresh'}</button>` : '';
    const reason = x => { const m = /^Task-(\d+) /.exec(x); return m ? `<a href="#" data-jump="${m[1]}">Task-${m[1]}</a> ${esc(x.slice(m[0].length))}` : esc(x); };
    return `<section class="summary ${stale.length ? 'stale' : ''}" id="summary">
      <div class="shead"><span class="lbl">Summary</span>${when ? `<span class="when">${esc(when)}${s.source === 'planner' ? ' · planner' : ''}</span>` : ''}${stale.length ? `<span class="chip stalechip">${stale.length === 1 && /^written /.test(stale[0]) ? esc(stale[0]) : `${stale.length} change${stale.length === 1 ? '' : 's'} since`}</span>` : ''}<span class="spacer"></span>${btn}</div>
      ${s ? `<p class="stext">${esc(s.text)}</p>` : `<p class="hint">No summary yet. ${writer}</p>`}
      ${stale.length && !busy ? `<ul class="since">${stale.slice(0, 4).map(x => `<li>${reason(x)}</li>`).join('')}${stale.length > 4 ? `<li>and ${stale.length - 4} more</li>` : ''}</ul><p class="hint">${summary.auto ? 'A new summary is written automatically' + (summary.can_refresh ? '; Refresh does it now, with a sweep.' : '.') : 'Automatic rewriting is off (Settings → Claude)' + (summary.can_refresh ? '; Refresh writes a new one.' : '.')}</p>` : ''}
      ${busy ? `<p class="hint"><span class="spin"></span>${rf.state === 'requested' ? 'Starting…' : (sweepsSaid() ? `Sweeping ${sweepsSaid()}, then the planner.` : 'Running the planner.') + ' About a minute.'}</p>` : ''}
      ${!busy && rf.last && !rf.last.ok ? `<p class="hint bad">The last refresh did not finish cleanly: ${esc(rf.last.line || '')}</p>` : ''}</section>`;
  }
  function swapSummary() {
    const el = document.getElementById('summary'); if (!el) return;
    el.outerHTML = summaryHtml(); wireSummary();
  }
  function wireSummary() {
    const b = document.getElementById('sum-refresh'); if (b) b.onclick = refreshSummary;
    document.querySelectorAll('#summary [data-jump]').forEach(a => a.onclick = e => { e.preventDefault(); jumpTo(+a.dataset.jump, false); });
  }
  async function refreshSummary() {
    try { summary = await api('/api/summary/refresh', { method: 'POST' }); }
    catch (e) { toast(e.message === '409' ? 'This instance runs no scheduler, so it cannot sweep' : 'Refresh failed: ' + e.message); return; }
    swapSummary(); watchSummary();
  }
  function watchSummary() { clearTimeout(sumTimer); sumTimer = setTimeout(pollSummary, sumBusy() ? 3000 : 45000); }
  async function pollSummary() {
    const was = sumBusy();
    if (document.hidden || (!was && ui.tab !== 'today')) { watchSummary(); return; }
    let next; try { next = await api('/api/summary'); } catch (_) { watchSummary(); return; }
    const changed = JSON.stringify(next) !== JSON.stringify(summary);
    summary = next;
    if (was && !sumBusy()) {     // a refresh just ended: the sweeps and the planner may have changed rows, Review and the Log
      refreshEnded();
      try {
        // The lists are redrawn; a tab with something being typed or decided (Settings, Chat, Review) only gets fresh data and the rail.
        if (['today', 'timeline', 'projects', 'log'].includes(ui.tab)) await load();
        else { data = await api('/api/tasks?status=open'); renderRail(); wireRail(); }
      } catch (_) { swapSummary(); }
      watchSummary();
      return;
    }
    if (changed) swapSummary();
    watchSummary();
  }

  // ---------- reminders ----------
  // A reminder is staged under its own key, so it rides in the same batch as the task's decision
  // (Keep, Later, even Drop) without replacing it.
  const REPEATS = [['', 'once'], ['daily', 'every day'], ['weekdays', 'weekdays'], ['weekly', 'every week'], ['monthly', 'every month']];
  function defaultFireAt() { const d = TODAY(); d.setDate(d.getDate() + 1); return d.toISOString().slice(0, 10) + 'T09:00'; }
  function reminderForm(id) {
    const st = staged.get(id + ':r');
    if (st) return `<div class="remform"><span class="chip rem">⏰ staged: ${esc(st.label)}</span><button class="btn small" data-rem-clear="${id}">Remove</button></div>`;
    if (ui.remOpen !== id) return `<button class="btn small" data-rem-open="${id}">⏰ Set reminder</button>`;
    return `<div class="remform" data-rem-form="${id}">
      <input type="datetime-local" data-f="fire_at" value="${defaultFireAt()}" aria-label="When">
      <select data-f="repeat" aria-label="Repeat">${REPEATS.map(([v, l]) => `<option value="${v}">${l}</option>`).join('')}</select>
      <input type="text" data-f="label" placeholder="Label (optional)" aria-label="Label">
      <button class="btn small" data-rem-add="${id}">Add</button><button class="btn small" data-rem-cancel="${id}">Cancel</button></div>`;
  }
  // An attribute edit (due date) is staged under "<id>:due", beside the task's decision if any. `extra` may
  // carry `expect` and `note` (both sent with the change) and `ev` (a suggestion's evidence id, never sent);
  // a key whose value is undefined is left out. `quiet` (the editor, stage 6) stages without a render.
  function stageExtra(key, task_id, op, fields, label, extra = {}, quiet = false) {
    const more = Object.fromEntries(Object.entries(extra || {}).filter(([, v]) => v !== undefined));
    const prev = staged.get(key);
    if (prev && prev.fsug && prev.fsug !== more.fsug) {      // a filing chip's value under this key (the editor's): the same value keeps it accepted
      if (JSON.stringify(prev.fields) === JSON.stringify(fields)) more.fsug = prev.fsug; else chipLeft(prev, true);
    }
    staged.set(key, { task_id, op, fields, label, ...more });
    try { localStorage.setItem('factotum.staged', JSON.stringify([...staged])); } catch (_) {}
    if (!quiet) render();
  }
  // `quiet` (the editor, stage 6): no render and no toast; the editor redraws what is around its fields itself.
  function stageDue(id, due, quiet = false) {
    stageExtra(id + ':due', id, 'set', { due: due || null }, due ? 'due ' + dayWords(due) : 'due date removed', {}, quiet);      // the page's own words: "due Fri Nov 14"
    if (!quiet) toast((due ? 'Due date staged' : 'Removal of the due date staged') + ' · press Apply in the tray to save it');
  }
  function dueForm(id, current) {
    const st = staged.get(id + ':due');
    if (st) return `<span class="chip staged">${esc(st.label)}</span><button class="btn small" data-due-clear="${id}">Undo</button>`;
    if (ui.dueOpen !== id) return `<button class="btn small" data-due-open="${id}">📅 ${current ? 'Change due' : 'Set due'}</button>${current ? `<button class="btn small" data-due-none="${id}" title="The task keeps everything else and becomes undated">✕ Remove due date</button>` : ''}`;
    return `<span class="remform" data-due-form="${id}"><input type="date" value="${esc((current || '').slice(0, 10))}" aria-label="Due date"><button class="btn small" data-due-set="${id}">Set</button>${current ? `<button class="btn small" data-due-none="${id}">Remove due date</button>` : ''}<button class="btn small" data-due-cancel="${id}">Cancel</button></span>`;
  }
  function wireDue(root) {
    root.querySelectorAll('[data-due-open]').forEach(b => b.onclick = () => { ui.dueOpen = +b.dataset.dueOpen; render(); });
    root.querySelectorAll('[data-due-cancel]').forEach(b => b.onclick = () => { ui.dueOpen = null; render(); });
    root.querySelectorAll('[data-due-set]').forEach(b => b.onclick = () => { const id = +b.dataset.dueSet; const v = root.querySelector(`[data-due-form="${id}"] input`).value; if (!v) { toast('Pick a date'); return; } ui.dueOpen = null; stageDue(id, v); });
    root.querySelectorAll('[data-due-none]').forEach(b => b.onclick = () => { ui.dueOpen = null; stageDue(+b.dataset.dueNone, null); });
    root.querySelectorAll('[data-due-clear]').forEach(b => b.onclick = () => { staged.delete(+b.dataset.dueClear + ':due'); try { localStorage.setItem('factotum.staged', JSON.stringify([...staged])); } catch (_) {} render(); });
  }
  function stageReminder(id) {
    const f = document.querySelector(`[data-rem-form="${id}"]`); if (!f) return;
    const fire_at = f.querySelector('[data-f=fire_at]').value, repeat = f.querySelector('[data-f=repeat]').value, label = f.querySelector('[data-f=label]').value.trim();
    if (!fire_at) { toast('Pick a date and time'); return; }
    const fields = { fire_at, label: label || null, repeat: repeat || null };
    staged.set(id + ':r', { task_id: id, op: 'add_reminder', fields, label: `remind ${fmtWhen(fire_at)}${repeat ? ' · ' + repeat : ''}${label ? ' · ' + label : ''}` });
    try { localStorage.setItem('factotum.staged', JSON.stringify([...staged])); } catch (_) {}
    ui.remOpen = null; render();
    toast('Reminder staged · press Apply in the tray to save it');
  }
  function wireReminders(root) {
    root.querySelectorAll('[data-rem-open]').forEach(b => b.onclick = () => { ui.remOpen = +b.dataset.remOpen; render(); });
    root.querySelectorAll('[data-rem-cancel]').forEach(b => b.onclick = () => { ui.remOpen = null; render(); });
    root.querySelectorAll('[data-rem-add]').forEach(b => b.onclick = () => stageReminder(+b.dataset.remAdd));
    root.querySelectorAll('[data-rem-clear]').forEach(b => b.onclick = () => { staged.delete(+b.dataset.remClear + ':r'); try { localStorage.setItem('factotum.staged', JSON.stringify([...staged])); } catch (_) {} render(); });
  }

  // ---------- staging ----------
  function persistStaged() { try { localStorage.setItem('factotum.staged', JSON.stringify([...staged])); } catch (_) {} }
  // A filing chip's staged value (`fsug`: its evidence) has left the tray: the "accepted" verdict staged with it goes
  // too, or, when another value took its place (`replaced`), becomes a dismissal, so Apply never records a
  // suggestion as accepted that was not what was chosen.
  function chipLeft(entry, replaced) {
    const eid = entry && entry.fsug; if (!eid) return;
    const j = staged.get('e:' + eid); if (!j || (j.fields || {}).verdict !== 'applied') return;
    if (replaced) staged.set('e:' + eid, Object.assign({}, j, { fields: Object.assign({}, j.fields, { verdict: 'dismissed' }), label: 'filing suggestion not taken' }));
    else staged.delete('e:' + eid);
  }
  function discardStaged(keys) {   // keys as strings; undefined = every one
    [...staged.keys()].filter(k => !keys || keys.has(String(k))).forEach(k => { const e = staged.get(k); staged.delete(k); chipLeft(e, false); });
    if (!staged.size) { ui.trayOpen = false; ui.trayOff = new Set(); }
    persistStaged(); render();
  }
  function stage(id, op, fields, label) {
    staged.set(id, { task_id: id, op, fields: fields || {}, label });
    try { localStorage.setItem('factotum.staged', JSON.stringify([...staged])); } catch (_) {}
    render();
  }
  function act(id, kind, extra) {
    const t = view(byId(id) || { id });
    const ops = {
      done: () => stage(id, 'done', {}, 'done'),
      drop: () => stage(id, 'drop', {}, 'dropped'),
      snooze: () => stage(id, 'set', { snoozed_until: isoPlus(1) }, 'snoozed to tomorrow'),
      tomorrow: () => stageDue(id, isoPlus(1)),
      nextweek: () => stageDue(id, isoPlus(7 - ((TODAY().getDay() + 6) % 7))),
      setdue: () => stageDue(id, extra),
      cleardue: () => stageDue(id, null),
      top: () => t.top_rank ? stage(id, 'unpin', {}, 'unpinned') : stage(id, 'pin_top', {}, 'pinned'),
      longterm: () => stage(id, 'set', { longterm: t.longterm ? 0 : 1 }, t.longterm ? 'not long-term' : 'long-term'),
      keep: () => stage(id, 'keep', {}, 'kept'),
      due: () => stageDue(id, extra),
      reopen: () => stage(id, 'reopen', {}, 'reopened'),
    };
    (ops[kind] || (() => {}))();
  }
  // keys: the staged entries to apply (the tray's dropdown); undefined = every one
  async function applyAll(keys) {
    const picked = keys ? [...staged].filter(([k]) => keys.has(String(k))) : [...staged];
    // New tasks first: the batch resolves a link's ref to a new task only after that task is made.
    const ordered = picked.filter(([, s]) => s.op === 'create').concat(picked.filter(([, s]) => s.op !== 'create'));
    const changes = ordered.map(([, s]) => {      // what the server takes: never the label or `ev`
      const c = { task_id: s.task_id, op: s.op, fields: s.fields };
      if (s.expect) c.expect = s.expect;
      if (s.note) c.note = s.note;
      if (s.from_chat) c.from_chat = s.from_chat;      // the server makes the stored proposal, never these fields
      return c;
    });
    const chatMids = [...new Set(picked.map(([, s]) => s.from_chat && s.from_chat.message_id).filter(Boolean))];
    if (!changes.length) return;
    // A Done or Drop on a task others hang on, with no outcome said yet: the toast offers to say one (design §8.5).
    // Read before the request, while the task is still in the open list with its links.
    const say = picked.map(([, s]) => s).filter(s => (s.op === 'done' || s.op === 'drop') && !(s.fields || {}).outcome && hangsOn(byId(s.task_id)))
      .map(s => sayInfo(byId(s.task_id)));
    try {
      const res = await api('/api/batches/apply', { method: 'POST', body: JSON.stringify({ source: ui.tab === 'review' ? 'review' : 'ui', changes }) });
      lastBatch = res.batch_id;
      // What came from a conversation is remembered as applied (a skipped one stays stageable; a made create is
      // marked by the server, which knows whether it was made).
      const skippedIds = new Set((res.skipped || []).map(k => k.task_id).filter(x => x != null));
      const fromProps = picked.filter(([, s]) => s.prop && s.op !== 'create' && !(s.task_id != null && skippedIds.has(s.task_id)));
      if (fromProps.length) { fromProps.forEach(([, s]) => appliedProps.set(s.prop, res.batch_id)); persistApplied(); }
      if (keys) { picked.forEach(([k]) => staged.delete(k)); persistStaged(); } else { staged = new Map(); try { localStorage.removeItem('factotum.staged'); } catch (_) {} }
      ui.trayOpen = false; ui.trayOff = new Set();
      ui.sel.clear(); logData = null;
      if (ui.tab !== 'review') review = null;   // a stale Review page would still show the old due date; re-fetch on the next visit
      await load();
      if (ui.tab === 'review') {
        // The decisions are in; today counts as reviewed (phase 2 skips the push), and the next
        // page waits for a click instead of appearing under the user's hands.
        await api('/api/review/finish', { method: 'POST' });
        await loadReview();
        ui.reviewPaused = true; render();
      }
      if (chatMids.length) reloadPlacesWith(chatMids);      // a staged new task now reads "created Task-N" where it was proposed
      // A tray ref (m<message>-<index>) is the page's own name for a new task: never shown.
      const why = String(res.skipped.length ? res.skipped[0].why : '').replace(/no task made in this batch has ref 'm\d+-\d+'/, 'the new task it links to was not in this Apply');
      // Nothing applied: the server kept no batch (batch_id null), so the toast says why and offers no Undo.
      const msg = !res.applied && !(res.created || []).length && res.skipped.length
        ? `Nothing applied · ${res.skipped.length === 1 ? 'skipped' : res.skipped.length + ' skipped'}: ${why}`
        : `${res.applied} change${res.applied === 1 ? '' : 's'} applied` + (res.skipped.length ? ` · ${res.skipped.length} skipped: ${why}` : '');
      const closed = say.filter(x => !res.skipped.some(k => k.task_id === x.id));      // a skipped Done closed nothing
      closed.forEach(x => sayable.set(x.id, x));
      if (closed.length) toast(msg, true, { buttons: [{ text: 'Say how it went', attrs: `data-say="${closed[0].id}"`, run: () => askOutcome(closed[0].id) }], holdMs: 20000 });
      else toast(msg, true);
      if ((res.ripple_queued || []).length) watchRipple(res.batch_id);
    } catch (e) { toast('Apply failed: ' + e.message); }
  }
  // opts: {buttons: [{text, attrs, run}], holdMs, batch}: buttons after Undo, how long the toast stays (8 s
  // otherwise), and the batch its Undo names (the latest Apply's otherwise).
  // While the outcome question is open the toast is the question: the latest message waits (with the batch its
  // Undo meant then) and shows when it closes. `toast.next` is a message that shows when the current one ends.
  function toast(msg, undo, opts) {
    const el = document.getElementById('toast');
    if (asking) { toast.held = [msg, undo, Object.assign({}, opts, { batch: (opts || {}).batch != null ? opts.batch : lastBatch })]; return; }
    const o = opts || {}, extra = o.buttons || [], batch = o.batch != null ? o.batch : lastBatch;
    el.classList.remove('ask');
    el.innerHTML = esc(msg) + (undo && batch ? ' <button id="undo">Undo</button>' : '') + extra.map((b, i) => ` <button data-tb="${i}" ${b.attrs || ''}>${esc(b.text)}</button>`).join('');
    el.classList.add('on');
    clearTimeout(toast.t);
    toast.t = setTimeout(() => {
      el.classList.remove('on');
      const next = toast.next; toast.next = null;
      if (next && !asking) toast(...next);
      else if (next) toast.held = toast.held || next;
    }, o.holdMs || 8000);
    extra.forEach((b, i) => { el.querySelector(`[data-tb="${i}"]`).onclick = () => b.run(); });
    const u = document.getElementById('undo');
    if (u) u.onclick = async () => {
      const id = batch; u.disabled = true;
      let res;
      try { res = await undoBatch(id); } catch (e) { toast('Undo failed: ' + e.message, true, { batch: id }); return; }     // the batch stays undoable: Undo again
      if (res.undone === false) { await load(); toast(undoSaid(res), true, { batch: id }); return; }     // not undone: its Undo stays offered
      if (lastBatch === id) lastBatch = null;
      await load(); toast(undoSaid(res));
    };
  }

  // ---------- ripples (stage 5): the outcome question ----------
  // Done is instant; the question follows, in the toast, and never blocks (design §8.5). It is asked only
  // when something hangs on the task: an open task waits on it, or it is a step of an open goal (the
  // server's `reminders.hanging_on` counts the same two). A task that only waits on another is not asked.
  const OUTCOME_MAX = 300;        // an outcome's length, as batches._fields cuts it
  const sayable = new Map();      // task id → what the question says about it, read before the Apply that closed it
  function hangsOn(t) {
    return !!(t && t.rel && ((t.rel.frees_open || []).length || (t.rel.goal_id != null && isOpenTask(t.rel.goal_id))));
  }
  function sayInfo(t) {
    return { id: t.id, name: t.name, frees: (t.rel.frees_open || []).slice(), goal: t.rel.goal_id != null && isOpenTask(t.rel.goal_id) ? t.rel.goal_id : null };
  }
  function askOutcome(id) {
    const s = sayable.get(id) || { id, name: (nameOf(id) || {}).name || '', frees: [], goal: null };
    const again = !!(asking && asking.id === id);      // redrawn with a reason it was not saved: the line and the reason stay
    if (!again) draft.outcome = '';
    asking = { id, name: s.name, err: again ? asking.err : '' };
    clearTimeout(toast.t);
    const el = document.getElementById('toast');
    el.classList.add('on', 'ask');
    el.innerHTML = outcomeHtml();
    const box = document.getElementById('outcome-say');
    box.oninput = () => { draft.outcome = box.value; };
    box.onkeydown = e => {
      if (e.key === 'Enter' && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); saveOutcome(); }
      else if (e.key === 'Escape') { e.preventDefault(); closeAsk(); }
      e.stopPropagation();      // no key typed here acts on the list
    };
    el.onkeydown = e => {      // focus on a button or a link in the question: no key reaches the list or the Review card behind it
      if (!asking) return;
      if (e.key === 'Escape') { e.preventDefault(); closeAsk(); }
      e.stopPropagation();
    };
    el.querySelector('[data-say-save]').onclick = saveOutcome;
    el.querySelector('[data-say-skip]').onclick = () => closeAsk();
    el.querySelectorAll('[data-open-task]').forEach(a => a.onclick = e => { e.preventDefault(); goTask(+a.dataset.openTask); });
    box.focus();
  }
  function outcomeHtml() {
    const s = sayable.get(asking.id) || { frees: [], goal: null };
    const ref = n => `<a href="/t/${n}" data-open-task="${n}">Task-${n}</a>`;
    const hangs = [];
    if (s.frees.length) hangs.push(`${s.frees.map(ref).join(s.frees.length === 2 ? ' and ' : ', ')} ${s.frees.length === 1 ? 'waits' : 'wait'} on this`);
    if (s.goal != null) hangs.push(`a step of ${ref(s.goal)}`);
    return `<div class="q">How did ${ref(asking.id)} turn out?</div>
      ${asking.name ? `<div class="tname">${esc(asking.name)}</div>` : ''}
      <input id="outcome-say" maxlength="${OUTCOME_MAX}" autocomplete="off" placeholder="Optional, one line" aria-label="How did Task-${asking.id} turn out?" value="${esc(draft.outcome)}">
      <div class="hint">Optional${hangs.length ? ' · ' + hangs.join(' · ') : ''}</div>
      ${asking.err ? `<div class="hint bad">${esc(asking.err)}</div>` : ''}
      <div class="acts"><button class="btn primary" data-say-save>Save</button><button class="btn" data-say-skip>Nothing to add</button></div>`;
  }
  function closeAsk() {
    asking = null; draft.outcome = '';
    const el = document.getElementById('toast');
    el.classList.remove('ask', 'on'); el.innerHTML = ''; el.onkeydown = null;
    const held = toast.held; toast.held = null;
    if (held) toast(...held);
  }
  // The owner's own line, applied at once through the write path (one batch, undoable): an outcome ripples at once.
  async function saveOutcome() {
    if (!asking || asking.busy) return;
    const id = asking.id, text = draft.outcome.trim();
    if (!text) { closeAsk(); return; }
    asking.busy = true;
    document.querySelectorAll('#toast .acts button').forEach(b => { b.disabled = true; });
    let res;
    try { res = await api('/api/batches/apply', { method: 'POST', body: JSON.stringify({ source: 'ui', changes: [{ task_id: id, op: 'set', fields: { outcome: text } }] }) }); }
    catch (e) {
      if (!asking || asking.id !== id) return;
      asking.busy = false;
      asking.err = e.message === 'signed out' ? '' : 'Not saved: ' + (e.detail || e.message) + '. Your line is kept.';
      askOutcome(id);
      return;
    }
    // A message held while the question was open (an Apply that failed meanwhile, a ripple that landed) is
    // not dropped: it shows when this one's own message ends.
    let held = null;
    if (asking && asking.id === id) { held = toast.held; toast.held = null; closeAsk(); }
    if (held) toast.next = held;
    const brief = held ? { holdMs: 4000 } : undefined;
    if (!res.applied) { toast('Not saved: ' + ((res.skipped[0] || {}).why || 'nothing changed'), false, brief); return; }
    lastBatch = res.batch_id; logData = null;
    await load();
    toast('Saved', true, brief);
    if ((res.ripple_queued || []).length) watchRipple(res.batch_id);
  }

  // ---------- ripples (stage 5): landing ----------
  // After an Apply that queued a ripple, the page asks GET /api/ripple?after=<that batch> at once, then every
  // 15 s while something is still pending and the page is visible, for at most 10 minutes; with ripples off or
  // no scheduler here it asks once and stops. What landed is read first: an answer can
  // carry a landed ripple and an empty queue together. One watcher at a time; a newer Apply replaces it.
  const RIPPLE_EVERY = 15000, RIPPLE_FOR = 600000;
  let rippleWatch = null;          // {after, until, timer, paused}
  function watchRipple(batchId) {
    if (rippleWatch) clearTimeout(rippleWatch.timer);
    rippleWatch = { after: batchId, until: Date.now() + RIPPLE_FOR, timer: null, paused: false };
    pollRipple(rippleWatch);
  }
  async function pollRipple(w) {
    if (rippleWatch !== w) return;
    if (document.hidden) { w.paused = true; return; }      // picked up again when the page is visible
    let r = null;
    try { r = await api('/api/ripple?after=' + w.after); } catch (_) {}      // no answer: asked again in 15 s
    if (rippleWatch !== w) return;
    if (r && (!r.enabled || !r.scheduler)) { rippleWatch = null; return; }
    const landed = r ? (r.landed || []).filter(l => !l.undone && (l.tasks || []).length) : [];
    if (landed.length) {
      w.after = Math.max(...landed.map(l => l.batch_id));
      await rippleLanded(landed);
      if (rippleWatch !== w) return;
    }
    if ((r && !(r.pending || []).length) || Date.now() >= w.until) { rippleWatch = null; return; }
    w.timer = setTimeout(() => pollRipple(w), RIPPLE_EVERY);
  }
  document.addEventListener('visibilitychange', () => {
    const w = rippleWatch;
    if (!document.hidden && w && w.paused) { w.paused = false; if (Date.now() < w.until) pollRipple(w); else rippleWatch = null; }
  });
  // The lists are read again, then the toast says how many linked tasks changed, with See changes (the first
  // one, opened with its History) and Undo. A draft-backed box (the command box, Review's ask and note, the
  // picker, the outcome line in the toast) survives the redraw, so typing there does not hold the reload back;
  // only a box whose value lives in the DOM alone does (an open reminder or due-date form, focus in another
  // input of the panel such as Settings). Then the lists are read when See changes is pressed.
  function domHoldsTyping() {
    if (ui.remOpen !== null || ui.dueOpen !== null) return true;
    const a = document.activeElement;
    return !!(a && a.matches('input, textarea, select') && a.closest('#panel') && !isDraftBox(a.id));
  }
  async function rippleLanded(landed) {
    const ids = new Set(landed.flatMap(l => l.tasks.map(t => t.id))), n = ids.size;
    const first = landed[0].tasks[0].id, batch = landed[0].batch_id;
    logData = null;
    let stale = domHoldsTyping();
    if (!stale) { try { await load(); } catch (_) { stale = true; } }
    toast(`${n} linked task${n === 1 ? '' : 's'} brought up to date`, false, { holdMs: 15000, buttons: [
      { text: 'See changes', attrs: `data-ripple-see="${first}"`, run: async () => { if (stale) { try { await load(); } catch (_) {} } jumpTo(first, true); } },
      { text: 'Undo', attrs: `data-ripple-undo="${batch}"`, run: () => undoAndSay(batch) }] });
  }
  // The sign-in has expired (see `expired`): a full-screen "Sign in again" over the whole app, whose
  // button reloads at the same address. The tray is in localStorage, so it survives the reload. The rest
  // of the app is made inert so nothing behind the screen can be reached with Tab.
  function signedOut() {
    const el = document.getElementById('signedout');
    if (!el.hidden) return;
    el.hidden = false;
    for (const c of document.querySelector('.app').children) if (c !== el) c.inert = true;
    document.getElementById('signin').focus();
    syncBadge();      // signed out: the icon is cleared
  }
  // A reload the app asks for (Sign in again, Update) keeps what is in the command box: it is put in
  // sessionStorage, which survives the sign-in's round trip in this tab, and read back once on load.
  function reloadKeepingDraft() {
    try { if (draft.cmd.trim()) sessionStorage.setItem('factotum.cmd', draft.cmd); } catch (_) {}
    location.reload();
  }
  document.getElementById('signin').onclick = reloadKeepingDraft;

  // ---------- links (stage 4b): words and staging keys ----------
  // Where a staged change sits in the tray. A link is keyed by both ends and its kind, never by the bare id,
  // so it stands beside the task's decision instead of replacing it; a note sits beside both.
  function relKey(a, kind, b) { return `rel:${a}:${kind}:${b}`; }
  function stageKey(c, suffix) {
    const id = c.task_id, f = c.fields || {};
    if (c.op === 'link' || c.op === 'unlink') return relKey(id, f.kind, f.other);
    if (c.op === 'merge') return relKey(id, 'same_as', f.into);
    if (c.op === 'add_note') return `${id}:${suffix || 'note'}`;
    if (c.op === 'add_reminder') return id + ':r';
    if (c.op === 'judge_evidence') return 'e:' + f.evidence_id;
    if (c.op === 'create') return 'new:' + (suffix || Date.now());      // a task that does not exist yet has no id to key it by
    return suffix ? `${id}:${suffix}` : id;
  }
  function cut60(s) { s = String(s == null ? '' : s); return s.length > 60 ? s.slice(0, 60) + '…' : s; }
  // Read with the source task in front: "Task-12 now waits on Task-14".
  function linkPredicate(kind, other, removed) {
    const t = 'Task-' + other;
    if (kind === 'waits_on') return removed ? `no longer waits on ${t}` : `now waits on ${t}`;
    if (kind === 'part_of') return removed ? `is no longer part of ${t}` : `is now part of ${t}`;
    if (kind === 'same_as') return removed ? `is no longer merged into ${t}` : `merged into ${t}`;
    return '';
  }
  // A change in words, or '' when the caller's own label says it better.
  function changeWords(c) {
    const f = c.fields || {};
    if (c.op === 'link') return linkPredicate(f.kind, f.other, false);
    if (c.op === 'unlink') return linkPredicate(f.kind, f.other, true);
    if (c.op === 'merge') return linkPredicate('same_as', f.into, false);
    if (c.op === 'add_note') return `note “${cut60(f.text)}”`;
    if ((c.op === 'done' || c.op === 'drop') && f.outcome) return `${c.op === 'done' ? 'done' : 'dropped'} · “${cut60(f.outcome)}”`;
    return '';
  }
  function stageChange(c, label, extra, suffix) {
    const key = stageKey(c, suffix);
    stageExtra(key, c.task_id, c.op, c.fields || {}, changeWords(c) || label, extra);
    return key;
  }
  // A staged edit of a text field carries the text it started from (the server's, never the staged view),
  // so Apply skips it when the text changed meanwhile instead of writing over the newer one.
  function textExpect(id, fields) {
    const t = byId(id); if (!t || !fields) return undefined;
    const out = {};
    for (const k of ['context', 'name']) if (k in fields) out[k] = t[k] == null ? '' : t[k];
    return Object.keys(out).length ? out : undefined;
  }
  function suggestionStaged(eid) { return staged.has('e:' + eid) || [...staged.values()].some(s => s.ev === eid); }
  // A conversation's proposals already applied: "<message>-<index>" → the batch that applied them (stage 6). The
  // server marks a made create itself; a set, link, note or decision is remembered here, so neither the place nor
  // the Chat tab can stage it and apply it a second time. Kept in this browser (the newest 400); an Undo of that
  // batch makes them stageable again.
  let appliedProps = new Map();
  try { appliedProps = new Map(JSON.parse(localStorage.getItem('factotum.applied') || '[]').filter(e => Array.isArray(e) && typeof e[0] === 'string')); } catch (_) { appliedProps = new Map(); }
  function persistApplied() {
    const kept = [...appliedProps].slice(-400); appliedProps = new Map(kept);
    try { localStorage.setItem('factotum.applied', JSON.stringify(kept)); } catch (_) {}
  }
  const propApplied = (mid, i) => appliedProps.has(mid + '-' + i);
  // `undone: false`: nothing was restored because everything the batch wrote was changed since; the batch stays
  // undoable, so nothing here lets go of it (its task, the card it made, the proposals it applied).
  async function undoBatch(id) {
    const res = await api(`/api/batches/${id}/undo`, { method: 'POST' });
    if (res && res.undone === false) return res;
    const made = addedBy.get(id);      // an add undone: nothing about the dropped task stays attached, selected or open
    if (made != null) { addedBy.delete(id); ui.sel.delete(made); ui.open.delete(made); if (ui.focus === made) ui.focus = null; }
    if (cardMade.has(id)) { const t = cardMade.get(id); cardMade.delete(id); ui.sel.delete(t); ui.open.delete(t); if (ui.focus === t) ui.focus = null; review = null; }      // a card's Create undone: Review is read again, so the card is back
    const before = appliedProps.size;
    for (const [k, b] of [...appliedProps]) if (b === id) appliedProps.delete(k);
    if (appliedProps.size !== before) persistApplied();
    return res;
  }
  // The ripples the batch set off follow it: undone with it, or kept when a linked task's text changed after.
  function undoSaid(res) {
    if (res.already) return 'Already undone';
    if (res.undone === false) {
      const w = (res.skipped || []).map(s => s.why);
      return 'Not undone: ' + (w.slice(0, 2).join('; ') || 'it was changed since') + (w.length > 2 ? ` · +${w.length - 2} more` : '');
    }
    const k = (res.skipped || []).length, u = (res.ripples_undone || []).length, kept = (res.ripples_kept || []).length;
    const said = ['Undone'];
    if (k) said.push(`${k} left as ${k === 1 ? 'it is' : 'they are'}: ${res.skipped[0].why}`);
    if (u) said.push(u === 1 ? 'the ripple it set off was undone too' : `the ${u} ripples it set off were undone too`);
    if (kept) said.push(`${kept === 1 ? 'a ripple it set off was' : kept + ' ripples it set off were'} kept, since the text changed after (undo in History if it no longer fits)`);
    return said.join(' · ');
  }

  // ---------- links (stage 4b): badges ----------
  // A task the page names, from the open list or, for one it did not load (closed, merged away), from `neighbours`.
  function nameOf(id) { return byId(id) || (data.neighbours || {})[String(id)] || null; }
  const isOpenTask = id => { const n = nameOf(id); return !!(n && n.status === 'open'); };
  const REL_OPS = ['link', 'unlink', 'merge'];
  const emptyRel = () => ({ links: [], waits_on_open: [], frees_open: [], goal_id: null, steps_open: 0, steps_total: 0, needed_by: null, needed_by_id: null, needed_by_via: null, changed: null, suggestions: [] });
  // A task's links with what is staged drawn in: a staged link at either end, an unlink, a merge, a suggestion
  // judged in the tray. undefined when nothing staged touches it (the server's `rel` stands as it is).
  function overlayRel(t) {
    // A link to a staged new task (its other end a tray ref, `m<message>-<index>`) is drawn once that task exists.
    const mine = [...staged.values()].filter(s => REL_OPS.includes(s.op) && typeof (s.fields || {}).other !== 'string' && (s.task_id === t.id || (s.fields || {}).other === t.id || (s.fields || {}).into === t.id));
    const sugg = (t.rel && t.rel.suggestions) || [];
    if (!mine.length && !sugg.some(s => suggestionStaged(s.evidence_id))) return undefined;
    const base = t.rel || emptyRel();
    const r = Object.assign({}, base, { links: (base.links || []).slice(), waits_on_open: (base.waits_on_open || []).slice(), frees_open: (base.frees_open || []).slice() });
    const drop = (arr, x) => arr.filter(y => y !== x);
    let suggs = sugg.filter(s => !suggestionStaged(s.evidence_id));
    for (const s of mine) {
      const f = s.fields || {};
      if (s.op === 'merge') {        // the pair's suggestions are settled by it; the duplicate's status is view()'s
        const other = s.task_id === t.id ? f.into : s.task_id;
        suggs = suggs.filter(x => x.other !== other);
        continue;
      }
      const dir = s.task_id === t.id ? 'out' : 'in', other = dir === 'out' ? f.other : s.task_id, kind = f.kind;
      const same = l => l.kind === kind && l.dir === dir && l.other === other;
      if (s.op === 'link') {
        if (r.links.some(same)) continue;      // already linked: Apply would skip it
        r.links.push({ kind, dir, other, staged: true });
        if (kind === 'waits_on' && isOpenTask(other)) {
          if (dir === 'out') r.waits_on_open = [other].concat(drop(r.waits_on_open, other));     // first, so its badge names it
          else r.frees_open = drop(r.frees_open, other).concat([other]);      // last: "do first" keeps naming what makes it urgent
        }
        if (kind === 'part_of') {
          if (dir === 'out') r.goal_id = other;
          else { r.steps_total += 1; if (isOpenTask(other)) r.steps_open += 1; }
        }
      } else {      // unlink
        if (!r.links.some(same)) continue;
        r.links = r.links.filter(l => !same(l));
        if (kind === 'waits_on') {
          if (dir === 'out') r.waits_on_open = drop(r.waits_on_open, other);
          else r.frees_open = drop(r.frees_open, other);
        }
        if (kind === 'part_of') {
          if (dir === 'out' && r.goal_id === other) r.goal_id = (r.links.find(l => l.kind === 'part_of' && l.dir === 'out') || {}).other || null;
          if (dir === 'in') { r.steps_total = Math.max(0, r.steps_total - 1); if (isOpenTask(other)) r.steps_open = Math.max(0, r.steps_open - 1); }
        }
      }
    }
    if (!r.frees_open.length && ['frees', 'chain'].includes(r.needed_by_via)) Object.assign(r, { needed_by: null, needed_by_id: null, needed_by_via: null });     // it frees nothing now
    r.suggestions = suggs;
    return r;
  }
  function isWaiting(x) { return !!(x.rel && x.rel.waits_on_open && x.rel.waits_on_open.length); }
  // The badges a row carries, in frame 1's order. `jump` names the task a tap goes to, `open` the row's own task.
  function badgesFor(x) {
    const r = x.rel; if (!r) return [];
    const links = r.links || [], out = [];
    const more = n => n > 1 ? ` +${n - 1}` : '';
    const tagged = (kind, dir, other) => links.some(l => l.staged && l.kind === kind && l.dir === dir && (other === undefined || l.other === other));
    const title = n => { const t = nameOf(n); return `Task-${n}` + (t ? ` · ${t.name}` + (t.status === 'done' ? ' (done)' : t.status === 'dropped' ? ' (dropped)' : '') : ''); };
    const frees = r.frees_open || [], waits = r.waits_on_open || [], sugg = (r.suggestions || []).filter(s => s.signal !== 'filing');      // a filing suggestion is a chip (filingChipsHtml)
    if (frees.length) {
      const n = r.needed_by_via === 'frees' && r.needed_by_id != null ? r.needed_by_id : frees[0];
      out.push({ kind: 'frees', look: tagged('waits_on', 'in', n) ? 'staged' : 'ok', text: (r.needed_by ? 'do first: frees ' : 'frees ') + `Task-${n}` + more(frees.length), jump: n, title: title(n) });
    }
    if (waits.length) out.push({ kind: 'waits', look: tagged('waits_on', 'out', waits[0]) ? 'staged' : 'wait', text: `waits on Task-${waits[0]}` + more(waits.length), jump: waits[0], title: title(waits[0]) });
    if (r.steps_total > 0) {
      const done = r.steps_total - (r.steps_open || 0);
      out.push({ kind: 'steps', look: tagged('part_of', 'in') ? 'staged' : 'ok', text: `${done} of ${r.steps_total} steps done`, open: x.id, done, total: r.steps_total, title: `Task-${x.id}: ${r.steps_open || 0} of ${r.steps_total} steps still open` });
    }
    if (r.changed) {
      const by = r.changed.by;
      out.push({ kind: 'changed', look: 'chg', text: by != null ? `updated by Task-${by}` : 'updated by a linked task', open: x.id, title: by != null ? title(by) : 'Changed by a linked task since you last looked' });
    }
    if (r.goal_id != null) {
      const goals = links.filter(l => l.kind === 'part_of' && l.dir === 'out').length;
      out.push({ kind: 'partof', look: tagged('part_of', 'out', r.goal_id) ? 'staged' : 'ok', text: `part of Task-${r.goal_id}` + more(goals), jump: r.goal_id, title: title(r.goal_id) });
    }
    if (sugg.length) {
      const s = sugg[0], n = s.other, T = `Task-${n}`;
      const said = s.kind === 'same_as' ? `same as ${T}?` : s.kind === 'part_of' ? (s.dir === 'in' ? `step: ${T}?` : `part of ${T}?`)
        : s.kind === 'waits_on' ? (s.dir === 'in' ? `frees ${T}?` : `waits on ${T}?`) : `linked to ${T}?`;
      out.push({ kind: 'suggest', look: 'sus', text: said + more(sugg.length), jump: n, title: title(n) });
    }
    return out;
  }
  const BADGE_ICON = {
    frees: '<svg aria-hidden="true" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14"></path><path d="M13 6l6 6-6 6"></path></svg>',
    waits: '<svg aria-hidden="true" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"></circle><path d="M12 7v5l3 2"></path></svg>',
    changed: '<span class="dot" aria-hidden="true"></span>',
  };
  const stepsBar = (done, total) => `<span class="bar" aria-hidden="true"><span style="width:${total ? Math.round(100 * done / total) : 0}%"></span></span>`;
  function badgeHtml(b) {
    const words = '<span>' + esc(b.text).replace(/Task-\d+/, '<span class="tid">$&</span>') + '</span>';      // one flex item: no gap before a "?" or "+1"
    const inner = (b.kind === 'steps' ? stepsBar(b.done, b.total) : BADGE_ICON[b.kind] || '') + words;
    const target = b.jump != null ? `href="/t/${b.jump}" data-lb="${b.jump}"` : `href="/t/${b.open}" data-lb-open="${b.open}"`;
    return `<a class="lb ${b.look}${b.kind === 'steps' ? ' steps' : ''}" ${target} title="${esc(b.title)}">${inner}</a>`;
  }
  // The steps badge alone and not a link (Review's progress chip).
  function stepsChip(rel) {
    if (!rel || !(rel.steps_total > 0)) return '';
    const done = rel.steps_total - (rel.steps_open || 0);
    return `<span class="lb ok steps">${stepsBar(done, rel.steps_total)}<span>${done} of ${rel.steps_total} steps done</span></span>`;
  }
  // A row's link badges and "needed for <date>": every badge on a desktop; one on a phone, the rest counted.
  function linkChipsHtml(x) {
    if (!x.rel) return '';
    const bs = badgesFor(x), r = x.rel;
    const need = r.needed_by ? new Date(String(r.needed_by).slice(0, 10) + 'T12:00:00').toLocaleDateString([], { month: 'short', day: 'numeric' }) : null;
    if (!PHONE()) return bs.map(badgeHtml).join('') + (need ? `<span class="chip need">needed for ${need}</span>` : '');
    const k = bs.length - 1;
    return (bs.length ? badgeHtml(bs[0]) : '') + (k > 0 ? `<span class="chip morelinks">${k === 1 ? '1 more link' : k + ' more links'}</span>` : '') + (need ? `<span class="chip need">for ${need}</span>` : '');
  }
  function wireBadges(root) {
    root.querySelectorAll('[data-lb]').forEach(a => a.addEventListener('click', e => { e.preventDefault(); e.stopPropagation(); goFromPage(+a.dataset.lb); }));
    root.querySelectorAll('[data-lb-open]').forEach(a => a.addEventListener('click', e => { e.preventDefault(); e.stopPropagation(); openTask(+a.dataset.lbOpen); }));
  }
  // A badge's tap: the named task's row on a desktop, its page on a phone.
  function goTask(id) { if (PHONE()) openTask(id); else jumpTo(id, false); }
  // A task named on the task page (a Linked tasks card, a Task-N in History): on a phone, or when no list
  // shows it, its page opens over this one with the trail; otherwise its row, like a badge's tap.
  function goFromPage(id) {
    if (ui.page !== null && (PHONE() || !listed(id))) openPage(id, true, ui.page);
    else goTask(id);
  }
  // Some open task has something a badge shows (a finished merge's same_as alone does not count, nor a filing
  // suggestion: that is a chip on a task added by hand, not a badge).
  function hasLinks() {
    return openOnes().some(x => x.rel && ((x.rel.links || []).some(l => l.kind === 'part_of' || l.kind === 'waits_on') || (x.rel.suggestions || []).some(s => s.signal !== 'filing') || x.rel.changed));
  }
  // The badge colours, under a list whose drawn rows carry a badge (`groups`: the list's groups, a folded one draws
  // no rows); the Linked view passes none.
  function legendHtml(groups) {
    if (PHONE() || !hasLinks()) return '';
    if (groups && !groups.some(g => !folded(g.key) && g.rows.some(x => badgesFor(x).length))) return '';
    return `<div class="lblegend"><span><i class="ok"></i>a link you or Claude confirmed</span><span><i class="sus"></i>a link the app suspects, one tap to confirm</span><span><i class="chg"></i>changed by a linked task since you last looked</span></div>`;
  }

  // ---------- suggestions (stage 7): filing chips ----------
  // What the filing run proposed for a task made by hand with only a name: a project, an effort (`filing` on the
  // task) and a link or twin (a suggestion with signal `filing`). Each is a dashed chip on the row, the opened row and
  // the task page: a tap stages it with its verdict, × dismisses it. Gone once either is staged.
  const FILING_SAID = { part_of: ['part of', 'step:'], waits_on: ['waits on', 'frees'], same_as: ['same as', 'same as'] };
  const filingLinks = x => ((x.rel && x.rel.suggestions) || []).filter(s => s.signal === 'filing' && FILING_SAID[s.kind]);
  function filingChipsHtml(x) {
    if (x.status !== 'open') return '';
    const chip = (eid, label, title) => `<span class="fsug"><button class="chip fsug-ok" data-fsug="${eid}" data-id="${x.id}" title="${esc(title)}">${esc(label).replace(/Task-\d+/, '<span class="tid">$&</span>')}</button><button class="fsug-no" data-fsug-no="${eid}" data-id="${x.id}" aria-label="Not this">×</button></span>`;
    const said = 'Suggested when you made it · tap to accept';
    // A field the editor has already staged keeps the editor's choice: its chip waits (a tap would replace that choice
    // under the same key). It is back if the edit is taken back, and the server drops it once the field is set.
    const srv = byId(x.id) || detailCache.get(x.id) || x;
    const edited = f => staged.has(editKey(x.id, f.field)) || (x[f.field] ?? null) !== (srv[f.field] ?? null);
    const fields = (x.filing || []).filter(f => !suggestionStaged(f.evidence_id) && !edited(f)).map(f => chip(f.evidence_id, f.label, said));
    const links = filingLinks(x).filter(s => !suggestionStaged(s.evidence_id)).map(s => {
      const T = `Task-${s.other}`, n = nameOf(s.other);
      return chip(s.evidence_id, `${FILING_SAID[s.kind][s.dir === 'in' ? 1 : 0]} ${T}?`, `${said} · ${T}${n ? ' ' + n.name : ''}`);
    });
    return fields.concat(links).join('');
  }
  function wireFilingChips(root) {
    // The server's task (the open list, or the opened task's payload) holds what the chip proposed.
    const src = id => byId(id) || detailCache.get(id) || {};
    root.querySelectorAll('[data-fsug]').forEach(b => b.addEventListener('click', e => {
      e.preventDefault(); e.stopPropagation();
      const id = +b.dataset.id, eid = +b.dataset.fsug, t = src(id);
      const f = (t.filing || []).find(c => c.evidence_id === eid);
      if (f) {      // under the editor's key: the row and the editor show it before Apply, a later choice replaces it
        stageJudge(eid, id, 'applied', 'filing suggestion accepted');      // first: the set below names its verdict (fsug)
        stageChange({ task_id: id, op: 'set', fields: { [f.field]: f.value } }, `filed: ${f.field} ${f.value}`, { fsug: eid }, 'edit-' + f.field);
      } else {
        const s = filingLinks(t).find(c => c.evidence_id === eid); if (!s) return;
        const c = s.kind === 'same_as'
          ? { task_id: s.keep === id ? s.other : id, op: 'merge', fields: { into: s.keep != null ? s.keep : s.other } }
          : { task_id: s.dir === 'out' ? id : s.other, op: 'link', fields: { kind: s.kind, other: s.dir === 'out' ? s.other : id } };
        stageChange(c, '', { ev: eid });
      }
      toast('Staged · press Apply in the tray');
    }));
    root.querySelectorAll('[data-fsug-no]').forEach(b => b.addEventListener('click', e => {
      e.preventDefault(); e.stopPropagation();
      stageJudge(+b.dataset.fsugNo, +b.dataset.id, 'dismissed', 'filing suggestion dismissed');
    }));
  }

  // ---------- links (stage 4b): filters and the Linked view ----------
  // One remembered choice for Today and Timeline. Without links the bar is not drawn and the choice reads as All.
  function setLinkView(v) { linkView = v; try { localStorage.setItem('factotum.linkview', v); } catch (_) {} }
  const effectiveLinkView = () => hasLinks() ? linkView : 'all';
  // Ready now hides a waiting task, never a hard-dated one (design §7.4): that one stays, dimmed.
  const readyRow = x => !isWaiting(x) || !!(x.hard && x.due);
  const dayMon = iso => iso ? new Date(String(iso).slice(0, 10) + 'T12:00:00').toLocaleDateString([], { month: 'short', day: 'numeric' }) : '';
  // What belongs together: a goal with its steps, a chain of waiting tasks from its first task, a suspected twin.
  // A task may sit in more than one group.
  function linkedGroups() {
    const open = openOnes(), byOpen = new Map(open.map(x => [x.id, x]));
    const out = [];
    const dueThenScore = (a, b) => (a.due || '9').localeCompare(b.due || '9') || (b.score || 0) - (a.score || 0);
    for (const g of open.filter(x => x.rel && x.rel.steps_total > 0).sort(dueThenScore)) {
      const steps = (g.rel.links || []).filter(l => l.kind === 'part_of' && l.dir === 'in').map(l => l.other).filter((id, i, a) => a.indexOf(id) === i);
      const closed = steps.filter(id => !byOpen.has(id)).map(id => { const t = byId(id); return t ? view(t) : nameOf(id); }).filter(Boolean);
      const total = g.rel.steps_total, done = total - (g.rel.steps_open || 0);
      out.push({ key: 'linked:g' + g.id, kind: 'goal', rows: [g].concat(steps.filter(id => byOpen.has(id)).map(id => byOpen.get(id))), closed, done, total });
    }
    for (const h of open.filter(x => x.rel && (x.rel.frees_open || []).length && !isWaiting(x))) {
      const rows = [h], seen = new Set([h.id]);
      const walk = t => { for (const id of (t.rel && t.rel.frees_open) || []) { const w = byOpen.get(id); if (!w || seen.has(id)) continue; seen.add(id); rows.push(w); walk(w); } };
      walk(h);
      if (rows.length < 2) continue;
      const late = rows.slice(1).filter(x => x.hard && x.due && fmtDue(x.due).days < 0).sort((a, b) => a.due.localeCompare(b.due))[0];
      out.push({ key: 'linked:w' + h.id, kind: 'waits', rows, late });
    }
    const twins = new Set();
    for (const x of open) for (const s of (x.rel && x.rel.suggestions) || []) {
      if (s.kind !== 'same_as' || twins.has(s.evidence_id) || suggestionStaged(s.evidence_id) || !byOpen.has(s.other)) continue;
      twins.add(s.evidence_id);
      const keep = s.keep != null ? s.keep : s.other, goes = keep === x.id ? s.other : x.id;
      if (!byOpen.has(keep) || !byOpen.has(goes) || keep === goes) continue;
      out.push({ key: 'linked:t' + s.evidence_id, kind: 'twin', rows: [byOpen.get(keep), byOpen.get(goes)], keep, goes, eid: s.evidence_id, why: s.why });
    }
    return out;
  }
  const SOURCE_SAID = { Canvas: 'from Canvas', Plaud: 'from a recording', Mail: 'from mail', Manual: 'made by hand', Ripple: 'suggested after a linked task changed' };
  function twinHtml(g) {
    const cell = x => { const d = fmtDue(x.due), from = SOURCE_SAID[x.source] || (x.source ? 'from ' + x.source : '');
      return `<div class="twt"><div><span class="code">Task-${x.id}</span> <span class="name">${esc(x.name)}</span></div><div class="src">${esc([from, d ? 'due ' + d.label : 'no date'].filter(Boolean).join(', '))}</div></div>`; };
    return `<section class="lgroup twin"><div class="lhead"><span class="lt">Possibly the same task, twice</span><span class="spacer"></span>${g.why ? `<span class="why">${esc(g.why)}</span>` : ''}</div>
      <div class="tw">${cell(g.rows[0])}${cell(g.rows[1])}<div class="twb"><button class="btn small primary" data-twin-merge="${g.goes}:${g.keep}:${g.eid}">Merge into ${g.keep}</button><button class="btn small" data-twin-not="${g.eid}:${g.goes}:${g.keep}">Not the same</button></div></div></section>`;
  }
  const TICK = '<svg role="img" aria-label="Done" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12l5 5 9-10"></path></svg>';
  const CROSS = '<svg role="img" aria-label="Dropped" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M7 7l10 10"></path><path d="M17 7L7 17"></path></svg>';
  function closedStepHtml(t) {
    const dropped = t.status === 'dropped', when = dayMon(t.completed_at);
    return `<a class="lstep closed${dropped ? ' dropped' : ''}" href="/t/${t.id}" data-lb="${t.id}">${dropped ? CROSS : TICK}<span class="body"><span><span class="code">Task-${t.id}</span> <span class="name">${esc(t.name)}</span></span>${t.outcome ? `<span class="outcome">Outcome: <span>${esc(t.outcome)}</span></span>` : ''}</span><span class="chip ${dropped ? 'dropped' : 'donechip'}">${dropped ? 'dropped' : 'done'}${when ? ' ' + esc(when) : ''}</span></a>`;
  }
  // Steps and waiting tasks are drawn indented: a copy carries `under`, never the row openOnes() returned.
  const under = x => Object.assign({}, x, { under: true });
  function linkedHtml() {
    const gs = linkedGroups();
    if (!gs.length) return '<div class="empty">No linked tasks.</div>';
    return gs.map(g => {
      if (g.kind === 'twin') return twinHtml(g);
      if (g.kind === 'goal') return `<section class="lgroup"><div class="lhead"><span class="lt">Steps of one goal</span><span class="spacer"></span>${stepsBar(g.done, g.total)}<span class="cnt">${g.done} of ${g.total} done</span>${listsSteps(g.rows[0].context) ? `<button class="btn small" data-make-steps="${g.rows[0].id}">Make them tasks</button>` : ''}</div>
        ${row(g.rows[0])}${g.closed.map(closedStepHtml).join('')}${g.rows.slice(1).map(x => row(under(x))).join('')}</section>`;
      const k = g.rows.length - 1, late = g.late && fmtDue(g.late.due);
      return `<section class="lgroup"><div class="lhead"><span class="lt">${k === 1 ? 'One task is holding up another' : `One task is holding up ${k} others`}</span><span class="spacer"></span>${late ? `<span class="late">the waiting task is ${esc(late.days === -1 ? '1d overdue' : late.label)}</span>` : ''}</div>
        ${row(g.rows[0])}${g.rows.slice(1).map(x => row(under(x))).join('')}</section>`;
    }).join('');
  }
  function linkBarHtml() {
    if (!hasLinks()) return '';
    const open = openOnes(), lv = linkView;
    const linked = new Set(); linkedGroups().forEach(g => g.rows.forEach(x => linked.add(x.id)));
    const views = [['all', 'All', open.length], ['linked', 'Linked', linked.size], ['ready', 'Ready now', open.filter(readyRow).length]];
    return `<div class="linkbar"><div class="seg" role="tablist" aria-label="Show">${views.map(([k, l, n]) => `<button role="tab" data-linkview="${k}" aria-selected="${lv === k}">${l}<span class="n">${n}</span></button>`).join('')}</div>
      <span class="lhint">${lv === 'linked' ? 'Tasks that belong together, shown together.' : 'A badge names the linked task. Click it to jump there.'}</span></div>`;
  }

  // ---------- links (stage 4b): Today's order ----------
  // Within one group, a waiting task is drawn under the first task it waits on that the group holds, chains
  // included (depth first, roots in their current order). A loop in old data draws each row once.
  function nestWaiting(rows) {
    const ids = new Set(rows.map(x => x.id)), parent = new Map(), kids = new Map();
    for (const w of rows) {
      const p = ((w.rel && w.rel.waits_on_open) || []).find(id => id !== w.id && ids.has(id));
      if (p == null) continue;
      parent.set(w.id, p);
      if (!kids.has(p)) kids.set(p, []);
      kids.get(p).push(w);
    }
    if (!parent.size) return rows;
    const out = [], seen = new Set();
    const walk = (x, child) => {
      if (seen.has(x.id)) return;
      seen.add(x.id); out.push(child ? under(x) : x);
      for (const k of kids.get(x.id) || []) walk(k, true);
    };
    rows.filter(x => !parent.has(x.id)).forEach(x => walk(x, false));
    rows.forEach(x => walk(x, false));      // rows of a loop, which no root reaches
    return out;
  }
  // Today's groups in link order. A task that frees a hard-dated task in an earlier group is drawn in that
  // group, just before it (never pushed to a later one); then each group nests its waiting tasks. `open` is
  // todayGroups' own list, so a snoozed or filtered-out task is never pulled in. A hard-dated waiting task
  // never leaves its group. The rows given are never changed: an indented row is a copy.
  function arrangeToday(groups, open) {
    const gs = groups.map(g => Object.assign({}, g, { rows: g.rows.slice() }));
    const holds = (g, id) => g.rows.some(y => y.id === id);
    for (const x of open) {
      const r = x.rel;
      if (!r || !r.needed_by || r.needed_by_id == null || !['frees', 'chain'].includes(r.needed_by_via)) continue;
      if (isWaiting(x) && x.hard && x.due) continue;
      const H = r.needed_by_id, h = gs.findIndex(g => holds(g, H));
      if (h < 0 || gs.slice(0, h + 1).some(g => holds(g, x.id))) continue;
      gs.slice(h + 1).forEach(g => { g.rows = g.rows.filter(y => y.id !== x.id); });
      gs[h].rows.splice(gs[h].rows.findIndex(y => y.id === H), 0, x);
    }
    gs.forEach(g => { g.rows = nestWaiting(g.rows); });
    return gs;
  }

  // ---------- the opened task (stage 4b): linked tasks, provenance, History ----------
  // GET /api/tasks/<id> is asked only for a task that is open on screen (a desktop row, the page's task) and
  // kept by id. load() marks every entry stale and what is shown stays until the new answer arrives. On
  // arrival the page re-renders, or, while something is typed (typingNow), only the two containers the
  // answer fills are swapped in place, so the box being typed in is never redrawn.
  const detailCache = new Map();     // id → the payload (+ stale: true after a load()), {id, missing: true} on 404, or {id, failed: <message>}
  const detailBusy = new Set();      // ids whose request is in flight
  let detailGen = 0;                 // bumped by load(): an answer to a request sent before it arrives stale
  async function loadDetail(id, before) {
    if (detailBusy.has(id)) return;
    detailBusy.add(id);
    const gen = detailGen, more = before != null;
    let d;
    try {
      const got = await api(`/api/tasks/${id}` + (more ? '?before=' + before : ''));
      const had = detailCache.get(id);
      d = more && had && had.history
        ? Object.assign({}, got, { history: { entries: had.history.entries.concat(got.history.entries), more: got.history.more, next_before: got.history.next_before } })
        : got;
      const t = byId(id); if (t && t.rel) t.rel.changed = null;      // the server just stamped it: the "updated by" badge goes
    } catch (e) {
      if (e.message === 'signed out' || more) {       // signed out has its own screen; a failed Earlier keeps what is shown
        detailBusy.delete(id);
        if (more && e.message !== 'signed out') toast('Could not load earlier History: ' + e.message);
        return;
      }
      d = e.message === '404' ? { id, missing: true } : { id, failed: e.message };
    }
    detailBusy.delete(id);
    if (gen !== detailGen) d.stale = true;
    detailCache.set(id, d);
    // A task page still saying "Loading…" has no containers to swap (and no box to type in): it is drawn whole.
    typingNow() && document.querySelector(`#panel [data-dhist="${id}"]`) ? swapDetail(id) : render();
  }
  // Called at the end of render(): fetch what an opened row or the task page needs and has not got (or got
  // before a load()). The task page reads its task from here when no open list holds it.
  function ensureDetails() {
    const ids = PHONE() ? [] : [...ui.open].filter(id => document.querySelector(`#panel .row.open[data-id="${id}"]`));
    if (ui.page !== null) ids.push(ui.page);
    for (const id of ids) { const d = detailCache.get(id); if ((!d || d.stale) && !detailBusy.has(id)) loadDetail(id); }
  }
  const tref = n => `<a class="tref" href="/t/${n}" data-open-task="${n}">Task-${n}</a>`;
  const linkTasks = s => esc(s).replace(/Task-(\d+)/g, (_, n) => tref(n));
  // A date or a timestamp as "Nov 10", in this browser's zone (a date alone is read at noon).
  const monDay = iso => { const s = String(iso || ''); if (!s) return ''; const d = new Date(s.length > 10 ? s : s + 'T12:00:00'); return isNaN(d) ? '' : d.toLocaleDateString([], { month: 'short', day: 'numeric' }); };
  // A stamp's day where the owner is (YYYY-MM-DD), as History's times are local; a bare date is already a day.
  const localDay = iso => { const s = String(iso || ''); if (s.length <= 10) return s; const d = new Date(s); if (isNaN(d)) return s.slice(0, 10); const p = n => String(n).padStart(2, '0'); return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`; };
  const histWhen = iso => { const d = new Date(iso || ''); if (isNaN(d)) return ''; const p = n => String(n).padStart(2, '0'); return `${monDay(iso)} ${p(d.getHours())}:${p(d.getMinutes())}`; };
  function whereHtml(x) {
    return `<section class="dsec"><h4>Where this stands</h4><p class="ctx">${x.context ? esc(x.context) : 'Nothing written yet.'}</p></section>`;
  }
  // The content of [data-dlinks]: what only the task's own payload knows. Nothing before it arrives.
  function detailAsyncHtml(id) {
    const d = detailCache.get(id);
    if (!d || d.missing || d.failed) return '';
    return rewrittenHtml(d) + linkedListHtml(d) + nearbyHtml(d);
  }
  // When the text was last rewritten by a run another task set off. An undone rewrite is not the text any more.
  function rewrittenHtml(d) {
    const e = ((d.history || {}).entries || []).find(e => e.field === 'context' && !e.undone);
    const from = e && !rippleNoted(e, d.id) && e.cause && Array.isArray(e.cause.from) ? e.cause.from[0] : null;      // a note is not a rewrite
    if (!Number.isInteger(from)) return '';
    return `<p class="rewritten">Rewritten ${esc(monDay(e.at))} after ${tref(from)}. Earlier versions are in History.</p>`;
  }
  const LINK_KIND = { part_of: { out: 'part of', in: 'step' }, waits_on: { out: 'waits on', in: 'frees' }, same_as: { out: 'merged into', in: 'merged from' } };
  // An open task's step and waiting links each have Remove beside the entry (never inside its link), staging an
  // unlink; a staged removal is drawn struck through. A merge is undone in History, not here (stage 6).
  function linkedListHtml(d) {
    const rs = d.relations || [];
    if (!rs.length) return '';
    return `<section class="dsec"><h4>Linked tasks</h4>${rs.map(r => {
      const entry = linkedEntryHtml(r);
      if (d.status !== 'open' || !['part_of', 'waits_on'].includes(r.kind) || !(r.other || {}).id) return entry;
      const src = r.dir === 'out' ? d.id : r.other.id, dst = r.dir === 'out' ? r.other.id : d.id, st = staged.get(relKey(src, r.kind, dst));
      if (st && st.op === 'unlink') return `<div class="lkrow">${entry.replace('class="lk', 'class="lk removing')}<span class="chip staged">removal staged</span></div>`;
      return `<div class="lkrow">${entry}<button class="btn small rm" data-unlink="${src}|${r.kind}|${dst}">Remove</button></div>`;
    }).join('')}</section>`;
  }
  function linkedEntryHtml(r) {
    const o = r.other || {}, closed = o.status !== 'open';
    const kind = r.kind === 'waits_on' && r.dir === 'out' && closed ? 'waited on' : (LINK_KIND[r.kind] || {})[r.dir] || r.kind;
    let state;
    if (closed) state = `${o.status}${o.completed_at ? ' ' + monDay(o.completed_at) : ''}${o.outcome ? ': ' + o.outcome : ''}`;
    else {
      const g = byId(o.id), due = fmtDue(o.due), steps = g && g.rel && g.rel.steps_total > 0 ? g.rel : null;
      state = 'open' + (steps ? `, ${steps.steps_total - (steps.steps_open || 0)} of ${steps.steps_total} steps done` : '') + (due ? `, due ${due.label}` : '');
    }
    return `<a class="lk${closed ? ' closed' : ''}" href="/t/${o.id}" data-open-task="${o.id}"><span class="k">${esc(kind)}</span><span class="b"><span><span class="code">Task-${o.id}</span> <span class="name">${esc(o.name)}</span></span><span class="s">${esc(state)}</span></span></a>`;
  }
  // Provenance (design §4): tasks that share a recording or a mail thread, one line per place; never a link.
  function nearbyHtml(d) {
    const places = new Map();
    for (const n of d.nearby || []) {
      const k = n.via + '\n' + (n.label || '');
      if (!places.has(k)) places.set(k, { via: n.via, label: n.label, ids: [] });
      places.get(k).ids.push(n.id);
    }
    if (!places.size) return '';
    const lines = [...places.values()].map(p => `Same ${esc(p.via)}${p.label ? ` “${esc(p.label)}”` : ''} as ${p.ids.map(tref).join(', ')}.`);
    lines[lines.length - 1] += ' Shown for context, not a link.';
    return `<section class="dsec"><h4>Came from the same place</h4>${lines.map(l => `<p class="near">${l}</p>`).join('')}</section>`;
  }
  // Who made a change, from its batch's source.
  const WHO = { ui: 'you', review: 'you', command: 'you', automation: 'automatic', planner: 'planner', plaud: 'a recording', mail: 'mail', canvas: 'Canvas',
    connector: 'a connector', skill: 'Claude Code', chat: 'chat', ntfy: 'a reminder', import: 'import' };
  function whoText(e) {
    if (e.source === 'ripple') { const f = e.cause && Array.isArray(e.cause.from) ? e.cause.from[0] : null; return { text: Number.isInteger(f) ? `ripple from Task-${f}` : 'ripple', cls: 'chg' }; }
    const text = WHO[e.source] || String(e.source || '');
    return { text, cls: text === 'you' ? 'you' : '' };
  }
  const HIST_QUIET = new Set(['completed_at', 'closed_by', 'due_is_datetime', 'last_reviewed_at', 'close_on_sent_to']);
  const textOf = v => v == null ? '' : String(v);
  const isAppend = e => textOf(e.new) !== '' && textOf(e.new).startsWith(textOf(e.old));
  // A ripple only rewrites a text, and a rewrite its guard refused became a note (cause.noted): it says which
  // itself, so a rewrite of an empty text is not read as a note.
  const rippleNoted = (e, selfId) => e.source === 'ripple' && !!(e.cause && Array.isArray(e.cause.noted) && e.cause.noted.includes(selfId));
  const isNote = (e, selfId) => e.source === 'ripple' ? rippleNoted(e, selfId) : isAppend(e);
  // One History line, as HTML (task numbers are links). A link entry names the other task, seen from this one.
  const LINK_SAID = {
    'out waits_on': ['Now waits on {T}.', 'No longer waits on {T}.'], 'in waits_on': ['Now frees {T}.', 'No longer frees {T}.'],
    'out part_of': ['Linked as part of {T}.', 'No longer part of {T}.'], 'in part_of': ['{T} linked as a step.', '{T} is no longer a step.'],
    'out same_as': ['Merged into {T}.', 'No longer merged into {T}.'], 'in same_as': ['{T} merged into this task.', '{T} is no longer merged into this task.'] };
  const VERDICT_SAID = { applied: 'Suggestion accepted.', dismissed: 'Suggestion dismissed.', reviewed: 'Suggestion seen.', staged: 'Suggestion staged.' };
  // A ripple's rewrite that the guard refused (design §8.3) became a dated note; History says why.
  const NOTED_SAID = ' (the ripple\'s rewrite was not applied — it was empty, too long, or would have lost a link or a task reference — so its reason was added as a note)';
  function historyWords(e, selfId) {
    const v = e.new, o = e.old;
    if (e.field === 'link') {
      const l = (v != null ? v : o) || {}, said = LINK_SAID[`${l.dir} ${l.kind}`];
      if (said && Number.isInteger(l.other) && l.other !== selfId) return said[v != null ? 0 : 1].replace('{T}', tref(l.other));
    }
    if (e.field === 'context' && isNote(e, selfId)) return `Note added: “${linkTasks(textOf(v).slice(textOf(o).length).trim())}”` + (rippleNoted(e, selfId) ? NOTED_SAID : '');
    if (e.field === 'context') return 'Text rewritten.';
    if (e.field === 'name') return 'Renamed.';
    if (e.field === 'outcome') return `Outcome: “${linkTasks(textOf(v))}”`;
    if (e.field === 'evidence' && VERDICT_SAID[(v || {}).verdict]) return VERDICT_SAID[v.verdict];
    if (e.field === 'created') return 'Created.';
    const s = fieldSaid(e);
    return esc(s.charAt(0).toUpperCase() + s.slice(1) + '.');
  }
  function diffHtml(e, selfId) {
    if (!['context', 'name', 'why_now'].includes(e.field) || (e.field === 'context' ? isNote(e, selfId) : isAppend(e))) return '';
    return beforeAfter(e.old, e.new);
  }
  // The content of [data-dhist]: one entry per batch, newest first, with its Undo.
  function historyHtml(id) {
    const d = detailCache.get(id);
    const head = '<div class="hhead"><h4>History</h4><span class="spacer"></span><span class="hint">how this task got to where it is</span></div>';
    if (!d) return head + '<p class="hint">Loading History…</p>';
    if (d.missing || d.failed) return head + `<p class="hint">Could not load History (${esc(d.missing ? '404' : d.failed)}).</p>`;
    const h = d.history || { entries: [] }, groups = [];
    for (const e of h.entries) { const g = groups[groups.length - 1]; if (g && g[0].batch_id === e.batch_id) g.push(e); else groups.push([e]); }
    const items = groups.map(g => {
      const e0 = g[0], who = whoText(e0), shown = g.filter(e => !HIST_QUIET.has(e.field));
      const undo = e0.undone ? '<span class="chip">undone</span>'
        : `<button class="btn small" data-hist-undo="${e0.batch_id}">${e0.batch_tasks > 1 ? `Undo all ${e0.batch_changes} changes made together` : 'Undo'}</button>`;
      const lines = shown.length ? shown.map(e => `<div class="hl">${historyWords(e, id)}${e.note ? ` <span class="why">Why: ${linkTasks(e.note)}</span>` : ''}</div>${diffHtml(e, id)}`).join('')
        : '<div class="hl">Reviewed, kept as it is.</div>';
      // A ripple whose cause was undone after its text was edited stays (batches.undo): said, with this Undo.
      const causeUndone = e0.source === 'ripple' && !!(e0.cause && e0.cause.cause_undone_at);
      const kept = causeUndone && !e0.undone ? '<div class="hl kept">Its cause was undone; this was kept because the text changed after. Undo it here if it no longer fits.</div>' : '';
      return `<div class="hitem${e0.undone ? ' undone' : ''}"><div class="hh"><span class="when">${esc(histWhen(e0.at))}</span><span class="who ${who.cls}">${esc(who.text)}</span>${causeUndone ? '<span class="chip causechip">cause undone</span>' : ''}<span class="spacer"></span>${undo}</div>${lines}${kept}</div>`;
    }).join('');
    return head + (items ? `<div class="hlist">${items}</div>` : '<p class="hint">Nothing has changed since it was created.</p>')
      + (h.more ? `<button class="btn small" data-hist-more="${id}">Earlier</button>` : '');
  }
  // In place, while something is typed: the two containers, and the task's link badges (its "updated by"
  // badge goes once the answer has stamped it; a phone's "N more links" counts one fewer).
  function swapDetail(id) {
    document.querySelectorAll(`#panel [data-dlinks="${id}"]`).forEach(el => { el.innerHTML = detailAsyncHtml(id); wireDetail(el); });
    document.querySelectorAll(`#panel [data-dhist="${id}"]`).forEach(el => { el.innerHTML = historyHtml(id); wireDetail(el); });
    const t = byId(id);
    if (t) document.querySelectorAll(`#panel [data-lbs="${id}"]`).forEach(el => { el.innerHTML = linkChipsHtml(view(t)); wireBadges(el); });
  }
  function wireDetail(root) {
    root.querySelectorAll('[data-open-task]').forEach(a => a.onclick = e => { e.preventDefault(); goFromPage(+a.dataset.openTask); });
    root.querySelectorAll('[data-hist-undo]').forEach(b => b.onclick = async () => {
      const id = +b.closest('[data-dhist]').dataset.dhist;
      b.disabled = true;
      let res;
      try { res = await undoBatch(+b.dataset.histUndo); } catch (e) { b.disabled = false; toast('Undo failed: ' + e.message); return; }
      toast(undoSaid(res));
      const d = detailCache.get(id); if (d) d.stale = true;
      logData = null;
      await load();
    });
    root.querySelectorAll('[data-hist-more]').forEach(b => b.onclick = () => { const id = +b.dataset.histMore, d = detailCache.get(id); if (d && d.history) loadDetail(id, d.history.next_before); });
    root.querySelectorAll('[data-unlink]').forEach(b => b.onclick = () => {      // Linked tasks' Remove (stage 6)
      const [src, kind, dst] = b.dataset.unlink.split('|');
      stageChange({ task_id: +src, op: 'unlink', fields: { kind, other: +dst } }, '');
      toast('Removal staged · press Apply in the tray');
    });
  }

  // ---------- rendering ----------
  function render() {
    document.body.classList.toggle('page-on', ui.page !== null);      // before measuring: the page hides the bar on a phone
    const bar = document.querySelector('header.bar'); if (bar) document.documentElement.style.setProperty('--bar-h', bar.offsetHeight + 'px');
    renderRail();
    const p = document.getElementById('panel');
    const fns = { today: renderToday, timeline: renderTimeline, projects: renderProjects, review: renderReview, chat: renderChat, log: renderLog, settings: renderSettings };
    const held = heldDraftBox(p);
    taskPlacesDrawn.clear(); editorsDrawn.clear();
    p.innerHTML = ui.page !== null ? taskPageHtml() : starterHtml() + pendingHtml() + fns[ui.tab]();
    // The focus mark on the copy j / k reached, not on every copy of a task drawn twice
    const rowsNow = [...p.querySelectorAll('.row')];
    if (ui.focusAt != null && rowsNow[ui.focusAt] && +rowsNow[ui.focusAt].dataset.id === ui.focus) rowsNow.forEach((r, n) => { if (n !== ui.focusAt) r.classList.remove('focus'); });
    restoreDraftBox(held);
    p.querySelectorAll('textarea.grow').forEach(grow);
    document.querySelectorAll('#tabs button').forEach(b => { b.setAttribute('aria-selected', b.dataset.tab === ui.tab); if (b.dataset.tab === ui.tab && PHONE() && b.scrollIntoView) b.scrollIntoView({ block: 'nearest', inline: 'center' }); });
    document.getElementById('n-today').textContent = openOnes().filter(x => x.due && fmtDue(x.due).days <= 1).length || '';
    showCount();
    renderRemind();
    renderTray();
    renderSheet();
    bottomRule();
    wire();
    ensureDetails();
  }
  // On a phone one thing sits at the bottom (stage 6): the tray, the short bar, the task page's bar, Review's
  // decisions, or else, on a list tab, the round New task button.
  function bottomRule() {
    document.body.classList.toggle('fab-on', PHONE() && LIST_TABS.includes(ui.tab) && ui.page === null && !staged.size && !ui.sel.size && !ui.sheet);
  }
  function starterHtml() {
    if (!(settings && settings.starter)) return '';
    return `<div class="starter" role="status"><b>Starter settings in use.</b> <span>Factotum is running on config/factotum.starter.yaml, not a file of your own.</span> ${ui.tab !== 'settings' ? '<button class="btn small" data-starter-help>How to make it your own</button>' : ''}</div>`;
  }
  function pendingHtml() {
    if (!pending.length) return '';
    return pending.map(p => `<div class="pending" data-pending="${p.id}"><div class="phead"><span class="chip top">${p.source === 'connector' ? 'Staged by Claude' : 'Staged by Claude Code'}</span><b>${esc(p.label || '')}</b><span class="hint" style="margin:0">${new Date(p.created_at).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}</span></div>
      <div class="plist">${p.changes.map(c => { const t = byId(c.task_id);
        if (c.op === 'create') return `<span class="chip">new task: ${esc(String((c.fields || {}).name || '').slice(0, 60))}${(c.fields || {}).due ? ' · due ' + esc(String(c.fields.due)) : ''}${(c.reminders || []).length ? ` · ${c.reminders.length} reminder${c.reminders.length === 1 ? '' : 's'}` : ''}${c.watch_sent_to ? ' · closes when mail to ' + esc(c.watch_sent_to) + ' is sent' : ''}</span>`;
        return `<span class="chip">Task-${c.task_id}${t ? ' ' + esc(t.name.slice(0, 40)) : ''} · ${esc(changeWords(c) || (c.op === 'watch_sent' ? 'close when mail' + ((c.fields || {}).to ? ' to ' + c.fields.to : '') + ' is sent' : c.op === 'set' ? Object.entries(c.fields || {}).filter(([k]) => k !== 'due_is_datetime').map(([k, v]) => `${k} ${v === null ? 'cleared' : String(v).slice(0, 40)}`).join(', ') : c.op === 'add_reminder' ? 'remind ' + fmtWhen(c.fields.fire_at) : c.op))}</span>`; }).join('')}</div>
      <div class="pact"><button class="btn small primary" data-pending-apply="${p.id}">Apply ${p.changes.length} change${p.changes.length === 1 ? '' : 's'}</button><button class="btn small" data-pending-dismiss="${p.id}">Dismiss</button></div></div>`).join('');
  }
  function renderRail() {
    const top = topList();
    topPos = new Map(top.map((x, i) => [x.id, i + 1]));
    const rail = document.getElementById('rail');
    rail.classList.toggle('collapsed', !ui.railOpen);
    document.querySelector('main').classList.toggle('rail-min', !ui.railOpen);
    // One tile per item: its rank, the name on up to three lines, then code, project and date. Folded,
    // only the rank is left (ringed red or amber by the date), and it still takes a click.
    rail.innerHTML = `<h2 id="rail-head" title="${ui.railOpen ? 'Fold the Top list' : 'Unfold the Top list'}"><span class="lbl">Top <span>${top.length} of ${data.top_cap}</span></span><button class="tog" aria-expanded="${ui.railOpen}" aria-label="${ui.railOpen ? 'Fold the Top list' : 'Unfold the Top list'}"></button></h2>
      <ol id="rail-list">${top.map((x, i) => { const d = fmtDue(x.due); const u = d ? (d.days < 0 ? 'crit' : d.days <= 1 ? 'warn' : '') : '';
        return `<li draggable="true" tabindex="0" role="button" data-id="${x.id}" class="${u}" title="${esc(x.name)}${d ? ' · ' + d.label : ''} — click to go to it, double-click to open its details"><span class="rank">${i + 1}</span><span class="tb"><span class="t">${esc(x.name)}</span><span class="m"><span class="code">Task-${x.id}</span><span>${esc(x.project || '—')}</span><span class="due ${u}">${d ? d.label + (x.hard ? ' · hard' : '') : 'no date'}</span></span></span></li>`; }).join('')}</ol>
      <div class="drop" id="rail-drop">Drag a row here, or press <b>t</b></div>`;
    document.getElementById('rail-head').onclick = () => { ui.railOpen = !ui.railOpen; try { localStorage.setItem('factotum.rail', ui.railOpen ? '1' : '0'); } catch (_) {} renderRail(); wireRail(); };
  }
  // Where a task's row is: this tab if it lists it, else Today, else Timeline (which lists every
  // open task). The section is unfolded, the row focused, scrolled to and flashed; `expand` opens its details.
  function groupsOf(tab) {
    if ((tab === 'today' || tab === 'timeline') && effectiveLinkView() === 'linked') return linkedGroups();
    return tab === 'today' ? todayGroups().groups : tab === 'timeline' ? timelineGroups() : tab === 'projects' ? projectGroups().map(([p, rows]) => ({ key: 'projects:' + p, rows })) : [];
  }
  // The list (this tab, else Today, else Timeline) and section that draws the task's row, or null.
  function findRow(id) {
    const tabs = [ui.tab, 'today', 'timeline'].filter((t, i, a) => ['today', 'timeline', 'projects'].includes(t) && a.indexOf(t) === i);
    for (const tab of tabs) { const g = groupsOf(tab).find(g => g.kind !== 'twin' && g.rows.some(r => r.id === id)); if (g) return { tab, key: g.key }; }      // a twin card draws no rows to scroll to
    return null;
  }
  // Some list draws the task's row once the link filter is All.
  function listed(id) {
    const was = linkView; linkView = 'all';
    try { return !!findRow(id); } finally { linkView = was; }
  }
  // `replace` (only when opening at /t/<id>) swaps the address instead of pushing the tab's: a /timeline
  // pushed above /t/<id> would let Back return there, jump and push again, so Back could never leave.
  // Returns 'row', or 'page' when no list holds the task (closed, merged, missing): its page opens instead.
  function jumpTo(id, expand, replace = false) {
    if (expand && PHONE()) { openTask(id); return 'page'; }      // a phone opens the task page instead (a Top tile's double-tap)
    let hit = findRow(id);
    if (!hit && linkView !== 'all') {      // filtered out by Linked or Ready now: All shows it (the choice stays when All does not either)
      const was = linkView; linkView = 'all';
      hit = findRow(id);
      if (hit) setLinkView('all'); else linkView = was;
    }
    if (!hit) { openPage(id, !replace); return 'page'; }
    const from = ui.tab, wasPage = ui.page !== null;
    ui.page = null;      // a desktop's task page gives way to the row
    if (folded(hit.key, hit.tab === 'projects')) setFold(hit.key, false);
    ui.tab = hit.tab; ui.focus = id; if (expand) ui.open.add(id);
    if (from !== hit.tab || wasPage) setAddress(addressOf(hit.tab), replace);
    render();
    const el = document.querySelector(`#panel .row[data-id="${id}"]`);
    if (el) { el.scrollIntoView({ block: 'center', behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' }); el.classList.add('flash'); }
    if (hit.tab === 'timeline' && from !== 'timeline') toast(`Task-${id} is not on Today · shown in Timeline`);
    return 'row';
  }
  // What a prompt carries: the selected rows; on the list tabs, the focused row when nothing is
  // selected. In Chat only what is attached there, so a thread can start with nothing at all.
  function attachedIds() { return ui.sel.size ? [...ui.sel] : (ui.tab !== 'chat' && ui.focus ? [ui.focus] : []); }
  function grow(el) { el.style.height = 'auto'; el.style.height = Math.min(el.scrollHeight + 2, 260) + 'px'; }
  // Open tasks matching what is typed in Chat's "attach a task" box: a number, or every word of the name.
  function pickMatches() { return matchOpen(draft.pick, ui.sel); }
  // Open tasks matching a typed number or words, leaving out `exclude` (shared with the task picker, stage 6).
  function matchOpen(text, exclude) {
    const q = (text || '').trim().toLowerCase(); if (!q) return [];
    const num = /^(task[-\s]?)?\d+$/.test(q) ? q.replace(/\D/g, '') : null, words = q.split(/\s+/);
    return openOnes().filter(x => !exclude.has(x.id) && (num ? String(x.id).startsWith(num) : words.every(w => (x.name + ' ' + (x.project || '')).toLowerCase().includes(w))))
      .sort((a, b) => num ? (String(b.id) === num) - (String(a.id) === num) || a.id - b.id : (b.score || 0) - (a.score || 0)).slice(0, 8);     // the exact number first
  }
  function chatAttach() {
    const ids = attachedIds();
    const chips = ids.map(id => { const t = byId(id); return `<span class="chip att" title="${esc(t ? t.name : '')}">Task-${id}${t ? ` <span class="nm">${esc(t.name)}</span>` : ''}<button data-detach="${id}" aria-label="Remove Task-${id} from this prompt">×</button></span>`; }).join('');
    const hits = ui.pickOpen ? pickMatches() : [];
    return `<div class="attach">${ids.length ? chips : '<span>No tasks attached</span>'}
        <button class="btn small" id="chat-pick-open" aria-expanded="${!!ui.pickOpen}">+ Attach a task</button>${ids.length > 1 ? '<button class="btn small" id="chat-detach-all">Remove all</button>' : ''}</div>
      ${ui.pickOpen ? `<div class="pick"><input id="chat-pick" value="${esc(draft.pick)}" placeholder="A task number, or words from its name" aria-label="Find a task to attach" autocomplete="off">
        <div class="hits">${hits.map(x => { const d = fmtDue(x.due); return `<button data-pick="${x.id}"><span class="code">Task-${x.id}</span> <span class="nm">${esc(x.name)}</span><span class="chip proj">${esc(x.project || '—')}</span>${d ? `<span class="chip">${d.label}</span>` : ''}</button>`; }).join('') || (draft.pick.trim() ? '<div class="hint">No open task matches.</div>' : '')}</div></div>` : ''}`;
  }
  // Mail is on (settings not loaded yet: as if it were): the mail buttons and words show only then.
  const mailOn = () => !settings || !!settings.effective['sources.mail.enabled'];
  // What Refresh sweeps, in words: only the inputs that are on (settings not loaded yet: all four).
  function sweepsSaid() {
    const e = settings && settings.effective;
    const s = [['sources.mail.enabled', 'mail'], ['sources.canvas.enabled', 'Canvas'], ['sources.calendar.enabled', 'the calendar'], ['sources.slack.enabled', 'Slack']].filter(([k]) => !e || e[k]).map(([, w]) => w);
    return s.length > 1 ? s.slice(0, -1).join(', ') + ' and ' + s[s.length - 1] : s[0] || '';
  }
  // What a chat prompt can ask about: mail and calendar only when their input is on (settings not loaded yet: all three).
  function chatTopics() {
    const eff = settings && settings.effective;
    const t = ['tasks'].concat(!eff || eff['sources.mail.enabled'] ? ['mail'] : [], !eff || eff['sources.calendar.enabled'] ? ['calendar'] : []);
    return 'your ' + (t.length > 1 ? t.slice(0, -1).join(', ') + ' or ' + t[t.length - 1] : t[0]);
  }
  function composer() {
    const ids = attachedIds(), inChat = ui.tab === 'chat';
    const ask = inChat ? 'Ask about ' + chatTopics() : '';
    const ph = inChat ? (TOUCH() ? ask : ask + ' · Enter sends, Shift+Enter starts a new line')
      : PHONE() ? 'done 389 · push 364 fri · or ask Claude' : 'Command: done 389 392 · push 364 fri · nodue 364 · top 23 · longterm 38 · or ask Claude in plain words';
    const box = `<div class="cmd"><textarea id="cmd" class="grow" rows="1" placeholder="${ph}" aria-label="Prompt or command">${esc(draft.cmd)}</textarea><button class="btn primary" id="cmd-go" ${inChat && chat.busy ? 'disabled' : ''}>Send</button></div>`;
    if (inChat) return `<div class="composer">${chatAttach()}${box}</div>`;       // no row hints and no bulk bar here: there are no rows
    const phone = PHONE();      // a tap opens the task page there; the tick box selects
    return `${handBarHtml()}${findHtml()}${ui.newOpen && !phone ? newLineHtml() : ''}<div class="composer"><div class="attach">${ids.length ? 'Attached: ' + ids.map(id => `<span class="chip">Task-${id}</span>`).join('') : phone ? 'Nothing attached · tick a row to attach it' : 'Nothing attached · tap a row to attach it'}</div>
      ${box}<div data-add-prev="cmd">${addPreviewHtml('cmd')}</div>
      <p class="hint keys">Tap a row to select it (the bulk bar appears) · <kbd>j</kbd><kbd>k</kbd> move · <kbd>d</kbd> done · <kbd>x</kbd> drop · <kbd>s</kbd> snooze · <kbd>t</kbd> top · <kbd>l</kbd> long-term · <kbd>e</kbd> details · double-click a row for details · <kbd>Esc</kbd> deselect</p>
      <p class="hint touch">${phone ? 'Tap a row to open it · tick the box to select it' : 'Tap a row to select it · double-tap or ▸ for details'}</p></div>
      ${phone ? shortBar() : `<div class="bulk" id="bulk"><b id="bulk-n"></b> selected
        <button class="btn small" data-bulk="done">Done</button><button class="btn small" data-bulk="drop">Drop</button>
        <button class="btn small" data-bulk="snooze">Snooze to tomorrow</button>
        <span class="remform"><input type="date" id="bulk-date" aria-label="Due date"><button class="btn small" data-bulk="setdue">Set due</button><button class="btn small" data-bulk="cleardue">Remove due date</button></span>
        <button class="btn small" data-bulk="top">Pin to Top</button><button class="btn small" data-bulk="longterm">Long-term</button>${ui.sel.size === 2 ? `<button class="btn small" data-bulk="link" aria-expanded="${ui.bulkLink}">Link</button>` : ''}
        <span class="spacer"></span><button class="btn small" data-bulk="clear">Clear</button>${bulkLinkHtml()}</div>`}`;
  }
  // The phone's bulk bar: one line (Done, Drop, Snooze, More, ✕) that takes the tray's place at the
  // bottom while rows are selected; More opens a second line with the rest of desktop's actions.
  function shortBar() {
    return `<div class="bulk short" id="bulk"><b id="bulk-n"></b>
        <button class="btn small" data-bulk="done">Done</button><button class="btn small" data-bulk="drop">Drop</button><button class="btn small" data-bulk="snooze">Snooze</button>
        ${ui.sel.size === 2 ? `<button class="btn small" data-bulk="link" aria-expanded="${ui.bulkLink}">Link</button>` : ''}<button class="btn small" id="bulk-more" aria-expanded="${ui.bulkMore}">More</button>
        <span class="spacer"></span><button class="btn small" data-bulk="clear" aria-label="Clear selection">✕</button>${bulkLinkHtml()}
        ${ui.bulkMore ? `<div class="bulk-more">
          <span class="remform"><input type="date" id="bulk-date" aria-label="Due date"><button class="btn small" data-bulk="setdue">Set due</button><button class="btn small" data-bulk="cleardue">Remove due date</button></span>
          <button class="btn small" data-bulk="top">Pin to Top</button><button class="btn small" data-bulk="longterm">Long-term</button></div>` : ''}</div>`;
  }
  // A section of rows with a heading that folds it: {key, title, rows, cls, tag}. A folded section
  // renders no rows, so j/k and the bulk bar only ever see what is on screen.
  function group(g) {
    if (!g.rows.length) return '';
    const shut = folded(g.key);
    return `<section class="group ${shut ? 'folded' : ''}"><h3 class="${g.cls || ''}"><button class="fold" data-fold="${esc(g.key)}" aria-expanded="${!shut}">${g.title}<span class="n">${g.rows.length}</span></button></h3>${shut ? '' : `<div class="rows">${g.rows.map(x => row(x, g.tag ? g.tag(x) : '')).join('')}</div>`}</section>`;
  }
  function row(x, extraChip) {
    const cls = ['row', x.status === 'done' ? 'done' : '', x.status === 'dropped' ? 'drop' : '', ui.open.has(x.id) ? 'open' : '', ui.sel.has(x.id) ? 'sel' : '', ui.focus === x.id ? 'focus' : '', isWaiting(x) ? 'waiting' : '', x.under ? 'under' : ''].join(' ');
    return `<div class="${cls}" data-id="${x.id}">
      <div class="main">
        <input type="checkbox" id="ck-${x.id}" aria-label="Select" ${ui.sel.has(x.id) ? 'checked' : ''}>
        <div class="title"><span class="code">Task-${x.id}</span> <span class="name">${esc(x.name)}</span></div>
        <button class="chev" aria-label="Details" data-chev="${x.id}"></button>
        <div class="meta"${extraChip ? ` data-extra="${esc(extraChip)}"` : ''}>${chipsHtml(x, extraChip)}</div>
      </div>
      <div class="details">${ui.open.has(x.id) ? detailsHtml(x) : ''}</div></div>`;
  }
  // A task's chips (Top, long-term, date, effort, what is staged…): the row's .meta and the task page's.
  function chipsHtml(x, extraChip) {
    const d = fmtDue(x.due);
    const st = staged.get(x.id);
    const dueCls = d ? (d.days < 0 ? 'crit' : d.days <= 1 ? 'warn' : '') : '';
    const fadeDays = x.fade_at ? daysTo(x.fade_at) : null;
    return [
      `<span class="lbs" data-lbs="${x.id}">${linkChipsHtml(x)}</span>`,      // one box (display: contents) swapDetail redraws
      filingChipsHtml(x),      // outside .lbs: swapDetail rewrites that box with the link badges alone
      x.top_rank ? `<span class="chip top">Top ${topPos.get(x.id) || x.top_rank}</span>` : '',
      x.longterm_effective ? `<span class="chip lt">Long-term</span>` : '',
      x.close_on_sent_since ? `<span class="chip" title="Closes itself when a message to ${esc(x.close_on_sent_to || 'the watched address')} is sent">✉ closes on send</span>` : '',
      fadeDays !== null && fadeDays <= 7 && !x.top_rank ? `<span class="chip fade">fades ${fadeDays <= 0 ? 'now' : 'in ' + fadeDays + 'd'}</span>` : '',
      (x.reminders || []).length ? `<span class="chip rem">⏰ ${fmtWhen(x.reminders[0].fire_at)}</span>` : '',
      `<span class="chip proj">${esc(x.project || '—')}</span>`,
      extraChip || '',
      d ? `<span class="chip due ${dueCls} ${x.hard ? 'hard' : ''}" data-due="${x.id}" title="${x.hard ? 'Hard deadline' : 'Soft date'} · click to cycle">${d.label}</span>` : `<span class="chip due" data-due="${x.id}" title="No date · click to set">no date</span>`,
      `<span class="chip eff">${esc(x.effort || '?')}</span>`,
      st ? `<span class="chip staged">${esc(st.label)}</span>` : '',
      stagedCountHtml(x.id),
    ].join('');
  }
  // What an opened row shows, in two columns: Where this stands, Linked tasks, Came from the same place and
  // the facts (score, why now, source, links, lifecycle, reminders); History beside them; then, for an open
  // task, its actions. The task page lays out the same parts (taskPageHtml). The two [data-…] containers
  // fill when the task's own payload arrives (detailCache). An open task's left column starts with its editor (stage 6).
  function detailsHtml(x) {
    const edit = isOpenRow(x.id) && !editorsDrawn.has(x.id);      // the one copy of a row listed twice that edits (and links by hand)
    return `<div class="dgrid"><div class="dcol">${edit ? editorHtml(x) : whereHtml(x)}<div data-dlinks="${x.id}">${detailAsyncHtml(x.id)}</div>${edit ? linkByHandHtml(x) : ''}${factsHtml(x)}</div>
        <div class="dcol" data-dhist="${x.id}">${historyHtml(x.id)}</div></div>${x.status !== 'open' ? '' : actionsHtml(x, edit)}${taskPlaceHtml(x.id)}`;
  }
  function factsHtml(x) {
    const links = (x.links || []).map(l => {
      if (l.kind === 'plaud_note') return `<a href="https://web.plaud.ai/file/${esc(l.ref)}" target="_blank" rel="noopener" title="${esc(l.label || '')}">Plaud</a>`;
      if (l.kind === 'note_page') return `<a href="${esc(l.ref)}" target="_blank" rel="noopener" title="${esc(l.label || '')}">Note</a>`;
      if (l.kind === 'calendar') return `<span class="chip">📅 ${esc(l.label || 'event')}</span>`;
      if (l.kind === 'notion_page') return `<a href="${NOTION}${l.ref.replace(/-/g, '')}" target="_blank" rel="noopener">Notion row</a>`;
      if (l.kind === 'mail_thread' || l.kind === 'mail_message') return `<span class="chip">✉ ${esc(l.label || l.kind)}</span>`;
      return `<a href="${esc(l.ref)}" target="_blank" rel="noopener">${esc(l.label || l.kind)}</a>`;
    }).join('');
    const srcLink = x.source_link ? `<a href="${esc(x.source_link)}" target="_blank" rel="noopener">${x.source === 'Plaud' ? 'Plaud note' : x.source === 'Canvas' ? 'Canvas' : 'Source'}</a>` : '';
    return `<dl><dt>Score</dt><dd>${x.score} · ${x.priority} · ${esc((x.score_reasons || []).join(' · '))}</dd>
            <dt>Why now</dt><dd>${esc(x.why_now) || '<span class="code">none yet</span>'}</dd></dl>
        <dl><dt>Source</dt><dd>${esc(x.source || '?')}${x.account ? ' · ' + esc(x.account) : ''} · ${x.mentions} mention${x.mentions === 1 ? '' : 's'} · created ${esc(localDay(x.created_at))}${x.legacy_status ? ' · was ' + esc(x.legacy_status) : ''}</dd>
            <dt>Links</dt><dd class="links">${srcLink}${links || (srcLink ? '' : '<span class="code">none</span>')}</dd>
            <dt>Lifecycle</dt><dd>${x.tier}${x.fade_at ? ' · fades ' + x.fade_at : ''}${x.last_call_at ? ' · last call ' + x.last_call_at : ''}${x.snoozed_until ? ' · snoozed until ' + x.snoozed_until : ''}</dd>
            <dt>Reminders</dt><dd>${(x.reminders || []).map(r => `<span class="chip rem">${fmtWhen(r.fire_at)}${r.repeat ? ' · ' + esc(r.repeat) : ''} ${esc(r.label || '')}</span>`).join(' ') || '<span class="code">none</span>'}</dd></dl>`;
  }
  // `more`: this copy draws More's line when it is open (one copy of a row listed twice; stage 6).
  function actionsHtml(x, more = true) {
    return `<div class="actions">
          <button class="btn small" data-act="done" data-id="${x.id}">Done</button>
          <button class="btn small" data-act="drop" data-id="${x.id}">Drop</button>
          <button class="btn small" data-act="snooze" data-id="${x.id}">Snooze to tomorrow</button>
          <button class="btn small" data-act="top" data-id="${x.id}">${x.top_rank ? 'Unpin' : 'Pin to Top'}</button>
          <button class="btn small" data-act="longterm" data-id="${x.id}">${x.longterm ? 'Clear long-term' : 'Mark long-term'}</button>
          ${dueForm(x.id, x.due)}
          ${reminderForm(x.id)}
          <span class="spacer"></span>
          ${mailOn() ? `<button class="btn small" data-chat-canned="search_mail" data-id="${x.id}" title="A chat turn: finds the threads about this task and offers to link them">✉ Search related emails</button>
          <button class="btn small" data-chat-canned="draft_reply" data-id="${x.id}" title="A chat turn: saves a reply draft in Mailbird, never sends">✉ Draft reply</button>` : ''}
          <button class="btn small" data-chat-canned="related" data-id="${x.id}" title="A chat turn: finds the tasks this one belongs with and offers to link them">Ask Claude what else is related</button>
          <button class="btn small" data-ask-task="${x.id}">Ask Claude</button>
          <button class="btn small" data-more="${x.id}" aria-expanded="${ui.moreFor === x.id}">More</button>
        </div>${more ? moreHtml(x) : ''}`;
  }
  // ---------- the task page (stage 1, stage 4b) ----------
  // A task full-screen, rendered into #panel in place of the tab (render()): on a phone every task a tap
  // opens; on a desktop a task no list shows (closed, merged, missing), styled for a wide screen. An
  // open task is drawn like its row, staged changes included; whether it is closed is the server's word
  // (a staged Done keeps the actions, so the tray's Discard still makes sense). A task the open list
  // lacks comes from the opened task's payload (detailCache, asked by ensureDetails) and is read-only,
  // with Reopen. On a phone an open task's Done / Later / More are pinned at the bottom (.pagebar); its
  // other actions show under More.
  function taskPageHtml() {
    const id = ui.page, d = detailCache.get(id), from = history.state && history.state.from;
    const bar = `<div class="pbar"><button class="btn" id="page-back">‹ ${Number.isInteger(from) ? 'Task-' + from : 'Back'}</button><span class="code">Task-${id}</span></div>`;
    const srv = byId(id) || (d && !d.missing && !d.failed ? d : null);
    if (!srv) return `<div class="taskpage" data-id="${id}">${bar}<div class="empty">${!d ? `Loading Task-${id}…` : d.missing ? `Task-${id} does not exist.` : `Could not load Task-${id} (${esc(d.failed)}).`}</div></div>`;
    const closed = srv.status !== 'open', phone = PHONE();
    const x = closed ? srv : Object.assign({}, view(srv), { status: srv.status });
    const st = staged.get(id);
    const reopen = !closed ? '' : st && st.op === 'reopen' ? `<span class="chip staged">${esc(st.label)}</span>`
      : `<button class="btn" data-act="reopen" data-id="${id}">Reopen</button>`;
    const acts = closed || (phone && !ui.pageMore) ? '' : phone ? `<section class="dsec pmore"><h4>More</h4>${actionsHtml(x)}</section>` : actionsHtml(x);
    const later = ui.pageLater ? `<div class="page-later"><span>Comes back on:</span><button class="btn small" data-page-later="tomorrow">Tomorrow</button><button class="btn small" data-page-later="fri">Friday</button><button class="btn small" data-page-later="nextweek">Next week</button></div>` : '';
    const pagebar = closed || !phone ? '' : `<div class="pagebar">${later}<button data-page="done">Done</button><button data-page="later" aria-expanded="${ui.pageLater}">Later</button><button data-page="more" aria-expanded="${ui.pageMore}">More</button></div>`;
    return `<div class="taskpage${pagebar ? ' barred' : ''}" data-id="${id}">${bar}<h2>${esc(x.name)}</h2><div class="meta">${chipsHtml(x)}</div>
      ${closed ? `<div class="closedline"><p>This task is ${esc(srv.status)}.</p>${reopen}</div>` : ''}
      <div class="details"><div class="dgrid"><div class="dcol">${isOpenRow(id) ? editorHtml(x) : whereHtml(x)}<div data-dlinks="${id}">${detailAsyncHtml(id)}</div>${isOpenRow(id) ? linkByHandHtml(x) : ''}${factsHtml(x)}</div>
        <div class="dcol" data-dhist="${id}">${historyHtml(id)}</div></div>${acts}${taskPlaceHtml(id)}</div>${pagebar}</div>`;
  }
  // A Later on the page's pinned bar: the task leaves the lists until the day, like the Review card's Later.
  function pageLater(kind) {
    const id = ui.page, when = { tomorrow: [isoPlus(1), 'tomorrow'], fri: [nextFri(), 'Friday'], nextweek: [isoPlus(7 - ((TODAY().getDay() + 6) % 7)), 'next week'] }[kind];
    if (id === null || !when) return;
    ui.pageLater = false;
    stage(id, 'set', { snoozed_until: when[0] }, 'later · back ' + when[1]);
  }
  // On a phone the task page; elsewhere the task's row, expanded in place. `push` is false when the address
  // is already /t/<id>; `from` is the task page it was opened from (the trail).
  function openTask(id, push = true, from = null) {
    if (!PHONE()) { jumpTo(id, true); return; }
    openPage(id, push, from);
  }
  // The task page on either device.
  function openPage(id, push = true, from = null) {
    if (ui.page === null) ui.pageScroll = window.scrollY;      // the list's place; a page opened over a page keeps it
    ui.page = id; ui.pageMore = ui.pageLater = false;
    if (push) setAddress('/t/' + id, false, from);
    render();                  // ensureDetails asks for the task's payload when it has none
    window.scrollTo(0, 0);
  }
  function closeTask() { ui.page = null; render(); window.scrollTo(0, ui.pageScroll); }
  // The task page's pinned bar sits above the tray when the tray shows (like the bulk bar on a desktop).
  function placePageBar() {
    const pb = document.querySelector('#panel .pagebar'); if (!pb) return;
    const tray = document.getElementById('tray');
    pb.style.bottom = tray.classList.contains('on') ? tray.offsetHeight + 'px' : '';
  }
  const dueDays = x => x.due ? fmtDue(x.due).days : null;
  // Shown wherever a list of open tasks would be, when there are none: how to add a first task (stage 6).
  function noTasksHtml() {
    // A phone's sheet reads no shorthand: it has taps for the date, project, effort and a link, and its Enter only
    // puts the keyboard away, so the phone is pointed at the taps and Add task.
    const how = PHONE() ? 'Tap <b>+</b> at the bottom, type what needs doing, tap a day, a project or an effort if you like, then <b>Add task</b>.'
      : 'Press <kbd>n</kbd> or <b>New task</b> and type one line, for example <code>Email the lab the dataset fri ~S</code>, then Enter.';
    return `<div class="empty"><b>No open tasks yet.</b> ${how} Or write a sentence in the box above and Claude files it for you. Tasks also arrive from the inputs you switch on in Settings and from suggestions in Review. Coming from a Notion tasks database? See docs/SETUP.md §5.</div>`;
  }
  function todayGroups(lv, withLater) {
    const ready = (lv || effectiveLinkView()) === 'ready';
    const o = openOnes().filter(x => (withLater || !(x.snoozed_until && x.snoozed_until > data.today)) && (!ready || readyRow(x)));   // Later = hidden until its date
    const by = f => o.filter(f);
    const today = by(x => dueDays(x) === 0), tomorrow = by(x => dueDays(x) === 1);
    return { open: o, groups: arrangeToday([
      { key: 'today:overdue', title: 'Overdue · hard deadlines', cls: 'crit', rows: by(x => x.hard && dueDays(x) !== null && dueDays(x) < 0) },
      { key: 'today:today', title: 'Due today', cls: 'warn', rows: today },
      { key: 'today:tomorrow', title: 'Due tomorrow', rows: tomorrow },
      { key: 'today:starting', title: 'Starting soon · reminders set', rows: by(x => (x.reminders || []).some(r => daysTo(r.fire_at.slice(0, 10)) <= 1) && !today.includes(x) && !tomorrow.includes(x)) },
      { key: 'today:week', title: 'Rest of the week', rows: by(x => dueDays(x) > 1 && dueDays(x) <= 7) }], o) };
  }
  function renderToday() {
    const { groups } = todayGroups();      // snoozed rows are not in these groups but are open: Timeline lists them
    const lists = effectiveLinkView() === 'linked' ? linkedHtml() : groups.map(group).join('') +
      (!openOnes().length ? noTasksHtml() : groups.some(g => g.rows.length) ? '' : todayEmptyHtml());
    return summaryHtml() + composer() + todayPlaceHtml() + linkBarHtml() + lists + legendHtml(effectiveLinkView() === 'linked' ? null : groups) +
      `<div class="note">Soft dates that slipped, new items, and fading backlog are not here. They wait in <b>Review</b> so this screen stays short.</div>`;
  }
  // Today with no rows. Under Ready now the waiting tasks it hides may be due this week: say so, not "nothing is due".
  function todayEmptyHtml() {
    const hidden = effectiveLinkView() !== 'ready' ? 0 : new Set(todayGroups('all').groups.flatMap(g => g.rows.map(x => x.id))).size;
    // Rows Today would show but for Later: "nothing is due" would not be true
    const later = hidden ? 0 : new Set(todayGroups(null, true).groups.flatMap(g => g.rows.map(x => x.id))).size;
    if (!hidden && later) return `<div class="empty">Nothing is due today or this week except ${later === 1 ? 'one task' : later + ' tasks'} you set for later. Every open task is on Timeline.</div>`;
    if (!hidden) return '<div class="empty">Nothing is due today or this week. Every open task is on Timeline.</div>';
    return `<div class="empty">Nothing here is ready now: ${hidden === 1 ? 'the one task' : `all ${hidden} tasks`} due today or this week ${hidden === 1 ? 'waits' : 'wait'} on another task. <b>All</b> shows ${hidden === 1 ? 'it' : 'them'}.</div>`;
  }
  const RECENT_CAP = 30;
  function timelineGroups() {
    const ready = effectiveLinkView() === 'ready';
    const o = openOnes().filter(x => !ready || readyRow(x)), dd = dueDays;
    const sortDue = (a, b) => (a.due || '').localeCompare(b.due || '');
    return [
      { key: 'timeline:overdue', title: 'Overdue', cls: 'crit', rows: o.filter(x => dd(x) !== null && dd(x) < 0).sort(sortDue) },
      { key: 'timeline:today', title: 'Today', cls: 'warn', rows: o.filter(x => dd(x) === 0) },
      { key: 'timeline:week', title: 'This week', rows: o.filter(x => dd(x) > 0 && dd(x) <= 7).sort(sortDue) },
      { key: 'timeline:nextweek', title: 'Next week', rows: o.filter(x => dd(x) > 7 && dd(x) <= 14).sort(sortDue) },
      { key: 'timeline:later', title: 'Later', rows: o.filter(x => dd(x) > 14).sort(sortDue) },
      { key: 'timeline:longterm', title: 'Undated · long-term', rows: o.filter(x => dd(x) === null && x.longterm_effective) },
      { key: 'timeline:backlog', title: 'Undated · backlog', rows: o.filter(x => dd(x) === null && !x.longterm_effective).sort((a, b) => (b.score || 0) - (a.score || 0)) },
      // Newest first, by creation date: the place to look for what a recording or sweep just added.
      { key: 'timeline:recent', title: 'Recently added · newest first', rows: o.slice().sort((a, b) => (b.created_at || '').localeCompare(a.created_at || '')).slice(0, RECENT_CAP),
        tag: x => `<span class="chip" title="Created ${esc(x.created_at || '')}">added ${x.created_at ? new Date(x.created_at).toLocaleDateString([], { month: 'short', day: 'numeric' }) : '?'}</span>` }];
  }
  function renderTimeline() {
    if (!openOnes().length) return composer() + todayPlaceHtml() + noTasksHtml();
    const linked = effectiveLinkView() === 'linked', groups = linked ? null : timelineGroups();
    return composer() + todayPlaceHtml() + linkBarHtml() + (linked ? linkedHtml() : groups.map(group).join('')) + legendHtml(groups);
  }
  function projectGroups() {
    const m = new Map();
    openOnes().forEach(x => { const k = x.project || '—'; if (!m.has(k)) m.set(k, []); m.get(k).push(x); });
    return [...m.entries()].sort((a, b) => b[1].length - a[1].length);
  }
  function renderProjects() {
    if (!openOnes().length) return composer() + todayPlaceHtml() + noTasksHtml();
    return composer() + todayPlaceHtml() + projectGroups().map(([p, rows]) => {
      const fading = rows.filter(x => x.fade_at && daysTo(x.fade_at) <= 7).length;
      const due = rows.filter(x => x.due && fmtDue(x.due).days <= 7).length;
      const open = !folded('projects:' + p, true);         // a project starts folded; what was opened stays open next time
      return `<div class="proj-group ${open ? 'open' : ''}"><button data-proj="${esc(p)}" aria-expanded="${open}">${esc(p)} <span class="n">${rows.length} open · ${due} due this week</span>${fading ? `<span class="warnn">${fading} fading</span>` : ''}</button>${open ? `<div class="rows">${rows.sort((a, b) => (a.due || '9').localeCompare(b.due || '9')).map(x => row(x)).join('')}</div>` : ''}</div>`;
    }).join('');
  }
  // One page of the queue. Ids already staged in the tray are excluded server-side, so a page is
  // always `cap` fresh items and the counter counts this page only.
  // Each load stamps the page's rows as offered, so a render waiting for one never asks again
  // (`reviewLoading`); only the newest load's answer is kept, and a failed one waits for Try again.
  let reviewLoading = null, reviewSeq = 0, reviewFailed = '';
  async function loadReview() {
    const n = ++reviewSeq;
    reviewFailed = '';
    const exclude = [...staged.keys()].filter(k => typeof k === 'number').join(',');
    const p = api('/api/review' + (exclude ? '?exclude=' + exclude : ''));
    reviewLoading = p;
    let r;
    try { r = await p; }
    catch (e) { if (n === reviewSeq) { reviewFailed = e.detail || e.message || String(e); render(); } throw e; }
    finally { if (reviewLoading === p) reviewLoading = null; }
    if (n !== reviewSeq) return;      // a newer load was asked for (an Apply, Review another): its answer wins
    review = r;
    if (review.total === undefined) review.total = review.items.length;   // older server
    ui.reviewIdx = 0; ui.reviewPaused = false; ui.laterOpen = false; render();
  }
  // ---------- Review (stage 4b): link reasons in words, link suggestions ----------
  // A link reason's colour; any other reason keeps the rule it had (last call red, new / back teal, else amber).
  const REASON_CLS = { freed: 'ok', steps_done: 'ok', blocker_gone: 'warn', linked_changed: 'chg', link_suggested: 'new', ripple: 'chg' };
  // The card's reason line: the server's sentence for a link reason (every Task-N in it opens that task),
  // else the reason's label; then the other reasons and the planner's and the ripple's flags.
  // A card whose only proposals (ripples aside) are mail cards ("This mail is about Task-N") says From your
  // mail, not Planner suggests, and its planner flag adds nothing: the mail line under it says it.
  function reasonText(item) {
    const { reasons, primary, flags } = item, said = (item.texts || {})[primary];
    const props = (item.evidence || []).filter(e => e.change && e.by !== 'ripple');
    const mailOnly = props.length > 0 && props.every(e => e.change.op === 'follow_thread');
    const label = r => r === 'planner' && mailOnly ? 'From your mail' : review.labels[r];
    const head = said ? esc(said).replace(/Task-(\d+)/g, '<a href="/t/$1" data-open-task="$1">Task-$1</a>') : esc(label(primary));
    const flag = k => flags && flags[k] && !(k === 'planner' && mailOnly) ? ` · <b>${esc(flags[k])}</b>` : '';
    return head + (reasons.length > 1 ? ' · also ' + esc(reasons.slice(1).map(label).join(', ')) : '') + flag('planner') + flag('ripple');
  }
  // "This mail is about Task-N. Follow its thread?" (mail_triage's about card, a follow_thread proposal): who
  // wrote, the subject and why, then Follow its thread or Not about this. Drawn in place of the Accept line.
  function mailAboutHtml(e, tid) {
    const c = e.change || {}, m = c.mail || {}, f = c.fields || {}, subject = m.subject || f.subject || '';
    const from = [m.sender ? esc(m.sender) : '', subject ? `“${esc(subject)}”` : ''].filter(Boolean).join(' · ');
    const hint = from || e.reason ? ` <span class="hint">— ${from}${from && e.reason ? ', ' : ''}${e.reason ? esc(e.reason) : ''}</span>` : '';
    return `<div class="extra mailabout"><span class="chip">From your mail</span><span>This mail is about Task-${tid}. Follow its thread?${hint}</span>`
      + `<button class="btn small primary" data-follow="${e.id}">Follow its thread</button><button class="btn small" data-notabout="${e.id}">Not about this</button></div>`;
  }
  // Which of two tasks a merge keeps when the suggestion does not say: the Canvas row, else the older, else
  // the lower id (relations.default_survivor's rule, on what the page holds).
  function defaultKeep(a, b) {
    const A = nameOf(a) || {}, B = nameOf(b) || {}, ca = A.source === 'Canvas', cb = B.source === 'Canvas';
    if (ca !== cb) return ca ? a : b;
    const ka = A.created_at || '', kb = B.created_at || '';
    return ka !== kb ? (ka < kb ? a : b) : Math.min(a, b);
  }
  const suggestionKeep = (s, card) => s.keep != null ? s.keep : defaultKeep(card, s.other);
  // A suggested link on the card: the question, one tap to confirm it, or Not related. Gone once either is staged.
  function linkSuggestionsHtml(item) {
    const card = item.task.id;
    return (item.link_suggestions || []).filter(s => !suggestionStaged(s.evidence_id)).map(s => {
      const N = s.other, out = s.dir === 'out', T = `<span class="code">Task-${N}</span>${s.other_name ? ' ' + esc(s.other_name) : ''}`;
      const btn = (act, words, primary) => `<button class="btn small${primary ? ' primary' : ''}" data-lsug="${s.evidence_id}" data-lsug-act="${act}">${esc(words)}</button>`;
      let q, bs;
      if (s.kind === 'same_as') { q = `Same task as ${T}?`; bs = btn('merge', `Merge into ${suggestionKeep(s, card)}`, true) + btn('not', 'Not the same'); }
      else if (s.kind === 'part_of' || s.kind === 'waits_on') {
        q = s.kind === 'part_of' ? (out ? `Part of ${T}?` : `${T} is a step of this?`) : (out ? `Waits on ${T}?` : `${T} waits on this?`);
        bs = btn('link', 'Link', true) + btn('not', 'Not related');
      } else { q = `Related to ${T}?`; bs = btn('step', 'is a step of it') + btn('waits', 'waits on it') + btn('same', 'is the same task') + btn('not', 'Not related'); }
      return `<div class="extra lsug"><span class="chip">Suggested link</span><span>${q}</span>${bs}${s.why ? `<span class="hint">— ${esc(s.why)}</span>` : ''}</div>`;
    }).join('');
  }
  // What a suggestion's button stages: the link or merge, carrying the suggestion (the server judges it with the
  // change), or the suggestion dismissed.
  function linkSuggestionAct(card, eid, act) {
    const item = review && review.items.find(i => i.task.id === card);
    const s = item && (item.link_suggestions || []).find(x => x.evidence_id === eid); if (!s) return;
    const N = s.other;
    if (act === 'not') { stageJudge(eid, card, 'dismissed', `${s.kind === 'same_as' ? 'not the same as' : 'not related to'} Task-${N}`); return; }
    let c;
    if (act === 'link') c = { task_id: s.dir === 'out' ? card : N, op: 'link', fields: { kind: s.kind, other: s.dir === 'out' ? N : card } };
    else if (act === 'step') c = { task_id: card, op: 'link', fields: { kind: 'part_of', other: N } };
    else if (act === 'waits') c = { task_id: card, op: 'link', fields: { kind: 'waits_on', other: N } };
    else if (act === 'merge' || act === 'same') { const K = act === 'merge' ? suggestionKeep(s, card) : defaultKeep(card, N); c = { task_id: K === card ? N : card, op: 'merge', fields: { into: K } }; }
    else return;
    stageChange(c, '', { ev: eid });
    toast('Link staged · decide on the card, then Apply');
  }
  function renderReview() {
    if (!review) {
      if (reviewFailed) return `<div class="empty">Could not load Review: ${esc(reviewFailed)} <button class="btn small" id="review-retry">Try again</button></div>`;
      if (!reviewLoading) loadReview().catch(() => {});      // a failure is drawn above, from reviewFailed
      return '<div class="empty">Loading review queue…</div>';
    }
    const items = review.items.filter(i => !staged.has(i.task.id));
    const total = review.items.length;
    const remaining = ui.reviewPaused ? review.total : review.total - total;
    const sugg = (review.suggestions || []).filter(x => !staged.has('e:' + x.id) && !x.created);
    if (!items.length && sugg.length && !(ui.reviewPaused && remaining > 0)) return suggestionCard(sugg[0], sugg.length);
    if (ui.reviewPaused || !items.length) {
      const more = remaining > 0 ? `<p><button class="btn" id="review-more">Review another ${Math.min(review.cap, remaining)} of the remaining ${remaining}</button></p>` : '';
      const head = ui.reviewPaused ? (remaining ? 'Batch applied.' : 'Batch applied. Nothing left to review.') : total ? `${staged.size} decision${staged.size === 1 ? '' : 's'} staged.` : 'Nothing to review.';
      const body = ui.reviewPaused ? '' : total ? '<p>Apply them in the tray, or keep going.</p>' : '';
      const what = !ui.reviewPaused && !total ? '<p class="hint">Review gathers what needs a decision: soft dates that slipped, new tasks, backlog about to fade, and suggestions from the inputs you switch on.</p>' : '';
      return `<div class="review"><div class="finish"><h2>${head}</h2>${what}${body}${more}${review.last_review_at ? `<p class="hint">Last review: ${review.last_review_at}</p>` : ''}</div></div>`;
    }
    const { task: raw, primary } = items[0];
    const x = view(raw);   // staged edits (a due date set on this card) show at once, not only after Apply
    const d = fmtDue(x.due);
    const cls = REASON_CLS[primary] || (primary === 'last_call' ? 'crit' : primary === 'new' || primary === 'back' ? 'new' : 'warn');
    const idx = total - items.length;
    const note = ui.rvNote.id === x.id ? ui.rvNote.text : '';
    return `<div class="review">
      <div class="progress"><span>${idx + 1} of ${total}</span><div class="track"><div class="fill" style="width:${Math.round(100 * idx / total)}%"></div></div><span>~${Math.max(1, Math.ceil(items.length * 12 / 60))} min left</span></div>
      <div class="card" data-id="${x.id}">
        <div class="why ${cls}">${reasonText(items[0])}</div>
        <h2>${esc(x.name)}</h2>
        <div class="sub"><span class="code">Task-${x.id}</span><span class="chip proj">${esc(x.project || '—')}</span>${d ? `<span class="chip due ${d.days < 0 ? 'crit' : ''} ${x.hard ? 'hard' : ''}">${d.label}</span>` : '<span class="chip">no date</span>'}<span class="chip eff">${esc(x.effort || '?')}</span>${x.rel && x.rel.steps_total ? stepsChip(x.rel) : ''}<span class="chip">${x.mentions} mention${x.mentions === 1 ? '' : 's'}</span><span class="chip">score ${x.score}</span></div>
        ${sinceHtml(items[0])}
        <p>${esc(x.why_now || (x.score_reasons || []).join(' · '))}</p>${x.context ? `<p class="ctx ${ui.ctxOpen === x.id ? 'open' : ''}">${esc(x.context)}</p><button class="btn small more" data-ctx-more="${x.id}">${ui.ctxOpen === x.id ? 'Less' : 'More'}</button>` : ''}
        <textarea id="rv-note" rows="2" placeholder="${PHONE() ? 'Note · Done or Drop: the outcome · Keep or Later: added to the text' : `Optional note · Done or Drop: the outcome (up to ${OUTCOME_MAX} characters) · Keep or Later: added to the text`}">${esc(note)}</textarea>
        <div class="links">${x.source_link ? `<a href="${esc(x.source_link)}" target="_blank" rel="noopener">${x.source === 'Plaud' ? 'Plaud note' : 'Source'}</a>` : ''}</div>
        <div class="big"><button class="done" data-rv="done">Done <kbd>d</kbd></button><button data-rv="keep">Keep <kbd>k</kbd></button><button class="later" data-rv="later">Later <kbd>s</kbd></button><button class="drop" data-rv="drop">Drop <kbd>x</kbd></button></div>
        <div class="later-menu ${ui.laterOpen ? 'on' : ''}"><span class="hint">Comes back on:</span><button class="btn small" data-rv="tomorrow">Tomorrow</button><button class="btn small" data-rv="fri">Friday</button><button class="btn small" data-rv="nextweek">Next week</button><button class="btn small" data-rv="pickdate">📅 ${ui.datePick ? 'Hide date' : 'Pick a date…'}</button></div>
        ${ui.laterOpen && ui.datePick ? `<div class="remform datepick"><label for="rv-date" class="hint">Comes back on</label><input type="date" id="rv-date" min="${data.today}" value="${isoPlus(1)}" aria-label="Pick a date"><button class="btn small primary" id="rv-date-go">Use this date</button></div>` : ''}
        ${(items[0].evidence || []).filter(e => e.change && !staged.has('e:' + e.id)).map(e => e.by === 'ripple' ? rippleLineHtml(e, x.id) : e.change.op === 'follow_thread' ? (suggestionStaged(e.id) ? '' : mailAboutHtml(e, x.id)) : `<div class="extra"><span class="chip">Planner suggests</span><span>${esc(e.label)}${e.reason ? ` <span class="hint" style="margin:0">— ${esc(e.reason)}</span>` : ''}</span><button class="btn small primary" data-rv-accept="${e.id}">Accept</button></div>`).join('')}
        ${linkSuggestionsHtml(items[0])}
        <div class="extra"><button class="btn small" data-rv="top">★ Pin to Top</button><button class="btn small" data-rv="longterm">Mark long-term (never fades)</button>${dueForm(x.id, x.due)}${reminderForm(x.id)}</div>
        ${reviewPlaceHtml(x.id)}
      </div>
      <div class="note">Keep = no change; the item will not be asked about again until something changes. Later = hidden until the date you pick, then back in Review and Today. Decisions stage into the tray; Apply writes them all at once.</div></div>`;
  }
  // A planner create with no task behind it: one card, Create or Dismiss (the dismissal is a batch
  // change like any other, so the Log tab undoes it).
  function suggestionCard(sg, n) {
    const slack = sg.kind === 'slack_mention', mail = sg.mail, f = sg.fields || {}, rip = !slack && !mail && sg.by === 'ripple';
    const link = slack && f.source_link;
    const d = f.due ? fmtDue(String(f.due)) : null;
    const day = iso => new Date(iso.length > 10 && !/[zZ+]/.test(iso.slice(10)) ? iso + 'Z' : iso).toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
    // What Create would make, so the decision is about the task and not only its title.
    const would = [f.counterpart ? `<span class="chip" title="The task's counterpart. A task named “Reply to …” closes itself when you answer in this thread; for anything else the planner reads what you sent.">↩ ${esc(f.counterpart)}</span>` : '', f.project ? `<span class="chip proj">${esc(f.project)}</span>` : '', d ? `<span class="chip due ${f.hard ? 'hard' : ''}">${d.label}${f.hard ? ' · hard' : ''}</span>` : (mail ? '<span class="chip">no date</span>' : ''), f.effort ? `<span class="chip eff">${esc(f.effort)}</span>` : ''].join('');
    return `<div class="review">
      <div class="progress"><span>${n} suggestion${n === 1 ? '' : 's'} waiting</span><div class="track"></div></div>
      <div class="card" data-sugg="${sg.id}">
        <div class="why ${rip ? 'chg' : 'new'}">${slack ? 'Mentioned in Slack' : mail ? 'From your mail' : rip ? 'Ripple suggests' : 'Planner suggests'}</div>
        <h2>${esc(slack || mail ? f.name || sg.label : sg.label.replace(/^create:\s*/i, ''))}</h2>
        ${would ? `<div class="sub">${would}</div>` : ''}
        ${(sg.links || []).map(l => `<div class="sg-link">Will be ${l.kind === 'waits_on' ? 'waiting on' : 'a step of'} <a href="/t/${l.other}" data-open-task="${l.other}">Task-${l.other}</a>${l.other_name ? ' ' + esc(l.other_name) : ''}</div>`).join('')}
        ${mail && f.context ? `<p>${esc(f.context)}</p>` : ''}
        ${sg.reason ? `<p class="ctx">${esc(sg.reason)}</p>` : ''}
        ${mail ? `<p class="hint from">✉ ${esc(mail.sender || '')} · “${esc(mail.subject || '')}”${mail.at ? ' · ' + day(String(mail.at)) : ''}${mail.account ? ` · to ${esc(mail.account)}` : ''}</p>` : ''}
        <p class="hint">${sg.at && !mail ? (slack ? 'Posted ' : 'Suggested ') + new Date(sg.at).toLocaleDateString([], { month: 'short', day: 'numeric' }) : ''}${slack && f.project ? ` · files under ${esc(f.project)}` : ''}</p>
        ${link ? `<div class="links"><a href="${esc(link)}" target="_blank" rel="noopener">Open in Slack</a></div>` : ''}
        <div class="big"><button class="done" data-sugg-create="${sg.id}">Create task</button><button class="drop" data-sugg-dismiss="${sg.id}">Dismiss</button></div>
        ${mail ? `<div class="extra"><button class="btn small" data-sugg-open="${sg.id}">✉ Show the message in Mailbird</button></div>` : ''}
      </div>
      <div class="note">${mail ? 'Create makes an open task (source Mail) linked to this thread, so a reply or a message you send is noticed from then on. ' : `Create makes an open task (source ${slack ? 'Slack, the message linked' : rip ? 'Ripple: suggested after a linked task changed' : 'Planner'}) that shows as New in Review; `}Dismiss stages a change you can undo from the Log tab.</div></div>`;
  }
  function stageJudge(evidenceId, taskId, verdict, label) { stageExtra('e:' + evidenceId, taskId, 'judge_evidence', { evidence_id: evidenceId, verdict }, label); }

  // ---------- ripples (stage 5): Review ----------
  // "Since you last looked": what the linked tasks did, then what the ripple did to this task's text,
  // with See what changed (before / after, as History shows a rewrite) and Undo (design §12.4).
  // The before / after block (History's `diffHtml` draws it too), for any two texts.
  function beforeAfter(before, after) {
    const val = v => textOf(v) ? linkTasks(textOf(v)) : '<span class="code">none</span>';      // escaped, then Task-N linked
    return `<div class="hdiff"><span class="bl">before</span><span class="bv">${val(before)}</span><span class="al">after</span><span class="av">${val(after)}</span></div>`;
  }
  const SINCE_WHAT = { name: 'its name', text: 'its text', due: 'its due date', hard: 'its hard deadline', outcome: 'its outcome' };
  const sentence = s => /[.!?…]$/.test(s) ? s : s + '.';
  function sinceHtml(item) {
    const s = item && item.since; if (!s) return '';
    const lines = (s.by || []).map(b => {
      const T = tref(b.id), on = monDay(b.completed_at);
      if (b.did === 'done') return `${T} finished${on ? ' ' + on : ''}.${b.outcome ? ' Outcome: ' + linkTasks(sentence(String(b.outcome).trim())) : ''}`;
      if (b.did === 'dropped') return `${T} was dropped${on ? ' ' + on : ''}.`;
      if (b.did === 'merged') return `${T} had another task merged into it.`;
      const what = (b.what || []).map(w => SINCE_WHAT[w]).filter(Boolean);
      return what.length ? `${T} changed: ${what.join(', ')}.` : `${T} changed.`;
    }).map(l => `<div class="sl">${l}</div>`);
    let said = '';
    if (s.noted) said = 'A note was added to this task\'s text.';
    else if (s.cause_undone) said = 'This task\'s text was brought up to date. Its cause was undone since; the text was kept because it changed after.';
    else if (s.text) said = 'This task\'s text was brought up to date.';
    if (s.undone) said += (said ? ' ' : '') + 'Undone.';
    const open = ui.sinceOpen === s.batch_id && s.text;
    const acts = (s.text ? `<button class="btn small" data-since-see="${s.batch_id}" aria-expanded="${!!open}">See what changed</button>` : '')
      + (s.undone ? '' : `<button class="btn small" data-since-undo="${s.batch_id}">Undo</button>`);
    const diff = open ? beforeAfter(s.text.before, s.text.after) + (s.text.note ? `<div class="sl swhy">Why: ${linkTasks(s.text.note)}</div>` : '') : '';
    return `<div class="since"><h4>Since you last looked</h4>${lines.join('')}${said || acts ? `<div class="sl said">${said ? `<span>${esc(said)}</span>` : ''}${acts}</div>` : ''}${diff}</div>`;
  }
  // A ripple's proposal on the card: Accept and Dismiss. A proposed text shows its before and after first;
  // when the text has changed since the ripple read it, the line says so and offers Dismiss only (Accept
  // would be refused by the change's `expect`).
  const normText = v => String(v == null ? '' : v).split(/\s+/).filter(Boolean).join(' ');
  function rippleLineHtml(e, tid) {
    const c = e.change || {}, f = c.fields || {};
    const text = c.op === 'set' && 'context' in f;
    const before = text ? ((c.expect || {}).context != null ? c.expect.context : null) : null;
    const t = byId(tid);
    const stale = text && t && before != null && normText(before) !== normText(t.context);
    const diff = text ? beforeAfter(before, f.context) : '';
    const note = stale ? '<span class="hint stale">This task\'s text has changed since the ripple read it, so this rewrite would write over the newer text. Dismiss it, or edit the text yourself.</span>' : '';
    return `<div class="extra rsug"><span class="chip rip">Ripple suggests</span><span class="what">${linkTasks(e.label)}${e.reason ? ` <span class="hint" style="margin:0">— ${linkTasks(e.reason)}</span>` : ''}</span>${diff}${note}`
      + `${stale ? '' : `<button class="btn small primary" data-rv-accept="${e.id}">Accept</button>`}<button class="btn small" data-rv-dismiss="${e.id}">Dismiss</button></div>`;
  }
  // The one undo-and-tell step (the Review card's Undo; the landing toast's too): the toast says what was
  // left as it is, the Log and the list are read again.
  async function undoAndSay(id) {
    let res;
    try { res = await undoBatch(id); } catch (e) { toast('Undo failed: ' + (e.detail || e.message)); return null; }
    if (res.undone === false) toast(undoSaid(res), true, { batch: id });      // not undone: the Undo stays offered
    else toast(undoSaid(res));
    logData = null;
    await load();
    return res;
  }
  // ---------- Chat (SPEC §8.3) ----------
  // A tiny Markdown renderer for reply_md: escape first, then paragraphs, lists, code, bold, links.
  function md(src) {
    const t = esc(src || '');
    const blocks = t.split(/\n{2,}/).map(b => b.trim()).filter(Boolean);
    return blocks.map(b => {
      if (/^```/.test(b)) return `<pre>${b.replace(/^```\w*\n?/, '').replace(/```$/, '')}</pre>`;
      const lines = b.split('\n');
      if (lines.every(l => /^\s*[-*] /.test(l))) return `<ul>${lines.map(l => `<li>${inline(l.replace(/^\s*[-*] /, ''))}</li>`).join('')}</ul>`;
      if (lines.every(l => /^\s*\d+\. /.test(l))) return `<ol>${lines.map(l => `<li>${inline(l.replace(/^\s*\d+\. /, ''))}</li>`).join('')}</ol>`;
      if (/^#{1,4} /.test(b)) return `<h4>${inline(b.replace(/^#+ /, ''))}</h4>`;
      return `<p>${lines.map(inline).join('<br>')}</p>`;
    }).join('');
    function inline(l) {
      return l.replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>').replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<i>$2</i>')
        .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
    }
  }
  async function loadChatThreads() { const r = await api('/api/chat/threads'); chat.threads = r.threads; if (chat.current === null && chat.threads.length) chat.current = chat.threads[0].id; if (chat.current !== null) await loadChatThread(chat.current); else render(); }
  async function loadChatThread(id) { try { const r = await api(`/api/chat/threads/${id}`); chat.current = id; chat.messages = r.messages; chat.scroll = true; } catch (_) { chat.current = null; chat.messages = []; } render(); }
  const propKey = (p, mid, i) => stageKey(p, ['done', 'drop', 'pin_top', 'longterm'].includes(p.op) ? undefined : 'c' + mid + '-' + i);
  // An op the page cannot stage is left out (false), never turned into a `set`; the toast's count says so.
  // A new task is staged as `from_chat` (the server makes the stored proposal); a link to a new task of the
  // same reply names it by its tray ref, `m<message>-<index>`, and is labelled with its name.
  // The tray entry carries `prop` ("<message>-<index>", never sent), so Apply can remember it as applied.
  function stageProposal(p, mid, i) {
    if (!stageProposalAs(p, mid, i)) return false;
    const e = staged.get(placeKey(p, mid, i)); if (e) { e.prop = mid + '-' + i; persistStaged(); }
    return true;
  }
  // A mail thread a reply proposes to follow (`link_thread`) is a `follow_thread` change, written by Apply and taken
  // back by Undo like the rest; a thread the task follows already (`applied`) has nothing to stage.
  const threadWords = f => (f || {}).subject ? `follow the mail thread “${cut60(f.subject)}”` : 'follow a mail thread';
  function stageProposalAs(p, mid, i) {
    if (p.op === 'link_thread') {
      if (p.applied) return false;
      const f = p.fields || {};
      stageChange({ task_id: p.task_id, op: 'follow_thread', fields: { conversation_id: f.conversation_id, subject: f.subject || null, account: f.account || null, at: f.at || null } },
        threadWords(f).replace(/^follow/, 'follows'), {}, 'c' + mid + '-' + i);
      return true;
    }
    if (p.op === 'create') {
      stageExtra('new:c' + mid + '-' + i, null, 'create', p.fields || {}, 'new task: ' + cut60((p.fields || {}).name), { from_chat: { message_id: mid, index: i } });
      return true;
    }
    if (p.op === 'link' && typeof (p.fields || {}).other === 'string') {
      const f = p.fields, sib = siblingOf(proposalsOf(mid), f.other); if (!sib) return false;
      const other = 'm' + mid + '-' + sib.j;
      stageExtra(relKey(p.task_id, f.kind, other), p.task_id, 'link', Object.assign({}, f, { other }),
        `${f.kind === 'part_of' ? 'is now part of' : 'now waits on'} “${cut60(sib.name)}”`);
      return true;
    }
    const label = p.label || p.op;
    if (['done', 'drop'].includes(p.op)) stage(p.task_id, p.op, p.fields || {}, changeWords(p) || label);      // an outcome rides along
    else if (['pin_top', 'longterm'].includes(p.op)) stage(p.task_id, p.op, {}, label);
    else if (p.op === 'add_reminder') stageExtra(p.task_id + ':r', p.task_id, 'add_reminder', p.fields, label);
    else if (['link', 'unlink', 'merge', 'add_note'].includes(p.op)) stageChange(p, label, {}, 'c' + mid + '-' + i);
    else if (p.op === 'set') stageExtra(propKey(p, mid, i), p.task_id, 'set', p.fields, setWords(p.fields) || label, { expect: textExpect(p.task_id, p.fields) });
    else return false;
    return true;
  }
  function proposalsHtml(m) {
    if (!m.proposals || !m.proposals.length) return '';
    const items = m.proposals.map((p, i) => {
      const key = propKey(p, m.id, i); const isStaged = staged.has(key), done = propApplied(m.id, i);
      const ev = (p.evidence || []).map(e => `<span class="chip">${esc(e.label || e.kind)}</span>`).join('');
      const fields = p.fields && Object.keys(p.fields).length && p.op !== 'link_thread' ? `<span class="code">${esc(Object.entries(p.fields).filter(([k]) => k !== 'due_is_datetime').map(([k, v]) => `${k}: ${v === null ? 'cleared' : String(v).slice(0, 60)}`).join(' · '))}</span>` : '';
      let ctl;
      if (p.op === 'create') ctl = p.applied ? `<span class="chip staged">created Task-${p.task_id}</span>` : `<button class="btn small" data-chat-create="${m.id}" data-i="${i}">Create task</button>`;
      else if (p.op === 'link_thread' && p.applied) ctl = `<span class="chip staged">linked</span>`;
      else if (done) ctl = `<span class="chip staged">applied</span>`;
      else if (isStaged) ctl = `<span class="chip staged">staged</span>`;
      else ctl = `<label><input type="checkbox" data-chat-prop="${m.id}" data-i="${i}" checked> stage</label>`;
      return `<div class="prop">${ctl}<span class="plabel">${p.task_id != null ? `<span class="code">Task-${p.task_id}</span> ` : ''}${esc(p.op === 'link_thread' ? threadWords(p.fields) : p.label || p.op)}</span>${fields}${ev}</div>`;
    }).join('');
    const stageable = m.proposals.some((p, i) => p.op !== 'create' && !(p.op === 'link_thread' && p.applied) && !propApplied(m.id, i) && !(staged.has(propKey(p, m.id, i))));
    return `<div class="props"><div class="phead">Proposals${stageable ? `<button class="btn small primary" data-chat-stage="${m.id}">Stage selected</button>` : ''}</div>${items}</div>`;
  }
  function draftsHtml(m) {
    if (!m.drafts || !m.drafts.length) return '';
    return `<div class="drafts">${m.drafts.map((d, i) => `<div class="prop"><span class="chip rem">✉ draft</span><span class="plabel">${esc(d.subject || (d.kind === 'reply' ? 'reply' : 'new message'))}${d.to && d.to.length ? ' → ' + esc(d.to.join(', ')) : ''}${d.account ? ` <span class="code">${esc(d.account)}</span>` : ''}</span>${d.warn ? `<span class="chip fade" title="${esc(d.warn)}">⚠ ${esc(d.warn)}</span>` : ''}<button class="btn small" data-chat-draft="${m.id}" data-i="${i}">Open in Mailbird</button></div>`).join('')}
      <div class="hint">Drafts are saved in Mailbird and never sent by Factotum.</div></div>`;
  }
  function messageHtml(m) {
    if (m.role === 'user') return `<div class="msg user"><div class="bubble">${esc(m.text)}</div>${m.attached && m.attached.length ? `<div class="att">${m.attached.map(id => `<span class="chip">Task-${id}</span>`).join('')}</div>` : ''}</div>`;
    const cost = (m.cost_usd != null ? `<span class="chip">$${Number(m.cost_usd).toFixed(2)}</span>` : '') + (m.duration_ms ? `<span class="chip">${Math.round(m.duration_ms / 1000)} s</span>` : '');
    if (m.error) return `<div class="msg ai bad"><div class="bubble">The run failed: ${esc(m.error)}${m.text ? `<pre>${esc(m.text.slice(0, 600))}</pre>` : ''}</div>${cost}</div>`;
    const rej = m.rejected && m.rejected.length ? `<details class="rej"><summary>${m.rejected.length} proposal${m.rejected.length === 1 ? '' : 's'} rejected by Factotum</summary>${m.rejected.map(r => `<div>${esc(r)}</div>`).join('')}</details>` : '';
    return `<div class="msg ai"><div class="bubble">${md(m.text)}</div>${proposalsHtml(m)}${draftsHtml(m)}${rej}<div class="meta">${cost}</div></div>`;
  }
  function liveHtml() {
    if (!chat.live) return '';
    return `<div class="msg ai live"><div class="bubble"><span class="spin"></span> ${esc(chat.live.status || 'Working…')}${chat.live.texts.length ? `<div class="hint">${esc(chat.live.texts[chat.live.texts.length - 1].slice(0, 300))}</div>` : ''}</div></div>`;
  }
  function renderChat() {
    if (!chat.threads) { loadChatThreads(); return '<div class="empty">Loading chat…</div>'; }
    const when = t => new Date(t.last_at || t.created_at).toLocaleDateString([], { month: 'short', day: 'numeric' });
    const cur = chat.threads.find(t => t.id === chat.current);
    const list = `<nav class="threads ${ui.chatDrawer ? 'open' : ''}" id="chat-threads" aria-label="Threads">
        <div class="thead"><b>Threads</b><button class="btn small primary" id="chat-new">+ New</button><button class="btn small close" id="chat-threads-close" aria-label="Close">×</button></div>
        ${chat.threads.length ? chat.threads.map(t => `<button class="thread ${t.id === chat.current ? 'cur' : ''}" data-thread="${t.id}"><span class="t">${esc(t.title || 'untitled')}</span><span class="d">${threadTag(t)}${when(t)}</span></button>`).join('') : '<div class="hint">No threads yet.</div>'}
      </nav>`;
    const head = `<div class="chat-head"><button class="btn small" id="chat-threads-toggle" aria-label="Threads">☰ Threads</button><span class="cur">${cur ? esc(cur.title || 'untitled') : 'New thread'}</span>${cur ? `<span class="spacer"></span><button class="btn small" id="chat-delete" ${chat.busy ? 'disabled' : ''} title="Delete this thread and its messages. Nothing it applied is undone.">Delete thread</button>` : ''}</div>`;
    const eff = settings && settings.effective;
    const noMail = !!eff && !eff['sources.mail.enabled'];      // settings not loaded yet: today's text
    const STARTERS = ['What should I work on next, and why?', 'What is overdue, and what could I drop?'].concat(noMail ? [] : ['Is there anything in my mail from this week that I have not dealt with?']);
    const intro = noMail
      ? `Ask about your tasks${eff['sources.calendar.enabled'] ? ', your calendar' : ''}. A prompt can carry tasks (＋ Attach a task below, or select rows on another tab) or none at all. Everything Claude proposes is staged into the tray; nothing changes until you Apply.`
      : 'Ask about your tasks, your mail or your calendar. A prompt can carry tasks (＋ Attach a task below, or select rows on another tab) or none at all. Everything Claude proposes is staged into the tray; nothing changes until you Apply. Drafts it writes land in Mailbird and are never sent.';
    const body = chat.messages.length || chat.live ? chat.messages.map(messageHtml).join('') + liveHtml()
      : `<div class="note">${intro}</div>
         <div class="starters">${STARTERS.map((t, i) => `<button class="btn small" data-starter="${i}">${esc(t)}</button>`).join('')}</div>`;
    return `<div class="chat">${list}<div class="chat-main">${head}<div class="msgs" id="msgs">${body}</div>${composer()}</div><div class="backdrop ${ui.chatDrawer ? 'on' : ''}" id="chat-backdrop"></div></div>`;
  }
  // Where a thread was started (stage 6): a Review card, an opened task or the Today box; nothing for the Chat tab's own.
  function threadTag(t) {
    const tag = t.origin === 'review' && t.task_id != null ? 'Review · Task-' + t.task_id : t.origin === 'task' && t.task_id != null ? 'Task-' + t.task_id : t.origin === 'today' ? 'Today' : '';
    return tag ? `<span class="otag">${tag}</span> · ` : '';
  }
  const CANNED_SAID = { search_mail: 'Search related emails', draft_reply: 'Draft a reply', related: 'What else is related?', split: 'Split into steps' };      // a canned turn's own bubble
  // A turn's server-sent events, one `onEvent(ev)` each, until the stream ends.
  async function readTurn(resp, onEvent) {
    const reader = resp.body.getReader(); const dec = new TextDecoder(); let buf = '';
    for (;;) {
      const { value, done } = await reader.read(); if (done) break;
      buf += dec.decode(value, { stream: true });
      let i; while ((i = buf.indexOf('\n\n')) >= 0) {
        const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
        const line = chunk.split('\n').find(l => l.startsWith('data:')); if (!line) continue;
        onEvent(JSON.parse(line.slice(5)));
      }
    }
  }
  // One turn of the Chat tab: POST, then read the server-sent events until done.
  async function sendChat(text, canned, attached) {
    if (chat.busy) { toast('A chat turn is already running'); return; }
    chat.busy = true; chat.live = { status: 'Starting…', texts: [] }; chat.scroll = true;
    // the threads first: go() renders, and a Chat render with no threads loads them and the current thread's messages over this turn's
    if (!chat.threads) { try { const r = await api('/api/chat/threads'); chat.threads = r.threads; } catch (_) { chat.threads = []; } }
    ui.page = null; go('chat');
    const threadId = chat.current, before = chat.messages;
    chat.messages = [...chat.messages, { role: 'user', text: text || CANNED_SAID[canned] || canned, attached }];
    render();
    let result = null, error = null;
    try {
      const resp = await fetch('/api/chat/turn', { method: 'POST', headers: { 'Content-Type': 'application/json' }, redirect: 'manual', body: JSON.stringify({ thread_id: threadId, text, attached, canned }) });
      if (expired(resp)) { signedOut(); throw new Error('signed out'); }
      if (resp.status === 409) {      // this thread answers elsewhere (a place on another device): nothing was asked, the prompt goes back in the box
        chat.busy = false; chat.live = null; chat.messages = before;
        if (text) draft.cmd = draft.cmd.trim() ? text + '\n' + draft.cmd : text;
        render(); toast('Claude is still answering in this thread'); return;
      }
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      await readTurn(resp, ev => {
        if (ev.type === 'meta') chat.current = ev.thread_id;
        else if (ev.type === 'status') { chat.live.status = ev.text; render(); }
        else if (ev.type === 'text') { chat.live.texts.push(ev.text); render(); }
        else if (ev.type === 'done') result = ev.result;
        else if (ev.type === 'error') error = ev.error;
      });
    } catch (e) { error = e.message; }
    chat.busy = false; chat.live = null;
    try { const r = await api('/api/chat/threads'); chat.threads = r.threads; } catch (_) {}
    if (chat.current !== null) { try { const r = await api(`/api/chat/threads/${chat.current}`); chat.messages = r.messages; chat.scroll = true; } catch (_) {} }
    if (result) { toast(`Answered · $${Number(result.cost_usd || 0).toFixed(2)}` + (result.drafts && result.drafts.length ? ' · draft saved in Mailbird' : '')); if ((result.proposals || []).some(p => p.applied)) load(); }
    else toast('Chat failed: ' + error);
    render();
  }

  // ---------- in place (stage 6): the conversation ----------
  // One component (design §12.6) for a Review card, the Today box and an opened task: the place's thread, what
  // Claude is doing while it answers, every proposal with a tick box (this task, other tasks, new tasks, links),
  // drafts, rejections, and a reply box that continues the same thread. One turn runs at a time on the page.
  // A text box whose text lives in `draft`: render() and swapPlace() put its focus and caret back. The second
  // form is the editor's fields and a step's line (`ed-name-12`, `step-line-12`).
  function isDraftBox(id) { return DRAFT_BOXES.includes(id) || /^(ed-(name|context|who)|step-line)-\d+$/.test(id || ''); }
  let refocusing = false;      // focus is being put back after a redraw: the phone's keyboard handling leaves the scroll alone
  function heldDraftBox(root) {
    const a = document.activeElement;
    return a && root.contains(a) && isDraftBox(a.id) ? { id: a.id, a: a.selectionStart, b: a.selectionEnd } : null;
  }
  function restoreDraftBox(held) {
    if (!held) return;
    const el = document.getElementById(held.id); if (!el) return;
    refocusing = true;
    try { el.focus({ preventScroll: true }); try { el.setSelectionRange(held.a, held.b); } catch (_) {} } finally { refocusing = false; }
  }
  const places = new Map();      // key → place; the key is origin + ':' + task id ("review:30", "task:30", "today:")
  function placeOf(origin, taskId) {
    const key = origin + ':' + (taskId ?? '');
    if (!places.has(key)) places.set(key, { key, origin, task_id: taskId ?? null, thread_id: null, messages: [], named: {}, live: null, loaded: false, off: new Set(), more: false, fresh: false });
    return places.get(key);
  }
  // A second call while one loads gets the first's promise, so a sender can wait for the thread before its turn.
  function loadPlace(pl) {
    if (pl.loading) return pl.loading;
    return (pl.loading = (async () => {
      let r = null;
      try { r = await api('/api/chat/place?origin=' + pl.origin + (pl.task_id != null ? '&task_id=' + pl.task_id : '')); }
      catch (e) { if (!(e && (e.message === 'signed out' || e.network))) console.error('Loading a conversation failed:', e); }
      pl.loading = null; pl.loaded = true;      // a failure shows the place empty rather than asking again on every redraw
      if (r && !pl.live) {      // while a turn runs its own answer is appended when it ends
        if (pl.origin === 'today' && r.thread && r.thread.id === closedToday()) {      // closed here: not drawn, the next sentence starts anew
          Object.assign(pl, { thread_id: null, messages: [], named: {}, fresh: true });
        } else { pl.thread_id = r.thread ? r.thread.id : null; pl.messages = r.messages || []; pl.named = r.named || {}; }
      }
      if (typingNow()) swapPlace(pl); else render();
    })());
  }
  // The place's container alone, redrawn and wired, with the focus and caret of a box inside it kept.
  function swapPlace(pl) {
    const el = document.querySelector(`#panel [data-place="${pl.key}"]`); if (!el || !pl.opts) return;
    const held = heldDraftBox(el);
    el.outerHTML = placeHtml(pl, pl.opts);
    const fresh = document.querySelector(`#panel [data-place="${pl.key}"]`); if (!fresh) return;
    restoreDraftBox(held);
    fresh.querySelectorAll('textarea.grow').forEach(grow);
    wirePlaces(fresh);
  }
  // Every loaded place showing one of these messages reads it again (a proposal there was made into a task).
  function reloadPlacesWith(mids) { for (const pl of places.values()) if (pl.loaded && pl.messages.some(m => mids.includes(m.id))) loadPlace(pl); }
  // A turn from a place. The caller has emptied draft[draftKey]; a refused or unsent turn puts the text back
  // there, ahead of anything typed since. `attached`: the tasks it carries (a place about a task carries that
  // task; otherwise the selection, unless the caller says, as the phone sheet does with [], that none ride along).
  async function sendTurn(pl, text, canned, draftKey, attached) {
    const giveBack = () => { if (draftKey && text) draft[draftKey] = (draft[draftKey] || '').trim() ? text + '\n' + draft[draftKey] : text; };
    if (chat.busy) { giveBack(); render(); toast('Claude is still answering · your text is kept'); return; }
    chat.busy = true; chat.place = pl.key; pl.live = { status: 'Starting…', texts: [] };
    render();
    const body = { thread_id: pl.thread_id, origin: pl.origin, task_id: pl.task_id, fresh: pl.fresh, text, attached: pl.task_id ? [pl.task_id] : attached || attachedIds(), canned };
    let result = null, error = null, refused = null, started = false;
    try {
      const resp = await fetch('/api/chat/turn', { method: 'POST', headers: { 'Content-Type': 'application/json' }, redirect: 'manual', body: JSON.stringify(body) });
      noteBuild(resp);
      if (expired(resp)) { signedOut(); refused = ''; }      // the sign-in screen says it; its reload keeps nothing but the command box
      else if (resp.status === 409) refused = 'Claude is still answering in this conversation · your text is kept';
      else if (!resp.ok) refused = `Not sent (HTTP ${resp.status}) · your text is kept`;
      else await readTurn(resp, ev => {
        if (ev.type === 'meta') { started = true; if (ev.thread_id) pl.thread_id = ev.thread_id; }
        else if (ev.type === 'status') { pl.live.status = ev.text; render(); }
        else if (ev.type === 'text') { pl.live.texts.push(ev.text); render(); }
        else if (ev.type === 'done') result = ev.result;
        else if (ev.type === 'error') { error = ev.error || 'run failed'; if (ev.result && ev.result.thread_id) pl.thread_id = ev.result.thread_id; }
      });
    } catch (e) {
      if (started) error = e.message;      // the question reached the server: the thread holds it
      else refused = 'Not sent: the server did not answer · your text is kept';
    }
    if (refused === null && !result && !error) error = 'the answer ended before Claude finished';
    chat.busy = false; chat.place = null; pl.live = null;
    if (refused !== null) { giveBack(); render(); if (refused) toast(refused); return; }
    const said = text || CANNED_SAID[canned] || canned || '';
    if (result) {
      pl.messages.push({ role: 'user', text: said },
        { id: result.message_id, role: 'assistant', text: result.reply_md, proposals: result.proposals || [], drafts: result.drafts || [], rejected: result.rejected || [], read: result.read || [], cost_usd: result.cost_usd, duration_ms: result.duration_ms });
      pl.thread_id = result.thread_id; Object.assign(pl.named, result.named || {}); pl.fresh = false;
      toast(`Answered · $${Number(result.cost_usd || 0).toFixed(2)}`);
    } else {
      pl.messages.push({ role: 'user', text: said }, { role: 'assistant', error });
      toast('Claude failed: ' + error);
    }
    chat.threads = null;      // the Chat tab lists this thread too; read the list again there
    render();
  }
  // The words of a proposal: `main` (what it does), `sub` (where it hangs, and whether it is another task).
  const cut40 = s => { s = String(s == null ? '' : s); return s.length > 40 ? s.slice(0, 40) + '…' : s; };
  const PLACE_OPS = new Set(['create', 'set', 'done', 'drop', 'pin_top', 'longterm', 'add_reminder', 'add_note', 'link', 'unlink', 'merge', 'link_thread']);      // what stageProposal stages
  function proposalsOf(mid) {
    for (const pl of places.values()) { const m = pl.messages.find(x => x.id === mid); if (m) return m.proposals || []; }
    const m = chat.messages.find(x => x.id === mid); return (m && m.proposals) || [];
  }
  function siblingOf(props, ref) {
    const j = (props || []).findIndex(q => q && q.op === 'create' && (q.fields || {}).ref === ref);
    return j < 0 ? null : { j, name: String(props[j].fields.name || '') };
  }
  const dayWords = iso => { const d = new Date(String(iso).slice(0, 10) + 'T12:00:00'); return isNaN(d) ? String(iso) : d.toLocaleDateString([], { weekday: 'short' }) + ' ' + d.toLocaleDateString([], { month: 'short', day: 'numeric' }); };
  function setWords(f) {
    const out = [];
    for (const [k, v] of Object.entries(f || {})) {
      if (k === 'due') out.push(v ? 'due ' + dayWords(v) : 'due date removed');
      else if (k === 'name') out.push(`name “${cut60(v)}”`);
      else if (k === 'context') out.push('text rewritten');
      else if (k === 'project') out.push('project ' + (v || 'none'));
      else if (k === 'effort') out.push('effort ' + (v || 'none'));
      else if (k === 'hard') out.push(v ? 'hard deadline' : 'soft date');
      else if (k === 'waiting_on') out.push(v ? 'waiting on ' + v : 'no longer waiting');
      else if (k === 'counterpart') out.push(v ? 'for ' + v : 'for nobody named');
      else if (k === 'snoozed_until') out.push(v ? 'later, back ' + dayWords(v) : 'back now');
      else if (k !== 'due_is_datetime') out.push(`${k} ${v == null ? 'cleared' : cut60(v)}`);
    }
    return out.join(', ');
  }
  function proposalText(p, m, pl) {
    const f = p.fields || {}, props = (m && m.proposals) || [], T = 'Task-' + p.task_id, sub = [];
    let main;
    if (p.op === 'create') {
      main = 'New task: ' + (f.name || '');
      for (const l of f.links || []) {
        const word = l.kind === 'part_of' ? 'part of' : 'waits on';
        if (typeof l.other === 'string') { const s = siblingOf(props, l.other); sub.push(`${word} “${cut40(s ? s.name : l.other)}”`); }
        else sub.push(`${word} Task-${l.other}`);
      }
    } else if (p.op === 'link' && typeof f.other === 'string') {
      const s = siblingOf(props, f.other);
      main = `${T} ${f.kind === 'part_of' ? 'is now part of' : 'now waits on'}: ${s ? s.name : f.other}`;
    } else if (REL_OPS.includes(p.op)) main = `${T} ${changeWords(p)}`;
    else if (p.op === 'link_thread') main = `${T}: ${threadWords(f)}`;
    else if (p.op === 'set') main = `${T}: ${setWords(f)}`;
    else main = (p.task_id != null ? T + ': ' : '') + (changeWords(p) || p.label || p.op);
    // Only a place about a task has another task; a create's task_id, once made, is the new task itself.
    if (pl.task_id != null && p.task_id != null && p.task_id !== pl.task_id && p.op !== 'create') sub.push('a change to another task, staged from here');
    return { main, sub: sub.join(', ') };
  }
  // The tray key stageProposal puts a proposal under: a link to a new task by its tray ref, the rest as propKey.
  function placeKey(p, mid, i) {
    const f = p.fields || {};
    if (p.op === 'create') return 'new:c' + mid + '-' + i;
    if (p.op === 'link' && typeof f.other === 'string') { const s = siblingOf(proposalsOf(mid), f.other); return relKey(p.task_id, f.kind, 'm' + mid + '-' + (s ? s.j : f.other)); }
    return propKey(p, mid, i);
  }
  // Not staged, not made or applied yet: what the stage button stages (when ticked).
  const placeOpen = (p, mid, i) => PLACE_OPS.has(p.op) && !p.applied && !propApplied(mid, i) && !staged.has(placeKey(p, mid, i));
  const andList = a => a.length < 2 ? a.join('') : a.slice(0, -1).join(', ') + ' and ' + a[a.length - 1];
  function placeMsgHtml(pl, m, newest) {
    if (m.role === 'user') return `<div class="pmsg user">${esc(m.text)}</div>`;
    if (m.error) return `<div class="pmsg ai bad">The run failed: ${esc(m.error)}</div>`;
    const read = (m.read || []).length ? `<div class="pread">Read ${andList(m.read.map(tref))}.</div>` : '';
    const props = m.proposals || [];
    let list = '';
    if (props.length) {
      const row = (p, i) => {
        const k = `${pl.key}|${m.id}|${i}`, t = proposalText(p, m, pl);
        const out = placeTicked(pl, p, m.id, i) ? placeLeftOut(pl, props, p, m.id) : null;
        if (out !== null) t.sub = (t.sub ? t.sub + ', ' : '') + `left out while “${cut40(out)}” is not ticked`;
        const words = `<span class="pw"><span class="pm">${linkTasks(t.main)}</span>${t.sub ? ` <span class="ps">${linkTasks(t.sub)}</span>` : ''}</span>`;
        if (p.applied) return `<div class="pprop"><span></span>${words}<span class="chip staged">${p.op === 'create' && p.task_id ? 'created ' + tref(p.task_id) : 'linked'}</span></div>`;
        if (propApplied(m.id, i)) return `<div class="pprop"><span></span>${words}<span class="chip staged">applied</span></div>`;
        if (staged.has(placeKey(p, m.id, i))) return `<div class="pprop"><span></span>${words}<span class="chip staged">staged</span></div>`;
        if (!PLACE_OPS.has(p.op)) return `<div class="pprop"><span></span>${words}<span></span></div>`;
        return `<label class="pprop"><input type="checkbox" data-pprop="${esc(k)}" ${pl.off.has(k) ? '' : 'checked'} aria-label="Stage this">${words}${p.op === 'create' ? `<span class="pbtns"><button class="btn small" data-place-create="${esc(k)}">Create</button><button class="btn small" data-edit-first="${esc(k)}">Edit first</button></span>` : '<span></span>'}</label>`;
      };
      const short = PHONE() && !pl.more && props.length > 2;
      const rows = (short ? props.slice(0, 2) : props).map((p, i) => row(p, i)).join('')
        + (short ? `<div class="pmore">and ${props.length - 2} more · <button class="linkish" data-place-more="${esc(pl.key)}">Show</button></div>` : '');
      let foot = '';
      if (newest && props.some((p, i) => placeOpen(p, m.id, i))) {
        const n = props.filter((p, i) => placeTicked(pl, p, m.id, i) && placeLeftOut(pl, props, p, m.id) === null).length;
        const what = pl.opts.decision ? `Stage ${n > 1 ? 'all ' : ''}${n} with this decision` : `Stage ${n}`;
        foot = `<div class="pfoot"><button class="btn primary" data-place-stage="${esc(pl.key)}|${m.id}" ${n ? '' : 'disabled'}>${what}</button><span class="hint">One Apply, one Undo.</span></div>`;
      }
      list = `<div class="pprops"><div class="pphead"><span>Proposals</span><span class="hint">untick any you do not want</span></div>${rows}${foot}</div>`;
    }
    const drafts = (m.drafts || []).length ? `<div class="pdrafts">${m.drafts.map((d, i) => `<div class="pdraft"><span>✉ draft ${esc(d.subject || (d.kind === 'reply' ? 'reply' : 'new message'))}${d.to && d.to.length ? ' → ' + esc(d.to.join(', ')) : ''}</span><button class="btn small" data-pdraft="${esc(pl.key)}|${m.id}|${i}">Open in Mailbird</button></div>`).join('')}</div>` : '';
    const rej = (m.rejected || []).length ? `<details class="prej"><summary>${m.rejected.length} proposal${m.rejected.length === 1 ? '' : 's'} rejected by Factotum</summary>${m.rejected.map(r => `<div>${esc(r)}</div>`).join('')}</details>` : '';
    return `<div class="pmsg ai">${md(m.text)}</div>${read}${list}${drafts}${rej}`;
  }
  // opts: {decision, title, hint, box: {id, key, first, next}}: the stage button's words (with a Review decision or
  // alone), the head, and the reply box (its id, its `draft` key, its placeholder before and after the first answer).
  function placeHtml(pl, opts) {
    pl.opts = opts;
    const msgs = pl.messages, lastAi = msgs.map(m => m.role).lastIndexOf('assistant'), ai = lastAi >= 0 ? msgs[lastAi] : null;
    const body = msgs.map((m, i) => placeMsgHtml(pl, m, i === lastAi)).join('');
    const live = pl.live ? `<div class="plive"><span class="spin"></span> ${esc(pl.live.status || 'Working…')}${pl.live.texts.length ? `<div class="hint">${esc(String(pl.live.texts[pl.live.texts.length - 1]).slice(0, 300))}</div>` : ''}</div>` : '';
    const b = opts.box;
    const chips = ai ? `<div class="pchips">${ai.cost_usd != null ? `<span class="chip">$${Number(ai.cost_usd).toFixed(2)}</span>` : ''}${ai.duration_ms ? `<span class="chip">${Math.round(ai.duration_ms / 1000)} s</span>` : ''}${pl.thread_id ? `<a href="/chat" data-goto-chat="${pl.thread_id}">Also kept as a thread in Chat</a>` : ''}</div>` : '';
    return `<div class="place" data-place="${esc(pl.key)}">
      <div class="phead"><span>${esc(opts.title)}</span><span class="spacer"></span><span class="phint">${esc(opts.hint)}</span></div>
      ${body}${live}
      <div class="preply"><textarea id="${b.id}" class="grow" rows="1" placeholder="${esc(msgs.length ? b.next : b.first)}" aria-label="${esc(opts.title)}">${esc(draft[b.key] || '')}</textarea><button class="btn" data-place-ask="${esc(pl.key)}" ${chat.busy ? 'disabled' : ''}>Ask</button></div>
      ${chips}</div>`;
  }
  // Every ticked, unstaged, unmade proposal of that message, into the tray.
  // A link to a new task of the same reply is left out when that task will not be in the tray (unticked, not
  // made, not staged): Apply could only skip it. The toast says so in words.
  const placeTicked = (pl, p, mid, i) => placeOpen(p, mid, i) && !pl.off.has(`${pl.key}|${mid}|${i}`);
  // The name of the new task a ref link waits for when that task will not be in the tray; null otherwise.
  function placeLeftOut(pl, props, p, mid) {
    const f = p.fields || {};
    if (p.op !== 'link' || typeof f.other !== 'string') return null;
    const s = siblingOf(props, f.other), q = s && props[s.j];
    return q && !q.applied && !propApplied(mid, s.j) && !staged.has(placeKey(q, mid, s.j)) && !placeTicked(pl, q, mid, s.j) ? s.name : null;
  }
  function stagePlace(pl, mid, decision) {
    const props = proposalsOf(mid);
    let n = 0; const left = [];
    props.forEach((p, i) => {
      if (!placeTicked(pl, p, mid, i)) return;
      const out = placeLeftOut(pl, props, p, mid);
      if (out !== null) { left.push(out); return; }
      if (stageProposal(p, mid, i)) n++;
    });
    const why = left.length ? ` · ${left.length} left out: ${left.length === 1 ? 'it links to' : 'they link to'} ${andList(left.map(x => `“${cut40(x)}”`))}, not ticked` : '';
    toast((decision ? `${n} staged with this decision · decide on the card, then Apply` : `${n} staged · press Apply in the tray`) + why);
    render();
  }
  async function placeCreate(mid, i) {
    try {
      const r = await api('/api/chat/create', { method: 'POST', body: JSON.stringify({ message_id: mid, index: i }) });
      toast(`Created Task-${r.task_id}` + ((r.skipped || []).length ? ` · ${r.skipped[0].why}` : ''));
      await load();
      reloadPlacesWith([mid]);
    } catch (e) { toast('Create failed: ' + (e.detail || e.message)); }
  }
  function askPlace(pl) {
    const b = pl.opts.box, v = (draft[b.key] || '').trim(); if (!v) return;
    draft[b.key] = '';
    const el = document.getElementById(b.id); if (el) { el.value = ''; grow(el); }
    sendTurn(pl, v, null, b.key);
  }
  function wirePlaces(root) {      // root: the panel, or one place's container (swapPlace)
    (root.matches('[data-place]') ? [root] : root.querySelectorAll('[data-place]')).forEach(el => {
      const pl = places.get(el.dataset.place); if (!pl || !pl.opts) return;
      const box = el.querySelector('#' + pl.opts.box.id); if (box) typing(box, pl.opts.box.key, () => askPlace(pl));
      el.querySelectorAll('[data-place-ask]').forEach(b => b.onclick = () => askPlace(pl));
      el.querySelectorAll('[data-pprop]').forEach(ck => ck.onchange = () => { const k = ck.dataset.pprop; if (ck.checked) pl.off.delete(k); else pl.off.add(k); swapPlace(pl); });
      el.querySelectorAll('[data-place-more]').forEach(b => b.onclick = e => { e.preventDefault(); pl.more = true; swapPlace(pl); });
      el.querySelectorAll('[data-place-stage]').forEach(b => b.onclick = () => stagePlace(pl, +b.dataset.placeStage.split('|')[1], !!pl.opts.decision));
      el.querySelectorAll('[data-place-create]').forEach(b => b.onclick = e => { e.preventDefault(); const [, mid, i] = b.dataset.placeCreate.split('|'); placeCreate(+mid, +i); });
      el.querySelectorAll('[data-edit-first]').forEach(b => b.onclick = e => { e.preventDefault(); const [, mid, i] = b.dataset.editFirst.split('|'); editFirst(+mid, +i); });
      el.querySelectorAll('[data-pdraft]').forEach(b => b.onclick = async () => {
        const [, mid, i] = b.dataset.pdraft.split('|'); const m = pl.messages.find(x => x.id === +mid); const d = m && (m.drafts || [])[+i]; if (!d) return;
        try { await api('/api/chat/drafts/open', { method: 'POST', body: JSON.stringify({ draft: d }) }); toast('Shown in Mailbird'); }
        catch (e) { toast('Mailbird did not open it (' + e.message + '); the draft is in its Drafts folder'); }
      });
      el.querySelectorAll('[data-open-task]').forEach(a => a.onclick = e => { e.preventDefault(); goFromPage(+a.dataset.openTask); });
      el.querySelectorAll('[data-goto-chat]').forEach(a => a.onclick = e => { e.preventDefault(); chat.current = +a.dataset.gotoChat; chat.threads = null; go('chat'); });
    });
  }
  // Review: the card's conversation, at the bottom of the card (frame 4). A new card starts with an empty box.
  function reviewPlaceHtml(id) {
    if (draft.askFor !== id) { draft.ask = ''; draft.askFor = id; }
    const pl = placeOf('review', id);
    if (!pl.loaded) loadPlace(pl);
    return placeHtml(pl, { decision: true, title: 'Ask Claude about this task', hint: 'the conversation stays on this card',
      box: { id: 'rv-ask', key: 'ask', first: 'Ask Claude about this task, e.g. “which steps are left?”; what it proposes stages with your decision', next: 'Ask a follow-up, or say what to change' } });
  }
  // Today, Timeline and Projects: the Today box's conversation, under the box, once it has a message or a turn
  // runs. Loaded once per page load, so a conversation of the last 12 hours is there after a reload. Close
  // empties it here; the next sentence starts a new thread (`fresh`), the closed one stays in Chat. The closed
  // thread's id is remembered in this browser, so a reload does not draw it again; a newer thread is drawn.
  function closedToday() { try { return +localStorage.getItem('factotum.todayClosed') || null; } catch (_) { return null; } }
  function todayPlaceHtml() {
    const pl = placeOf('today', null);
    if (!pl.loaded) loadPlace(pl);
    if (!pl.messages.length && !pl.live) return '';
    return `<div class="ptoday">${placeHtml(pl, { decision: false, title: 'Claude', hint: 'also kept as a thread in Chat',
      box: { id: 'today-ask', key: 'askToday', first: 'Ask a follow-up', next: 'Ask a follow-up' } })}<button class="btn small pclose" data-place-close="today:" ${pl.live ? 'disabled' : ''}>Close</button></div>`;
  }
  // An opened task (its row, or the task page): the conversation about it, once Ask Claude or a canned button
  // was pressed there. One task at a time; it shares the thread of the task's Review card.
  const taskPlacesDrawn = new Set();      // a row Timeline lists twice (a section and Recently added) draws it once
  function taskPlaceHtml(id) {
    if (ui.askTask !== id || taskPlacesDrawn.has(id)) return '';
    taskPlacesDrawn.add(id);
    if (draft.askTaskFor !== id) { draft.askTask = ''; draft.askTaskFor = id; }
    const pl = placeOf('task', id);
    if (!pl.loaded) loadPlace(pl);
    return placeHtml(pl, { decision: false, title: 'Ask Claude about Task-' + id, hint: 'the conversation stays on this task',
      box: { id: 'task-ask', key: 'askTask', first: 'Ask about this task', next: 'Ask a follow-up' } });
  }
  // The phone's keyboard: --kb is its height (what the visual viewport lost at the bottom), so a sheet or the Review
  // card can pad for it; while a text box in the panel or the sheet has focus, nothing pinned covers it (kb-open).
  function fitKeyboard() {
    const vv = window.visualViewport; if (!vv) return;
    document.documentElement.style.setProperty('--kb', Math.max(0, Math.round(innerHeight - vv.height - vv.offsetTop)) + 'px');
    const a = document.activeElement;
    if (document.body.classList.contains('kb-open') && a && a.matches('input, textarea')) {      // a smaller window: the box stays in view
      const r = a.getBoundingClientRect(), big = document.body.classList.contains('kb-note') && document.querySelector('#panel .review .card .big');
      if (r.top < 0 || r.bottom > vv.height - (big ? big.offsetHeight : 0)) showBox(a, false);      // above Review's decisions too
    }
  }
  // A box into view: a box in the New task sheet scrolls the sheet alone (scrollIntoView would also scroll the list
  // under the fixed sheet, which the user then finds moved when it closes); any other box is centred on the page.
  function showBox(t, smooth) {
    const sh = t.closest('#sheet');
    if (!sh) { t.scrollIntoView({ block: 'center', behavior: smooth ? 'smooth' : 'auto' }); return; }
    const r = t.getBoundingClientRect(), s = sh.getBoundingClientRect(), vv = window.visualViewport, bottom = Math.min(s.bottom, vv ? vv.offsetTop + vv.height : innerHeight);
    if (r.top < s.top) sh.scrollTop -= s.top - r.top + 12; else if (r.bottom > bottom) sh.scrollTop += r.bottom - bottom + 12;
  }
  if (window.visualViewport) { visualViewport.addEventListener('resize', fitKeyboard); visualViewport.addEventListener('scroll', fitKeyboard); }
  const typesText = el => el && el.matches && el.matches('textarea, input:not([type=checkbox]):not([type=radio]):not([type=button]):not([type=submit])') && !!el.closest('#panel, #sheet');
  document.addEventListener('focusin', e => {
    if (!PHONE() || !typesText(e.target)) return;
    document.body.classList.add('kb-open');
    // Review's note box keeps the four decisions (the note is Done's outcome); they sit just above the keyboard. The
    // class outlives the box's blur by the same moment as kb-open, so the tap that blurred it lands on Done.
    document.body.classList.toggle('kb-note', e.target.id === 'rv-note');
    if (refocusing) return;      // a redraw put the focus back: the box is where it was
    const t = e.target;
    setTimeout(() => { if (document.activeElement === t) showBox(t, !window.matchMedia('(prefers-reduced-motion: reduce)').matches); }, 300);
  });
  // A moment later, and only when no text box took the focus: a tap that blurs a box near the bottom (a picker's hit
  // under a phone task page's More) must land on what was under the finger before the pinned bars come back (stage 6).
  document.addEventListener('focusout', () => setTimeout(() => { if (!typesText(document.activeElement)) document.body.classList.remove('kb-open', 'kb-note'); }, 300));
  let logData = null;
  async function loadLog() { logData = await api('/api/log?limit=' + logLimit); render(); }
  // The Log has four views of the same two lists (SPEC §9): Timeline merges changes and runs by time,
  // newest first; Changes and Runs show one list; All stacks both. The choice is remembered.
  const RUN_NAMES = { 'notion_mirror': 'mirror', 'sync-file': 'recording', 'chat-turn': 'chat', 'planner-morning': 'planner', 'today-summary': 'summary', 'today_summary': 'summary',
    'mail-triage': 'mail triage', 'mail_triage': 'mail triage', 'mail_sweep': 'mail sweep', 'canvas_sweep': 'canvas sweep', 'slack_sweep': 'slack sweep', 'calendar_sweep': 'calendar sweep',
    'morning_brief': 'brief', 'review_push': 'review nudge', 'reminder': 'reminder', 'refresh': 'refresh', 'expire': 'expire' };
  const ROUTINE = new Set(['notion_mirror', 'mail_sweep', 'canvas_sweep', 'slack_sweep', 'calendar_sweep', 'reminder', 'review_push', 'expire']);
  const SOURCE_NAMES = { ui: 'you', review: 'review', plaud: 'recording', planner: 'planner', canvas: 'canvas', mail: 'mail', connector: 'Claude', skill: 'Claude Code', chat: 'chat', automation: 'automation', ripple: 'ripple', ntfy: 'reminder', command: 'you', import: 'import' };
  const logWhen = iso => iso ? new Date(iso).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : '';
  const logVal = v => v === null || v === undefined ? '—' : typeof v === 'object' ? esc(JSON.stringify(v)) : esc(String(v));
  const routine = r => r.ok && !Number(r.cost_usd) && ROUTINE.has(r.kind);
  const shownRuns = () => logData.runs.filter(r => !(logQuiet && routine(r)));
  // A link change in words, without the task code every Log surface prints first; null for the `in` row
  // (the other task's row of the same relation says it).
  // A merged task reopened: its batch also removes the task's same_as, and the two read as one thing.
  const reopenedIn = (b, id) => !!b && b.changes.some(x => x.task_id === id && x.field === 'status' && x.new === 'open');
  const unmerge = c => !!c && c.field === 'link' && c.new == null && !!c.old && c.old.kind === 'same_as' && c.old.dir !== 'in';
  function logLinkWords(c, b) {
    if (!c || c.field !== 'link') return null;
    const v = c.new != null ? c.new : c.old;
    if (!v || v.dir === 'in') return null;
    if (unmerge(c) && reopenedIn(b, c.task_id)) return `reopened (was merged into Task-${v.other})`;
    return linkPredicate(v.kind, v.other, c.new == null);
  }
  const noteAfter = c => c.note ? ` <span class="cnote">— ${esc(c.note)}</span>` : '';
  function changeLines(b) {
    return b.changes.filter(c => c.field !== 'link' || logLinkWords(c)).map(c => c.field === 'link' ? `<div class="logc"><span class="code">Task-${c.task_id}</span> <span class="name">${esc(c.task_name || '')}</span> <span class="f">${esc(logLinkWords(c, b))}</span>${noteAfter(c)}</div>`
      : c.field === 'evidence' ? `<div class="logc">${c.task_id ? `<span class="code">Task-${c.task_id}</span> <span class="name">${esc(c.task_name || '')}</span>` : '<span class="code">suggestion</span>'} <span class="f">evidence #${c.new && c.new.evidence_id}</span> ${esc((c.new || {}).verdict || '')}</div>`
      : c.field === 'created' ? `<div class="logc"><span class="code">Task-${c.task_id}</span> <span class="name">${esc(c.task_name || '')}</span> <span class="f">created</span></div>`
      : c.field === 'thread' || (c.field === 'reminder' && (c.new || {}).snoozed) ? `<div class="logc"><span class="code">Task-${c.task_id}</span> <span class="name">${esc(c.task_name || '')}</span> <span class="f">${esc(fieldSaid(c))}</span>${noteAfter(c)}</div>`
      : `<div class="logc"><span class="code">Task-${c.task_id}</span> <span class="name">${esc(c.task_name || '')}</span> <span class="f">${esc(c.field)}</span> ${logVal(c.old)} → ${logVal(c.new)}${noteAfter(c)}</div>`).join('');
  }
  // One line for a batch: per task, the change that matters most (a status over a date over a note).
  function batchLine(b) {
    const NOISE = new Set(['completed_at', 'closed_by', 'due_is_datetime', 'last_reviewed_at', 'close_on_sent_to']);
    const RANK = { status: 5, created: 5, due: 4, snoozed_until: 3, reminder: 3, close_on_sent_since: 3, thread: 3 };
    const cause = b.cause || {}, from = Array.isArray(cause.from) && cause.from.length ? cause.from[0] : null;
    if (cause.kind === 'ripple' && from != null) {
      const k = new Set(b.changes.filter(c => c.task_id && !NOISE.has(c.field)).map(c => c.task_id)).size;
      return `Ripple from Task-${from}: ${k} task${k === 1 ? '' : 's'} updated` + (cause.cause_undone_at && !b.undone_at ? ' · cause undone, kept' : '');
    }
    const per = new Map();
    for (const c of b.changes) {
      if (NOISE.has(c.field)) continue;
      const v = c.new;
      if (c.field === 'link' && !logLinkWords(c)) continue;
      const label = c.field === 'link' ? logLinkWords(c, b) : c.field === 'status' ? (v === 'open' ? 'reopened' : String(v)) : c.field === 'created' ? 'created' : c.field === 'due' ? (v ? 'due ' + String(v).slice(0, 10) : 'due date removed')
        : c.field === 'snoozed_until' ? (v ? 'later, back ' + String(v).slice(0, 10) : 'back from later') : c.field === 'reminder' ? ((v || {}).snoozed ? fieldSaid(c) : 'reminder set') : c.field === 'top_rank' ? (v ? 'Top' : 'unpinned')
        : c.field === 'evidence' ? ((v || {}).verdict === 'dismissed' ? 'dismissed' : 'seen') : c.field === 'thread' ? fieldSaid(c) : c.field === 'close_on_sent_since' ? (v ? 'closes on send' : 'watch ended') : c.field.replace(/_/g, ' ');
      const key = c.task_id || 'e' + ((v || {}).evidence_id || '');
      // an added same_as (a merge) outranks everything; a removed one only as part of a reopen, which it then says
      const r = c.field === 'link' ? ((v || {}).kind === 'same_as' ? 6 : unmerge(c) && reopenedIn(b, c.task_id) ? 5.5 : 4) : RANK[c.field] || 1, cur = per.get(key);
      if (!cur || r > cur.r) per.set(key, { r, label, id: c.task_id });
    }
    const items = [...per.values()];
    if (!items.length) return 'no changes';
    const after = from != null ? `after Task-${from} · ` : '';
    if (items.length > 2 && items.every(i => i.label === items[0].label)) return `${after}${items.length} tasks · ${items[0].label}`;
    return after + items.slice(0, 3).map(i => (i.id ? `Task-${i.id} ` : 'suggestion ') + i.label).join(' · ') + (items.length > 3 ? ` · +${items.length - 3} more` : '');
  }
  // One changed field in words, lower case ("due Nov 3 → Nov 5", "note added: “…”"): the Log's digest and History.
  function fieldSaid(c) {
    const short = v => { const t = String(v); return t.length > 16 && /^\d{4}-\d\d-\d\dT/.test(t) ? t.slice(0, 16).replace('T', ' ') : t; };
    const cut = (t, n) => { t = String(t).replace(/\s+/g, ' ').trim(); return t.length > n ? t.slice(0, n - 1) + '…' : t; };
    const o = c.old, v = c.new;
    if (c.field === 'status') return v === 'open' ? 'reopened' : String(v);
    if (c.field === 'created') return 'created';
    if (c.field === 'due') return v ? (o ? `due ${short(o)} → ${short(v)}` : `due ${short(v)}`) : 'due date removed';
    if (c.field === 'snoozed_until') return v ? `later, back ${short(v)}` : 'back from later';
    // a notification's Snooze is a new reminder marked `snoozed` (reminders.act)
    if (c.field === 'reminder' && v && v.snoozed && v.fire_at) return 'reminder snoozed to ' + fmtWhen(v.fire_at);
    if (c.field === 'reminder') return 'reminder ' + (v && v.fire_at ? short(v.fire_at) : 'set') + (v && v.repeat ? ' · ' + v.repeat : '');
    if (c.field === 'top_rank') return v ? (o ? `Top ${o} → ${v}` : 'pinned to Top') : 'unpinned';
    if (c.field === 'longterm') return v ? 'long-term' : 'not long-term';
    if (c.field === 'hard') return v ? 'hard deadline' : 'soft date';
    if (c.field === 'waiting_on') return v ? 'waiting on ' + cut(v, 50) : 'no longer waiting';
    if (c.field === 'close_on_sent_since') return v ? 'closes when the mail is sent' : 'sent-mail watch ended';
    if (c.field === 'evidence') return 'evidence #' + ((v || {}).evidence_id || '?') + ' ' + ((v || {}).verdict || '');
    if (c.field === 'thread') return v && v.subject ? `follows the mail thread “${cut(v.subject, 80)}”` : v ? 'follows a mail thread' : 'no longer follows the mail thread';
    if (c.field === 'context' || c.field === 'why_now') { const added = o && v && String(v).startsWith(String(o)) ? String(v).slice(String(o).length) : v;
      return (c.field === 'context' ? (o && v && String(v).startsWith(String(o)) ? 'note added: ' : 'context: ') : 'why now: ') + '“' + cut(added == null ? '' : added, 180) + '”'; }
    return c.field.replace(/_/g, ' ') + ' ' + (v === null || v === undefined ? 'cleared' : '→ ' + cut(typeof v === 'object' ? JSON.stringify(v) : v, 60));
  }
  // An opened batch in the Timeline: one line per task saying what happened to it, in words. The
  // bookkeeping that rides along (completed_at, closed_by, last_reviewed_at) is left to the Changes view.
  function batchDigest(b) {
    const QUIET = new Set(['completed_at', 'closed_by', 'due_is_datetime', 'last_reviewed_at', 'close_on_sent_to']);
    const per = new Map();
    for (const c of b.changes) {
      const k = c.task_id || 'e'; if (!per.has(k)) per.set(k, { id: c.task_id, name: c.task_name, said: [], quiet: 0, skipped: 0 }); const g = per.get(k);
      if (c.field === 'link') { const w = logLinkWords(c, b); w ? g.said.push(w + (c.note ? ' — ' + c.note : '')) : g.skipped++; continue; }
      if (c.field === 'status' && c.new === 'open' && b.changes.some(x => x.task_id === c.task_id && unmerge(x))) continue;      // said by the link: "reopened (was merged into …)"
      QUIET.has(c.field) ? g.quiet++ : g.said.push(fieldSaid(c) + (c.note ? ' — ' + c.note : ''));
    }
    return [...per.values()].filter(g => g.said.length || g.quiet || !g.skipped).map(g => `<div class="logc dg">${g.id ? `<span class="code">Task-${g.id}</span> <span class="name">${esc(g.name || '')}</span>` : '<span class="code">suggestion</span>'}
      <span class="what">${g.said.length ? g.said.map(esc).join(' · ') : 'reviewed, kept as it is'}</span></div>`).join('') || '<div class="hint">No field changed.</div>';
  }
  function changesHtml() {
    return logData.batches.map(b => `<div class="logb ${b.undone_at ? 'undone' : ''}">
        <div class="loghead"><b>#${b.id}</b> <span class="chip">${esc(b.source)}</span> <span>${logWhen(b.applied_at || b.created_at)}</span> <span>${b.changes.length} change${b.changes.length === 1 ? '' : 's'}</span>
          ${b.undone_at ? `<span class="chip">undone ${logWhen(b.undone_at)}</span>` : b.changes.length ? `<span class="spacer"></span><button class="btn small" data-undo="${b.id}">Undo</button>` : ''}</div>
        ${changeLines(b)}</div>`).join('') || '<div class="empty">No batches yet.</div>';
  }
  function runsHtml() {
    const runs = shownRuns();
    return runs.length ? `<div class="scroll"><table class="runs"><tr><th>Run</th><th>Started</th><th>Took</th><th>Cost</th><th>Result</th></tr>${runs.map(r => `<tr class="${r.ok ? '' : 'bad'}"><td>${esc(r.kind)}</td><td>${logWhen(r.started_at)}</td><td>${r.duration_ms ? Math.round(r.duration_ms / 1000) + ' s' : ''}</td><td>${r.cost_usd != null ? '$' + Number(r.cost_usd).toFixed(2) : ''}</td><td>${esc(r.error || r.summary || (r.ok ? 'ok' : ''))}</td></tr>`).join('')}</table></div>`
      : `<div class="note">${logData.runs.length ? 'Only routine runs in this window; untick “Hide routine runs” to see them.' : 'Nothing has run yet. Reminders, sweeps, the brief and the Claude runs with their cost land here when they do something.'}</div>`;
  }
  // Changes and runs as one list, newest first, under a heading per day. A row is one line; a click
  // opens it (a batch's field changes, a run's whole result). Where one list was cut at the limit
  // the timeline stops at that list's oldest entry, so its tail never shows one kind only.
  function timelineHtml() {
    const at = x => Date.parse(x.applied_at || x.created_at || x.started_at) || 0;
    const cut = Math.max(logData.more && logData.more.batches && logData.batches.length ? Math.min(...logData.batches.map(at)) : 0, logData.more && logData.more.runs && logData.runs.length ? Math.min(...logData.runs.map(at)) : 0);
    const items = [...logData.batches.map(b => ({ t: at(b), b })), ...shownRuns().map(r => ({ t: at(r), r }))].filter(i => i.t >= cut).sort((x, y) => y.t - x.t);
    if (!items.length) return '<div class="empty">Nothing has happened yet.</div>';
    const dayKey = t => new Date(t).toDateString(), today = new Date().toDateString(), yest = new Date(Date.now() - 86400000).toDateString();
    const dayLabel = t => { const k = dayKey(t), d = new Date(t).toLocaleDateString([], { weekday: 'long', month: 'short', day: 'numeric' }); return k === today ? 'Today · ' + d : k === yest ? 'Yesterday · ' + d : d; };
    const time = t => new Date(t).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    let out = '', day = null;
    for (const i of items) {
      if (dayKey(i.t) !== day) { day = dayKey(i.t); const same = items.filter(x => dayKey(x.t) === day), cost = same.reduce((a, x) => a + (x.r ? Number(x.r.cost_usd) || 0 : 0), 0);
        out += `${out ? '</div>' : ''}<h4 class="tlday">${dayLabel(i.t)}<span class="n">${same.filter(x => x.b).length} change batch${same.filter(x => x.b).length === 1 ? '' : 'es'} · ${same.filter(x => x.r).length} run${same.filter(x => x.r).length === 1 ? '' : 's'}${cost ? ' · $' + cost.toFixed(2) : ''}</span></h4><div class="tlist">`; }
      if (i.b) {
        const b = i.b, key = 'b' + b.id, open = logOpen.has(key);
        out += `<div class="tl change ${b.undone_at ? 'undone' : ''} ${open ? 'open' : ''}" data-tl="${key}"><span class="t">${time(i.t)}</span><span class="chip k">${esc(SOURCE_NAMES[b.source] || b.source)}</span>
          <span class="m"><span class="code">#${b.id}</span> ${esc(batchLine(b))}</span><span class="x">${b.undone_at ? 'undone' : `${b.changes.length} change${b.changes.length === 1 ? '' : 's'}`}</span>
          ${b.undone_at || !b.changes.length ? '<span class="nobtn"></span>' : `<button class="btn small" data-undo="${b.id}">Undo</button>`}<span class="car" aria-hidden="true"></span>
          ${open ? `<div class="more">${batchDigest(b)}<div class="hint">Every field, old → new, is in the Changes view (#${b.id}).</div></div>` : ''}</div>`;
      } else {
        const r = i.r, key = 'r' + r.id, open = logOpen.has(key), text = r.error || r.summary || (r.ok ? 'ok' : 'failed');
        out += `<div class="tl run ${r.ok ? '' : 'bad'} ${Number(r.cost_usd) ? 'claude' : ''} ${open ? 'open' : ''}" data-tl="${key}"><span class="t">${time(i.t)}</span><span class="chip k">${esc(RUN_NAMES[r.kind] || r.kind)}</span>
          <span class="m">${esc(text)}</span><span class="x">${Number(r.cost_usd) ? '$' + Number(r.cost_usd).toFixed(2) + ' · ' : ''}${r.duration_ms >= 1000 ? Math.round(r.duration_ms / 1000) + ' s' : ''}</span><span class="nobtn"></span><span class="car" aria-hidden="true"></span></div>`;
      }
    }
    return out + '</div>';
  }
  function renderLog() {
    if (!logData) { loadLog(); return '<div class="empty">Loading log…</div>'; }
    const views = [['timeline', 'Timeline', ''], ['changes', 'Changes', logData.batches.length], ['runs', 'Runs', shownRuns().length], ['all', 'All', '']];
    const bar = `<div class="logbar"><div class="seg" role="tablist" aria-label="Log view">${views.map(([k, l, n]) => `<button role="tab" data-logview="${k}" aria-selected="${logView === k}">${l}${n !== '' ? `<span class="n">${n}</span>` : ''}</button>`).join('')}</div>
      ${logView === 'changes' ? '' : `<label class="quiet" title="The Notion mirror, the sweeps, reminders and the review nudge when they cost nothing and did not fail"><input type="checkbox" id="log-quiet" ${logQuiet ? 'checked' : ''}> Hide routine runs</label>`}</div>`;
    const more = logData.more && (logData.more.batches || logData.more.runs) && logLimit < 200 ? `<p class="logmore"><button class="btn small" id="log-more">Show older</button></p>` : '';
    const body = logView === 'timeline' ? timelineHtml() : logView === 'changes' ? changesHtml() : logView === 'runs' ? runsHtml()
      : `<h3>Changes</h3>${changesHtml()}<h3>Runs · automations and Claude</h3>${runsHtml()}`;
    return `<div class="log ${logView}">${bar}${body}${more}</div>`;
  }
  let rippleInfo = null;    // GET /api/ripple, fetched with the settings (Settings → Claude says when ripples wait)
  async function loadSettings() { [settings, connectors, rippleInfo] = await Promise.all([api('/api/settings'), api('/api/connectors').catch(() => null), api('/api/ripple').catch(() => null)]); render(); }
  // The ripple model's choices: the three names, plus the effective value when the config names another
  // (a full model id), first, so the page never says a model other than the one the runs use.
  function rippleModels(cur) {
    const names = ['sonnet', 'haiku', 'opus'];
    return cur && !names.includes(cur) ? [String(cur), ...names] : names;
  }
  function renderSettings() {
    if (!settings) { loadSettings(); return '<div class="empty">Loading settings…</div>'; }
    if (pushInfo === null) loadPush();
    const e = settings.effective;
    const f = (key, label, sub, control) => `<div class="field"><label for="s-${key}">${label}<span class="sub">${sub}</span></label><div>${control}</div></div>`;
    const num = (key, min, max) => `<input type="number" id="s-${key}" data-key="${key}" value="${e[key] == null ? '' : e[key]}" min="${min}" max="${max}" ${Number.isInteger(min) ? '' : 'step="0.05"'}>`;
    // A switch is its box and the word On inside one label: a tap on either toggles it (the row's title is a label
    // for the same box), 44 px tall on a phone; the change handler on the box still saves only that key.
    const bool = key => `<label class="sw"><input type="checkbox" id="s-${key}" data-key="${key}" ${e[key] ? 'checked' : ''}><span>On</span></label>`;   // effective has every key, its default filled in by the server
    const text = (key, w) => `<input type="text" id="s-${key}" data-key="${key}" value="${esc(e[key] || '')}" style="width:${w || '100%'}">`;
    const time = key => `<input type="time" id="s-${key}" data-key="${key}" value="${esc(e[key] || '')}">`;
    const accounts = (settings.accounts || []).map((a, i) => `<tr><td><input type="text" id="acc-${i}-address" data-acc="${i}" data-f="address" value="${esc(a.address)}"></td><td><input type="text" id="acc-${i}-display" data-acc="${i}" data-f="display" value="${esc(a.display || '')}" style="width:90px"></td><td><input type="text" id="acc-${i}-roles" data-acc="${i}" data-f="roles" value="${esc((a.roles || []).join(','))}" style="width:150px"></td><td><label class="sw" aria-label="Account on"><input type="checkbox" id="acc-${i}-enabled" data-acc="${i}" data-f="enabled" ${a.enabled ? 'checked' : ''}></label></td><td><button class="btn small" data-accdel="${i}">×</button></td></tr>`).join('');
    return `<div class="settings">
      ${settings.starter ? `<div class="note starter-note"><b>These are the starter settings</b> (<code>config/factotum.starter.yaml</code>): no name, no addresses, every input and output off, no brief, no planner, no automatic summary, no notifications. Ripples and filing are on: each is a small Claude run (a few cents) after something you did, and Undo takes back what it wrote; untick them under Claude to keep them off. To make Factotum your own, copy <code>config/factotum.example.yaml</code> to <code>config/factotum.yaml</code>, fill in your projects, your addresses and the integrations you use (docs/SETUP.md), and restart Factotum. A switch changed here is stored in the database and wins over the file.</div>` : `<div class="note">Overrides are stored in the database. Defaults and everything structural (projects, weights, sources, property names) live in <code>${esc(settings.config_file)}</code>; edit that file to adapt Factotum to another workflow.</div>`}
      <h3>Review and lifecycle</h3>
      ${f('notify.review_time', 'Review reminder', 'The evening review nudge, sent where reminders go. Quiet if you already reviewed today.', time('notify.review_time'))}
      ${f('lifecycle.fade_days', 'Backlog fade', 'Undated items fade after this many quiet days. A new mention resets the clock.', num('lifecycle.fade_days', 7, 120) + ' days')}
      ${f('lifecycle.fade_warn_days', 'Fade warning', 'Shown in Review this many days before fading.', num('lifecycle.fade_warn_days', 1, 14) + ' days')}
      ${f('lifecycle.last_call_days', 'Last call', 'Hard deadlines this far past due go to Review once, then lapse.', num('lifecycle.last_call_days', 1, 30) + ' days')}
      ${f('lifecycle.top_cap', 'Top cap', 'Soft limit on the Top rail.', num('lifecycle.top_cap', 3, 15))}
      ${f('lifecycle.review_cap', 'Review cap', 'Max items per Review session.', num('lifecycle.review_cap', 5, 50))}
      ${f('links.suggestions_per_review', 'Suggested links', 'At most this many suggested links a day, in Review and as dashed badges; the rest wait for another day. 0 shows none (what was suggested for a task you just added still shows as chips on it).', num('links.suggestions_per_review', 0, 20) + ' a day')}
      <h3>Notifications</h3>
      ${f('notify.provider', 'Send reminders to', 'Reminders and the evening review nudge go here. ntfy needs its app and a topic; The app needs notifications turned on below, on each device.', `<select id="s-notify.provider" data-key="notify.provider">${[['ntfy', 'ntfy'], ['app', 'The app'], ['both', 'Both']].map(([v, l]) => `<option value="${v}" ${e['notify.provider'] === v ? 'selected' : ''}>${l}</option>`).join('')}</select>`)}
      ${pushDeviceHtml()}
      <div class="push-devices"><div class="lbl">Devices with notifications on</div>${pushDevicesHtml()}</div>
      ${f('notify.app.badge', 'Count on the app icon', 'The number of Review items on the installed app’s icon. The open app keeps it current, and notifications that are sent anyway carry it, so it also moves while the app is closed; never a notification of its own. An iPhone shows it only after notifications were allowed for the app, Android and Firefox show none.', bool('notify.app.badge'))}
      ${f('notify.ntfy.enabled', 'ntfy push', 'Reminders and the review nudge through the ntfy app, when "Send reminders to" includes ntfy.', bool('notify.ntfy.enabled') + ' &nbsp; topic ' + text('notify.ntfy.topic', '220px'))}
      <h3>Inputs</h3>
      ${f('sources.plaud.enabled', 'Plaud recordings', 'IMAP trigger + plaud CLI (phase 3).', bool('sources.plaud.enabled'))}
      ${f('sources.canvas.enabled', 'Canvas', 'New items become hard-deadline tasks, submitted ones close.', bool('sources.canvas.enabled') + ' &nbsp; every ' + num('sources.canvas.sweep_minutes', 5, 1440) + ' min')}
      ${f('sources.mail.enabled', 'Email via Mailbird', 'Replies flip Waiting, sent mail closes reply tasks, invitations and confirmations feed the planner.', bool('sources.mail.enabled') + ' &nbsp; every ' + num('sources.mail.sweep_minutes', 5, 1440) + ' min')}
      ${f('sources.mail.suggest.enabled', 'Tasks from mail', 'New mail that is about none of your tasks is read by one light Claude run, and what it leaves you to do becomes a suggestion card in Review. Nothing becomes a task without your Create. The run happens only when there is new mail.', bool('sources.mail.suggest.enabled') + ' &nbsp; at most every ' + num('sources.mail.suggest.minutes', 5, 1440) + ' min')}
      ${f('sources.calendar.enabled', 'Google Calendar', 'Needs config/google-credentials.json (docs/SETUP.md §6).', bool('sources.calendar.enabled') + ' &nbsp; every ' + num('sources.calendar.sweep_minutes', 5, 1440) + ' min &nbsp; calendar id ' + text('sources.calendar.calendar_id', '160px'))}
      ${f('sources.slack.enabled', 'Slack', 'Chat can search and read the workspaces named in the config file (docs/SETUP.md §12); tokens in .env. The sweep turns @-mentions of you into Review items.', bool('sources.slack.enabled') + ' &nbsp; every ' + num('sources.slack.sweep_minutes', 5, 1440) + ' min')}
      ${f('sources.discord.enabled', 'Discord', 'Chat can list channels and read recent messages in the servers named in the config file (docs/SETUP.md §13).', bool('sources.discord.enabled'))}
      <h3>Email accounts</h3>
      <table><tr><td class="code">address</td><td class="code">label</td><td class="code">roles (monitor, plaud_trigger, brief_recipient)</td><td class="code">on</td><td></td></tr>${accounts}</table>
      <button class="btn small" id="acc-add">+ account</button>
      <h3>Outputs</h3>
      ${f('sinks.notion_notes.enabled', 'Notion notes', 'One page per recording in the Plaud notes database.', bool('sinks.notion_notes.enabled'))}
      ${f('sinks.notion_tasks_mirror.enabled', 'Notion tasks mirror', 'One-way copy of every task row; never read back. Property names in the config file.', bool('sinks.notion_tasks_mirror.enabled') + ' &nbsp; every ' + num('sinks.notion_tasks_mirror.sweep_minutes', 2, 1440) + ' min')}
      ${f('notify.morning_brief.enabled', 'Morning brief', 'Email at this time: Top, due today, reminders.', bool('notify.morning_brief.enabled') + ' ' + time('notify.morning_brief.time') + ' to ' + text('notify.morning_brief.to', '240px'))}
      <h3>Claude</h3>
      ${f('claude.planner.enabled', 'Morning planner', 'One Claude run before the brief: the verdict, why-now lines and proposals. Off = the plain brief.', bool('claude.planner.enabled'))}
      ${f('claude.summary.auto', 'Today summary', 'Rewritten by one light Claude run when a task it is about changes, something becomes urgent, or the day turns. Off: the Today tab still says what changed, and Refresh rewrites it.', bool('claude.summary.auto') + ' &nbsp; at most every ' + num('claude.summary.auto_minutes', 1, 240) + ' min')}
      ${f('claude.filing.enabled', 'Filing a task you add', 'When you add a task with only a name, one small Claude run with no tools suggests its project, effort, a likely link and a likely twin, as dashed chips you accept with one tap. It never changes what you typed. About 4 cents a new task (measured 2026-11-11); each run\'s cost is in the Log.', bool('claude.filing.enabled'))}
      ${f('claude.ripple.enabled', 'Ripples', 'When a linked task is closed or changes, one light Claude run brings the tasks linked to it up to date: it may rewrite their text (each rewrite is in History with Undo) and suggests anything else on Review. About 3 cents a run (measured 2026-11-11), at most ' + esc(e['claude.ripple.hourly_cap']) + ' runs an hour.', bool('claude.ripple.enabled') + ' &nbsp; after ' + num('claude.ripple.delay_seconds', 10, 3600) + ' s of quiet · at most ' + num('claude.ripple.hourly_cap', 1, 60) + ' runs an hour · $ ' + num('claude.budgets_usd.ripple', 0.05, 2) + ' a run · ' + `<select id="s-claude.ripple.model" data-key="claude.ripple.model" aria-label="Ripple model">${rippleModels(e['claude.ripple.model']).map(m => `<option value="${esc(m)}" ${(e['claude.ripple.model'] || 'sonnet') === m ? 'selected' : ''}>${esc(m)}</option>`).join('')}</select>`)}
      ${settings.claude_unavailable && (e['claude.ripple.enabled'] || e['claude.filing.enabled']) ? `<p class="hint warn">Ripples and filing wait: ${settings.claude_unavailable === 'missing' ? 'the claude program is not installed on this machine (or claude.binary does not point at it).' : 'Claude is not signed in on this machine (run claude once in a terminal and sign in).'}</p>` : ''}
      ${rippleInfo && rippleInfo.waiting_for_scheduler ? `<p class="hint warn">${rippleInfo.pending.length} ${rippleInfo.pending.length === 1 ? 'ripple' : 'ripples'} waiting: this instance runs no scheduler, so they run when the service does.</p>` : ''}
      ${f('claude.budgets_usd.chat_turn', 'Chat turn budget', 'Dollars a single chat turn may spend before Claude stops.', '$ ' + num('claude.budgets_usd.chat_turn', 0.05, 5) )}
      <h3>Connections</h3>
      ${f('server.oauth.enabled', 'claude.ai / Claude Desktop connector', `Factotum is the OAuth server behind <code>${esc(connectors ? connectors.url : '')}</code>: add that URL as a custom connector and approve it once on the consent page (docs/SETUP.md §16). What a connector may do with tasks is the next setting.`, bool('server.oauth.enabled'))}
      ${f('server.mcp_writes', 'What Claude may do with tasks', 'For the connector and the skills in Claude Code. Stage: changes wait in the tray for your Apply. Apply: they are written at once, each call one batch you can undo in the Log.', `<select id="s-server.mcp_writes" data-key="server.mcp_writes"><option value="stage" ${e['server.mcp_writes'] === 'apply' ? '' : 'selected'}>Stage for my Apply</option><option value="apply" ${e['server.mcp_writes'] === 'apply' ? 'selected' : ''}>Apply directly</option></select>`)}
      <p class="hint" style="margin:6px 0 10px"><button class="btn small" id="conn-arm">Allow a new connector (10 min)</button> ${connectors && connectors.armed_until ? `armed until ${esc(new Date(connectors.armed_until).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }))} — approve the connector in the app that asked` : 'Tap this right before approving a connector for the first time; the consent page refuses a connector nobody is expecting.'}</p>
      ${connectors && connectors.connectors.length ? `<table><tr><td class="code">connector</td><td class="code">registered</td><td class="code">last used</td><td class="code">live tokens</td><td></td></tr>${connectors.connectors.map(c => `<tr class="${c.revoked_at ? 'muted' : ''}"><td>${esc(c.name || c.client_id)}</td><td>${esc((c.created_at || '').slice(0, 10))}</td><td>${esc((c.last_used_at || '—').slice(0, 16).replace('T', ' '))}</td><td>${c.revoked_at ? 'revoked ' + esc(c.revoked_at.slice(0, 10)) : c.tokens}</td><td>${c.revoked_at ? '' : `<button class="btn small" data-conn-revoke="${esc(c.client_id)}">Revoke</button>`}</td></tr>`).join('')}</table>` : '<p class="hint">No connector has registered yet.</p>'}
      <p style="margin-top:16px" class="hint">Every control saves when it changes. <button class="btn small" id="settings-save">Save now</button> <span id="settings-state"></span></p>
    </div>`;
  }
  function renderTray() {
    const n = staged.size;
    document.getElementById('tray').classList.toggle('on', n > 0);
    document.body.classList.toggle('tray-on', n > 0);
    document.getElementById('tray-count').textContent = `${n} change${n === 1 ? '' : 's'}`;
    document.getElementById('tray-list').textContent = [...staged.values()].map(s => (s.task_id ? `Task-${s.task_id} ` : '') + s.label).join(' · ');
    const open = ui.trayOpen && n > 0;
    const tg = document.getElementById('tray-toggle'); tg.setAttribute('aria-expanded', open); tg.querySelector('.caret').textContent = open ? '▾' : '▴';
    const panel = document.getElementById('tray-panel'); panel.hidden = !open;
    document.body.classList.toggle('tray-open', open);
    if (!open) { panel.innerHTML = ''; return; }
    const picked = [...staged].filter(([k]) => !ui.trayOff.has(String(k))).length;
    panel.innerHTML = `<div class="rows">${[...staged].map(([k, s]) => `<label><input type="checkbox" data-tray-key="${esc(String(k))}" ${ui.trayOff.has(String(k)) ? '' : 'checked'}><span>${s.task_id ? `<span class="code">Task-${s.task_id}</span> ${esc((byId(s.task_id) || {}).name || '')}` : ''}</span><span class="what">${esc(s.label)}</span></label>`).join('')}</div>
      <div class="acts"><span class="hint">${picked} of ${n} selected</span><button class="btn small" id="tray-discard-sel" ${picked ? '' : 'disabled'}>Discard selected</button><button class="btn small primary" id="tray-apply-sel" ${picked ? '' : 'disabled'}>Apply selected</button></div>`;
    panel.querySelectorAll('[data-tray-key]').forEach(ck => ck.onchange = () => { if (ck.checked) ui.trayOff.delete(ck.dataset.trayKey); else ui.trayOff.add(ck.dataset.trayKey); renderTray(); });
    const sel = () => new Set([...staged.keys()].map(String).filter(k => !ui.trayOff.has(k)));
    document.getElementById('tray-apply-sel').onclick = () => applyAll(sel());
    document.getElementById('tray-discard-sel').onclick = () => discardStaged(sel());
  }

  // ---------- by hand (stage 6): finding any task ----------
  // GET /api/search over every status. Answers are cached per query until the next load(); a query is asked
  // 250 ms after the last keystroke. Find any task (`/`, or the button) lists the rows; the task picker (pickerHtml)
  // is the box other places use to choose a task, open ones first and closed ones when the place allows.
  const find = { cache: new Map(), found: null, timer: null, want: '' };
  function searchTasks(q) {
    q = q.trim();
    if (!find.cache.has(q)) {
      const p = api('/api/search?q=' + encodeURIComponent(q));
      p.catch(() => { if (find.cache.get(q) === p) find.cache.delete(q); });      // a failed answer is never kept: the same words ask again
      find.cache.set(q, p);
    }
    return find.cache.get(q);
  }
  // Ask for q after a pause; the answer is kept (for the picker, by query; for the finder, as `found`) and the page redrawn.
  function wantSearch(q, asFound) {
    q = q.trim(); clearTimeout(find.timer);
    if (!q) { if (asFound) find.found = null; return; }
    find.timer = setTimeout(async () => {
      let res;
      // The server's own words when it gave a reason (`detail`), not only the status
      try { res = await searchTasks(q); } catch (e) { res = { results: [], more: false, error: (e && (e.detail || (e.network ? 'the server did not answer' : e.message))) || 'no answer' }; }
      find.done = find.done || new Map(); find.done.set(q, res);
      if (asFound) { if (q !== draft.findQ.trim()) return; find.found = { q, res }; }
      render();
    }, 250);
  }
  const monthDay = iso => iso ? new Date(String(iso).length > 10 ? iso : iso + 'T12:00:00').toLocaleDateString([], { month: 'short', day: 'numeric' }) : '';
  function statusChip(r) {
    if (r.status === 'open') { const d = fmtDue(r.due); return `<span class="chip">open</span>${d ? `<span class="chip">${esc(d.label)}</span>` : ''}`; }
    if (r.merged_into) return `<span class="chip">merged into Task-${r.merged_into}</span>`;
    const when = monthDay(r.completed_at);
    return `<span class="chip">${esc(r.status)}${when ? ' ' + esc(when) : ''}</span>`;
  }
  function findHtml() {
    if (!ui.findOpen || !LIST_TABS.includes(ui.tab)) return '';
    const f = find.found, q = draft.findQ.trim();
    let rows = '';
    if (q && f && f.q === q) {
      rows = f.res.error ? `<div class="hint">Search failed: ${esc(f.res.error)}</div>` : f.res.results.map(r => {
        const closed = r.status !== 'open', st = staged.get(r.id);
        const reopen = !closed ? '' : st && st.op === 'reopen' ? '<span class="chip staged">reopen staged</span>' : `<button class="btn small" data-find-reopen="${r.id}">Reopen</button>`;
        return `<div class="hit${closed ? ' closed' : ''}"><span class="code">Task-${r.id}</span><span class="nm">${esc(r.name)}</span><span class="chips">${statusChip(r)}</span>
          <span class="acts"><button class="btn small" data-find-open="${r.id}">Open</button>${reopen}</span>
          ${closed && r.outcome ? `<div class="outcome">Outcome: ${esc(r.outcome)}</div>` : ''}</div>`;
      }).join('') || '<div class="hint">No task matches.</div>';
      if (f.res.more && !f.res.error) rows += '<div class="hint">More match; add a word.</div>';
    }
    return `<div class="find"><input id="find-q" placeholder="A task number, or words from its name, open or closed" autocomplete="off" aria-label="Find any task" value="${esc(draft.findQ)}">${rows ? `<div class="hits">${rows}</div>` : ''}</div>`;
  }
  function openFind() {
    ui.findOpen = true;
    if (ui.page !== null || !LIST_TABS.includes(ui.tab)) go(LIST_TABS.includes(ui.tab) ? ui.tab : 'today'); else render();
    const el = document.getElementById('find-q'); if (el) { el.focus({ preventScroll: true }); el.setSelectionRange(el.value.length, el.value.length); }
  }
  function closeFind() { ui.findOpen = false; render(); }
  function wireFind(panel) {
    const open = document.getElementById('find-open');
    if (open) open.onclick = () => { if (ui.findOpen) closeFind(); else openFind(); };
    const fq = document.getElementById('find-q');
    if (fq) {
      fq.oninput = () => { draft.findQ = fq.value; wantSearch(fq.value, true); };
      fq.addEventListener('keydown', e => { if (e.key === 'Escape') { e.preventDefault(); closeFind(); } e.stopPropagation(); });
      if (draft.findQ.trim() && !(find.found && find.found.q === draft.findQ.trim())) wantSearch(draft.findQ, true);      // a return to the list with text typed
    }
    panel.querySelectorAll('[data-find-open]').forEach(b => b.onclick = () => {
      const id = +b.dataset.findOpen, r = ((find.found || { res: { results: [] } }).res.results.find(x => x.id === id));
      if (r && r.status !== 'open') openPage(id); else goTask(id);
    });
    panel.querySelectorAll('[data-find-reopen]').forEach(b => b.onclick = () => act(+b.dataset.findReopen, 'reopen'));
    panel.querySelectorAll('input[id^="pick-"]').forEach(inp => {
      const slot = inp.id.slice(5);
      inp.oninput = e => { draft.picks[slot] = inp.value; if (pickOpts[slot] && pickOpts[slot].closed) wantSearch(inp.value, false); if (!e.isComposing) render(); };
      inp.addEventListener('keydown', e => {
        if (e.key === 'Enter' && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); const h = pickHits(slot)[0]; if (h) takePick(slot, h.id); }
        e.stopPropagation();
      });
    });
    panel.querySelectorAll('[data-picker]').forEach(b => b.onclick = () => takePick(b.dataset.picker, +b.dataset.hit));
  }
  // The shared task picker: a box and at most 8 hits, open tasks first and then (when `closed`) closed ones. The
  // caller sets pickers[slot] = (id) => …, run on a hit or on Enter (the first hit).
  const pickers = {}, pickOpts = {};
  function pickHits(slot) {
    const o = pickOpts[slot] || { exclude: new Set() }, q = (draft.picks[slot] || '').trim(); if (!q) return [];
    const open = matchOpen(q, o.exclude).map(x => ({ id: x.id, name: x.name, status: 'open', due: x.due }));
    const have = new Set(open.map(x => x.id)), out = open.slice();
    if (o.closed) {
      const res = find.done && find.done.get(q);
      if (res) for (const r of res.results) if (r.status !== 'open' && !o.exclude.has(r.id) && !have.has(r.id)) out.push(r);
    }
    return out.slice(0, 8);
  }
  function takePick(slot, id) { draft.picks[slot] = ''; if (pickers[slot]) pickers[slot](id); }
  function pickerHtml(slot, opts) {
    pickOpts[slot] = opts;
    const q = (draft.picks[slot] || '').trim();
    if (q && opts.closed && !(find.done && find.done.has(q))) wantSearch(q, false);
    const hits = pickHits(slot), dr = opts.closed && find.done && find.done.get(q), closedErr = dr && dr.error;
    return `<div class="picker"><input id="pick-${slot}" value="${esc(draft.picks[slot] || '')}" placeholder="${esc(opts.placeholder)}" aria-label="${esc(opts.placeholder)}" autocomplete="off">
      <div class="hits">${hits.map(x => `<button data-picker="${slot}" data-hit="${x.id}"><span class="code">Task-${x.id}</span> <span class="nm">${esc(x.name)}</span>${statusChip(x)}</button>`).join('') || (q ? `<div class="hint">${closedErr ? 'Search failed: ' + esc(closedErr) : 'No ' + (opts.closed ? '' : 'open ') + 'task matches.'}</div>` : '')}</div></div>`;
  }

  // ---------- by hand (stage 6): New task ----------
  // One line (design §12.7, frame 6): `n` or New task opens it on a desktop's list tabs; a preview says how the line
  // will be filed and what each word means; Enter makes the task at once (POST /api/add, one batch: Undo drops it).
  // The line is sent as typed (nothing stripped); the Today command box takes `add …` / `new …` / `+ …` too, and
  // only there is the verb stripped (`verb: true`). A key rides with every add: two Enters with nothing typed
  // between them carry the same key, so the server makes one task; an edited line gets a new key. Each box has
  // its own key (`line`, `cmd`, `sheet`, `step:<id>`), so a response lost in one box never makes an add from
  // another box the same task; Duplicate has no box and takes a fresh key every time.
  // The same pattern as the server's (commands.ADD_RE): a bare `add` / `new` / `+` is an add line with no name, so it
  // is refused as one ("a task needs a name") and never sent to /api/commands or to Claude as a sentence.
  const ADD_RE = /^\s*(?:(?:add|new)(?:\s+|$)|\+\s*)/i;
  const BARE_ADD_RE = /^\s*(?:add|new|\+)\s*$/i;
  function newKey() {
    const b = new Uint8Array(12); crypto.getRandomValues(b);
    return Array.from(b, x => (x % 36).toString(36)).join('');
  }
  const addKeys = {};      // box → the key its next add carries
  const addKeyOf = box => addKeys[box] || (addKeys[box] = newKey());
  const renewKey = box => { addKeys[box] = newKey(); };
  const LIST_TABS = ['today', 'timeline', 'projects'];
  function handBarHtml() {
    const phone = PHONE(), findBtn = `<button class="btn small" id="find-open" aria-expanded="${ui.findOpen}">Find any task${phone ? '' : ' <kbd>/</kbd>'}</button>`;
    if (phone) return `<div class="handbar">${findBtn}</div>`;      // a phone has the round button for New task, and no key to press
    return `<div class="handbar"><button class="btn small" id="new-open" aria-expanded="${ui.newOpen}">+ New task <kbd>n</kbd></button>${findBtn}</div>`;
  }
  function newLineHtml() {
    return `<div class="newline"><label class="lbl" for="new-line">New task</label>
      <input id="new-line" autocomplete="off" placeholder="Email the lab the dataset fri #project ~S after 120" value="${esc(draft.newLine)}">
      <div data-add-prev="new">${addPreviewHtml('new')}</div>
      <div class="nlact"><button class="btn primary" id="new-add">Add</button><button class="btn" id="new-add-open">Add and open</button><span class="hint">The task exists the moment you press Enter. Undo removes it.</span></div></div>`;
  }
  // The last preview each box was answered with: {text, res}; drawn only while its box holds exactly that text
  // (never an answer about other words: after Edit first, between a keystroke and its answer, after a failed preview).
  const addPrev = { new: null, cmd: null }, addPrevT = {};
  const addText = slot => slot === 'cmd' ? draft.cmd : draft.newLine;
  function addPreviewHtml(slot) {
    const cur = addText(slot) || '', p = addPrev[slot];
    if (!cur.trim() || (slot === 'cmd' && !ADD_RE.test(cur)) || !p || p.text !== cur || !p.res || !p.res.is_add) return '';
    const r = p.res, parts = r.parts || [], refused = r.refused || [];
    if (!r.name) return `<div class="addprev"><div class="lbl">Not added yet</div>
      <div class="refused">${refused.map(x => `<div>${x.words ? `<span class="code">${esc(x.words)}</span> ` : ''}${esc(x.why)}</div>`).join('')}</div></div>`;
    const chip = x => x.kind === 'waits_on' || x.kind === 'part_of' ? `<span class="lb ${x.kind === 'waits_on' ? 'wait' : 'ok'}">${linkTasks(x.label)}</span>`
      : `<span class="chip${x.kind === 'project' ? ' proj' : x.kind === 'due' || x.kind === 'effort' ? ' mono' : ''}">${esc(x.label)}</span>`;
    return `<div class="addprev"><div class="lbl">Will be added as</div>
      <div class="aprow"><div class="apname"><span class="newtag">new</span> <span class="nm">${esc(r.name)}</span></div><div class="apchips">${parts.map(chip).join('')}</div></div>
      ${parts.length ? `<div class="legend">${parts.map(x => `<span><span class="code">${esc(x.words)}</span> ${esc(x.legend)}</span>`).join('')}</div>` : ''}
      ${refused.length ? `<div class="refused">${refused.map(x => `<div>${x.words ? `<span class="code">${esc(x.words)}</span> ` : ''}${esc(x.why)}</div>`).join('')}</div>` : ''}</div>`;
  }
  // Only the preview's container is redrawn: the box being typed in is left alone.
  function swapAddPrev(slot) {
    const el = document.querySelector(`#panel [data-add-prev="${slot}"]`); if (!el) return;
    el.innerHTML = addPreviewHtml(slot); wireDetail(el);
  }
  // 250 ms after the last keystroke; an answer for text that has changed since is dropped. A preview about other
  // words goes at once (swapAddPrev draws nothing for them), and a failed one leaves none.
  function previewAdd(slot, text) {
    clearTimeout(addPrevT[slot]);
    swapAddPrev(slot);
    if (!text.trim() || (slot === 'cmd' && !ADD_RE.test(text))) return;
    addPrevT[slot] = setTimeout(async () => {
      let res;
      try { res = await api('/api/add/preview?text=' + encodeURIComponent(text) + (slot === 'cmd' ? '&verb=1' : '')); }
      catch (_) { if (text === addText(slot)) { addPrev[slot] = null; swapAddPrev(slot); } return; }
      if (text !== addText(slot)) return;
      addPrev[slot] = { text, res }; swapAddPrev(slot);
    }, 250);
  }
  // Makes the task now. input: {text, verb?} (a line) or {fields}; opts: {box, open, clear, said}: the box it came
  // from (its key; none: a fresh key), and what to do once made. Returns its id, or null when it was not made (the
  // caller's text stays) or another add is still in flight (the call is ignored).
  let adding = false;
  const addedBy = new Map();      // an add's batch → the task it made, so its Undo can let go of that task
  const cardMade = new Map();     // a suggestion card's Create batch → its task: its Undo lets go of the task and reads Review again
  async function addTask(input, opts = {}) {
    if (adding) return null;
    adding = true;
    try {
      let res;
      const key = opts.box ? addKeyOf(opts.box) : newKey();
      try { res = await api('/api/add', { method: 'POST', body: JSON.stringify(Object.assign({}, input, { key })) }); }
      catch (e) {
        if (!(e && e.message === 'signed out')) toast('Not added: ' + (e.detail || (e.network ? 'the server did not answer' : e.message)));
        return null;
      }
      const id = res.task_id;
      if (opts.clear) opts.clear();
      if (opts.box && addKeys[opts.box] === key) renewKey(opts.box);
      lastBatch = res.batch_id;
      if (res.batch_id != null && !res.already) addedBy.set(res.batch_id, id);
      try { await load(); } catch (_) {}      // the task is made either way; the lists catch up on the next load
      // Adding never changes the tab and never focuses or attaches the new task (the next sentence to Claude would
      // carry it); the box being typed in keeps the focus, so a second task can be typed at once. Where the row is
      // drawn in this view it flashes. The toast's Open goes to it; Add and open opens it.
      if (opts.open) openTask(id);
      else { const el = document.querySelector(`#panel .row[data-id="${id}"]`); if (el) { el.classList.remove('flash'); void el.offsetWidth; el.classList.add('flash'); } }
      toast((opts.said ? opts.said(id) : `Added Task-${id}`) + (res.already ? ' (already added)' : '')
        + ((res.refused || []).length ? ' · ' + res.refused.map(r => (r.words ? r.words + ': ' : '') + r.why).join(' · ') : '')
        + ((res.skipped || []).length ? ' · ' + res.skipped[0].why : ''), true, { buttons: [{ text: 'Open', run: () => goTask(id) }] });
      return id;
    } finally { adding = false; }
  }
  function focusNewLine() {
    const el = document.getElementById('new-line'); if (!el) return;
    el.focus({ preventScroll: true }); el.setSelectionRange(el.value.length, el.value.length);
  }
  // `n`, the New task button, Edit first: the line opens on this list tab (Today from any other tab or a task page).
  function openNewLine() {
    ui.newOpen = true;
    if (ui.page !== null || !LIST_TABS.includes(ui.tab)) go(LIST_TABS.includes(ui.tab) ? ui.tab : 'today'); else render();
    focusNewLine();
  }
  function addFromLine(open) {
    const sent = draft.newLine;
    if (!sent.trim()) { focusNewLine(); return; }
    // A line typed further while the add was in flight is kept (it got a new key, so Enter makes it too).
    addTask({ text: sent }, { box: 'line', open, clear: () => { if (draft.newLine === sent) draft.newLine = ''; } }).then(id => { if (id != null && !open) focusNewLine(); });
  }
  // A proposed new task as an add line: name, date (with ! when hard), #project, ~effort, under N, after N.
  const compact = s => String(s || '').replace(/[^A-Za-z0-9]/g, '');      // the server compares projects the same way
  function addLineOf(f) {
    const ln = [String(f.name || '').trim()], links = f.links || [];
    if (f.due) ln.push(String(f.due).slice(0, 10) + (f.hard ? '!' : ''));
    if (f.project && compact(f.project)) ln.push('#' + compact(f.project));
    if (f.effort) ln.push('~' + f.effort);
    const n = kind => { const l = links.find(l => l && l.kind === kind && Number.isInteger(l.other)); return l ? l.other : null; };
    if (n('part_of') != null) ln.push('under ' + n('part_of'));
    if (n('waits_on') != null) ln.push('after ' + n('waits_on'));
    return ln.filter(Boolean).join(' ');
  }
  // Edit first on a proposed new task: its line goes into the New task line, to be changed and added with Enter.
  // On a phone it fills the New task sheet instead (sheetOf).
  function editFirst(mid, i) {
    const p = proposalsOf(mid)[i]; if (!p || p.op !== 'create') return;
    const f = p.fields || {};
    if (PHONE()) {
      draft.newName = String(f.name || '').trim(); renewKey('sheet');
      openSheet(sheetOf(f));
      if ((f.links || []).some(l => l && typeof l.other === 'string')) toast('A link to another new task is left out');
      return;
    }
    draft.newLine = addLineOf(f); renewKey('line');
    openNewLine();
    previewAdd('new', draft.newLine);
    if ((f.links || []).some(l => l && typeof l.other === 'string')) toast('A link to another new task is left out');
  }
  function wireNew(panel) {
    const open = document.getElementById('new-open');
    if (open) open.onclick = () => { if (ui.newOpen) { ui.newOpen = false; render(); } else openNewLine(); };
    const nl = document.getElementById('new-line');
    if (nl) {
      nl.oninput = () => { draft.newLine = nl.value; previewAdd('new', nl.value); };
      nl.addEventListener('keydown', e => {
        if (e.key === 'Escape') { e.preventDefault(); ui.newOpen = false; render(); }      // the text stays in draft
        else if (e.key === 'Enter' && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); addFromLine(false); }
        e.stopPropagation();      // no key typed here acts on the list
      });
    }
    const add = document.getElementById('new-add'); if (add) add.onclick = () => addFromLine(false);
    const addOpen = document.getElementById('new-add-open'); if (addOpen) addOpen.onclick = () => addFromLine(true);
  }
  // A changed line is a new add: a new key (the New task line, the Today box holding an add line, the phone
  // sheet's name, a step's line). The Today box's preview follows what is typed there.
  document.addEventListener('input', e => {
    const id = (e.target && e.target.id) || '';
    const box = id === 'new-line' ? 'line' : id === 'new-name' ? 'sheet' : /^step-line-\d+$/.test(id) ? 'step:' + id.slice(10)
      : id === 'cmd' && ADD_RE.test(e.target.value) ? 'cmd' : null;
    if (box) renewKey(box);
    if (id === 'cmd' && ui.tab !== 'chat') previewAdd('cmd', e.target.value);
  });

  // ---------- by hand (stage 6): New task on a phone ----------
  // A round button at the bottom (when nothing else is there, bottomRule) opens a sheet (frame 11): the name, then
  // taps for the date, project, effort and one link of each kind, since typed shorthand is awkward on a phone
  // keyboard. Add task makes it at once like the desktop line (POST /api/add with {fields}, nothing stripped from
  // the name); Let Claude file it sends the name as a sentence to the Today box's conversation. `ui.sheet` holds the
  // taps, `draft.newName` the name, so a redraw or a Cancel loses nothing typed.
  const SHEET_EFFORTS = ['XS', 'S', 'M', 'L'];
  const sheetDays = () => [['Today', data.today], ['Tomorrow', isoPlus(1)], ['Fri', nextFri()]];
  const freshSheet = () => ({ due: null, pick: false, project: '', effort: '', links: [], hit: null, linking: false });
  // A proposed new task's fields as the sheet's taps: a day that is not one of the three shows under Pick a date;
  // a link to a task number becomes a chip (the last of each kind), a link to another new task's ref is left out.
  function sheetOf(f) {
    const due = f.due ? String(f.due).slice(0, 10) : null, links = [];
    for (const l of f.links || []) {
      if (!l || !Number.isInteger(l.other) || !['part_of', 'waits_on'].includes(l.kind)) continue;
      const j = links.findIndex(x => x.kind === l.kind); if (j >= 0) links.splice(j, 1);
      links.push({ kind: l.kind, other: l.other });
    }
    return Object.assign(freshSheet(), { due, pick: !!due && !sheetDays().some(([, d]) => d === due),
      project: (data.projects || []).includes(f.project) ? f.project : '', effort: f.effort || '', links });
  }
  // A task the link picker found: an open one from the lists, a closed one from the search answers.
  function sheetHitInfo(id) {
    const t = byId(id); if (t) return { id, name: t.name, status: 'open', due: t.due };
    for (const res of (find.done || new Map()).values()) { const r = (res.results || []).find(x => x.id === id); if (r) return r; }
    return { id, name: '', status: 'open' };
  }
  const SHEET_X = '<svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"><path d="M6 6l12 12"></path><path d="M18 6L6 18"></path></svg>';
  function sheetHtml() {
    const s = ui.sheet, tog = (attrs, on, label) => `<button type="button" class="stog" ${attrs} aria-pressed="${on}">${label}</button>`;
    const dayOn = s.pick ? null : (sheetDays().find(([, d]) => d === s.due) || [])[0];      // Tomorrow and Fri may be one day: the first lights
    const due = sheetDays().map(([l, d]) => tog(`data-sheet-due="${d}"`, l === dayOn, l)).join('') + tog('data-sheet-pick', s.pick, 'Pick a date');
    const chips = s.links.map(l => `<span class="lchip ${l.kind === 'waits_on' ? 'wait' : 'ok'}">${l.kind === 'waits_on' ? 'waits on' : 'part of'} <span class="code">Task-${l.other}</span><button type="button" data-sheet-unlink="${l.kind}" aria-label="Remove link to Task-${l.other}">${SHEET_X}</button></span>`).join('');
    let linking = '';
    if (s.linking) {
      linking = pickerHtml('sheet', { closed: true, exclude: new Set(s.links.map(l => l.other)), placeholder: 'A task number, or words from its name' });
      if (s.hit !== null) {
        const h = sheetHitInfo(s.hit), closed = h.status !== 'open';
        linking += `<div class="shit"><div class="shname"><span class="code">Task-${h.id}</span> <span class="nm">${esc(h.name)}</span>${statusChip(h)}</div>
          <div class="sbtns"><button type="button" class="stog" data-sheet-link="waits_on" ${closed ? `disabled title="Task-${h.id} is ${esc(h.status)}: nothing can wait on it"` : ''}>waits on it</button><button type="button" class="stog" data-sheet-link="part_of">is a step of it</button></div></div>`;
      }
    }
    return `<span class="handle" aria-hidden="true"></span>
      <div class="shead"><span class="stitle">New task</span><button type="button" class="slink-plain" data-sheet-cancel>Cancel</button></div>
      <label class="sfield">What needs doing<input id="new-name" type="text" autocomplete="off" enterkeyhint="done" value="${esc(draft.newName)}"></label>
      <div class="sfield"><span>Due</span><div class="stogs">${due}</div>${s.pick ? `<input type="date" id="sheet-date" value="${esc(s.due || '')}" aria-label="Due date">` : ''}</div>
      <div class="sgrid"><label class="sfield">Project<select id="sheet-project"><option value="">No project</option>${(data.projects || []).map(p => `<option value="${esc(p)}" ${p === s.project ? 'selected' : ''}>${esc(p)}</option>`).join('')}</select></label>
        <div class="sfield"><span>Effort</span><div class="seffort">${SHEET_EFFORTS.map(v => tog(`data-sheet-effort="${v}"`, s.effort === v, v)).join('')}</div></div></div>
      <div class="sfield"><span>Linked to</span><div class="slinks">${chips}<button type="button" class="stog slink" data-sheet-linking aria-expanded="${s.linking}">Link a task</button></div>${linking}</div>
      <button type="button" class="btn primary sadd" id="sheet-add">Add task</button>
      <div class="sclaude"><button type="button" class="btn" id="sheet-claude">Let Claude file it</button><div class="hint">Type or dictate a whole sentence. Claude names it, files it and links it.</div></div>`;
  }
  // Drawn by render() (and alone by the sheet's own taps), with the focus, caret and scroll inside it kept.
  function renderSheet() {
    const el = document.getElementById('sheet'), back = document.getElementById('sheet-back');
    document.body.classList.toggle('sheet-on', !!ui.sheet);
    el.hidden = back.hidden = !ui.sheet;
    if (!ui.sheet) { el.innerHTML = ''; return; }
    const held = heldDraftBox(el), top = el.scrollTop;
    el.innerHTML = sheetHtml();
    el.scrollTop = top;
    restoreDraftBox(held);
    wireSheet(el);
  }
  function focusSheetBox(id) {
    const el = document.getElementById(id); if (!el) return;
    el.focus({ preventScroll: true }); try { el.setSelectionRange(el.value.length, el.value.length); } catch (_) {}
  }
  // `n` on a phone, the round button, Edit first (with the proposal's taps): on this list tab, Today from any other.
  function openSheet(fill) {
    ui.sheet = fill || ui.sheet || freshSheet();
    if (ui.page !== null || !LIST_TABS.includes(ui.tab)) go(LIST_TABS.includes(ui.tab) ? ui.tab : 'today'); else render();
    focusSheetBox('new-name');
  }
  // New task on this device: the sheet on a phone, the line on a desktop. The `n` key and /new (stage 8) share it.
  function openNew() { if (PHONE()) openSheet(); else openNewLine(); }
  // The taps go; the name stays in draft.newName for the next time the sheet opens.
  function closeSheet() {
    const a = document.activeElement; if (a && a.closest && a.closest('#sheet')) a.blur();      // a removed box would leave kb-open on
    ui.sheet = null; draft.picks.sheet = '';
    render();
  }
  function sheetAdd() {
    const s = ui.sheet; if (!s) return;
    if (!draft.newName.trim()) { toast('Type what needs doing first'); focusSheetBox('new-name'); return; }
    const link = kind => (s.links.find(l => l.kind === kind) || {}).other ?? null;
    addTask({ fields: { name: draft.newName, due: s.due, project: s.project || null, effort: s.effort || null, after: link('waits_on'), under: link('part_of') } },
      { box: 'sheet', clear: () => { draft.newName = ''; } }).then(id => { if (id != null && ui.sheet) closeSheet(); });
  }
  // The name, as a sentence, to the Today box's conversation: the sheet closes and the tab becomes Today first.
  async function sheetToClaude() {
    const text = draft.newName.trim();
    if (!text) { toast('Type or dictate a sentence first'); focusSheetBox('new-name'); return; }
    if (chat.busy) { toast('Claude is still answering · your text is kept'); return; }
    draft.newName = '';
    closeSheet();
    if (ui.tab !== 'today' || ui.page !== null) go('today');
    const pl = placeOf('today', null);
    if (!pl.loaded) await loadPlace(pl);
    sendTurn(pl, text, null, 'newName', []);      // a sentence typed in the sheet is about nothing on the list
  }
  pickers.sheet = id => { if (ui.sheet) { ui.sheet.hit = id; renderSheet(); } };
  function wireSheet(el) {
    const s = ui.sheet, again = () => renderSheet();
    const nm = el.querySelector('#new-name');
    nm.oninput = () => { draft.newName = nm.value; };
    // On a touch keyboard Enter is its Done key, tapped to put the keyboard away and reach the taps under the name:
    // it only does that. Add task adds. A hardware keyboard's Enter adds, as the desktop line does.
    nm.addEventListener('keydown', e => {
      if (e.key === 'Escape') { e.preventDefault(); closeSheet(); }
      else if (e.key === 'Enter' && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); if (TOUCH()) nm.blur(); else sheetAdd(); }
      e.stopPropagation();      // no key typed here acts on the list
    });
    el.querySelector('[data-sheet-cancel]').onclick = closeSheet;
    el.querySelectorAll('[data-sheet-due]').forEach(b => b.onclick = () => { const d = b.dataset.sheetDue; s.due = b.getAttribute('aria-pressed') === 'true' ? null : d; s.pick = false; again(); });
    el.querySelector('[data-sheet-pick]').onclick = () => {
      s.pick = !s.pick; if (!s.pick) s.due = null;
      again();
      const di = document.getElementById('sheet-date'); if (di) { di.focus({ preventScroll: true }); try { di.showPicker(); } catch (_) {} }
    };
    const di = el.querySelector('#sheet-date'); if (di) di.onchange = () => { s.due = di.value || null; };
    const sp = el.querySelector('#sheet-project'); sp.onchange = () => { s.project = sp.value; };
    el.querySelectorAll('[data-sheet-effort]').forEach(b => b.onclick = () => { const v = b.dataset.sheetEffort; s.effort = s.effort === v ? '' : v; again(); });
    el.querySelectorAll('[data-sheet-unlink]').forEach(b => b.onclick = () => { s.links = s.links.filter(l => l.kind !== b.dataset.sheetUnlink); again(); });
    el.querySelector('[data-sheet-linking]').onclick = () => { s.linking = !s.linking; s.hit = null; draft.picks.sheet = ''; again(); if (s.linking) focusSheetBox('pick-sheet'); };
    el.querySelectorAll('[data-sheet-link]').forEach(b => b.onclick = () => {
      const kind = b.dataset.sheetLink;      // one link of each kind: a second replaces the first
      s.links = s.links.filter(l => l.kind !== kind).concat({ kind, other: s.hit });
      s.hit = null; s.linking = false; again();
    });
    const pk = el.querySelector('#pick-sheet');
    if (pk) {
      pk.oninput = e => { draft.picks.sheet = pk.value; wantSearch(pk.value, false); if (!e.isComposing) again(); };
      pk.addEventListener('keydown', e => {
        if (e.key === 'Enter' && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); const h = pickHits('sheet')[0]; if (h) takePick('sheet', h.id); }
        else if (e.key === 'Escape') { e.preventDefault(); s.linking = false; s.hit = null; draft.picks.sheet = ''; again(); }
        e.stopPropagation();
      });
    }
    el.querySelectorAll('[data-picker="sheet"]').forEach(b => b.onclick = () => takePick('sheet', +b.dataset.hit));
    el.querySelector('#sheet-add').onclick = sheetAdd;
    el.querySelector('#sheet-claude').onclick = sheetToClaude;
  }
  document.getElementById('fab-new').onclick = () => openSheet();
  document.getElementById('sheet-back').onclick = closeSheet;

  // ---------- by hand (stage 6): editing in place ----------
  // The opened task is its editor (design §12.8, frame 7): name, where it stands, due and hard deadline, project,
  // effort, and who it is for or who is awaited. Each field stages under its own key (`<id>:edit-<field>`; the date
  // under 4b's `<id>:due`), so edits sit beside the task's decision and each other and show on the row before Apply
  // (view); a text edit carries the text it started from (textExpect), so a newer text is never written over. Typed
  // text lives in draft.edit until it is staged, on Enter (Ctrl+Enter in the text) or on leaving the field; a value
  // equal to the server's removes its key instead. Staging redraws what is around the fields (editRefresh), never
  // the fields, so the focus moving on to the next field is kept. A closed task stays read-only (whereHtml).
  const EDIT_FIELDS = ['name', 'context', 'project', 'effort', 'hard', 'who'];
  const EFFORTS = ['XS', 'S', 'M', 'L'];
  function editKey(id, f) { return `${id}:edit-${f}`; }
  const isOpenRow = id => { const t = byId(id); return !!(t && t.status === 'open'); };      // the server's word, as on the task page
  const editorsDrawn = new Set();      // a row Timeline lists twice (a section and Recently added) gets one editor; the other copy reads
  // Every staged key that names the task: its decision, `<id>:…`, a link at either end, a verdict on its evidence.
  function keysFor(id) {
    const out = new Set(), sid = String(id);
    for (const [k, s] of staged) {
      const ks = String(k);
      if (ks === sid || ks.startsWith(sid + ':')) out.add(ks);
      else if (ks.startsWith('rel:')) { const p = ks.split(':'); if (p[1] === sid || p[3] === sid) out.add(ks); }
      else if (ks.startsWith('e:') && s && s.task_id === id) out.add(ks);
    }
    return out;
  }
  // The row's "N changes staged", when something besides its decision is staged (N counts them all).
  function stagedCountHtml(id) {
    const ks = keysFor(id);
    if (!ks.size || (ks.size === 1 && ks.has(String(id)))) return '';
    return `<span class="chip staged">${ks.size} change${ks.size === 1 ? '' : 's'} staged</span>`;
  }
  // Who: one field and a choice; "is for" writes `counterpart`, "waiting on" writes `waiting_on`.
  const WHO_COL = { for: 'counterpart', waiting: 'waiting_on' };
  const whoKindOf = x => x.waiting_on ? 'waiting' : 'for';
  const edKind = x => ui.edWho[x.id] || whoKindOf(x);
  const edDraft = id => (draft.edit[id] = draft.edit[id] || {});
  const edHas = (id, f) => !!(draft.edit[id] && f in draft.edit[id]);
  function edForget(id, f) { const d = draft.edit[id]; if (!d) return; delete d[f]; if (!Object.keys(d).length) delete draft.edit[id]; }
  // What a field shows: the text typed, else the value with the staged edits laid over (x is view(t)).
  function edValue(x, f) {
    if (edHas(x.id, f)) return draft.edit[x.id][f];
    const v = f === 'who' ? x[WHO_COL[edKind(x)]] : x[f];
    return v == null ? '' : String(v);
  }
  // The server's value of a typed field (who: of the column the choice names).
  function edServer(id, f) {
    const t = byId(id); if (!t) return '';
    const v = f === 'who' ? t[WHO_COL[edKind(view(t))]] : t[f];
    return v == null ? '' : String(v);
  }
  // Something typed in an editor field differs from the server's value (typingNow).
  function edTyped(id) {
    const d = draft.edit[id]; if (!d || !byId(id)) return false;
    return Object.keys(d).some(f => d[f] !== edServer(id, f));
  }
  function editorHtml(x) {
    const id = x.id, kind = edKind(x), due = x.due ? String(x.due).slice(0, 10) : '';
    editorsDrawn.add(id);
    const projects = (data.projects || []).slice(); if (x.project && !projects.includes(x.project)) projects.push(x.project);      // a project the config no longer lists still shows
    const field = (label, inner) => `<label class="edf"><span class="edl">${label}</span>${inner}</label>`;
    return `<section class="editor" data-editor="${id}">
      ${field('Name', `<input id="ed-name-${id}" class="edname" type="text" autocomplete="off" enterkeyhint="done" value="${esc(edValue(x, 'name'))}">`)}
      ${field('Where this stands', `<textarea id="ed-context-${id}" class="grow" rows="4">${esc(edValue(x, 'context'))}</textarea>`)}
      <div class="row2">
        ${field('Due', `<input id="ed-due-${id}" type="date" value="${esc(due)}">`)}
        ${field('Project', `<select id="ed-project-${id}"><option value="">No project</option>${projects.map(p => `<option value="${esc(p)}" ${p === x.project ? 'selected' : ''}>${esc(p)}</option>`).join('')}</select>`)}
      </div>
      <div class="row2">
        <div class="edf"><span class="edl" id="ed-effl-${id}">Effort</span><div class="edeff" role="group" aria-labelledby="ed-effl-${id}">${EFFORTS.map(v => `<button type="button" data-ed-effort="${id}" data-v="${v}" aria-pressed="${x.effort === v}">${v}</button>`).join('')}</div></div>
        <label class="edhard"${due ? '' : ' title="Set a due date first"'}><input type="checkbox" id="ed-hard-${id}" ${x.hard && due ? 'checked' : ''} ${due ? '' : 'disabled'}>Hard deadline: something breaks if this is late</label>
      </div>
      <div class="edf"><label class="edl" for="ed-who-${id}">Who is this for, or who are you waiting on</label>
        <div class="edwho"><select id="ed-whokind-${id}" aria-label="Is this task for them, or are you waiting on them">${[['for', 'is for'], ['waiting', 'waiting on']].map(([v, l]) => `<option value="${v}" ${v === kind ? 'selected' : ''}>${l}</option>`).join('')}</select><input id="ed-who-${id}" type="text" autocomplete="off" enterkeyhint="done" value="${esc(edValue(x, 'who'))}"></div></div>
      <div data-edstaged="${id}">${edStagedHtml(id)}</div>
    </section>`;
  }
  // The task's own staged line: everything staged that names it, with Discard and Apply for just those.
  function edWordsHtml(id) {
    const ks = keysFor(id), labels = [...staged].filter(([k]) => ks.has(String(k))).map(([, s]) => s.label);
    return `<b>Staged:</b> ${esc(labels.join(', '))}.`;
  }
  function edStagedHtml(id) {
    if (!keysFor(id).size) return '';
    return `<div class="edstaged"><span class="edwords">${edWordsHtml(id)}</span><button class="btn small" data-ed-discard="${id}">Discard</button><button class="btn small primary" data-ed-apply="${id}">Apply</button></div>`;
  }
  // null, '', 0 and false are all "nothing"; true is 1 (hard).
  const edNorm = v => v == null || v === '' || v === 0 || v === false ? null : v === true ? 1 : v;
  // One edit into the tray, or out of it when every value is the server's again; then the page around the fields.
  function stageEdit(id, f, fields, label, extra) {
    const t = byId(id); if (!t) return;
    const key = editKey(id, f);
    if (Object.entries(fields).every(([k, v]) => edNorm(t[k]) === edNorm(v))) { const e = staged.get(key); staged.delete(key); chipLeft(e, false); persistStaged(); }
    else stageExtra(key, id, 'set', fields, label, extra, true);
    editRefresh(id);
  }
  function edName(id, el) {
    if (!edHas(id, 'name')) return;      // nothing typed since it was last staged
    edForget(id, 'name');
    const v = el.value.trim(), t = byId(id);
    if (!v) { el.value = t ? view(t).name : ''; toast('A task needs a name · the name is as it was'); return; }
    if (el.value !== v) el.value = v;
    stageEdit(id, 'name', { name: v }, `name “${cut60(v)}”`, { expect: textExpect(id, { name: v }) });
  }
  function edContext(id, el) {
    if (!edHas(id, 'context')) return;
    edForget(id, 'context');
    const v = el.value.trim();
    if (el.value !== v) { el.value = v; grow(el); }
    stageEdit(id, 'context', { context: v }, 'text rewritten', { expect: textExpect(id, { context: v }) });
  }
  // A choice that differs from where the server keeps the name moves the text: the other column is emptied.
  function edWho(id, text, kind) {
    const t = byId(id); if (!t) return;
    edForget(id, 'who'); delete ui.edWho[id];
    const v = String(text || '').trim() || null, waiting = kind === 'waiting';
    const fields = { [WHO_COL[kind]]: v };
    if (kind !== whoKindOf(t)) fields[WHO_COL[waiting ? 'for' : 'waiting']] = null;
    stageEdit(id, 'who', fields, v ? (waiting ? 'waiting on ' + v : 'for ' + v) : (waiting ? 'no longer waiting' : 'for nobody named'));
  }
  // The date through 4b's stageDue; the server's date again takes the key out. No date means no hard deadline.
  function edDue(id, el) {
    const t = byId(id); if (!t) return;
    const v = el.value || null, server = t.due ? String(t.due).slice(0, 10) : null, key = id + ':due';
    const was = staged.has(key) ? (staged.get(key).fields || {}).due || null : server;
    if (v === was) return;      // the change and the blur that follows it
    if (v === server) { staged.delete(key); persistStaged(); } else stageDue(id, v, true);
    if (!v) { staged.delete(editKey(id, 'hard')); persistStaged(); }
    editRefresh(id);
  }
  // The parts of the page that show the task, redrawn in place: the tray, the Top list, the row's name and chips (or the
  // task page's), the editor's staged line (its buttons kept when it only changes words, so a press on one that set off
  // this redraw still lands), and the fields that follow others (hard needs a date; the effort taps).
  function editRefresh(id) {
    renderTray(); bottomRule(); placePageBar();
    renderRail(); wireRail();
    const t = byId(id); if (!t) return;
    const x = view(t);
    document.querySelectorAll(`#panel .row[data-id="${id}"] > .main .name, #panel .taskpage[data-id="${id}"] > h2`).forEach(el => { el.textContent = x.name; });
    afterPress('meta:' + id, () => {
      const t2 = byId(id); if (!t2) return;
      const x2 = view(t2);
      document.querySelectorAll(`#panel .row[data-id="${id}"] > .main > .meta, #panel .taskpage[data-id="${id}"] > .meta`).forEach(el => {
        patchHtml(el, chipsHtml(x2, el.dataset.extra || ''), r => { wireBadges(r); wireDueChips(r); wireFilingChips(r); });
      });
    });
    document.querySelectorAll(`#panel [data-edstaged="${id}"]`).forEach(el => {
      const words = el.querySelector('.edwords');
      if (words && keysFor(id).size) words.innerHTML = edWordsHtml(id);
      else { el.innerHTML = edStagedHtml(id); wireEdStaged(el); }
    });
    const due = !!x.due;
    document.querySelectorAll(`#panel #ed-hard-${id}`).forEach(h => { h.disabled = !due; h.checked = !!(x.hard && due); h.parentElement.title = due ? '' : 'Set a due date first'; });
    document.querySelectorAll(`#panel [data-ed-effort="${id}"]`).forEach(b => b.setAttribute('aria-pressed', String(b.dataset.v === x.effort)));
  }
  // A press in progress, from its pointerdown until just after its click: a redraw that would move what is under
  // the pointer waits until then (`afterPress`, one per key, the latest kept). The blur of a typed field stages it
  // in the middle of the press; the "1 change staged" chip that adds slid a desktop row's right-aligned chips away
  // from the pointer, and on a phone it wrapped to a new line and pushed the editor's buttons down, so the click
  // went elsewhere (2026-11-11). A finger's mouse events come after its pointerup, so a touch lasts until its click
  // (or half a second, for a tap that makes none).
  let pressing = false;
  const pressWaiting = new Map();
  function afterPress(key, f) { if (pressing) pressWaiting.set(key, f); else f(); }
  const pressOver = () => setTimeout(() => { pressing = false; const fs = [...pressWaiting.values()]; pressWaiting.clear(); fs.forEach(f => f()); }, 0);
  document.addEventListener('pointerdown', () => { pressing = true; }, true);
  document.addEventListener('pointerup', e => { if (e.pointerType === 'touch') setTimeout(pressOver, 500); else pressOver(); }, true);
  document.addEventListener('pointercancel', pressOver, true);
  document.addEventListener('click', pressOver, true);
  // Redraw a box from new markup in place: a child whose markup did not change stays the same node, the rest
  // is wired (`wire(root)`) before it goes in. Leaving a typed field stages it and calls editRefresh between the
  // mousedown and the click of the press that blurred it; a chip replaced there lost that click (a filing chip's
  // first tap after typing did nothing, 2026-11-11).
  function patchHtml(el, html, wire) {
    const tmp = document.createElement('div'); tmp.innerHTML = html; wire(tmp);
    const sig = n => n.nodeType === 1 ? n.outerHTML : n.nodeType + ':' + n.textContent;
    const old = [...el.childNodes], sigs = old.map(sig);
    let i = 0;
    for (const f of [...tmp.childNodes]) {
      const k = sigs.indexOf(sig(f), i);
      if (k < 0) { el.insertBefore(f, old[i] || null); continue; }
      for (; i < k; i++) old[i].remove();      // gone from the new markup
      i = k + 1;                                // old[k] stays as it is
    }
    for (; i < old.length; i++) old[i].remove();
  }
  function wireEdStaged(root) {
    root.querySelectorAll('[data-ed-discard]').forEach(b => b.onclick = () => { const id = +b.dataset.edDiscard; delete ui.edWho[id]; discardStaged(keysFor(id)); });
    root.querySelectorAll('[data-ed-apply]').forEach(b => b.onclick = () => applyAll(keysFor(+b.dataset.edApply)));
  }
  const enterKey = e => e.key === 'Enter' && !e.isComposing && e.keyCode !== 229;
  function wireEditors(root) {
    root.querySelectorAll('[data-editor]').forEach(ed => {
      const id = +ed.dataset.editor, $ = s => ed.querySelector(`#ed-${s}-${id}`);
      const nm = $('name'), cx = $('context'), who = $('who'), kind = $('whokind'), due = $('due'), proj = $('project'), hard = $('hard');
      // Text boxes: typed text into draft.edit; staged on Enter (Ctrl+Enter in the text) and on leaving the box. A box
      // redrawn with its typed text fires no `change` on leaving, so `blur` stages too (each is a no-op once staged).
      const box = (el, f, commit, enter) => {
        el.oninput = () => { edDraft(id)[f] = el.value; if (el.tagName === 'TEXTAREA') grow(el); };
        el.addEventListener('keydown', e => { if (e.key === 'Escape') return; if (enter(e)) { e.preventDefault(); commit(); } e.stopPropagation(); });
        el.onchange = el.onblur = commit;
      };
      box(nm, 'name', () => edName(id, nm), enterKey);
      box(cx, 'context', () => edContext(id, cx), e => enterKey(e) && (e.ctrlKey || e.metaKey));
      box(who, 'who', () => { if (edHas(id, 'who')) edWho(id, who.value, kind.value); }, enterKey);
      kind.onchange = () => { if (who.value.trim()) edWho(id, who.value, kind.value); else ui.edWho[id] = kind.value; };
      due.onchange = due.onblur = () => edDue(id, due);
      due.addEventListener('keydown', e => e.stopPropagation());
      proj.onchange = () => { const v = proj.value || null; stageEdit(id, 'project', { project: v }, v ? 'project ' + v : 'no project'); };
      hard.onchange = () => { const on = hard.checked ? 1 : 0; stageEdit(id, 'hard', { hard: on }, on ? 'hard deadline' : 'soft date'); };
      ed.querySelectorAll('[data-ed-effort]').forEach(b => b.onclick = () => stageEdit(id, 'effort', { effort: b.dataset.v }, 'effort ' + b.dataset.v));
      wireEdStaged(ed);
    });
  }

  // ---------- by hand (stage 6): linking and More ----------
  // Under Linked tasks an open task has Link a task (the shared picker, closed tasks included; then this task is a step
  // of it, waits on it, or is the same task) and, when it is not itself a step, Add a step (made at once, already linked,
  // in the goal's project). More, in the actions, has Split into steps (a `split` turn in the task's own conversation),
  // Duplicate (made at once and opened) and Merge into another. Two selected rows offer Link in the bulk bar, and a goal
  // in the Linked view whose text lists steps offers Make them tasks. Every link, removal and merge stages in the tray.
  const STEP_LINE = /^\s*(?:[-*•]|\d+[.)])\s+\S/;
  const listsSteps = text => String(text || '').split('\n').filter(l => STEP_LINE.test(l)).length >= 2;
  function linkByHandHtml(x) {
    const id = x.id, open = ui.linkFor === id;
    let pick = '';
    if (open) {
      pick = pickerHtml('link', { placeholder: 'Link a task: a number, or a few words', closed: true, exclude: new Set([id]) });
      if (ui.linkHit != null) {
        const h = sheetHitInfo(ui.linkHit), closed = h.status !== 'open';
        pick += `<div class="lkhit"><span class="code">Task-${h.id}</span> <span class="nm">${esc(h.name)}</span>${statusChip(h)}</div>
          <div class="lkchoice"><span class="lkthis">This task…</span><button class="btn small" data-link-as="part_of">is a step of it</button><button class="btn small" data-link-as="waits_on" ${closed ? `disabled title="Task-${h.id} is ${esc(h.status)}: nothing can wait on it"` : ''}>waits on it</button><button class="btn small" data-link-as="same_as">is the same task</button></div>`;
      }
    }
    const step = x.rel && x.rel.goal_id != null ? '' : `<div class="stepline"><input id="step-line-${id}" type="text" autocomplete="off" enterkeyhint="done" placeholder="Add a step" aria-label="Add a step to Task-${id}" value="${esc(draft.step[id] || '')}"><button class="btn small primary" data-step-add="${id}">Add step</button></div>
      <p class="hint stephint">The step is created already linked, in the goal's project.</p>`;
    return `<section class="dsec byhand"><div><button class="btn small" data-link-open="${id}" aria-expanded="${open}">Link a task</button></div>${open ? `<div class="linkpick">${pick}</div>` : ''}${step}</section>`;
  }
  // More's line under an opened task's actions; Merge into another opens its picker there.
  function moreHtml(x) {
    if (ui.moreFor !== x.id) return '';
    const id = x.id;
    return `<div class="more-row"><button class="btn small" data-more-split="${id}" title="A chat turn: Claude drafts the steps; one Apply creates and links them">Split into steps</button><button class="btn small" data-more-dup="${id}">Duplicate</button><button class="btn small" data-more-merge="${id}" aria-expanded="${ui.mergeFor === id}">Merge into another</button>
      ${ui.mergeFor === id ? pickerHtml('merge', { placeholder: 'Merge into: a number, or a few words', closed: true, exclude: new Set([id]) }) : ''}</div>`;
  }
  // The two selected rows in tick order, or swapped by ⇄.
  function bulkPair() { const [a, b] = [...ui.sel]; return ui.bulkSwap ? [b, a] : [a, b]; }
  function bulkLinkHtml() {
    if (!ui.bulkLink || ui.sel.size !== 2) return '';
    const [a, b] = bulkPair();
    return `<div class="linkline"><span class="code">Task-${a}</span><button class="btn small" data-bulk-link="part_of">is a step of</button><button class="btn small" data-bulk-link="waits_on">waits on</button><button class="btn small" data-bulk-link="same_as">is the same task as</button><span class="code">Task-${b}</span><button class="btn small" data-bulk-swap aria-label="Swap the two tasks" title="Swap the two tasks">⇄</button></div>`;
  }
  // a is this task, b the other: a is a step of b, waits on b, or the two are one task (a closed b is the one kept).
  function stageLinkChoice(a, b, kind, bClosed) {
    let c;
    if (kind === 'same_as') { const keep = bClosed ? b : defaultKeep(a, b); c = { task_id: keep === a ? b : a, op: 'merge', fields: { into: keep } }; }
    else c = { task_id: a, op: 'link', fields: { kind, other: b } };
    stageChange(c, '');
    toast('Link staged · press Apply in the tray');
  }
  pickers.link = id => { ui.linkHit = id; render(); };
  pickers.merge = id => {
    const from = ui.mergeFor; if (from == null) return;
    ui.mergeFor = ui.moreFor = null;
    stageChange({ task_id: from, op: 'merge', fields: { into: id } }, '');
    toast(`Merge staged: Task-${from} goes, Task-${id} stays`);
  };
  function addStep(id) {
    const t = byId(id), sent = draft.step[id] || '', name = sent.trim();
    if (!t) return;
    if (!name) { const el = document.getElementById('step-line-' + id); if (el) el.focus(); return; }
    addTask({ fields: { name, project: t.project, under: id } },
      { box: 'step:' + id, clear: () => { if ((draft.step[id] || '') === sent) delete draft.step[id]; }, said: n => 'Added Task-' + n + ' as a step of Task-' + id });
  }
  // A copy of what the server holds (not what is staged), under the same goal; opened in its editor. `/api/add` takes a
  // date, so a timed due is cut to its day (a refused one would take `hard` with it).
  function duplicateTask(id) {
    const t = byId(id); if (!t) return;
    const due = t.due ? String(t.due).slice(0, 10) : null;
    const f = { name: t.name, project: t.project, effort: t.effort, due, hard: due && t.hard ? 1 : null, context: t.context,
      counterpart: t.counterpart, waiting_on: t.waiting_on, under: t.rel ? t.rel.goal_id : null };
    ui.moreFor = null;
    addTask({ fields: Object.fromEntries(Object.entries(f).filter(([, v]) => v != null && v !== '')) }, { open: true });
  }
  // The task's conversation, shown in it, with its earlier messages loaded (so a `split` answer lands under them).
  async function taskConversation(id) {
    const pl = placeOf('task', id);
    if (!pl.loaded) await loadPlace(pl);
    ui.askTask = id;
    return pl;
  }
  function wireByHand(panel) {
    panel.querySelectorAll('[data-link-open]').forEach(b => b.onclick = () => {
      const id = +b.dataset.linkOpen, open = ui.linkFor !== id;
      ui.linkFor = open ? id : null; ui.linkHit = null; draft.picks.link = '';
      render();
      const el = open && document.getElementById('pick-link'); if (el) el.focus({ preventScroll: true });
    });
    panel.querySelectorAll('[data-link-as]').forEach(b => b.onclick = () => {
      const id = ui.linkFor, n = ui.linkHit; if (id == null || n == null) return;
      const closed = sheetHitInfo(n).status !== 'open';
      ui.linkFor = ui.linkHit = null;
      stageLinkChoice(id, n, b.dataset.linkAs, closed);
    });
    panel.querySelectorAll('input[id^="step-line-"]').forEach(inp => {
      const id = +inp.id.slice('step-line-'.length);
      inp.oninput = () => { draft.step[id] = inp.value; };
      inp.addEventListener('keydown', e => { if (enterKey(e)) { e.preventDefault(); addStep(id); } e.stopPropagation(); });
    });
    panel.querySelectorAll('[data-step-add]').forEach(b => b.onclick = () => addStep(+b.dataset.stepAdd));
    panel.querySelectorAll('[data-more]').forEach(b => b.onclick = () => { const id = +b.dataset.more; ui.moreFor = ui.moreFor === id ? null : id; ui.mergeFor = null; draft.picks.merge = ''; render(); });
    panel.querySelectorAll('[data-more-split]').forEach(b => b.onclick = async () => {
      const id = +b.dataset.moreSplit; ui.moreFor = null;
      sendTurn(await taskConversation(id), '', 'split', 'askTask');
    });
    panel.querySelectorAll('[data-more-dup]').forEach(b => b.onclick = () => duplicateTask(+b.dataset.moreDup));
    panel.querySelectorAll('[data-more-merge]').forEach(b => b.onclick = () => {
      const id = +b.dataset.moreMerge; ui.mergeFor = ui.mergeFor === id ? null : id; draft.picks.merge = '';
      render();
      const el = ui.mergeFor != null && document.getElementById('pick-merge'); if (el) el.focus({ preventScroll: true });
    });
    panel.querySelectorAll('[data-bulk-link]').forEach(b => b.onclick = () => {
      const [a, z] = bulkPair();
      ui.sel.clear(); ui.bulkLink = ui.bulkSwap = false;
      stageLinkChoice(a, z, b.dataset.bulkLink, false);
    });
    panel.querySelectorAll('[data-bulk-swap]').forEach(b => b.onclick = () => { ui.bulkSwap = !ui.bulkSwap; render(); });
    // The goal opens (its row on a desktop, its page on a phone) with its conversation, and the turn starts there.
    panel.querySelectorAll('[data-make-steps]').forEach(b => b.onclick = async () => {
      const goal = +b.dataset.makeSteps, pl = await taskConversation(goal);
      openTask(goal);
      sendTurn(pl, 'Make them tasks', 'split', 'askTask');
    });
  }

  // ---------- wiring ----------
  // A text box that grows: Enter sends (Shift+Enter is a new line); on a touch keyboard Enter is a new line and the button sends.
  function typing(el, key, send) {
    el.oninput = () => { draft[key] = el.value; grow(el); };
    el.addEventListener('keydown', e => { if (e.key === 'Escape') return; if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && e.keyCode !== 229 && !TOUCH()) { e.preventDefault(); send(); } e.stopPropagation(); });      // 229: Safari's Enter that commits an IME candidate
  }
  // On a phone Review's decisions are pinned at the bottom (app.css), above the tray when it shows, as the page bar is.
  // On a phone Later is pinned at the bottom while its menu is in the card: the page scrolls so the menu (and Pick a
  // date under it) ends just above the pinned decisions, where the finger is, whether the card was scrolled to its
  // top or its end. Again once a note box's keyboard has gone (kb-open is cleared 300 ms after the blur).
  function revealLaterMenu(again) {
    if (!PHONE()) return;
    const menu = document.querySelector('#panel .review .card .later-menu.on'); if (!menu) return;
    const last = document.querySelector('#panel .review .card .datepick') || menu;
    const big = document.querySelector('#panel .review .card .big');
    const b = big && big.getClientRects().length ? big.getBoundingClientRect() : null;
    const limit = (b && b.height ? b.top : innerHeight) - 8;
    const head = document.querySelector('header.bar'), top = head && getComputedStyle(head).position !== 'static' ? head.getBoundingClientRect().bottom + 8 : 8;
    // the menu's end on the bar's edge, unless that would push its start under the top bar
    window.scrollBy(0, Math.min(last.getBoundingClientRect().bottom - limit, menu.getBoundingClientRect().top - top));
    if (!again && document.body.classList.contains('kb-open')) setTimeout(() => revealLaterMenu(true), 350);
  }
  function placeReviewBar() {
    const big = document.querySelector('#panel .review .card .big'); if (!big) return;
    const tray = document.getElementById('tray');
    big.style.bottom = PHONE() && tray.classList.contains('on') ? tray.offsetHeight + 'px' : '';
  }
  // A due chip's click cycles the date: tomorrow, Friday, in a week, none (the panel's, and a row's chips redrawn in place).
  function wireDueChips(root) {
    root.querySelectorAll('[data-due]').forEach(c => c.addEventListener('click', () => {
      const id = +c.dataset.due; if (!byId(id)) return;      // a closed task's page: its date is not for changing
      const x = view(byId(id));
      const order = [isoPlus(1), nextFri(), isoPlus(7), null];
      const i = order.indexOf(x.due ? x.due.slice(0, 10) : null);
      act(id, 'due', order[(i + 1) % order.length]);
    }));
  }
  function wire() {
    const panel = document.getElementById('panel');
    panel.querySelectorAll('.row').forEach(r => {
      const id = +r.dataset.id;
      // A plain tap selects (phones have no shift); the checkbox is the same selection. Keyboard
      // focus follows the last tap so j/k keep working.
      const toggleSel = () => { ui.sel.has(id) ? ui.sel.delete(id) : ui.sel.add(id); ui.focus = id; ui.focusAt = [...panel.querySelectorAll('.row')].indexOf(r); render(); };
      // Two taps on the same row within 400 ms are a double-click: the first tap's selection flip
      // is undone and the details open. Tracked by hand because render() replaces the DOM node
      // between the two clicks, so the browser's dblclick event would not fire.
      // On a phone a tap opens the task page and only the tick box selects.
      r.querySelector('.main').addEventListener('click', e => {
        if (e.target.closest('input, button, a, .chip.due')) return;
        if (PHONE()) { openTask(id); return; }
        const now = Date.now();
        if (lastTap.id === id && now - lastTap.at < 400) { lastTap = { id: null, at: 0 }; ui.sel.has(id) ? ui.sel.delete(id) : ui.sel.add(id); ui.open.has(id) ? ui.open.delete(id) : ui.open.add(id); render(); return; }
        lastTap = { id, at: now }; toggleSel();
      });
      r.querySelector('input[type=checkbox]').addEventListener('change', toggleSel);
      r.querySelector('[data-chev]').addEventListener('click', () => { if (PHONE()) { openTask(id); return; } ui.open.has(id) ? ui.open.delete(id) : ui.open.add(id); render(); });
      r.querySelector('.main').setAttribute('draggable', 'true');
      r.querySelector('.main').addEventListener('dragstart', e => e.dataTransfer.setData('text/plain', String(id)));
    });
    const pb = document.getElementById('page-back'); if (pb) pb.onclick = () => history.back();
    panel.querySelectorAll('[data-page]').forEach(b => b.onclick = () => {
      const k = b.dataset.page;
      if (k === 'done') { ui.pageLater = false; act(ui.page, 'done'); }
      else if (k === 'later') { ui.pageLater = !ui.pageLater; render(); }
      else if (k === 'more') { ui.pageMore = !ui.pageMore; render(); }
    });
    panel.querySelectorAll('[data-page-later]').forEach(b => b.onclick = () => pageLater(b.dataset.pageLater));
    placePageBar();
    wireBadges(panel);
    wireFilingChips(panel);
    panel.querySelectorAll('[data-act]').forEach(b => b.addEventListener('click', () => act(+b.dataset.id, b.dataset.act)));
    wireDueChips(panel);
    panel.querySelectorAll('[data-bulk]').forEach(b => b.addEventListener('click', () => {
      const k = b.dataset.bulk; if (k === 'clear') { ui.sel.clear(); render(); return; }
      if (k === 'link') { ui.bulkLink = !ui.bulkLink; ui.bulkSwap = false; render(); return; }      // opens the Link line (stage 6)
      const d = k === 'setdue' ? (document.getElementById('bulk-date') || {}).value : null;
      if (k === 'setdue' && !d) { toast('Pick a date'); return; }
      [...ui.sel].forEach(id => act(id, k, d)); ui.sel.clear(); render();
    }));
    const bm = document.getElementById('bulk-more'); if (bm) bm.onclick = () => { ui.bulkMore = !ui.bulkMore; render(); };
    if (!ui.sel.size) ui.bulkMore = false;      // the next selection starts with the short bar on one line
    if (ui.sel.size !== 2) ui.bulkLink = ui.bulkSwap = false;      // Link is for exactly two rows
    const bulk = document.getElementById('bulk');
    if (bulk) {
      bulk.classList.toggle('on', ui.sel.size > 0); const n = document.getElementById('bulk-n'); if (n) n.textContent = ui.sel.size;
      // On a phone the tray slides away while rows are selected (app.css), so the bar sits at the bottom edge.
      const tray = document.getElementById('tray'); bulk.style.bottom = !PHONE() && tray.classList.contains('on') ? (tray.offsetHeight + 8) + 'px' : '';
    }
    document.body.classList.toggle('bulk-on', ui.sel.size > 0 && !!bulk);      // Chat has no bulk bar: what is selected there is what a prompt carries
    panel.querySelectorAll('[data-proj]').forEach(b => b.addEventListener('click', () => { const k = 'projects:' + b.dataset.proj; setFold(k, !folded(k, true)); render(); }));
    panel.querySelectorAll('[data-fold]').forEach(b => b.addEventListener('click', () => { setFold(b.dataset.fold, !folded(b.dataset.fold)); render(); }));
    wireSummary();
    panel.querySelectorAll('[data-rv]').forEach(b => b.addEventListener('click', () => reviewAct(b.dataset.rv)));
    panel.querySelectorAll('[data-ctx-more]').forEach(b => b.onclick = () => { ui.ctxOpen = ui.ctxOpen === +b.dataset.ctxMore ? null : +b.dataset.ctxMore; render(); });
    panel.querySelectorAll('[data-rv-accept]').forEach(b => b.onclick = () => {
      const eid = +b.dataset.rvAccept; const item = review.items.find(i => (i.evidence || []).some(e => e.id === eid)); const e = item && item.evidence.find(e => e.id === eid); if (!e || !e.change) return;
      const tid = item.task.id, c = Object.assign({ task_id: tid }, e.change);
      if (['done', 'drop'].includes(c.op)) {   // this IS the decision: the card's other flags are judged with it
        stage(tid, c.op, c.fields || {}, changeWords(c) || e.label);
        (item.evidence || []).forEach(o => { if (o.id !== eid && !staged.has('e:' + o.id)) stageJudge(o.id, tid, 'reviewed', o.kind === 'proposal' ? 'suggestion seen' : 'thread seen'); });
        ui.rvNote = { id: null, text: '' };
      }
      else if (['pin_top', 'longterm'].includes(c.op)) stageExtra(`${tid}:a${eid}`, tid, c.op, {}, e.label);   // an extra beside the decision, like the ★ button
      else if (c.op === 'add_reminder') stageExtra(tid + ':r', tid, 'add_reminder', c.fields, e.label);
      else if (['link', 'unlink', 'merge', 'add_note'].includes(c.op)) stageChange(c, e.label, {}, 'a' + eid);
      else if (c.op === 'set') stageExtra(`${tid}:c${eid}`, tid, 'set', c.fields || {}, e.label, { expect: c.expect || textExpect(tid, c.fields) });     // a ripple's text carries the text it read
      else { toast('This suggestion cannot be staged here'); return; }
      stageJudge(eid, tid, 'applied', 'suggestion accepted'); toast(['done', 'drop'].includes(c.op) ? 'Decision staged · Apply in the tray' : 'Suggestion staged · decide on the card, then Apply');
    });
    panel.querySelectorAll('[data-rv-dismiss]').forEach(b => b.onclick = () => { stageJudge(+b.dataset.rvDismiss, +b.closest('.card').dataset.id, 'dismissed', 'suggestion dismissed'); });
    // A mail card on a task: follow its thread (the link, with its verdict) or say the mail is not about it.
    const mailCard = eid => { const item = review && review.items.find(i => (i.evidence || []).some(e => e.id === eid)); const e = item && item.evidence.find(e => e.id === eid); return e && e.change && e.change.op === 'follow_thread' ? { e, tid: item.task.id } : null; };
    panel.querySelectorAll('[data-follow]').forEach(b => b.onclick = () => {
      const m = mailCard(+b.dataset.follow); if (!m) return;
      const { e, tid } = m, subject = (e.change.mail || {}).subject || (e.change.fields || {}).subject || '';
      stageChange({ task_id: tid, op: 'follow_thread', fields: e.change.fields }, subject ? `follows “${subject}”` : 'follows the mail thread', { ev: e.id }, 'f' + e.id);
      stageJudge(e.id, tid, 'applied', 'mail card accepted');
      toast('Staged · it follows the thread once you Apply');
    });
    panel.querySelectorAll('[data-notabout]').forEach(b => b.onclick = () => { const m = mailCard(+b.dataset.notabout); if (m) stageJudge(m.e.id, m.tid, 'dismissed', 'not about this mail'); });
    panel.querySelectorAll('[data-since-see]').forEach(b => b.onclick = () => { const id = +b.dataset.sinceSee; ui.sinceOpen = ui.sinceOpen === id ? null : id; render(); });
    panel.querySelectorAll('[data-since-undo]').forEach(b => b.onclick = async () => {
      b.disabled = true;
      if (!await undoAndSay(+b.dataset.sinceUndo)) { b.disabled = false; return; }
      review = null; await loadReview().catch(() => {}); render();      // a failed load says so in the panel
    });
    const rr = document.getElementById('review-retry'); if (rr) rr.onclick = () => { loadReview().catch(() => {}); render(); };
    panel.querySelectorAll('[data-lsug]').forEach(b => b.onclick = () => linkSuggestionAct(+b.closest('.card').dataset.id, +b.dataset.lsug, b.dataset.lsugAct));
    panel.querySelectorAll('[data-pending-apply]').forEach(b => b.onclick = async () => {
      try { const r = await api(`/api/proposals/${b.dataset.pendingApply}/apply`, { method: 'POST' }); lastBatch = r.batch_id; review = null; logData = null; await load(); toast(`${r.applied} change${r.applied === 1 ? '' : 's'} applied` + (r.skipped.length ? ` · ${r.skipped.length} skipped` : ''), true); if ((r.ripple_queued || []).length) watchRipple(r.batch_id); }
      catch (e) { toast('Apply failed: ' + e.message); }
    });
    panel.querySelectorAll('[data-pending-dismiss]').forEach(b => b.onclick = async () => {
      try { await api(`/api/proposals/${b.dataset.pendingDismiss}/dismiss`, { method: 'POST' }); await load(); toast('Dismissed'); } catch (e) { toast('Dismiss failed: ' + e.message); }
    });
    panel.querySelectorAll('[data-sugg-dismiss]').forEach(b => b.onclick = () => { stageJudge(+b.dataset.suggDismiss, null, 'dismissed', 'suggestion dismissed'); });
    panel.querySelectorAll('[data-sugg-create]').forEach(b => b.onclick = async () => {
      const id = +b.dataset.suggCreate;
      // One review batch (the task, its links, the card's verdict): the toast's Undo drops the task and brings the card back;
      // Create on a card whose task that Undo dropped reopens it. The queue is re-read, so the new row's card is next.
      try {
        const r = await api(`/api/review/suggestions/${id}/create`, { method: 'POST', body: '{}' });
        const sg = (review.suggestions || []).find(x => x.id === id); if (sg) sg.created = r.task_id;
        lastBatch = r.batch_id;
        if (r.batch_id != null) cardMade.set(r.batch_id, r.task_id);
        const said = r.reopened ? `Reopened Task-${r.task_id}` : r.already ? `Task-${r.task_id} was already created` : `Created Task-${r.task_id} · it shows as New in Review`;
        toast(said + ((r.skipped || []).length ? ' · ' + r.skipped[0] : ''), r.batch_id != null, { batch: r.batch_id, buttons: [{ text: 'Open', run: () => goTask(r.task_id) }] });
        await load(); await loadReview();
      }
      catch (e) { toast('Create failed: ' + e.message); }
    });
    // The date is used only on the button: a date input's own change event fires while the phone's
    // wheel is still moving, which once staged a date nobody chose.
    const rg = document.getElementById('rv-date-go'); if (rg) rg.onclick = () => { const rd = document.getElementById('rv-date'); if (!rd || !rd.value) { toast('Pick a date first'); return; } reviewAct('date', rd.value); };
    const rd = document.getElementById('rv-date'); if (rd) rd.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); if (rd.value) reviewAct('date', rd.value); } e.stopPropagation(); });
    const rn = document.getElementById('rv-note'); if (rn) rn.oninput = () => { ui.rvNote = { id: +rn.closest('.card').dataset.id, text: rn.value }; };
    const rm = document.getElementById('review-more'); if (rm) rm.onclick = () => loadReview();
    wireReminders(panel); wireDue(panel);
    panel.querySelectorAll('[data-undo]').forEach(b => b.onclick = async () => {
      b.disabled = true;
      let res;
      try { res = await undoBatch(+b.dataset.undo); } catch (e) { b.disabled = false; toast('Undo failed: ' + e.message); return; }
      await load(); logData = null; toast(undoSaid(res)); render();
    });
    // An opened task's canned buttons and Ask Claude: the conversation shows in that task (it used to switch to Chat).
    panel.querySelectorAll('[data-chat-canned]').forEach(b => b.onclick = async () => {
      const id = +b.dataset.id, pl = placeOf('task', id);
      if (!pl.loaded) await loadPlace(pl);      // the thread's earlier messages first, so the answer lands under them
      ui.askTask = id; sendTurn(pl, '', b.dataset.chatCanned, 'askTask');
    });
    panel.querySelectorAll('[data-ask-task]').forEach(b => b.onclick = () => {
      ui.askTask = +b.dataset.askTask; render();
      const el = document.getElementById('task-ask'); if (el) el.focus();
    });
    panel.querySelectorAll('[data-place-close]').forEach(b => b.onclick = () => {
      const pl = places.get(b.dataset.placeClose); if (!pl || pl.live) return;
      if (pl.origin === 'today' && pl.thread_id) { try { localStorage.setItem('factotum.todayClosed', String(pl.thread_id)); } catch (_) {} }
      Object.assign(pl, { thread_id: null, messages: [], named: {}, fresh: true, more: false }); pl.off.clear();
      if (pl.origin === 'today') draft.askToday = '';
      render();
    });
    wireDetail(panel);
    panel.querySelectorAll('[data-chat-stage]').forEach(b => b.onclick = () => {
      const mid = +b.dataset.chatStage; const m = chat.messages.find(x => x.id === mid); if (!m) return;
      let n = 0; panel.querySelectorAll(`[data-chat-prop="${mid}"]`).forEach(ck => { if (ck.checked && stageProposal(m.proposals[+ck.dataset.i], mid, +ck.dataset.i)) n++; });
      toast(n ? `${n} proposal${n === 1 ? '' : 's'} staged · press Apply in the tray` : 'Nothing selected'); render();
    });
    panel.querySelectorAll('[data-chat-create]').forEach(b => b.onclick = async () => {
      try { const r = await api('/api/chat/create', { method: 'POST', body: JSON.stringify({ message_id: +b.dataset.chatCreate, index: +b.dataset.i }) }); toast(`Created Task-${r.task_id}`); await load(); if (chat.current !== null) await loadChatThread(chat.current); }
      catch (e) { toast('Create failed: ' + e.message); }
    });
    panel.querySelectorAll('[data-chat-draft]').forEach(b => b.onclick = async () => {
      const m = chat.messages.find(x => x.id === +b.dataset.chatDraft); const d = m && m.drafts[+b.dataset.i]; if (!d) return;
      try { await api('/api/chat/drafts/open', { method: 'POST', body: JSON.stringify({ draft: d }) }); toast('Shown in Mailbird'); }
      catch (e) { toast('Mailbird did not open it (' + e.message + '); the draft is in its Drafts folder'); }
    });
    panel.querySelectorAll('[data-thread]').forEach(b => b.onclick = () => { ui.chatDrawer = false; loadChatThread(+b.dataset.thread); });
    const cn = document.getElementById('chat-new'); if (cn) cn.onclick = () => { chat.current = null; chat.messages = []; ui.chatDrawer = false; render(); };
    const tt = document.getElementById('chat-threads-toggle'); if (tt) tt.onclick = () => { ui.chatDrawer = !ui.chatDrawer; render(); };
    ['chat-threads-close', 'chat-backdrop'].forEach(id => { const el = document.getElementById(id); if (el) el.onclick = () => { ui.chatDrawer = false; render(); }; });
    if (ui.tab === 'chat' && chat.scroll) { chat.scroll = false; window.scrollTo({ top: document.documentElement.scrollHeight }); }
    wirePlaces(panel);
    wireNew(panel);
    wireFind(panel);
    wireEditors(panel);
    wireByHand(panel);
    placeReviewBar();
    const po = document.getElementById('chat-pick-open'); if (po) po.onclick = () => { ui.pickOpen = !ui.pickOpen; draft.pick = ''; render(); const el = document.getElementById('chat-pick'); if (el) el.focus(); };
    const pk = document.getElementById('chat-pick');
    if (pk) {
      pk.oninput = e => { draft.pick = pk.value; if (!e.isComposing) render(); };      // a re-render mid-composition would break an IME
      pk.addEventListener('compositionend', () => { draft.pick = pk.value; render(); });
      pk.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); const m = pickMatches()[0]; if (m) { ui.sel.add(m.id); draft.pick = ''; render(); } } else if (e.key === 'Escape') { ui.pickOpen = false; render(); const c = document.getElementById('cmd'); if (c) c.focus(); } e.stopPropagation(); });
    }
    panel.querySelectorAll('[data-pick]').forEach(b => b.onclick = () => { ui.sel.add(+b.dataset.pick); draft.pick = ''; render(); const el = document.getElementById('chat-pick'); if (el) el.focus(); });
    panel.querySelectorAll('[data-detach]').forEach(b => b.onclick = () => { ui.sel.delete(+b.dataset.detach); render(); });
    const da = document.getElementById('chat-detach-all'); if (da) da.onclick = () => { ui.sel.clear(); render(); };
    panel.querySelectorAll('[data-starter-help]').forEach(b => b.onclick = () => document.querySelector('#tabs button[data-tab="settings"]').click());
    panel.querySelectorAll('[data-starter]').forEach(b => b.onclick = () => { draft.cmd = b.textContent; render(); const el = document.getElementById('cmd'); if (el) { el.focus(); el.setSelectionRange(el.value.length, el.value.length); } });
    const cd = document.getElementById('chat-delete'); if (cd) cd.onclick = async () => {
      if (!confirm('Delete this thread and its messages? What it applied to your tasks stays as it is.')) return;
      try { await api(`/api/chat/threads/${chat.current}`, { method: 'DELETE' }); chat.current = null; chat.messages = []; chat.threads = null; toast('Thread deleted'); render(); } catch (e) { toast('Delete failed: ' + e.message); }
    };
    panel.querySelectorAll('[data-sugg-open]').forEach(b => b.onclick = async () => {
      try { await api(`/api/review/suggestions/${b.dataset.suggOpen}/open`, { method: 'POST' }); toast('Shown in Mailbird'); } catch (e) { toast('Mailbird could not show it (' + e.message + '); it may have been moved'); }
    });
    panel.querySelectorAll('[data-linkview]').forEach(b => b.onclick = () => { setLinkView(b.dataset.linkview); render(); });
    panel.querySelectorAll('[data-twin-merge]').forEach(b => b.onclick = () => {
      const [goes, keep, eid] = b.dataset.twinMerge.split(':').map(Number);
      stageChange({ task_id: goes, op: 'merge', fields: { into: keep } }, '', { ev: eid });
      toast('Merge staged · press Apply in the tray');
    });
    panel.querySelectorAll('[data-twin-not]').forEach(b => b.onclick = () => {
      const [eid, goes, keep] = b.dataset.twinNot.split(':').map(Number);
      stageJudge(eid, goes, 'dismissed', `not the same as Task-${keep}`);
    });
    panel.querySelectorAll('[data-logview]').forEach(b => b.onclick = () => { logView = b.dataset.logview; try { localStorage.setItem('factotum.logview', logView); } catch (_) {} render(); });
    const lq = document.getElementById('log-quiet'); if (lq) lq.onchange = () => { logQuiet = lq.checked; try { localStorage.setItem('factotum.logquiet', logQuiet ? '1' : '0'); } catch (_) {} render(); };
    const lm = document.getElementById('log-more'); if (lm) lm.onclick = () => { logLimit = Math.min(200, logLimit + 70); loadLog(); };
    panel.querySelectorAll('[data-tl]').forEach(r => r.addEventListener('click', e => { if (e.target.closest('button, a')) return; const k = r.dataset.tl; logOpen.has(k) ? logOpen.delete(k) : logOpen.add(k); render(); }));
    panel.querySelectorAll('[data-goto-chat]').forEach(a => a.onclick = e => { e.preventDefault(); chat.current = +a.dataset.gotoChat; chat.threads = null; go('chat'); });
    const cmd = document.getElementById('cmd'), cmdGo = document.getElementById('cmd-go');
    const cmdSend = () => {
      const v = draft.cmd.trim(); if (!v) return;
      // A bare add / new / + names no task: nothing is sent (not as a command, not to Claude), the text stays.
      if (ui.tab !== 'chat' && BARE_ADD_RE.test(v)) { toast('Not added: a task needs a name'); previewAdd('cmd', draft.cmd); if (cmd) cmd.focus(); return; }
      if (chat.busy && ui.tab === 'chat') { toast('Claude is still answering · your prompt is kept in the box'); return; }      // never clear what cannot be sent
      draft.cmd = ''; if (cmd) { cmd.value = ''; grow(cmd); } sendPrompt(v);
    };
    if (cmdGo) cmdGo.onclick = cmdSend;
    if (cmd) typing(cmd, 'cmd', cmdSend);
    document.getElementById('tray-apply').onclick = () => applyAll();
    document.getElementById('tray-discard').onclick = () => discardStaged();
    document.getElementById('tray-toggle').onclick = () => { ui.trayOpen = !ui.trayOpen; renderTray(); placePageBar(); placeReviewBar(); };
    const save = document.getElementById('settings-save'); if (save) save.onclick = saveSettings;
    const arm = document.getElementById('conn-arm'); if (arm) arm.onclick = async () => { try { await api('/api/connectors/arm', { method: 'POST' }); await loadSettings(); toast('A new connector may be approved for 10 minutes'); } catch (e) { toast('Failed: ' + e.message); } };
    panel.querySelectorAll('[data-conn-revoke]').forEach(b => b.onclick = async () => {
      if (!confirm('Revoke this connector? Its tokens stop working at once; it can connect again through the consent page.')) return;
      try { await api(`/api/connectors/${encodeURIComponent(b.dataset.connRevoke)}/revoke`, { method: 'POST' }); await loadSettings(); toast('Connector revoked'); } catch (e) { toast('Revoke failed: ' + e.message); }
    });
    if (ui.tab === 'settings') {
      // A change is captured at once (the map, not the DOM, is what a save reads), so leaving the tab
      // inside the 300 ms still saves it, and a control nobody touched is never sent.
      const later = () => { clearTimeout(saveSettings.t); saveSettings.t = setTimeout(saveSettings, 300); };
      panel.querySelectorAll('[data-key]').forEach(el => el.addEventListener('change', () => {
        const v = controlValue(el); if (v === undefined) return;
        settingsDirty.set(el.dataset.key, v); later();
      }));
      panel.querySelectorAll('[data-acc]').forEach(el => el.addEventListener('change', () => {
        const a = settings.accounts[+el.dataset.acc]; const f = el.dataset.f; if (!a) return;
        if (f === 'roles') a.roles = el.value.split(',').map(s => s.trim()).filter(Boolean);
        else if (f === 'enabled') a.enabled = el.checked; else a[f] = el.value;
        settingsDirty.set('#accounts', true); later();
      }));
      panel.querySelectorAll('[data-accdel]').forEach(b => b.onclick = () => { settings.accounts.splice(+b.dataset.accdel, 1); settingsDirty.set('#accounts', true); render(); later(); });
      const pOn = document.getElementById('push-on'); if (pOn) pOn.onclick = pushOn;      // pushOn asks the permission before its first await
      const pOff = document.getElementById('push-off'); if (pOff) pOff.onclick = pushOff;
      const pTest = document.getElementById('push-test'); if (pTest) pTest.onclick = pushTest;
      panel.querySelectorAll('[data-push-remove]').forEach(b => b.onclick = () => pushRemove(+b.dataset.pushRemove));
    }
    const accAdd = document.getElementById('acc-add'); if (accAdd) accAdd.onclick = () => { settings.accounts.push({ address: '', display: '', roles: ['monitor'], enabled: true }); render(); };
    wireRail();
  }
  function wireRail() {
    const list = document.getElementById('rail-list'); const drop = document.getElementById('rail-drop');
    list.querySelectorAll('li').forEach(li => {
      // A click goes to the task's row; two in a row open its details as well. The first waits 260 ms
      // for the second instead of acting at once: the jump scrolls the page, and on a phone the second
      // tap of a double-tap would land on whatever had moved under the finger.
      li.addEventListener('click', () => {
        const id = +li.dataset.id;
        if (railTap.timer && railTap.id === id) { clearTimeout(railTap.timer); railTap = { id: null, timer: null }; jumpTo(id, true); return; }
        clearTimeout(railTap.timer);
        railTap = { id, timer: setTimeout(() => { railTap = { id: null, timer: null }; jumpTo(id, false); }, 260) };
      });
      li.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); jumpTo(+li.dataset.id, e.key === 'Enter'); } });
      li.addEventListener('dragstart', e => e.dataTransfer.setData('text/plain', li.dataset.id));
      li.addEventListener('dragover', e => { e.preventDefault(); li.classList.add('over'); });
      li.addEventListener('dragleave', () => li.classList.remove('over'));
      li.addEventListener('drop', e => { e.preventDefault(); li.classList.remove('over'); reorderTop(+e.dataTransfer.getData('text/plain'), +li.dataset.id); });
    });
    drop.addEventListener('dragover', e => { e.preventDefault(); drop.classList.add('over'); });
    drop.addEventListener('dragleave', () => drop.classList.remove('over'));
    drop.addEventListener('drop', e => { e.preventDefault(); drop.classList.remove('over'); const id = +e.dataTransfer.getData('text/plain'); if (!view(byId(id)).top_rank) act(id, 'top'); });
  }
  function reorderTop(fromId, toId) {
    if (fromId === toId) return;
    const top = openOnes().filter(x => x.top_rank).sort((a, b) => a.top_rank - b.top_rank).map(x => x.id);
    let from = top.indexOf(fromId); if (from < 0) { top.push(fromId); from = top.length - 1; }
    const [m] = top.splice(from, 1); top.splice(top.indexOf(toId), 0, m);
    top.forEach((id, i) => stage(id, 'set', { top_rank: i + 1 }, 'Top ' + (i + 1)));
  }
  function reviewAct(kind, dateStr) {
    const items = review.items.filter(i => !staged.has(i.task.id)); if (!items.length) return;
    const x = items[0].task, id = x.id;
    if (kind === 'later') { ui.laterOpen = !ui.laterOpen; ui.datePick = false; render(); revealLaterMenu(); return; }
    if (kind === 'pickdate') { ui.datePick = !ui.datePick; render(); revealLaterMenu(); return; }
    if (kind === 'top' || kind === 'longterm') { act(id, kind); return; }   // extras, not decisions
    // A decision is a review: it sets last_reviewed_at (Keep does so server-side). An optional note saved
    // with Done or Drop is the task's outcome (design §8.5: appended to the text as well, it would ripple
    // twice). With Keep or Later it is staged beside the decision as add_note under "<id>:note": the server
    // appends it, dated, to the context as it is at Apply, so nothing typed here writes over a newer text.
    const text = ui.rvNote.id === id ? ui.rvNote.text.trim() : '';
    const later = (day, label) => stage(id, 'set', { snoozed_until: day, last_reviewed_at: data.today }, 'later · back ' + label);
    const closes = kind === 'done' || kind === 'drop';
    // An outcome is at most OUTCOME_MAX characters (the server cuts there): a longer note is never cut, it is
    // added to the text in full beside the decision, as before outcomes, and the toast says so.
    const long = closes && [...text].length > OUTCOME_MAX;
    if (closes && text && !long) stageChange({ task_id: id, op: kind, fields: { outcome: text } }, kind === 'done' ? 'done' : 'dropped');
    else if (kind === 'done') stage(id, 'done', {}, 'done');
    else if (kind === 'drop') stage(id, 'drop', {}, 'dropped');
    else if (kind === 'keep') stage(id, 'keep', {}, 'kept');
    else if (kind === 'tomorrow') later(isoPlus(1), 'tomorrow');
    else if (kind === 'fri') later(nextFri(), 'Friday');
    else if (kind === 'nextweek') later(isoPlus(7 - ((TODAY().getDay() + 6) % 7)), 'next week');
    else if (kind === 'date') { if (!dateStr) return; later(dateStr, dateStr); }
    else return;
    if (text && (!closes || long)) stageChange({ task_id: id, op: 'add_note', fields: { text } }, '');
    if (long) toast(`The note is longer than an outcome (${OUTCOME_MAX} characters), so it goes to the task's text instead`);
    // The flags that brought the card here (a thread update, a planner proposal) are judged in the
    // same batch, so the card does not come back tomorrow for the same reason.
    (items[0].evidence || []).forEach(e => { if (!staged.has('e:' + e.id)) stageJudge(e.id, id, 'reviewed', (e.kind === 'proposal' ? 'suggestion seen' : 'thread seen')); });
    ui.laterOpen = false; ui.datePick = false; ui.rvNote = { id: null, text: '' };
  }
  async function sendPrompt(text) {
    if (!text) return;
    // An add line makes the task at once (stage 6), never a tray command. The Chat tab's box keeps its behaviour.
    // The Send handler has emptied the box: what is there now was typed since, so the clear leaves it, and a line
    // not added goes back ahead of it.
    if (ui.tab !== 'chat' && ADD_RE.test(text)) {
      const id = await addTask({ text, verb: true }, { box: 'cmd', clear: () => {} });
      if (id == null) { draft.cmd = draft.cmd.trim() ? text + '\n' + draft.cmd : text; render(); }
      return;
    }
    let res;
    try { res = await api('/api/commands', { method: 'POST', body: JSON.stringify({ text }) }); }
    catch (e) {
      // Not sent (a restarting server, no network, a sign-out): the text goes back in the box, ahead of
      // anything typed since, so nothing is lost. A sign-out has its own screen; its reload keeps the box.
      draft.cmd = draft.cmd.trim() ? text + '\n' + draft.cmd : text;
      render();
      if (!(e && e.message === 'signed out')) toast('Not sent: the server did not answer');
      return;
    }
    if (res.is_command) {
      res.changes.forEach(c => stageChange(c, c.op === 'set' ? Object.entries(c.fields).map(([k, v]) => `${k} ${v || 'cleared'}`).join(', ') : c.op));
      toast(`Staged ${res.changes.length} change${res.changes.length === 1 ? '' : 's'}`);
    } else if (ui.tab !== 'chat') {      // plain words under the box, on this tab (stage 6); sendTurn gives them back if a turn runs
      const pl = placeOf('today', null);
      if (!pl.loaded) await loadPlace(pl);
      sendTurn(pl, text, null, 'cmd');
    } else if (chat.busy) {      // not a command, and a turn is running: give the text back instead of dropping it
      draft.cmd = text; toast('A chat turn is still running · your prompt is kept in the box'); render();
    } else {
      sendChat(text, null, attachedIds());
    }
  }
  // A Settings control's value as it would be saved; undefined for a number box that holds no number.
  function controlValue(el) {
    if (el.type === 'checkbox') return el.checked;
    if (el.type === 'number') { const n = Number(el.value); return el.value === '' || Number.isNaN(n) ? undefined : n; }
    return el.value;
  }
  // The save's render() rebuilds the panel from settings, and a change event comes only on blur, so the box
  // being typed in next (Tab from one field to the next) would lose its text and focus: take them before the
  // render, put them back after. Read just before the render, since typing goes on while the request runs.
  function heldSettingsBox() {
    const a = document.activeElement, panel = document.getElementById('panel');
    if (!a || !a.id || !panel.contains(a) || !a.matches('input, textarea, select')) return null;
    let s = null, e = null; try { s = a.selectionStart; e = a.selectionEnd; } catch (_) {}
    return { id: a.id, value: a.value, typed: a.matches('textarea, input:not([type=checkbox]):not([type=radio])'), s, e };
  }
  function restoreSettingsBox(held) {
    if (!held) return;
    const el = document.getElementById(held.id); if (!el) return;
    el.focus({ preventScroll: true });
    // Focus first, then the text: set while focused, the box still counts it as changed, so leaving it fires change.
    if (held.typed && el.value !== held.value) el.value = held.value;
    if (held.s !== null) try { el.setSelectionRange(held.s, held.e); } catch (_) {}
  }
  // Sends what changed and nothing else: the dirty keys, and the accounts table only when an account changed.
  async function saveSettings() {
    clearTimeout(saveSettings.t);
    if (!settingsDirty.size) { toast('Nothing to save'); return; }
    const snap = new Map(settingsDirty); settingsDirty.clear();
    const values = {};
    for (const [k, v] of snap) if (k !== '#accounts') values[k] = v;
    const body = { values };
    let waiting = false;      // a row still being typed: the server's table stays until it has an address
    if (snap.has('#accounts')) {
      if (settings.accounts.some(a => !String(a.address || '').trim())) { waiting = true; settingsDirty.set('#accounts', true); }
      else body.accounts = settings.accounts.map(a => ({ address: a.address, display: a.display, roles: a.roles, enabled: a.enabled }));
    }
    try {
      const kept = settings.accounts;
      settings = await api('/api/settings', { method: 'PUT', body: JSON.stringify(body) });
      // The page's rows, a half-typed one included, over the server's; also when an account changed during the request.
      if (!body.accounts || settingsDirty.has('#accounts')) settings.accounts = kept;
      toast(waiting ? 'Settings saved · the new account row needs an address' : 'Settings saved');
      const held = heldSettingsBox();
      render(); restoreSettingsBox(held);
    } catch (e) {
      for (const [k, v] of snap) if (!settingsDirty.has(k)) settingsDirty.set(k, v);      // a newer change made meanwhile wins
      toast('Save failed: ' + e.message);
    }
  }

  document.getElementById('tabs').addEventListener('click', e => { const b = e.target.closest('button'); if (!b) return; go(b.dataset.tab); });
  window.addEventListener('popstate', applyAddress);
  document.addEventListener('keydown', e => {
    if (!document.getElementById('signedout').hidden) return;      // signed out: no key acts on the app behind the screen
    if (e.key === 'Escape') {   // Esc: deselect everything; in Chat the selection is what a prompt carries, so it only closes the task picker
      lastTap = { id: null, at: 0 };
      if (ui.sheet) { closeSheet(); return; }      // the New task sheet closes first; what is under it stays as it was
      if (ui.page !== null) history.back();      // the task page closes like the phone's Back
      else if (ui.findOpen && LIST_TABS.includes(ui.tab)) { ui.findOpen = false; render(); }
      else if (ui.tab === 'chat') { if (ui.pickOpen) { ui.pickOpen = false; render(); } }
      else if (ui.sel.size) { ui.sel.clear(); render(); }
      if (e.target.matches('input, select, textarea')) e.target.blur(); return;
    }
    if (e.target.matches('input, select, textarea')) return;
    if (ui.sheet) return;      // the sheet covers the list: no key acts on what is under it
    if (e.key === '/' && !e.ctrlKey && !e.metaKey && !e.altKey && ui.page === null && LIST_TABS.includes(ui.tab)) { e.preventDefault(); openFind(); return; }
    if (ui.page !== null) return;      // the task page covers the tab: no key acts on what is under it
    // Before the returns below: Review has its own keys, and an empty list has no rows (and says "Press n").
    if (e.key === 'n' && !e.ctrlKey && !e.metaKey && !e.altKey) { e.preventDefault(); openNew(); return; }      // a phone's hardware keyboard opens the sheet
    if (ui.tab === 'review') { const map = { d: 'done', k: 'keep', s: 'later', x: 'drop', t: 'top', l: 'longterm' }; if (map[e.key]) { e.preventDefault(); reviewAct(map[e.key]); } return; }
    const visible = [...document.querySelectorAll('#panel .row')].map(r => +r.dataset.id); if (!visible.length) return;
    const i = visible.indexOf(ui.focus);
    // j / k walk the rows as drawn: a task drawn twice (the Linked view, Timeline's sections) has two places, and
    // the cursor is the place (focusAt), so it moves past the first copy; d, x, s, e act on the focused task.
    const at = ui.focusAt != null && visible[ui.focusAt] === ui.focus ? ui.focusAt : i;
    const step = n => { ui.focusAt = n; ui.focus = visible[n]; render(); };
    if (e.key === 'j') step(Math.min(visible.length - 1, at + 1));
    else if (e.key === 'k') step(Math.max(0, at - 1));
    // i < 0: the focused row is in a folded section or on another tab; a key must never act on a row that is not on screen
    else if (e.key === 'e' && i >= 0) { ui.open.has(ui.focus) ? ui.open.delete(ui.focus) : ui.open.add(ui.focus); render(); }
    else if (i >= 0 && { d: 'done', x: 'drop', s: 'snooze', t: 'top', l: 'longterm' }[e.key]) { act(ui.focus, { d: 'done', x: 'drop', s: 'snooze', t: 'top', l: 'longterm' }[e.key]); }
    else return;
    e.preventDefault();
  });
  window.addEventListener('resize', () => { const bar = document.querySelector('header.bar'); if (bar) document.documentElement.style.setProperty('--bar-h', bar.offsetHeight + 'px'); bottomRule(); });
  document.addEventListener('visibilitychange', onReturn);
  load().then(() => { applyAddress(); registerWorker(); return loadSettings(); }).then(loadPush).catch(e => { document.getElementById('panel').innerHTML = `<div class="empty">Could not load /api/tasks: ${esc(e.message)}</div>`; });
})();
