/* Factotum's service worker: notifications only.
 *
 * It handles `push` (show the notification) and `notificationclick` (open the task, or press Done /
 * Snooze through the reminder's token link). It has NO fetch handler, ever: one would let the worker
 * store or serve the app and make it stale (design §14). The app is always loaded from the server.
 *
 * The payload is the declarative Web Push format (`web_push: 8030`), which Safari shows without
 * running this script; Chrome, Edge and Firefox hand the same JSON to the `push` listener below.
 * What a notification shows is therefore decided twice: a change to it goes into
 * `app_push.payload` (the server) and `shape` (here) together.
 *
 * The payload's top-level `app_badge` is the Review count for the app's icon. Safari applies it
 * without running this script; here `setBadge` sets it. No push is ever sent for the number alone.
 *
 * The handlers are global functions so a test browser can run them through `worker.evaluate`.
 */
'use strict';

const CONFIRMED = { done: 'Marked done', snooze: 'Snoozed for 1 hour' };

function onOrigin(href) {
  try { return new URL(href, self.location.origin).origin === self.location.origin; } catch (_) { return false; }
}

// The declarative payload → showNotification's title and options. Anything missing or of the wrong
// type falls back, so a payload this script cannot read still shows "Factotum".
function shape(json) {
  const n = (json && typeof json === 'object' && json.notification && typeof json.notification === 'object') ? json.notification : {};
  const named = typeof n.title === 'string' && n.title !== '';
  const title = named ? n.title : 'Factotum';
  const options = {
    body: typeof n.body === 'string' ? n.body : (named ? '' : 'Open Factotum'),
    icon: '/static/icon-192.png',
  };
  if (typeof n.tag === 'string' && n.tag !== '') { options.tag = n.tag; options.renotify = true; }
  const actions = (Array.isArray(n.actions) ? n.actions : [])
    .filter(a => a && typeof a.action === 'string' && typeof a.title === 'string' && typeof a.navigate === 'string')
    .slice(0, 2)
    .map(a => ({ action: a.action, title: a.title, navigate: a.navigate }));
  options.actions = actions.map(a => ({ action: a.action, title: a.title }));
  options.data = {
    navigate: typeof n.navigate === 'string' ? n.navigate : '/',
    title, actions,
    task_id: n.data && n.data.task_id,
  };
  if (options.tag) options.data.tag = options.tag;      // the confirmation after Done / Snooze replaces this one
  return { title, options };
}

// The Review count for the app's icon (stage 8): the payload's top-level app_badge, an integer >= 0; anything else is no number.
function badgeOf(json) {
  const n = json && typeof json === 'object' ? json.app_badge : undefined;
  return Number.isInteger(n) && n >= 0 ? n : null;
}
// Sets (0: clears) the icon's number where the browser can; never throws, never rejects, so a notification never waits on it.
function setBadge(n) {
  try {
    const nav = self.navigator;
    if (n === null || !nav) return Promise.resolve();
    const p = n === 0 ? (typeof nav.clearAppBadge === 'function' ? nav.clearAppBadge() : null)
                      : (typeof nav.setAppBadge === 'function' ? nav.setAppBadge(n) : null);
    return Promise.resolve(p).catch(() => {});
  } catch (_) { return Promise.resolve(); }
}

// Every push shows a notification: when the shaped one is refused, a plain "Factotum" one instead.
function handlePush(json) {
  const s = shape(json);
  return Promise.all([setBadge(badgeOf(json)), self.registration.showNotification(s.title, s.options)
    .catch(() => self.registration.showNotification('Factotum', { body: 'Open Factotum', data: { navigate: '/' } }))])
    .then(r => r[1]);
}

// Open an address of the app: in a window already open (it follows the path without reloading), else
// in a new one. An address on another origin opens the app's start instead.
function openApp(href) {
  let url;
  try { url = new URL(href || '/', self.location.origin); } catch (_) { url = null; }
  if (!url || url.origin !== self.location.origin) url = new URL('/', self.location.origin);
  return self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(wins => {
    const win = wins.find(w => onOrigin(w.url));
    if (!win) return self.clients.openWindow(url.href);
    win.postMessage({ type: 'open', path: url.pathname + url.search });
    return Promise.resolve().then(() => win.focus()).catch(() => {});
  });
}

// A tap on the notification opens its task. A button calls the reminder's token link, which is behind
// the sign-in: an answer that is not ok (an expired sign-in is a redirect, opaque here and never ok;
// an expired token is an error page) or no answer at all (offline) opens the task instead.
function handleClick(data, action) {
  data = data || {};
  const a = action ? (Array.isArray(data.actions) ? data.actions : []).find(x => x && x.action === action) : null;
  if (!a || typeof a.navigate !== 'string' || !onOrigin(a.navigate)) return openApp(data.navigate);
  return fetch(a.navigate, { credentials: 'include', redirect: 'manual' }).then(r => {
    if (!r.ok) return openApp(data.navigate);
    const options = { body: data.title, silent: true, data: { navigate: data.navigate, title: data.title, actions: [] } };
    if (data.tag) options.tag = data.tag;
    return self.registration.showNotification(CONFIRMED[action] || 'Factotum', options);
  }, () => openApp(data.navigate));
}

self.addEventListener('install', () => { self.skipWaiting(); });
self.addEventListener('activate', event => { event.waitUntil(self.clients.claim()); });
self.addEventListener('push', event => {
  let json = null;
  try { json = event.data ? event.data.json() : null; } catch (_) { json = null; }
  event.waitUntil(handlePush(json));
});
self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil(handleClick(event.notification.data || {}, event.action));
});
