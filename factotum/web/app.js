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
  let chat = { threads: null, current: null, messages: [], live: null, busy: false, from: null };   // from: {review: id} when a turn was asked from a Review card
  let lastTap = { id: null, at: 0 };   // for the hand-rolled double-click on rows
  let ui = { tab: 'today', focus: null, sel: new Set(), open: new Set(), reviewIdx: 0, laterOpen: false, reviewPaused: false, rvNote: { id: null, text: '' }, remOpen: null, dueOpen: null, rvReply: null };
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
  let pending = [];           // batches a skill staged (/api/proposals): Apply or Dismiss, above every tab
  async function load() {
    data = await api('/api/tasks?status=open');
    try { pending = (await api('/api/proposals')).proposals; } catch (_) { pending = []; }
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
      if (ui.tab !== 'review') review = null;   // a stale Review page would still show the old due date; re-fetch on the next visit
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
    p.innerHTML = pendingHtml() + fns[ui.tab]();
    document.querySelectorAll('#tabs button').forEach(b => b.setAttribute('aria-selected', b.dataset.tab === ui.tab));
    document.getElementById('n-today').textContent = openOnes().filter(x => x.due && fmtDue(x.due).days <= 1).length || '';
    document.getElementById('n-review').textContent = review && (review.total || (review.suggestions || []).length) ? review.total + (review.suggestions || []).filter(x => !x.created).length : '';
    document.getElementById('remind').innerHTML = settings && settings.effective['notify.review_time']
      ? `<span>Review at</span><span class="pill">${esc(settings.effective['notify.review_time'])}</span>` : '';
    renderTray();
    wire();
  }
  function pendingHtml() {
    if (!pending.length) return '';
    return pending.map(p => `<div class="pending" data-pending="${p.id}"><div class="phead"><span class="chip top">Staged by Claude Code</span><b>${esc(p.label || '')}</b><span class="hint" style="margin:0">${new Date(p.created_at).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}</span></div>
      <div class="plist">${p.changes.map(c => { const t = byId(c.task_id); return `<span class="chip">Task-${c.task_id}${t ? ' ' + esc(t.name.slice(0, 40)) : ''} · ${esc(c.op === 'set' ? Object.entries(c.fields || {}).filter(([k]) => k !== 'due_is_datetime').map(([k, v]) => `${k} ${v === null ? 'cleared' : String(v).slice(0, 40)}`).join(', ') : c.op === 'add_reminder' ? 'remind ' + fmtWhen(c.fields.fire_at) : c.op)}</span>`; }).join('')}</div>
      <div class="pact"><button class="btn small primary" data-pending-apply="${p.id}">Apply ${p.changes.length} change${p.changes.length === 1 ? '' : 's'}</button><button class="btn small" data-pending-dismiss="${p.id}">Dismiss</button></div></div>`).join('');
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
      <div class="cmd"><input id="cmd" placeholder="Command: done 389 392 · push 364 fri · top 23 · longterm 38 · or ask Claude in plain words" aria-label="Prompt or command"><button class="btn primary" id="cmd-go">Send</button></div>
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
          <button class="btn small" data-chat-canned="search_mail" data-id="${x.id}" title="A chat turn: finds the threads about this task and offers to link them">✉ Search related emails</button>
          <button class="btn small" data-chat-canned="draft_reply" data-id="${x.id}" title="A chat turn: saves a reply draft in Mailbird, never sends">✉ Draft reply</button>
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
    const sugg = (review.suggestions || []).filter(x => !staged.has('e:' + x.id) && !x.created);
    if (!ui.reviewPaused && !items.length && sugg.length) return suggestionCard(sugg[0], sugg.length);
    if (ui.reviewPaused || !items.length) {
      const more = remaining > 0 ? `<p><button class="btn" id="review-more">Review another ${Math.min(review.cap, remaining)} of the remaining ${remaining}</button></p>` : '';
      const head = ui.reviewPaused ? (remaining ? 'Batch applied.' : 'Batch applied. Nothing left to review.') : total ? `${staged.size} decision${staged.size === 1 ? '' : 's'} staged.` : 'Nothing to review.';
      const body = ui.reviewPaused ? '' : total ? '<p>Apply them in the tray, or keep going.</p>' : '';
      return `<div class="review"><div class="finish"><h2>${head}</h2>${body}${more}${review.last_review_at ? `<p class="hint">Last review: ${review.last_review_at}</p>` : ''}</div></div>`;
    }
    const { task: raw, reasons, primary, flags } = items[0];
    const x = view(raw);   // staged edits (a due date set on this card) show at once, not only after Apply
    const d = fmtDue(x.due);
    const cls = primary === 'last_call' ? 'crit' : primary === 'new' || primary === 'back' ? 'new' : 'warn';
    const idx = total - items.length;
    const note = ui.rvNote.id === x.id ? ui.rvNote.text : '';
    return `<div class="review">
      <div class="progress"><span>${idx + 1} of ${total}</span><div class="track"><div class="fill" style="width:${Math.round(100 * idx / total)}%"></div></div><span>~${Math.max(1, Math.ceil(items.length * 12 / 60))} min left</span></div>
      <div class="card" data-id="${x.id}">
        <div class="why ${cls}">${esc(review.labels[primary])}${reasons.length > 1 ? ' · also ' + reasons.slice(1).map(r => review.labels[r]).join(', ') : ''}${flags && flags.planner ? ` · <b>${esc(flags.planner)}</b>` : ''}</div>
        <h2>${esc(x.name)}</h2>
        <div class="sub"><span class="code">Task-${x.id}</span><span class="chip proj">${esc(x.project || '—')}</span>${d ? `<span class="chip due ${d.days < 0 ? 'crit' : ''} ${x.hard ? 'hard' : ''}">${d.label}</span>` : '<span class="chip">no date</span>'}<span class="chip eff">${esc(x.effort || '?')}</span><span class="chip">${x.mentions} mention${x.mentions === 1 ? '' : 's'}</span><span class="chip">score ${x.score}</span></div>
        <p>${esc(x.why_now || (x.score_reasons || []).join(' · '))}</p>${x.context ? `<p class="ctx">${esc(x.context)}</p>` : ''}
        <textarea id="rv-note" rows="2" placeholder="Note to save with this decision (optional) — appended to the task's context">${esc(note)}</textarea>
        <div class="links">${x.source_link ? `<a href="${esc(x.source_link)}" target="_blank" rel="noopener">${x.source === 'Plaud' ? 'Plaud note' : 'Source'}</a>` : ''}</div>
        <div class="big"><button class="done" data-rv="done">Done <kbd>d</kbd></button><button data-rv="keep">Keep <kbd>k</kbd></button><button class="later" data-rv="later">Later <kbd>s</kbd></button><button class="drop" data-rv="drop">Drop <kbd>x</kbd></button></div>
        <div class="later-menu ${ui.laterOpen ? 'on' : ''}"><span class="hint">Comes back on:</span><button class="btn small" data-rv="tomorrow">Tomorrow</button><button class="btn small" data-rv="fri">Friday</button><button class="btn small" data-rv="nextweek">Next week</button><input type="date" id="rv-date" min="${data.today}" aria-label="Pick a date"></div>
        ${(items[0].evidence || []).filter(e => e.change && !staged.has('e:' + e.id)).map(e => `<div class="extra"><span class="chip">Planner suggests</span><span>${esc(e.label)}${e.reason ? ` <span class="hint" style="margin:0">— ${esc(e.reason)}</span>` : ''}</span><button class="btn small primary" data-rv-accept="${e.id}">Accept</button></div>`).join('')}
        <div class="extra"><button class="btn small" data-rv="top">★ Pin to Top</button><button class="btn small" data-rv="longterm">Mark long-term (never fades)</button>${dueForm(x.id, x.due)}${reminderForm(x.id)}</div>
        ${reviewAsk(x.id)}
      </div>
      <div class="note">Keep = no change; the item will not be asked about again until something changes. Later = hidden until the date you pick, then back in Review and Today. Decisions stage into the tray; Apply writes them all at once.</div></div>`;
  }
  // A planner create with no task behind it: one card, Create or Dismiss (the dismissal is a batch
  // change like any other, so the Log tab undoes it).
  function suggestionCard(sg, n) {
    return `<div class="review">
      <div class="progress"><span>${n} suggestion${n === 1 ? '' : 's'} from the planner</span><div class="track"></div></div>
      <div class="card" data-sugg="${sg.id}">
        <div class="why new">Planner suggests</div>
        <h2>${esc(sg.label.replace(/^create:\s*/i, ''))}</h2>
        ${sg.reason ? `<p class="ctx">${esc(sg.reason)}</p>` : ''}
        <p class="hint">${sg.at ? 'Suggested ' + new Date(sg.at).toLocaleDateString([], { month: 'short', day: 'numeric' }) : ''}</p>
        <div class="big"><button class="done" data-sugg-create="${sg.id}">Create task</button><button class="drop" data-sugg-dismiss="${sg.id}">Dismiss</button></div>
      </div>
      <div class="note">Create makes an open task (source Planner) that shows as New in Review; Dismiss stages a change you can undo from the Log tab.</div></div>`;
  }
  function stageJudge(evidenceId, taskId, verdict, label) { stageExtra('e:' + evidenceId, taskId, 'judge_evidence', { evidence_id: evidenceId, verdict }, label); }
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
  async function loadChatThread(id) { try { const r = await api(`/api/chat/threads/${id}`); chat.current = id; chat.messages = r.messages; } catch (_) { chat.current = null; chat.messages = []; } render(); }
  const propKey = (p, mid, i) => ['done', 'drop', 'pin_top', 'longterm'].includes(p.op) ? p.task_id : p.op === 'add_reminder' ? p.task_id + ':r' : `${p.task_id}:c${mid}-${i}`;
  function stageProposal(p, mid, i) {
    if (p.op === 'create' || p.op === 'link_thread') return false;
    const label = p.label || p.op;
    if (['done', 'drop', 'pin_top', 'longterm'].includes(p.op)) stage(p.task_id, p.op, {}, label);
    else if (p.op === 'add_reminder') stageExtra(p.task_id + ':r', p.task_id, 'add_reminder', p.fields, label);
    else stageExtra(propKey(p, mid, i), p.task_id, 'set', p.fields, label);
    return true;
  }
  function proposalsHtml(m) {
    if (!m.proposals || !m.proposals.length) return '';
    const items = m.proposals.map((p, i) => {
      const key = propKey(p, m.id, i); const isStaged = p.task_id != null && staged.has(key) && staged.get(key).label === (p.label || p.op);
      const ev = (p.evidence || []).map(e => `<span class="chip">${esc(e.label || e.kind)}</span>`).join('');
      const fields = p.fields && Object.keys(p.fields).length && p.op !== 'link_thread' ? `<span class="code">${esc(Object.entries(p.fields).filter(([k]) => k !== 'due_is_datetime').map(([k, v]) => `${k}: ${v === null ? 'cleared' : String(v).slice(0, 60)}`).join(' · '))}</span>` : '';
      let ctl;
      if (p.op === 'create') ctl = p.applied ? `<span class="chip staged">created Task-${p.task_id}</span>` : `<button class="btn small" data-chat-create="${m.id}" data-i="${i}">Create task</button>`;
      else if (p.op === 'link_thread') ctl = `<span class="chip staged">linked</span>`;
      else if (isStaged) ctl = `<span class="chip staged">staged</span>`;
      else ctl = `<label><input type="checkbox" data-chat-prop="${m.id}" data-i="${i}" checked> stage</label>`;
      return `<div class="prop">${ctl}<span class="plabel">${p.task_id != null ? `<span class="code">Task-${p.task_id}</span> ` : ''}${esc(p.label || p.op)}</span>${fields}${ev}</div>`;
    }).join('');
    const stageable = m.proposals.some((p, i) => !['create', 'link_thread'].includes(p.op) && !(staged.has(propKey(p, m.id, i))));
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
    const opts = chat.threads.map(t => `<option value="${t.id}" ${t.id === chat.current ? 'selected' : ''}>${esc(t.title || 'untitled')} · ${new Date(t.last_at || t.created_at).toLocaleDateString([], { month: 'short', day: 'numeric' })}</option>`).join('');
    const head = `<div class="chat-head"><select id="chat-thread" aria-label="Thread">${opts || '<option value="">no threads yet</option>'}</select><button class="btn small" id="chat-new">New thread</button></div>`;
    const body = chat.messages.length || chat.live ? chat.messages.map(messageHtml).join('') + liveHtml()
      : '<div class="note">Ask about your tasks, your mail or your calendar. Attach tasks by selecting rows on another tab first. Everything Claude proposes is staged into the tray; nothing changes until you Apply. Drafts it writes land in Mailbird and are never sent.</div>';
    return `<div class="chat">${head}<div class="msgs" id="msgs">${body}</div>${composer()}</div>`;
  }
  function reviewAsk(id) {
    const live = chat.busy && chat.from && chat.from.review === id ? `<div class="hint"><span class="spin"></span> ${esc((chat.live || {}).status || 'Working…')}</div>` : '';
    const reply = ui.rvReply && ui.rvReply.id === id ? `<div class="rvreply">${md(ui.rvReply.text)}${ui.rvReply.note ? `<div class="hint">${esc(ui.rvReply.note)}</div>` : ''}<div class="hint"><a href="#chat" data-goto-chat="${ui.rvReply.thread}">Open in Chat</a></div></div>` : '';
    return `<div class="cmd rvask"><input id="rv-ask" placeholder="Ask Claude about this task — e.g. “add that the first pass is done”; its proposals stage with this decision" aria-label="Ask Claude"><button class="btn small" id="rv-ask-go" ${chat.busy ? 'disabled' : ''}>Ask</button></div>${live}${reply}`;
  }
  // One turn: POST, then read the server-sent events until done. From a Review card (`from.review`)
  // the tab stays put and the proposals about that task are staged straight into the tray.
  async function sendChat(text, canned, attached, from) {
    if (chat.busy) { toast('A chat turn is already running'); return; }
    chat.busy = true; chat.live = { status: 'Starting…', texts: [] }; chat.from = from || null;
    if (!from) { ui.tab = 'chat'; location.hash = 'chat'; if (!chat.threads) { try { const r = await api('/api/chat/threads'); chat.threads = r.threads; } catch (_) { chat.threads = []; } } }
    const threadId = from ? null : chat.current;    // a Review question starts its own thread
    if (!from) chat.messages = [...chat.messages, { role: 'user', text: text || (canned === 'search_mail' ? 'Search related emails' : 'Draft a reply'), attached }];
    render();
    let result = null, error = null;
    try {
      const resp = await fetch('/api/chat/turn', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ thread_id: threadId, text, attached, canned }) });
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const reader = resp.body.getReader(); const dec = new TextDecoder(); let buf = '';
      for (;;) {
        const { value, done } = await reader.read(); if (done) break;
        buf += dec.decode(value, { stream: true });
        let i; while ((i = buf.indexOf('\n\n')) >= 0) {
          const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
          const line = chunk.split('\n').find(l => l.startsWith('data:')); if (!line) continue;
          const ev = JSON.parse(line.slice(5));
          if (ev.type === 'meta') { if (!from) chat.current = ev.thread_id; }
          else if (ev.type === 'status') { chat.live.status = ev.text; render(); }
          else if (ev.type === 'text') { chat.live.texts.push(ev.text); render(); }
          else if (ev.type === 'done') result = ev.result;
          else if (ev.type === 'error') error = ev.error;
        }
      }
    } catch (e) { error = e.message; }
    chat.busy = false; chat.live = null;
    if (from && from.review) {
      if (result) {
        let n = 0; (result.proposals || []).forEach((p, i) => { if (p.task_id === from.review && stageProposal(p, result.message_id, i)) n++; });
        const other = (result.proposals || []).filter(p => p.task_id !== from.review && p.op !== 'link_thread').length;
        ui.rvReply = { id: from.review, text: result.reply_md, thread: result.thread_id, note: (n ? `${n} proposal${n === 1 ? '' : 's'} staged with this decision.` : 'Nothing to stage.') + (other ? ` ${other} about other tasks are in the Chat tab.` : '') };
        toast(n ? `${n} proposal${n === 1 ? '' : 's'} staged · $${Number(result.cost_usd || 0).toFixed(2)}` : `Answered · $${Number(result.cost_usd || 0).toFixed(2)}`);
      } else toast('Chat failed: ' + error);
      chat.threads = null;  // the Review question made a new thread; refresh the list next time
      render(); return;
    }
    try { const r = await api('/api/chat/threads'); chat.threads = r.threads; } catch (_) {}
    if (chat.current !== null) { try { const r = await api(`/api/chat/threads/${chat.current}`); chat.messages = r.messages; } catch (_) {} }
    if (result) { toast(`Answered · $${Number(result.cost_usd || 0).toFixed(2)}` + (result.drafts && result.drafts.length ? ' · draft saved in Mailbird' : '')); if ((result.proposals || []).some(p => p.applied)) load(); }
    else toast('Chat failed: ' + error);
    render();
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
        ${b.changes.map(c => c.field === 'evidence' ? `<div class="logc">${c.task_id ? `<span class="code">Task-${c.task_id}</span> <span class="name">${esc(c.task_name || '')}</span>` : '<span class="code">suggestion</span>'} <span class="f">evidence #${c.new && c.new.evidence_id}</span> ${esc((c.new || {}).verdict || '')}</div>`
          : `<div class="logc"><span class="code">Task-${c.task_id}</span> <span class="name">${esc(c.task_name || '')}</span> <span class="f">${esc(c.field)}</span> ${val(c.old)} → ${val(c.new)}</div>`).join('')}
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
    const num = (key, min, max) => `<input type="number" id="s-${key}" data-key="${key}" value="${e[key] == null ? '' : e[key]}" min="${min}" max="${max}" ${Number.isInteger(min) ? '' : 'step="0.05"'}>`;
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
      ${f('sources.canvas.enabled', 'Canvas', 'New items become hard-deadline tasks, submitted ones close.', bool('sources.canvas.enabled') + ' &nbsp; every ' + num('sources.canvas.sweep_minutes', 5, 1440) + ' min')}
      ${f('sources.mail.enabled', 'Email via Mailbird', 'Replies flip Waiting, sent mail closes reply tasks, invitations and confirmations feed the planner.', bool('sources.mail.enabled') + ' &nbsp; every ' + num('sources.mail.sweep_minutes', 5, 1440) + ' min')}
      ${f('sources.calendar.enabled', 'Google Calendar', 'Needs config/google-credentials.json (docs/SETUP.md §6).', bool('sources.calendar.enabled') + ' &nbsp; every ' + num('sources.calendar.sweep_minutes', 5, 1440) + ' min &nbsp; calendar id ' + text('sources.calendar.calendar_id', '160px'))}
      ${f('sources.slack.enabled', 'Slack', 'Chat can search and read the workspaces named in the config file (docs/SETUP.md §12); tokens in .env.', bool('sources.slack.enabled'))}
      ${f('sources.discord.enabled', 'Discord', 'Chat can list channels and read recent messages in the servers named in the config file (docs/SETUP.md §13).', bool('sources.discord.enabled'))}
      <h3>Email accounts</h3>
      <table><tr><td class="code">address</td><td class="code">label</td><td class="code">roles (monitor, plaud_trigger, brief_recipient)</td><td class="code">on</td><td></td></tr>${accounts}</table>
      <button class="btn small" id="acc-add">+ account</button>
      <h3>Outputs</h3>
      ${f('sinks.notion_notes.enabled', 'Notion notes', 'One page per recording in the Plaud notes database.', bool('sinks.notion_notes.enabled'))}
      ${f('sinks.notion_tasks_mirror.enabled', 'Notion tasks mirror', 'One-way copy of every task row; never read back. Property names in the config file.', bool('sinks.notion_tasks_mirror.enabled') + ' &nbsp; every ' + num('sinks.notion_tasks_mirror.sweep_minutes', 2, 1440) + ' min')}
      ${f('notify.ntfy.enabled', 'ntfy push', 'Reminders and the review push.', bool('notify.ntfy.enabled') + ' &nbsp; topic ' + text('notify.ntfy.topic', '220px'))}
      ${f('notify.morning_brief.enabled', 'Morning brief', 'Email at this time: Top, due today, reminders.', bool('notify.morning_brief.enabled') + ' ' + time('notify.morning_brief.time') + ' to ' + text('notify.morning_brief.to', '240px'))}
      <h3>Claude</h3>
      ${f('claude.planner.enabled', 'Morning planner', 'One Claude run before the brief: the verdict, why-now lines and proposals. Off = the plain brief.', bool('claude.planner.enabled'))}
      ${f('claude.budgets_usd.chat_turn', 'Chat turn budget', 'Dollars a single chat turn may spend before Claude stops.', '$ ' + num('claude.budgets_usd.chat_turn', 0.05, 5) )}
      <p style="margin-top:16px" class="hint">Every control saves when it changes. <button class="btn small" id="settings-save">Save now</button> <span id="settings-state"></span></p>
    </div>`;
  }
  function renderTray() {
    const n = staged.size;
    document.getElementById('tray').classList.toggle('on', n > 0);
    document.getElementById('tray-count').textContent = `${n} change${n === 1 ? '' : 's'}`;
    document.getElementById('tray-list').textContent = [...staged.values()].map(s => (s.task_id ? `Task-${s.task_id} ` : '') + s.label).join(' · ');
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
    panel.querySelectorAll('[data-rv-accept]').forEach(b => b.onclick = () => {
      const eid = +b.dataset.rvAccept; const item = review.items.find(i => (i.evidence || []).some(e => e.id === eid)); const e = item && item.evidence.find(e => e.id === eid); if (!e || !e.change) return;
      const c = e.change, tid = item.task.id;
      if (['done', 'drop', 'pin_top', 'longterm'].includes(c.op)) stage(tid, c.op, {}, e.label);
      else if (c.op === 'add_reminder') stageExtra(tid + ':r', tid, 'add_reminder', c.fields, e.label);
      else stageExtra(`${tid}:c${eid}`, tid, 'set', c.fields || {}, e.label);
      stageJudge(eid, tid, 'applied', 'suggestion accepted'); toast('Suggestion staged · decide on the card, then Apply');
    });
    panel.querySelectorAll('[data-pending-apply]').forEach(b => b.onclick = async () => {
      try { const r = await api(`/api/proposals/${b.dataset.pendingApply}/apply`, { method: 'POST' }); lastBatch = r.batch_id; review = null; logData = null; await load(); toast(`${r.applied} change${r.applied === 1 ? '' : 's'} applied` + (r.skipped.length ? ` · ${r.skipped.length} skipped` : ''), true); }
      catch (e) { toast('Apply failed: ' + e.message); }
    });
    panel.querySelectorAll('[data-pending-dismiss]').forEach(b => b.onclick = async () => {
      try { await api(`/api/proposals/${b.dataset.pendingDismiss}/dismiss`, { method: 'POST' }); await load(); toast('Dismissed'); } catch (e) { toast('Dismiss failed: ' + e.message); }
    });
    panel.querySelectorAll('[data-sugg-dismiss]').forEach(b => b.onclick = () => { stageJudge(+b.dataset.suggDismiss, null, 'dismissed', 'planner suggestion dismissed'); });
    panel.querySelectorAll('[data-sugg-create]').forEach(b => b.onclick = async () => {
      const id = +b.dataset.suggCreate;
      try { const r = await api(`/api/review/suggestions/${id}/create`, { method: 'POST' }); const sg = (review.suggestions || []).find(x => x.id === id); if (sg) sg.created = r.task_id; toast(`Created Task-${r.task_id} · it shows as New in Review`); await load(); }
      catch (e) { toast('Create failed: ' + e.message); }
    });
    const rd = document.getElementById('rv-date'); if (rd) rd.onchange = () => reviewAct('date', rd.value);
    const rn = document.getElementById('rv-note'); if (rn) rn.oninput = () => { ui.rvNote = { id: +rn.closest('.card').dataset.id, text: rn.value }; };
    const rm = document.getElementById('review-more'); if (rm) rm.onclick = () => loadReview();
    wireReminders(panel); wireDue(panel);
    panel.querySelectorAll('[data-undo]').forEach(b => b.onclick = async () => { await api(`/api/batches/${b.dataset.undo}/undo`, { method: 'POST' }); await load(); logData = null; toast('Batch undone'); render(); });
    panel.querySelectorAll('[data-chat-canned]').forEach(b => b.onclick = () => sendChat('', b.dataset.chatCanned, [+b.dataset.id]));
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
    const ct = document.getElementById('chat-thread'); if (ct) ct.onchange = () => { if (ct.value) loadChatThread(+ct.value); };
    const cn = document.getElementById('chat-new'); if (cn) cn.onclick = () => { chat.current = null; chat.messages = []; render(); };
    const msgs = document.getElementById('msgs'); if (msgs) msgs.scrollTop = msgs.scrollHeight;
    const ask = document.getElementById('rv-ask'), askGo = document.getElementById('rv-ask-go');
    const askSend = () => { const v = ask.value.trim(); if (!v) return; ask.value = ''; const card = ask.closest('.card'); sendChat(v, null, [+card.dataset.id], { review: +card.dataset.id }); };
    if (askGo) askGo.onclick = askSend;
    if (ask) ask.addEventListener('keydown', e => { if (e.key === 'Enter') askSend(); if (e.key !== 'Escape') e.stopPropagation(); });
    panel.querySelectorAll('[data-goto-chat]').forEach(a => a.onclick = e => { e.preventDefault(); chat.current = +a.dataset.gotoChat; chat.threads = null; ui.tab = 'chat'; location.hash = 'chat'; render(); });
    const cmdGo = document.getElementById('cmd-go'); if (cmdGo) cmdGo.onclick = () => { const i = document.getElementById('cmd'); const v = i.value.trim(); i.value = ''; sendPrompt(v); };
    const cmd = document.getElementById('cmd'); if (cmd) cmd.addEventListener('keydown', e => { if (e.key === 'Escape') return; if (e.key === 'Enter') { const v = cmd.value.trim(); cmd.value = ''; sendPrompt(v); } e.stopPropagation(); });
    document.getElementById('tray-apply').onclick = applyAll;
    document.getElementById('tray-discard').onclick = () => { staged = new Map(); try { localStorage.removeItem('factotum.staged'); } catch (_) {} render(); };
    const save = document.getElementById('settings-save'); if (save) save.onclick = saveSettings;
    if (ui.tab === 'settings') panel.querySelectorAll('[data-key], [data-acc]').forEach(el => el.addEventListener('change', () => { clearTimeout(saveSettings.t); saveSettings.t = setTimeout(saveSettings, 300); }));
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
    // The flags that brought the card here (a thread update, a planner proposal) are judged in the
    // same batch, so the card does not come back tomorrow for the same reason.
    (items[0].evidence || []).forEach(e => { if (!staged.has('e:' + e.id)) stageJudge(e.id, id, 'reviewed', (e.kind === 'proposal' ? 'suggestion seen' : 'thread seen')); });
    ui.laterOpen = false; ui.rvNote = { id: null, text: '' };
  }
  async function sendPrompt(text) {
    if (!text) return;
    const res = await api('/api/commands', { method: 'POST', body: JSON.stringify({ text }) });
    if (res.is_command) {
      res.changes.forEach(c => stage(c.task_id, c.op, c.fields, c.op === 'set' ? Object.entries(c.fields).map(([k, v]) => `${k} ${v || 'cleared'}`).join(', ') : c.op));
      toast(`Staged ${res.changes.length} change${res.changes.length === 1 ? '' : 's'}`);
    } else {
      sendChat(text, null, attachedIds());
    }
  }
  async function saveSettings() {
    const values = {};
    document.querySelectorAll('#panel [data-key]').forEach(el => {
      const k = el.dataset.key;
      values[k] = el.type === 'checkbox' ? el.checked : el.type === 'number' ? Number(el.value) : el.value;
      if (el.type === 'number' && (el.value === '' || Number.isNaN(values[k]))) delete values[k];
    });
    const accounts = settings.accounts.map((a, i) => ({ address: a.address, display: a.display, roles: a.roles, enabled: a.enabled }));
    document.querySelectorAll('#panel [data-acc]').forEach(el => {
      const a = accounts[+el.dataset.acc]; const f = el.dataset.f;
      if (f === 'roles') a.roles = el.value.split(',').map(s => s.trim()).filter(Boolean);
      else if (f === 'enabled') a.enabled = el.checked; else a[f] = el.value;
    });
    const active = document.activeElement && document.activeElement.dataset ? document.activeElement.id : null;
    try { settings = await api('/api/settings', { method: 'PUT', body: JSON.stringify({ values, accounts }) }); toast('Settings saved'); render(); const el = active && document.getElementById(active); if (el) el.focus(); }
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
