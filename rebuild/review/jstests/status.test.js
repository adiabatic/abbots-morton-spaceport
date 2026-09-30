import test from 'node:test';
import assert from 'node:assert/strict';
import { bannerModel, remedyCommand } from '../static/status.js';

function checks(overrides = {}) {
  return {
    corpus: { level: 'ok', detail: 'corpus present', remedy: null },
    freshness: { level: 'ok', detail: 'all components fresh', remedy: null, components: {} },
    gates: { level: 'ok', detail: 'gates green', remedy: null },
    verdict_store: { level: 'ok', detail: 'store readable', remedy: null },
    fullest_verdicts: { level: 'ok', detail: 'fullest verdicts clean', remedy: null, path: null, count: null },
    blanks: { level: 'ok', detail: '3 blank units', count: 3 },
    ...overrides,
  };
}

function withChecks(checkOverrides = {}, top = {}) {
  return {
    ready: true,
    corpus: { dir: 'corpus', generated_at: 'gen-1', repo_head: 'head-1' },
    checks: checks(checkOverrides),
    ...top,
  };
}

test('a missing status reports the server as unavailable', () => {
  const model = bannerModel(null, 'gen-1');
  assert.equal(model.level, 'error');
  assert.equal(model.text, 'Status unavailable');
  assert.equal(model.remedy, 'restart the review server (make review-serve)');
  assert.equal(model.command, 'make review-serve');
});

test('an undefined status is also unavailable', () => {
  assert.equal(bannerModel(undefined, 'gen-1').level, 'error');
});

test('an error field folds the server message into the text', () => {
  const model = bannerModel({ error: 'boom' }, 'gen-1');
  assert.equal(model.level, 'error');
  assert.equal(model.text, 'Status unavailable — boom');
  assert.equal(model.remedy, 'restart the review server (make review-serve)');
});

test('a payload without checks is unavailable', () => {
  const model = bannerModel({ ready: true, corpus: { generated_at: 'gen-1' } }, 'gen-1');
  assert.equal(model.level, 'error');
  assert.equal(model.text, 'Status unavailable');
});

test('a corpus rebuilt since the page loaded is stale, and the page says it is moving onto it while the move is pending', () => {
  const status = withChecks({}, { corpus: { dir: 's', generated_at: 'gen-2', repo_head: 'h' } });
  const moving = bannerModel(status, 'gen-1', null, true);
  assert.equal(moving.level, 'stale');
  assert.equal(moving.text, 'Moving onto the rebuilt corpus…');
  assert.equal(moving.remedy, null);
  assert.equal(moving.command, null);
});

test('a corpus rebuilt since the page loaded asks for a reload when the page is not moving onto it', () => {
  const model = bannerModel(withChecks({}, { corpus: { dir: 's', generated_at: 'gen-2', repo_head: 'h' } }), 'gen-1');
  assert.equal(model.level, 'stale');
  assert.equal(model.text, 'Corpus rebuilt since this page loaded');
  assert.equal(model.remedy, 'reload the page');
  assert.equal(model.command, null);
});

test('a corpus check failure is stale and carries its own detail and remedy', () => {
  const model = bannerModel(
    withChecks({ corpus: { level: 'fail', detail: 'corpus dir missing', remedy: 'run make review-build' } }),
    'gen-1',
  );
  assert.equal(model.level, 'stale');
  assert.equal(model.text, 'corpus dir missing');
  assert.equal(model.remedy, 'run make review-build');
});

test('a command remedy is surfaced for copying; a prose remedy is not', () => {
  const stale = bannerModel(
    withChecks({
      freshness: {
        level: 'fail',
        detail: 'The build inputs changed since the corpus was generated: rune sources.',
        remedy: 'make artifact-cycle',
        components: {},
      },
    }),
    'gen-1',
  );
  assert.equal(stale.command, 'make artifact-cycle');
  const prose = bannerModel(
    withChecks({ verdict_store: { level: 'warn', detail: 'store stale', remedy: 'Import the carried verdicts in the app.' } }),
    'gen-1',
  );
  assert.equal(prose.command, null);
});

test('remedyCommand recognizes make and uv run invocations only', () => {
  assert.equal(remedyCommand('make review-build'), 'make review-build');
  assert.equal(remedyCommand('uv run python -m rebuild.review.build'), 'uv run python -m rebuild.review.build');
  assert.equal(remedyCommand('run make review-build'), null);
  assert.equal(remedyCommand('reload the page'), null);
  assert.equal(remedyCommand(null), null);
});

test('the fail scan takes freshness before gates', () => {
  const model = bannerModel(
    withChecks({
      freshness: { level: 'fail', detail: 'components stale', remedy: 'rebuild', components: {} },
      gates: { level: 'fail', detail: 'gates red', remedy: 'fix gates' },
    }),
    'gen-1',
  );
  assert.equal(model.level, 'stale');
  assert.equal(model.text, 'components stale');
  assert.equal(model.remedy, 'rebuild');
});

test('a gates failure surfaces once the earlier checks pass', () => {
  const model = bannerModel(
    withChecks({ gates: { level: 'fail', detail: 'boundary gate red', remedy: 'rerun gates' } }),
    'gen-1',
  );
  assert.equal(model.level, 'stale');
  assert.equal(model.text, 'boundary gate red');
  assert.equal(model.remedy, 'rerun gates');
});

test('a verdict-store failure is the last fail scanned', () => {
  const model = bannerModel(
    withChecks({ verdict_store: { level: 'fail', detail: 'store unreadable', remedy: 'restore autosave' } }),
    'gen-1',
  );
  assert.equal(model.level, 'stale');
  assert.equal(model.text, 'store unreadable');
  assert.equal(model.remedy, 'restore autosave');
});

test('any fail outranks a warn', () => {
  const model = bannerModel(
    withChecks({
      gates: { level: 'fail', detail: 'gate red', remedy: 'fix' },
      verdict_store: { level: 'warn', detail: 'store warn', remedy: 'later' },
    }),
    'gen-1',
  );
  assert.equal(model.level, 'stale');
  assert.equal(model.text, 'gate red');
});

test('a warning check renders warn with its detail and remedy', () => {
  const model = bannerModel(
    withChecks({ verdict_store: { level: 'warn', detail: 'store nearly full', remedy: 'export soon' } }),
    'gen-1',
  );
  assert.equal(model.level, 'warn');
  assert.equal(model.text, 'store nearly full');
  assert.equal(model.remedy, 'export soon');
});

test('a fullest verdicts warning is picked up after the four blocking checks pass', () => {
  const model = bannerModel(
    withChecks({ fullest_verdicts: { level: 'warn', detail: 'fullest verdicts drifting', remedy: null, path: 'p', count: 5 } }),
    'gen-1',
  );
  assert.equal(model.level, 'warn');
  assert.equal(model.text, 'fullest verdicts drifting');
  assert.equal(model.remedy, null);
});

test('an all-clear corpus is ready with the blank count', () => {
  const model = bannerModel(withChecks({ blanks: { level: 'ok', detail: '484 blanks', count: 484 } }), 'gen-1');
  assert.equal(model.level, 'ready');
  assert.equal(model.text, 'Ready — 484 blanks left');
  assert.equal(model.remedy, null);
  assert.equal(model.command, null);
});

test('a ready corpus with an unknown blank count omits the number', () => {
  const model = bannerModel(withChecks({ blanks: { level: 'ok', detail: 'blanks unknown', count: null } }), 'gen-1');
  assert.equal(model.level, 'ready');
  assert.equal(model.text, 'Ready');
  assert.equal(model.remedy, null);
});

test('with no page stamp to compare against, a fresh corpus is still ready', () => {
  const model = bannerModel(withChecks(), undefined);
  assert.equal(model.level, 'ready');
  assert.equal(model.text, 'Ready — 3 blanks left');
});

test('conflicts and orphans the server kept aside since the page loaded show as a warning', () => {
  const status = withChecks(
    {},
    {
      conflicts: {
        count: 302,
        newest_at: '2026-09-30T12:00:05Z',
        file: 'var/verdict-orphans/gen-0.json',
        recent: [
          ['2026-09-30T12:00:05Z', 2],
          ['2026-09-30T09:00:00Z', 300],
        ],
      },
      orphans: {
        count: 1,
        newest_at: '2026-09-30T11:00:00Z',
        file: 'var/verdict-orphans/gen-0.json',
        recent: [['2026-09-30T11:00:00Z', 1]],
      },
    },
  );
  const model = bannerModel(status, 'gen-1', '2026-09-30T12:00:00.000Z');
  assert.equal(model.level, 'warn');
  assert.equal(model.text, 'Saves kept aside by the server: 2 conflicted');
  assert.equal(model.remedy, 'see var/verdict-orphans/gen-0.json');
  assert.equal(model.command, null);
  const both = bannerModel(status, 'gen-1', '2026-09-30T10:00:00.000Z');
  assert.equal(both.text, 'Saves kept aside by the server: 2 conflicted, 1 orphaned');
  const sameSecond = bannerModel(status, 'gen-1', '2026-09-30T12:00:05.700Z');
  assert.equal(sameSecond.text, 'Saves kept aside by the server: 2 conflicted');
});

test('saves kept aside before the page loaded leave the banner alone', () => {
  const status = withChecks(
    {},
    {
      conflicts: { count: 2, newest_at: '2026-09-30T11:00:00Z', file: 'f', recent: [['2026-09-30T11:00:00Z', 2]] },
      orphans: { count: 0, newest_at: null, file: null, recent: [] },
    },
  );
  assert.equal(bannerModel(status, 'gen-1', '2026-09-30T12:00:00.000Z').level, 'ready');
  assert.equal(bannerModel(status, 'gen-1').level, 'ready');
});
