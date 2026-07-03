export const STATE_KEYS = ['class', 'batch', 'unit', 'group', 'config', 'family', 'status', 'machine', 'units'];

export const WORKLIST_EXIT_KEYS = ['class', 'batch', 'group', 'config', 'family', 'status'];

export function shedWorklist(patch) {
  for (const key of WORKLIST_EXIT_KEYS) {
    if (key in patch) return { units: null, ...patch };
  }
  return patch;
}

export function parseHash(hash) {
  const params = new URLSearchParams((hash ?? '').replace(/^#/, ''));
  const state = {};
  for (const key of STATE_KEYS) state[key] = params.get(key);
  if (state.batch !== null) {
    const parsed = Number.parseInt(state.batch, 10);
    state.batch = Number.isInteger(parsed) && parsed >= 0 ? parsed : null;
  }
  return state;
}

export function writeHash(state) {
  const params = new URLSearchParams();
  for (const key of STATE_KEYS) {
    const value = state[key];
    if (value === null || value === undefined || value === '') continue;
    params.set(key, String(value));
  }
  return params.toString();
}
