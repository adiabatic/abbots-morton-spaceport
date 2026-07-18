import test from 'node:test';
import assert from 'node:assert/strict';
import { bannerModel, remedyCommand } from '../static/status.js';

function checks(overrides = {}) {
  return {
    surface: { level: 'ok', detail: 'surface present', remedy: null },
    freshness: { level: 'ok', detail: 'all components fresh', remedy: null, components: {} },
    gates: { level: 'ok', detail: 'gates green', remedy: null },
    verdict_store: { level: 'ok', detail: 'store readable', remedy: null },
    frontier: { level: 'ok', detail: 'frontier clean', remedy: null, path: null, count: null },
    blanks: { level: 'ok', detail: '3 blank units', count: 3 },
    ...overrides,
  };
}

function withChecks(checkOverrides = {}, top = {}) {
  return {
    ready: true,
    surface: { dir: 'surface', generated_at: 'gen-1', repo_head: 'head-1' },
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
  const model = bannerModel({ ready: true, surface: { generated_at: 'gen-1' } }, 'gen-1');
  assert.equal(model.level, 'error');
  assert.equal(model.text, 'Status unavailable');
});

test('a surface rebuilt since the page loaded is stale', () => {
  const model = bannerModel(withChecks({}, { surface: { dir: 's', generated_at: 'gen-2', repo_head: 'h' } }), 'gen-1');
  assert.equal(model.level, 'stale');
  assert.equal(model.text, 'Surface rebuilt since this page loaded');
  assert.equal(model.remedy, 'reload the page');
  assert.equal(model.command, null);
});

test('a surface check failure is stale and carries its own detail and remedy', () => {
  const model = bannerModel(
    withChecks({ surface: { level: 'fail', detail: 'surface dir missing', remedy: 'run make review-build' } }),
    'gen-1',
  );
  assert.equal(model.level, 'stale');
  assert.equal(model.text, 'surface dir missing');
  assert.equal(model.remedy, 'run make review-build');
});

test('a command remedy is surfaced for copying; a prose remedy is not', () => {
  const stale = bannerModel(
    withChecks({
      freshness: {
        level: 'fail',
        detail: 'The build inputs changed since the surface was generated: rune sources.',
        remedy: "make artifact-cycle ARGS='--verdicts rebuild/evidence/verdicts-carried-0f5155b.json'",
        components: {},
      },
    }),
    'gen-1',
  );
  assert.equal(stale.command, "make artifact-cycle ARGS='--verdicts rebuild/evidence/verdicts-carried-0f5155b.json'");
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

test('a frontier warning is picked up after the four blocking checks pass', () => {
  const model = bannerModel(
    withChecks({ frontier: { level: 'warn', detail: 'frontier drifting', remedy: null, path: 'p', count: 5 } }),
    'gen-1',
  );
  assert.equal(model.level, 'warn');
  assert.equal(model.text, 'frontier drifting');
  assert.equal(model.remedy, null);
});

test('an all-clear surface is ready with the blank count', () => {
  const model = bannerModel(withChecks({ blanks: { level: 'ok', detail: '484 blanks', count: 484 } }), 'gen-1');
  assert.equal(model.level, 'ready');
  assert.equal(model.text, 'Ready — 484 blanks left');
  assert.equal(model.remedy, null);
  assert.equal(model.command, null);
});

test('a ready surface with an unknown blank count omits the number', () => {
  const model = bannerModel(withChecks({ blanks: { level: 'ok', detail: 'blanks unknown', count: null } }), 'gen-1');
  assert.equal(model.level, 'ready');
  assert.equal(model.text, 'Ready');
  assert.equal(model.remedy, null);
});

test('with no page stamp to compare against, a fresh surface is still ready', () => {
  const model = bannerModel(withChecks(), undefined);
  assert.equal(model.level, 'ready');
  assert.equal(model.text, 'Ready — 3 blanks left');
});
