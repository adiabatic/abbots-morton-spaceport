export const STATE_KEYS = ['class', 'batch', 'unit', 'group', 'config', 'family', 'status', 'machine', 'units'];

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
