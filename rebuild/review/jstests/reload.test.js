import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import {
  RELOAD_EVENT,
  VIEW_EXTRAS_MAX_AGE_MS,
  movesPage,
  pageIdentity,
  parseReloadPath,
  readMoveGuard,
  readViewExtras,
  targetKey,
  writeMoveGuard,
  writeViewExtras,
} from '../static/reload.js';

const PLUGIN_SOURCE = readFileSync(new URL('../static/reload-plugin.js', import.meta.url), 'utf8');

function loadPlugin() {
  const events = [];
  const window = {
    dispatchEvent(event) {
      events.push(event);
      return true;
    },
  };
  class CustomEvent {
    constructor(type, init) {
      this.type = type;
      this.detail = init?.detail;
    }
  }
  vm.runInNewContext(PLUGIN_SOURCE, { window, CustomEvent });
  return { window, events, plugin: new window.LiveReloadPluginAms(window, {}) };
}

const identity = { corpus: '2026-09-30T10:00:00Z', assets: 'static-aaa' };

test('the plugin registers under the name livereload scans for, with the identifier it keys plugins on', () => {
  const { window } = loadPlugin();
  assert.equal(typeof window.LiveReloadPluginAms, 'function');
  assert.equal(window.LiveReloadPluginAms.identifier, 'ams');
});

test('the plugin claims ams: paths and leaves every other path to livereload', () => {
  const { plugin } = loadPlugin();
  assert.equal(plugin.reload('ams:corpus/2026-09-30T11:00:00Z'), true);
  assert.equal(plugin.reload('/abs/rebuild/out/review/app.css'), false);
  assert.equal(plugin.reload('*'), false);
});

test('before the app has booted the plugin keeps the latest ams: path; after, it dispatches each one as an event', () => {
  const { window, events, plugin } = loadPlugin();
  plugin.reload('ams:corpus/one');
  plugin.reload('ams:assets/two');
  assert.equal(window.__amsPendingReload, 'ams:assets/two');
  assert.equal(events.length, 0);
  window.__amsReloadReady = true;
  plugin.reload('ams:corpus/three');
  assert.equal(events.length, 1);
  assert.equal(events[0].type, RELOAD_EVENT);
  assert.equal(events[0].detail.path, 'ams:corpus/three');
});

test('parseReloadPath reads the two ams: kinds and nothing else', () => {
  assert.deepEqual(parseReloadPath('ams:corpus/2026-09-30T11:00:00Z'), { kind: 'corpus', value: '2026-09-30T11:00:00Z' });
  assert.deepEqual(parseReloadPath('ams:assets/abc'), { kind: 'assets', value: 'abc' });
  assert.equal(parseReloadPath('ams:corpus/'), null);
  assert.equal(parseReloadPath('ams:other/x'), null);
  assert.equal(parseReloadPath('/review/app.js'), null);
  assert.equal(parseReloadPath(null), null);
});

test('a page moves only for a target that differs from what it loaded', () => {
  const page = pageIdentity({ generated_at: identity.corpus, inputs_fingerprint: { static: identity.assets } });
  assert.deepEqual(page, identity);
  assert.equal(movesPage({ kind: 'corpus', value: identity.corpus }, page), false);
  assert.equal(movesPage({ kind: 'corpus', value: '2026-09-30T11:00:00Z' }, page), true);
  assert.equal(movesPage({ kind: 'assets', value: identity.assets }, page), false);
  assert.equal(movesPage({ kind: 'assets', value: 'static-bbb' }, page), true);
  assert.equal(movesPage(null, page), false);
});

test('the moved-from guard blocks a target only when the reload left the page where it was', () => {
  const target = { kind: 'corpus', value: '2026-09-30T11:00:00Z' };
  const raw = writeMoveGuard(identity, target);
  assert.deepEqual(readMoveGuard(raw, identity), target);
  assert.equal(readMoveGuard(raw, { ...identity, corpus: target.value }), null);
  assert.deepEqual(readMoveGuard(raw, { ...identity, assets: 'static-bbb' }), target);
  assert.equal(readMoveGuard(null, identity), null);
  assert.equal(readMoveGuard('not json', identity), null);
  assert.equal(targetKey(target), 'corpus/2026-09-30T11:00:00Z');
});

test('view extras come back only for the same hash and only shortly after they were saved', () => {
  const saved = writeViewExtras(
    { hash: '#view=queue', scrollY: 420, queueAnchor: { cluster: 'c-aaa', delta: 12 }, notes: [['u-1', 'draft'], ['u-2', 7]] },
    1000,
  );
  assert.deepEqual(readViewExtras(saved, '#view=queue', 2000), {
    scrollY: 420,
    queueAnchor: { cluster: 'c-aaa', delta: 12 },
    notes: [['u-1', 'draft']],
    keptAsideSince: null,
  });
  assert.equal(readViewExtras(saved, '#view=queue&unit=u-1', 2000), null);
  assert.equal(readViewExtras(saved, '#view=queue', 1000 + VIEW_EXTRAS_MAX_AGE_MS + 1), null);
  assert.equal(readViewExtras(saved, '#view=queue', 999), null);
  assert.equal(readViewExtras(null, '#view=queue', 2000), null);
});

test('the view extras carry the time the moved-from page counted kept-aside saves from', () => {
  const since = '2026-09-30T09:00:00.000Z';
  const saved = writeViewExtras({ hash: '', scrollY: 0, queueAnchor: null, notes: [], keptAsideSince: since }, 1000);
  assert.equal(readViewExtras(saved, '', 2000).keptAsideSince, since);
  const garbled = writeViewExtras({ hash: '', scrollY: 0, queueAnchor: null, notes: [], keptAsideSince: 'soon' }, 1000);
  assert.equal(readViewExtras(garbled, '', 2000).keptAsideSince, null);
});
