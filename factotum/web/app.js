/* Factotum UI (phase 1). Talks only to /api. Pending changes live in this tab until Apply. */
(function () {
  'use strict';
  const PLAUD = 'https://web.plaud.ai/file/';
  const NOTION = 'https://app.notion.com/p/';
  const TABS = ['today', 'timeline', 'projects', 'review', 'chat', 'log', 'settings'];

  let data = { tasks: [], projects: [], today: new Date().toISOString().slice(0, 10), top_cap: 7 };
  let review = null;        // /api/review payload
  let settings = null;      // /api/settings payload
  let staged = new Map();   // task id -> {op, fields, label}
  let lastBatch = null;
  let lastTap = { id: null, at: 0 };   // for the hand-rolled double-click on rows
  let ui = { tab: 'today', focus: null, sel: new Set(), open: new Set(), reviewIdx: 0, laterOpen: false, reviewPaused: false, rvNote: { id: null, text: '' }, remOpen: null, dueOpen: null };
  try { const h = location.hash.replace('#', ''); if (TABS.includes(h)) ui.tab = h; } catch (_) {}
  try { const s = JSON.parse(localStorage.getItem('factotum.staged') || '[]'); staged = new Map(s); } catch (_) {}
  for (const [k, v] of staged) { if (!v || typeof v !== 'object') staged.delete(k); else if (v.task_id == null) v.task_id = typeof k === 'number' ? k : parseInt(k, 10); }  // trays saved before entries carried task_id

  const TODAY = () => new Date(data.today + 'T12:00:00');
  const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const byId = id => data.tasks.find(t => t.id === id);
  const api = (path, opts) => fetch(path, Object.assign({ headers: { 'Content-Type': 'application/json' } }, opts)).then(r => { if (!r.ok) throw new Error(r.status); return r.json(); });

  function view(t) {
    const st = staged.get(t.id), due = staged.get(t.id + ':due');
    if (!st && !due) return t;
    const v = Object.assign({}, t, due ? due.fields : {}, st ? st.fields : {});
    if (!st) return v;
    if (st.op === 'done') v.status = 'done';
    if (st.op === 'drop') v.status = 'dropped';
    if (st.op === 'pin_top') v.top_rank = v.top_rank || 99;
    if (st.op === 'unpin') v.top_rank = null;
    if (st.op === 'longterm') v.longterm = 1;
    return v;
  }
  const openOnes = () => data.tasks.map(view).filter(t => t.status === 'open');
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

  // ---------- loading ----------
  async function load() {
    data = await api('/api/tasks?status=open');
    document.getElementById('today-label').textContent = '· ' + TODAY().toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
    render();
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
  // An attribute edit (due date) is staged under "<id>:due", beside the task's decision if any.
  function stageExtra(key, task_id, op, fields, label) {
    staged.set(key, { task_id, op, fields, label });
    try { localStorage.setItem('factotum.staged', JSON.stringify([...staged])); } catch (_) {}
    render();
  }
  function stageDue(id, due) { stageExtra(id + ':due', id, 'set', { due: due || null }, due ? 'due ' + due : 'due cleared'); toast('Due date staged · press Apply in the tray to save it'); }
  function dueForm(id, current) {
    const st = staged.get(id + ':due');
    if (st) return `<span class="chip staged">${esc(st.label)}</span><button class="btn small" data-due-clear="${id}">Undo</button>`;
    if (ui.dueOpen !== id) return `<button class="btn small" data-due-open="${id}">📅 ${current ? 'Change due' : 'Set due'}</button>`;
    return `<span class="remform" data-due-form="${id}"><input type="date" value="${esc((current || '').slice(0, 10))}" aria-label="Due date"><button class="btn small" data-due-set="${id}">Set</button>${current ? `<button class="btn small" data-due-none="${id}">Clear due</button>` : ''}<button class="btn small" data-due-cancel="${id}">Cancel</button></span>`;
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
    };
    (ops[kind] || (() => {}))();
  }
  async function applyAll() {
    const changes = [...staged.values()].map(s => ({ task_id: s.task_id, op: s.op, fields: s.fields }));
    if (!changes.length) return;
    try {
      const res = await api('/api/batches/apply', { method: 'POST', body: JSON.stringify({ source: ui.tab === 'review' ? 'review' : 'ui', changes }) });
      lastBatch = res.batch_id;
      staged = new Map(); try { localStorage.removeItem('factotum.staged'); } catch (_) {}
      ui.sel.clear(); logData = null;
      await load();
      if (ui.tab === 'review') {
        // The decisions are in; today counts as reviewed (phase 2 skips the push), and the next
        // page waits for a click instead of appearing under the user's hands.
        await api('/api/review/finish', { method: 'POST' });
        await loadReview();
        ui.reviewPaused = true; render();
      }
      toast(`${res.applied} change${res.applied === 1 ? '' : 's'} applied` + (res.skipped.length ? ` · ${res.skipped.length} skipped` : ''), true);
    } catch (e) { toast('Apply failed: ' + e.message); }
  }
  function toast(msg, undo) {
    const el = document.getElementById('toast');
    el.innerHTML = esc(msg) + (undo && lastBatch ? ' <button id="undo">Undo</button>' : '');
    el.classList.add('on');
    clearTimeout(toast.t); toast.t = setTimeout(() => el.classList.remove('on'), 8000);
    const u = document.getElementById('undo');
    if (u) u.onclick = async () => { await api(`/api/batches/${lastBatch}/undo`, { method: 'POST' }); lastBatch = null; el.classList.remove('on'); await load(); };
  }

  // ---------- rendering ----------
  function render() {
    renderRail();
    const p = document.getElementById('panel');
    const fns = { today: renderToday, timeline: renderTimeline, projects: renderProjects, review: renderReview, chat: renderChat, log: renderLog, settings: renderSettings };
    p.innerHTML = fns[ui.tab]();
    document.querySelectorAll('#tabs button').forEach(b => b.setAttribute('aria-selected', b.dataset.tab === ui.tab));
    document.getElementById('n-today').textContent = openOnes().filter(x => x.due && fmtDue(x.due).days <= 1).length || '';
    document.getElementById('n-review').textContent = review && review.total ? review.total : '';
    document.getElementById('remind').innerHTML = settings && settings.effective['notify.review_time']
      ? `<span>Review at</span><span class="pill">${esc(settings.effective['notify.review_time'])}</span>` : '';
    renderTray();
    wire();
  }
  function renderRail() {
    const top = openOnes().filter(x => x.top_rank).sort((a, b) => a.top_rank - b.top_rank);
    document.getElementById('rail').innerHTML = `<h2>Top <span>${top.length} of ${data.top_cap}</span></h2>
      <ol id="rail-list">${top.map(x => { const d = fmtDue(x.due); return `<li draggable="true" data-id="${x.id}"><span class="grip">⋮⋮</span><span class="t" title="${esc(x.name)}">${esc(x.name)}</span>${d ? `<span class="due">${d.label}</span>` : ''}</li>`; }).join('')}</ol>
      <div class="drop" id="rail-drop">Drag a row here, or press <b>t</b></div>`;
  }
  function attachedIds() { return ui.sel.size ? [...ui.sel] : (ui.focus ? [ui.focus] : []); }
  function composer() {
    const ids = attachedIds();
    return `<div class="composer"><div class="attach">${ids.length ? 'Attached: ' + ids.map(id => `<span class="chip">Task-${id}</span>`).join('') : 'Nothing attached · tap a row to attach it'}</div>
      <div class="cmd"><input id="cmd" placeholder="Command: done 389 392 · push 364 fri · top 23 · longterm 38   (free text → Chat, phase 5)" aria-label="Prompt or command"><button class="btn primary" id="cmd-go">Send</button></div>
      <p class="hint">Tap a row to select it (the bulk bar appears) · <kbd>j</kbd><kbd>k</kbd> move · <kbd>d</kbd> done · <kbd>x</kbd> drop · <kbd>s</kbd> snooze · <kbd>t</kbd> top · <kbd>l</kbd> long-term · <kbd>e</kbd> details · double-click a row for details · <kbd>Esc</kbd> deselect</p></div>
      <div class="bulk" id="bulk"><b id="bulk-n"></b> selected
        <button class="btn small" data-bulk="done">Done</button><button class="btn small" data-bulk="drop">Drop</button>
        <button class="btn small" data-bulk="snooze">Snooze to tomorrow</button>
        <span class="remform"><input type="date" id="bulk-date" aria-label="Due date"><button class="btn small" data-bulk="setdue">Set due</button><button class="btn small" data-bulk="cleardue">Clear due</button></span>
        <button class="btn small" data-bulk="top">Pin to Top</button><button class="btn small" data-bulk="longterm">Long-term</button>
        <span class="spacer"></span><button class="btn small" data-bulk="clear">Clear</button></div>`;
  }
  function group(title, rows, cls, tag) {
    if (!rows.length) return '';
    return `<section class="group"><h3 class="${cls || ''}">${title}<span class="n">${rows.length}</span></h3><div class="rows">${rows.map(x => row(x, tag ? tag(x) : '')).join('')}</div></section>`;
  }
  function row(x, extraChip) {
    const d = fmtDue(x.due);
    const st = staged.get(x.id);
    const dueCls = d ? (d.days < 0 ? 'crit' : d.days <= 1 ? 'warn' : '') : '';
    const fadeDays = x.fade_at ? daysTo(x.fade_at) : null;
    const cls = ['row', x.status === 'done' ? 'done' : '', x.status === 'dropped' ? 'drop' : '', ui.open.has(x.id) ? 'open' : '', ui.sel.has(x.id) ? 'sel' : '', ui.focus === x.id ? 'focus' : ''].join(' ');
    const chips = [
      x.top_rank ? `<span class="chip top">Top ${x.top_rank}</span>` : '',
      x.longterm_effective ? `<span class="chip lt">Long-term</span>` : '',
      fadeDays !== null && fadeDays <= 7 && !x.top_rank ? `<span class="chip fade">fades ${fadeDays <= 0 ? 'now' : 'in ' + fadeDays + 'd'}</span>` : '',
      (x.reminders || []).length ? `<span class="chip rem">⏰ ${fmtWhen(x.reminders[0].fire_at)}</span>` : '',
      `<span class="chip proj">${esc(x.project || '—')}</span>`,
      extraChip || '',
      d ? `<span class="chip due ${dueCls} ${x.hard ? 'hard' : ''}" data-due="${x.id}" title="${x.hard ? 'Hard deadline' : 'Soft date'} · click to cycle">${d.label}</span>` : `<span class="chip due" data-due="${x.id}" title="No date · click to set">no date</span>`,
      `<span class="chip eff">${esc(x.effort || '?')}</span>`,
      st ? `<span class="chip staged">${esc(st.label)}</span>` : '',
    ].join('');
    const links = (x.links || []).map(l => {
      if (l.kind === 'plaud_note') return `<a href="https://web.plaud.ai/file/${esc(l.ref)}" target="_blank" rel="noopener" title="${esc(l.label || '')}">Plaud</a>`;
      if (l.kind === 'note_page') return `<a href="${esc(l.ref)}" target="_blank" rel="noopener" title="${esc(l.label || '')}">Note</a>`;
      if (l.kind === 'calendar') return `<span class="chip">📅 ${esc(l.label || 'event')}</span>`;
      if (l.kind === 'notion_page') return `<a href="${NOTION}${l.ref.replace(/-/g, '')}" target="_blank" rel="noopener">Notion row</a>`;
      if (l.kind === 'mail_thread' || l.kind === 'mail_message') return `<span class="chip">✉ ${esc(l.label || l.kind)}</span>`;
      return `<a href="${esc(l.ref)}" target="_blank" rel="noopener">${esc(l.label || l.kind)}</a>`;
    }).join('');
    const srcLink = x.source_link ? `<a href="${esc(x.source_link)}" target="_blank" rel="noopener">${x.source === 'Plaud' ? 'Plaud note' : x.source === 'Canvas' ? 'Canvas' : 'Source'}</a>` : '';
    return `<div class="${cls}" data-id="${x.id}">
      <div class="main">
        <input type="checkbox" id="ck-${x.id}" aria-label="Select" ${ui.sel.has(x.id) ? 'checked' : ''}>
        <div class="title"><span class="code">Task-${x.id}</span><span class="name">${esc(x.name)}</span></div>
        <div class="meta">${chips}<button class="chev" aria-label="Details" data-chev="${x.id}"></button></div>
      </div>
      <div class="details">
        <dl><dt>Score</dt><dd>${x.score} · ${x.priority} · ${esc((x.score_reasons || []).join(' · '))}</dd>
            <dt>Why now</dt><dd>${esc(x.why_now) || '<span class="code">none yet</span>'}</dd>
            <dt>Context</dt><dd>${esc(x.context) || '<span class="code">none</span>'}</dd></dl>
        <dl><dt>Source</dt><dd>${esc(x.source || '?')}${x.account ? ' · ' + esc(x.account) : ''} · ${x.mentions} mention${x.mentions === 1 ? '' : 's'} · created ${esc((x.created_at || '').slice(0, 10))}${x.legacy_status ? ' · was ' + esc(x.legacy_status) : ''}</dd>
            <dt>Links</dt><dd class="links">${srcLink}${links || (srcLink ? '' : '<span class="code">none</span>')}</dd>
            <dt>Lifecycle</dt><dd>${x.tier}${x.fade_at ? ' · fades ' + x.fade_at : ''}${x.last_call_at ? ' · last call ' + x.last_call_at : ''}${x.snoozed_until ? ' · snoozed until ' + x.snoozed_until : ''}</dd>
            <dt>Reminders</dt><dd>${(x.reminders || []).map(r => `<span class="chip rem">${fmtWhen(r.fire_at)}${r.repeat ? ' · ' + esc(r.repeat) : ''} ${esc(r.label || '')}</span>`).join(' ') || '<span class="code">none</span>'}</dd></dl>
        <div class="actions">
          <button class="btn small" data-act="done" data-id="${x.id}">Done</button>
          <button class="btn small" data-act="drop" data-id="${x.id}">Drop</button>
          <button class="btn small" data-act="snooze" data-id="${x.id}">Snooze to tomorrow</button>
          <button class="btn small" data-act="top" data-id="${x.id}">${x.top_rank ? 'Unpin' : 'Pin to Top'}</button>
          <button class="btn small" data-act="longterm" data-id="${x.id}">${x.longterm ? 'Clear long-term' : 'Mark long-term'}</button>
          ${dueForm(x.id, x.due)}
          ${reminderForm(x.id)}
          <span class="spacer"></span>
          <button class="btn small" disabled title="Phase 5">✉ Search related emails</button>
        </div></div></div>`;
  }
  function renderToday() {
    const o = openOnes().filter(x => !(x.snoozed_until && x.snoozed_until > data.today));   // Later = hidden until its date
    const by = f => o.filter(f);
    const overdueHard = by(x => x.due && x.hard && fmtDue(x.due).days < 0);
    const today = by(x => x.due && fmtDue(x.due).days === 0);
    const tomorrow = by(x => x.due && fmtDue(x.due).days === 1);
    const week = by(x => x.due && fmtDue(x.due).days > 1 && fmtDue(x.due).days <= 7);
    const starting = by(x => (x.reminders || []).some(r => daysTo(r.fire_at.slice(0, 10)) <= 1) && !today.includes(x) && !tomorrow.includes(x));
    return composer() + group('Overdue · hard deadlines', overdueHard, 'crit') + group('Due today', today, 'warn') + group('Due tomorrow', tomorrow) +
      group('Starting soon · reminders set', starting) + group('Rest of the week', week) +
      (o.length ? '' : '<div class="empty">No open tasks. Run <code>python -m factotum import-notion --apply</code> or add some.</div>') +
      `<div class="note">Soft dates that slipped, new items, and fading backlog are not here. They wait in <b>Review</b> so this screen stays short.</div>`;
  }
  const RECENT_CAP = 30;
  function renderTimeline() {
    const o = openOnes();
    const dd = x => x.due ? fmtDue(x.due).days : null;
    const sortDue = (a, b) => (a.due || '').localeCompare(b.due || '');
    return composer() + group('Overdue', o.filter(x => dd(x) !== null && dd(x) < 0).sort(sortDue), 'crit') + group('Today', o.filter(x => dd(x) === 0), 'warn') +
      group('This week', o.filter(x => dd(x) > 0 && dd(x) <= 7).sort(sortDue)) + group('Next week', o.filter(x => dd(x) > 7 && dd(x) <= 14).sort(sortDue)) +
      group('Later', o.filter(x => dd(x) > 14).sort(sortDue)) + group('Undated · long-term', o.filter(x => dd(x) === null && x.longterm_effective)) +
      group('Undated · backlog', o.filter(x => dd(x) === null && !x.longterm_effective).sort((a, b) => (b.score || 0) - (a.score || 0))) +
      // Newest first, by creation date: the place to look for what a recording or sweep just added.
      group('Recently added · newest first', o.slice().sort((a, b) => (b.created_at || '').localeCompare(a.created_at || '')).slice(0, RECENT_CAP), '',
        x => `<span class="chip" title="Created ${esc(x.created_at || '')}">added ${x.created_at ? new Date(x.created_at).toLocaleDateString([], { month: 'short', day: 'numeric' }) : '?'}</span>`);
  }
  function renderProjects() {
    const o = openOnes();
    const m = new Map();
    o.forEach(x => { const k = x.project || '—'; if (!m.has(k)) m.set(k, []); m.get(k).push(x); });
    return composer() + [...m.entries()].sort((a, b) => b[1].length - a[1].length).map(([p, rows]) => {
      const fading = rows.filter(x => x.fade_at && daysTo(x.fade_at) <= 7).length;
      const due = rows.filter(x => x.due && fmtDue(x.due).days <= 7).length;
      return `<div class="proj-group ${ui.open.has('p:' + p) ? 'open' : ''}"><button data-proj="${esc(p)}">${esc(p)} <span class="n">${rows.length} open · ${due} due this week</span>${fading ? `<span class="warnn">${fading} fading</span>` : ''}</button><div class="rows">${rows.sort((a, b) => (a.due || '9').localeCompare(b.due || '9')).map(row).join('')}</div></div>`;
    }).join('');
  }
  // One page of the queue. Ids already staged in the tray are excluded server-side, so a page is
  // always `cap` fresh items and the counter counts this page only.
  async function loadReview() {
    const exclude = [...staged.keys()].filter(k => typeof k === 'number').join(',');
    review = await api('/api/review' + (exclude ? '?exclude=' + exclude : ''));
    if (review.total === undefined) review.total = review.items.length;   // older server
    ui.reviewIdx = 0; ui.reviewPaused = false; ui.laterOpen = false; render();
  }
  function renderReview() {
    if (!review) { loadReview(); return '<div class="empty">Loading review queue…</div>'; }
    const items = review.items.filter(i => !staged.has(i.task.id));
    const total = review.items.length;
    const remaining = ui.reviewPaused ? review.total : review.total - total;
    if (ui.reviewPaused || !items.length) {
      const more = remaining > 0 ? `<p><button class="btn" id="review-more">Review another ${Math.min(review.cap, remaining)} of the remaining ${remaining}</button></p>` : '';
      const head = ui.reviewPaused ? (remaining ? 'Batch applied.' : 'Batch applied. Nothing left to review.') : total ? `${staged.size} decision${staged.size === 1 ? '' : 's'} staged.` : 'Nothing to review.';
      const body = ui.reviewPaused ? '' : total ? '<p>Apply them in the tray, or keep going.</p>' : '';
      return `<div class="review"><div class="finish"><h2>${head}</h2>${body}${more}${review.last_review_at ? `<p class="hint">Last review: ${review.last_review_at}</p>` : ''}</div></div>`;
    }
    const { task: x, reasons, primary } = items[0];
    const d = fmtDue(x.due);
    const cls = primary === 'last_call' ? 'crit' : primary === 'new' || primary === 'back' ? 'new' : 'warn';
    const idx = total - items.length;
    const note = ui.rvNote.id === x.id ? ui.rvNote.text : '';
    return `<div class="review">
      <div class="progress"><span>${idx + 1} of ${total}</span><div class="track"><div class="fill" style="width:${Math.round(100 * idx / total)}%"></div></div><span>~${Math.max(1, Math.ceil(items.length * 12 / 60))} min left</span></div>
      <div class="card" data-id="${x.id}">
        <div class="why ${cls}">${esc(review.labels[primary])}${reasons.length > 1 ? ' · also ' + reasons.slice(1).map(r => review.labels[r]).join(', ') : ''}</div>
        <h2>${esc(x.name)}</h2>
        <div class="sub"><span class="code">Task-${x.id}</span><span class="chip proj">${esc(x.project || '—')}</span>${d ? `<span class="chip due ${d.days < 0 ? 'crit' : ''} ${x.hard ? 'hard' : ''}">${d.label}</span>` : '<span class="chip">no date</span>'}<span class="chip eff">${esc(x.effort || '?')}</span><span class="chip">${x.mentions} mention${x.mentions === 1 ? '' : 's'}</span><span class="chip">score ${x.score}</span></div>
        <p>${esc(x.why_now || (x.score_reasons || []).join(' · '))}</p>${x.context ? `<p class="ctx">${esc(x.context)}</p>` : ''}
        <textarea id="rv-note" rows="2" placeholder="Note to save with this decision (optional) — appended to the task's context">${esc(note)}</textarea>
        <div class="links">${x.source_link ? `<a href="${esc(x.source_link)}" target="_blank" rel="noopener">${x.source === 'Plaud' ? 'Plaud note' : 'Source'}</a>` : ''}</div>
        <div class="big"><button class="done" data-rv="done">Done <kbd>d</kbd></button><button data-rv="keep">Keep <kbd>k</kbd></button><button class="later" data-rv="later">Later <kbd>s</kbd></button><button class="drop" data-rv="drop">Drop <kbd>x</kbd></button></div>
        <div class="later-menu ${ui.laterOpen ? 'on' : ''}"><span class="hint">Comes back on:</span><button class="btn small" data-rv="tomorrow">Tomorrow</button><button class="btn small" data-rv="fri">Friday</button><button class="btn small" data-rv="nextweek">Next week</button><input type="date" id="rv-date" min="${data.today}" aria-label="Pick a date"></div>
        <div class="extra"><button class="btn small" data-rv="top">★ Pin to Top</button><button class="btn small" data-rv="longterm">Mark long-term (never fades)</button>${dueForm(x.id, x.due)}${reminderForm(x.id)}</div>
      </div>
      <div class="note">Keep = no change; the item will not be asked about again until something changes. Later = hidden until the date you pick, then back in Review and Today. Decisions stage into the tray; Apply writes them all at once.</div></div>`;
  }
  function renderChat() {
    return `<div class="note">Chat arrives in phase 5: a prompt with the attached tasks as context, one headless Claude run with Mailbird read and draft tools, proposals you stage into the tray. See SPEC.md §8.3.</div>` + composer();
  }
  let logData = null;
  async function loadLog() { logData = await api('/api/log'); render(); }
  function renderLog() {
    if (!logData) { loadLog(); return '<div class="empty">Loading log…</div>'; }
    const when = iso => iso ? new Date(iso).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : '';
    const val = v => v === null || v === undefined ? '—' : typeof v === 'object' ? esc(JSON.stringify(v)) : esc(String(v));
    const batches = logData.batches.map(b => `<div class="logb ${b.undone_at ? 'undone' : ''}">
        <div class="loghead"><b>#${b.id}</b> <span class="chip">${esc(b.source)}</span> <span>${when(b.applied_at || b.created_at)}</span> <span>${b.changes.length} change${b.changes.length === 1 ? '' : 's'}</span>
          ${b.undone_at ? `<span class="chip">undone ${when(b.undone_at)}</span>` : b.changes.length ? `<span class="spacer"></span><button class="btn small" data-undo="${b.id}">Undo</button>` : ''}</div>
        ${b.changes.map(c => `<div class="logc"><span class="code">Task-${c.task_id}</span> <span class="name">${esc(c.task_name || '')}</span> <span class="f">${esc(c.field)}</span> ${val(c.old)} → ${val(c.new)}</div>`).join('')}
      </div>`).join('') || '<div class="empty">No batches yet.</div>';
    const runs = logData.runs.length ? `<table class="runs"><tr><th>Run</th><th>Started</th><th>Took</th><th>Cost</th><th>Result</th></tr>${logData.runs.map(r => `<tr class="${r.ok ? '' : 'bad'}"><td>${esc(r.kind)}</td><td>${when(r.started_at)}</td><td>${r.duration_ms ? Math.round(r.duration_ms / 1000) + ' s' : ''}</td><td>${r.cost_usd != null ? '$' + Number(r.cost_usd).toFixed(2) : ''}</td><td>${esc(r.error || r.summary || (r.ok ? 'ok' : ''))}</td></tr>`).join('')}</table>`
      : '<div class="note">Nothing has run yet. Reminders, the review push, the expire pass and the morning brief land here when they do something (cost $0.00); Claude runs with their cost start in phase 3.</div>';
    return `<div class="log"><h3>Changes</h3>${batches}<h3>Runs · automations and Claude</h3>${runs}</div>`;
  }
  async function loadSettings() { settings = await api('/api/settings'); render(); }
  function renderSettings() {
    if (!settings) { loadSettings(); return '<div class="empty">Loading settings…</div>'; }
    const e = settings.effective;
    const f = (key, label, sub, control) => `<div class="field"><label for="s-${key}">${label}<span class="sub">${sub}</span></label><div>${control}</div></div>`;
    const num = (key, min, max) => `<input type="number" id="s-${key}" data-key="${key}" value="${e[key]}" min="${min}" max="${max}">`;
    const bool = key => `<input type="checkbox" id="s-${key}" data-key="${key}" ${e[key] ? 'checked' : ''}>`;
    const text = (key, w) => `<input type="text" id="s-${key}" data-key="${key}" value="${esc(e[key] || '')}" style="width:${w || '100%'}">`;
    const time = key => `<input type="time" id="s-${key}" data-key="${key}" value="${esc(e[key] || '')}">`;
    const accounts = (settings.accounts || []).map((a, i) => `<tr><td><input type="text" data-acc="${i}" data-f="address" value="${esc(a.address)}"></td><td><input type="text" data-acc="${i}" data-f="display" value="${esc(a.display || '')}" style="width:90px"></td><td><input type="text" data-acc="${i}" data-f="roles" value="${esc((a.roles || []).join(','))}" style="width:150px"></td><td><input type="checkbox" data-acc="${i}" data-f="enabled" ${a.enabled ? 'checked' : ''}></td><td><button class="btn small" data-accdel="${i}">×</button></td></tr>`).join('');
    return `<div class="settings">
      <div class="note">Overrides are stored in the database. Defaults and everything structural (projects, weights, sources, property names) live in <code>${esc(settings.config_file)}</code>; edit that file to adapt Factotum to another workflow.</div>
      <h3>Review and lifecycle</h3>
      ${f('notify.review_time', 'Review reminder', 'Daily push via ntfy (phase 2). Quiet if you already reviewed today.', time('notify.review_time'))}
      ${f('lifecycle.fade_days', 'Backlog fade', 'Undated items fade after this many quiet days. A new mention resets the clock.', num('lifecycle.fade_days', 7, 120) + ' days')}
      ${f('lifecycle.fade_warn_days', 'Fade warning', 'Shown in Review this many days before fading.', num('lifecycle.fade_warn_days', 1, 14) + ' days')}
      ${f('lifecycle.last_call_days', 'Last call', 'Hard deadlines this far past due go to Review once, then lapse.', num('lifecycle.last_call_days', 1, 30) + ' days')}
      ${f('lifecycle.top_cap', 'Top cap', 'Soft limit on the Top rail.', num('lifecycle.top_cap', 3, 15))}
      ${f('lifecycle.review_cap', 'Review cap', 'Max items per Review session.', num('lifecycle.review_cap', 5, 50))}
      <h3>Inputs</h3>
      ${f('sources.plaud.enabled', 'Plaud recordings', 'IMAP trigger + plaud CLI (phase 3).', bool('sources.plaud.enabled'))}
      ${f('sources.canvas.enabled', 'Canvas', 'Assignments with submission state (phase 4).', bool('sources.canvas.enabled'))}
      ${f('sources.mail.enabled', 'Email via Mailbird', 'Sweeps for replies, waiting flips, linked threads (phase 4).', bool('sources.mail.enabled'))}
      ${f('sources.calendar.enabled', 'Google Calendar', 'Needs config/google-credentials.json (docs/SETUP.md §6).', bool('sources.calendar.enabled') + ' &nbsp; calendar id ' + text('sources.calendar.calendar_id', '160px'))}
      <h3>Email accounts</h3>
      <table><tr><td class="code">address</td><td class="code">label</td><td class="code">roles (monitor, plaud_trigger, brief_recipient)</td><td class="code">on</td><td></td></tr>${accounts}</table>
      <button class="btn small" id="acc-add">+ account</button>
      <h3>Outputs</h3>
      ${f('sinks.notion_notes.enabled', 'Notion notes', 'One page per recording in the Plaud notes database.', bool('sinks.notion_notes.enabled'))}
      ${f('sinks.notion_tasks_mirror.enabled', 'Notion tasks mirror', 'One-way copy of every task row; never read back. Property names in the config file.', bool('sinks.notion_tasks_mirror.enabled'))}
      ${f('notify.ntfy.enabled', 'ntfy push', 'Reminders and the review push.', bool('notify.ntfy.enabled') + ' &nbsp; topic ' + text('notify.ntfy.topic', '220px'))}
      ${f('notify.morning_brief.enabled', 'Morning brief', 'Email at this time: Top, due today, reminders.', bool('notify.morning_brief.enabled') + ' ' + time('notify.morning_brief.time') + ' to ' + text('notify.morning_brief.to', '240px'))}
      <p style="margin-top:16px"><button class="btn primary" id="settings-save">Save settings</button></p>
    </div>`;
  }
  function renderTray() {
    const n = staged.size;
    document.getElementById('tray').classList.toggle('on', n > 0);
    document.getElementById('tray-count').textContent = `${n} change${n === 1 ? '' : 's'}`;
    document.getElementById('tray-list').textContent = [...staged.values()].map(s => `Task-${s.task_id} ${s.label}`).join(' · ');
  }

  // ---------- wiring ----------
  function wire() {
    const panel = document.getElementById('panel');
    panel.querySelectorAll('.row').forEach(r => {
      const id = +r.dataset.id;
      // A plain tap selects (phones have no shift); the checkbox is the same selection. Keyboard
      // focus follows the last tap so j/k keep working.
      const toggleSel = () => { ui.sel.has(id) ? ui.sel.delete(id) : ui.sel.add(id); ui.focus = id; render(); };
      // Two taps on the same row within 400 ms are a double-click: the first tap's selection flip
      // is undone and the details open. Tracked by hand because render() replaces the DOM node
      // between the two clicks, so the browser's dblclick event would not fire.
      r.querySelector('.main').addEventListener('click', e => {
        if (e.target.closest('input, button, .chip.due')) return;
        const now = Date.now();
        if (lastTap.id === id && now - lastTap.at < 400) { lastTap = { id: null, at: 0 }; ui.sel.has(id) ? ui.sel.delete(id) : ui.sel.add(id); ui.open.has(id) ? ui.open.delete(id) : ui.open.add(id); render(); return; }
        lastTap = { id, at: now }; toggleSel();
      });
      r.querySelector('input[type=checkbox]').addEventListener('change', toggleSel);
      r.querySelector('[data-chev]').addEventListener('click', () => { ui.open.has(id) ? ui.open.delete(id) : ui.open.add(id); render(); });
      r.querySelector('.main').setAttribute('draggable', 'true');
      r.querySelector('.main').addEventListener('dragstart', e => e.dataTransfer.setData('text/plain', String(id)));
    });
    panel.querySelectorAll('[data-act]').forEach(b => b.addEventListener('click', () => act(+b.dataset.id, b.dataset.act)));
    panel.querySelectorAll('[data-due]').forEach(c => c.addEventListener('click', () => {
      const id = +c.dataset.due; const x = view(byId(id));
      const order = [isoPlus(1), nextFri(), isoPlus(7), null];
      const i = order.indexOf(x.due ? x.due.slice(0, 10) : null);
      act(id, 'due', order[(i + 1) % order.length]);
    }));
    panel.querySelectorAll('[data-bulk]').forEach(b => b.addEventListener('click', () => {
      const k = b.dataset.bulk; if (k === 'clear') { ui.sel.clear(); render(); return; }
      const d = k === 'setdue' ? (document.getElementById('bulk-date') || {}).value : null;
      if (k === 'setdue' && !d) { toast('Pick a date'); return; }
      [...ui.sel].forEach(id => act(id, k, d)); ui.sel.clear(); render();
    }));
    const bulk = document.getElementById('bulk');
    if (bulk) {
      bulk.classList.toggle('on', ui.sel.size > 0); const n = document.getElementById('bulk-n'); if (n) n.textContent = ui.sel.size;
      const tray = document.getElementById('tray'); bulk.style.bottom = tray.classList.contains('on') ? (tray.offsetHeight + 8) + 'px' : '';
    }
    document.body.classList.toggle('bulk-on', ui.sel.size > 0);
    panel.querySelectorAll('[data-proj]').forEach(b => b.addEventListener('click', () => { const k = 'p:' + b.dataset.proj; ui.open.has(k) ? ui.open.delete(k) : ui.open.add(k); render(); }));
    panel.querySelectorAll('[data-rv]').forEach(b => b.addEventListener('click', () => reviewAct(b.dataset.rv)));
    const rd = document.getElementById('rv-date'); if (rd) rd.onchange = () => reviewAct('date', rd.value);
    const rn = document.getElementById('rv-note'); if (rn) rn.oninput = () => { ui.rvNote = { id: +rn.closest('.card').dataset.id, text: rn.value }; };
    const rm = document.getElementById('review-more'); if (rm) rm.onclick = () => loadReview();
    wireReminders(panel); wireDue(panel);
    panel.querySelectorAll('[data-undo]').forEach(b => b.onclick = async () => { await api(`/api/batches/${b.dataset.undo}/undo`, { method: 'POST' }); await load(); logData = null; toast('Batch undone'); render(); });
    const cmdGo = document.getElementById('cmd-go'); if (cmdGo) cmdGo.onclick = () => { const i = document.getElementById('cmd'); const v = i.value.trim(); i.value = ''; sendPrompt(v); };
    const cmd = document.getElementById('cmd'); if (cmd) cmd.addEventListener('keydown', e => { if (e.key === 'Escape') return; if (e.key === 'Enter') { const v = cmd.value.trim(); cmd.value = ''; sendPrompt(v); } e.stopPropagation(); });
    document.getElementById('tray-apply').onclick = applyAll;
    document.getElementById('tray-discard').onclick = () => { staged = new Map(); try { localStorage.removeItem('factotum.staged'); } catch (_) {} render(); };
    const save = document.getElementById('settings-save'); if (save) save.onclick = saveSettings;
    const accAdd = document.getElementById('acc-add'); if (accAdd) accAdd.onclick = () => { settings.accounts.push({ address: '', display: '', roles: ['monitor'], enabled: true }); render(); };
    panel.querySelectorAll('[data-accdel]').forEach(b => b.onclick = () => { settings.accounts.splice(+b.dataset.accdel, 1); render(); });
    // rail drag & drop
    const list = document.getElementById('rail-list'); const drop = document.getElementById('rail-drop');
    list.querySelectorAll('li').forEach(li => {
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
    if (kind === 'later') { ui.laterOpen = !ui.laterOpen; render(); return; }
    if (kind === 'top' || kind === 'longterm') { act(id, kind); return; }   // extras, not decisions
    // A decision is a review: it sets last_reviewed_at (Keep does so server-side). An optional note
    // is appended to the task's context, dated, in the same change.
    const text = ui.rvNote.id === id ? ui.rvNote.text.trim() : '';
    const extra = text ? { context: (x.context ? x.context + '\n' : '') + data.today + ': ' + text } : {};
    const later = (day, label) => stage(id, 'set', { snoozed_until: day, last_reviewed_at: data.today, ...extra }, 'later · back ' + label);
    if (kind === 'done') stage(id, 'done', extra, 'done');
    else if (kind === 'drop') stage(id, 'drop', extra, 'dropped');
    else if (kind === 'keep') stage(id, 'keep', extra, 'kept');
    else if (kind === 'tomorrow') later(isoPlus(1), 'tomorrow');
    else if (kind === 'fri') later(nextFri(), 'Friday');
    else if (kind === 'nextweek') later(isoPlus(7 - ((TODAY().getDay() + 6) % 7)), 'next week');
    else if (kind === 'date') { if (!dateStr) return; later(dateStr, dateStr); }
    else return;
    ui.laterOpen = false; ui.rvNote = { id: null, text: '' };
  }
  async function sendPrompt(text) {
    if (!text) return;
    const res = await api('/api/commands', { method: 'POST', body: JSON.stringify({ text }) });
    if (res.is_command) {
      res.changes.forEach(c => stage(c.task_id, c.op, c.fields, c.op === 'set' ? Object.entries(c.fields).map(([k, v]) => `${k} ${v || 'cleared'}`).join(', ') : c.op));
      toast(`Staged ${res.changes.length} change${res.changes.length === 1 ? '' : 's'}`);
    } else {
      toast('Not a command. Free-text prompts open Chat in phase 5.');
    }
  }
  async function saveSettings() {
    const values = {};
    document.querySelectorAll('#panel [data-key]').forEach(el => {
      const k = el.dataset.key;
      values[k] = el.type === 'checkbox' ? el.checked : el.type === 'number' ? Number(el.value) : el.value;
    });
    const accounts = settings.accounts.map((a, i) => ({ address: a.address, display: a.display, roles: a.roles, enabled: a.enabled }));
    document.querySelectorAll('#panel [data-acc]').forEach(el => {
      const a = accounts[+el.dataset.acc]; const f = el.dataset.f;
      if (f === 'roles') a.roles = el.value.split(',').map(s => s.trim()).filter(Boolean);
      else if (f === 'enabled') a.enabled = el.checked; else a[f] = el.value;
    });
    try { settings = await api('/api/settings', { method: 'PUT', body: JSON.stringify({ values, accounts }) }); toast('Settings saved'); render(); }
    catch (e) { toast('Save failed: ' + e.message); }
  }

  document.getElementById('tabs').addEventListener('click', e => { const b = e.target.closest('button'); if (!b) return; ui.tab = b.dataset.tab; ui.laterOpen = false; location.hash = ui.tab; render(); });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') { lastTap = { id: null, at: 0 }; if (ui.sel.size) { ui.sel.clear(); render(); } if (e.target.matches('input, select, textarea')) e.target.blur(); return; }   // Esc: deselect everything
    if (e.target.matches('input, select, textarea')) return;
    if (ui.tab === 'review') { const map = { d: 'done', k: 'keep', s: 'later', x: 'drop', t: 'top', l: 'longterm' }; if (map[e.key]) { e.preventDefault(); reviewAct(map[e.key]); } return; }
    const visible = [...document.querySelectorAll('#panel .row')].map(r => +r.dataset.id); if (!visible.length) return;
    const i = visible.indexOf(ui.focus);
    if (e.key === 'j') { ui.focus = visible[Math.min(visible.length - 1, i + 1)]; render(); }
    else if (e.key === 'k') { ui.focus = visible[Math.max(0, i - 1)]; render(); }
    else if (e.key === 'e' && ui.focus) { ui.open.has(ui.focus) ? ui.open.delete(ui.focus) : ui.open.add(ui.focus); render(); }
    else if (ui.focus && { d: 'done', x: 'drop', s: 'snooze', t: 'top', l: 'longterm' }[e.key]) { act(ui.focus, { d: 'done', x: 'drop', s: 'snooze', t: 'top', l: 'longterm' }[e.key]); }
    else return;
    e.preventDefault();
  });
  load().then(loadSettings).catch(e => { document.getElementById('panel').innerHTML = `<div class="empty">Could not load /api/tasks: ${esc(e.message)}</div>`; });
})();
