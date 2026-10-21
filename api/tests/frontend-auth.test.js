const assert = require('node:assert/strict');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { test } = require('node:test');

test('network errors preserve browser authentication; HTTP 401 clears it', async () => {
  const shared = await import(pathToFileURL(path.resolve(__dirname, '../../web/dist/common.js')));
  const previousWindow = global.window;
  const previousFetch = global.fetch;
  const stored = new Map();
  global.window = {
    localStorage: {
      getItem: (key) => stored.get(key) || null,
      setItem: (key, value) => stored.set(key, value),
      removeItem: (key) => stored.delete(key),
    },
  };
  try {
    shared.saveStoredAuth('test-token', { email: 'test@example.test' });
    global.fetch = async () => { throw new TypeError('Request cancelled'); };
    assert.equal((await shared.verifyStoredAuth()).token, 'test-token');
    assert.ok(shared.readStoredAuth());
    global.fetch = async () => ({
      ok: false, status: 401, json: async () => ({ error: 'Invalid token' }),
    });
    await assert.rejects(shared.apiFetch('/api/auth/me'), (error) => error.status === 401);
    assert.equal(await shared.verifyStoredAuth(), null);
    assert.equal(shared.readStoredAuth(), null);
  } finally {
    global.window = previousWindow;
    global.fetch = previousFetch;
  }
});
