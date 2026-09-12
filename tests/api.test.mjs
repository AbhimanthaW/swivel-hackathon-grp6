import test from 'node:test';
import assert from 'node:assert/strict';
import { request, ApiError, api } from '../frontend/api.js';

// Transport-only tests. No sample municipal records, backend simulator or application fixtures.
test('surfaces structured backend errors without treating them as data', async () => {
  const previous = global.fetch;
  global.fetch = async () => new Response(JSON.stringify({ error: { code: 'BACKEND_UNAVAILABLE', message: 'Service unavailable' } }), { status: 503 });
  try { await assert.rejects(request('/overview'), error => error instanceof ApiError && error.status === 503 && error.code === 'BACKEND_UNAVAILABLE'); }
  finally { global.fetch = previous; }
});
test('invalid JSON produces an explicit contract error', async () => {
  const previous = global.fetch; global.fetch = async () => new Response('<html>Wrong service</html>');
  try { await assert.rejects(request('/overview'), error => error.code === 'INVALID_RESPONSE'); }
  finally { global.fetch = previous; }
});
test('unconfirmed writes are not reported as failures or success', async () => {
  const previous = global.fetch; global.fetch = async () => { throw new TypeError('Disconnected'); };
  try { await assert.rejects(request('/imports', { method: 'POST', body: {} }), error => error.code === 'OUTCOME_UNKNOWN' && /not confirmed/.test(error.message)); }
  finally { global.fetch = previous; }
});
test('multipart uploads let fetch set boundaries and carry revision/idempotency', async () => {
  const previous = global.fetch;
  global.fetch = async (path, options) => {
    assert.equal(path, '/api/v1/imports/opaque-id/files/assets');
    assert.equal(options.method, 'PUT');
    assert.equal(options.headers['Content-Type'], undefined);
    assert.equal(options.headers['Idempotency-Key'], 'transport-test-key');
    assert.equal(options.body.get('revision'), '7');
    assert.equal(options.body.get('file').name, 'upload.csv');
    return new Response(JSON.stringify({ error: { code: 'NOT_IMPLEMENTED', message: 'Transport test only' } }), { status: 503 });
  };
  try { await assert.rejects(api.upload('opaque-id','assets',new File([], 'upload.csv'),7,'transport-test-key'), error=>error.status===503); }
  finally { global.fetch = previous; }
});
test('mutations use the versioned route, JSON and a reusable idempotency key', async () => {
  const previous = global.fetch;
  global.fetch = async (path, options) => {
    assert.equal(path, '/api/v1/imports'); assert.equal(options.headers['Content-Type'], 'application/json');
    assert.equal(options.headers['Idempotency-Key'], 'logical-operation'); assert.equal(options.body, '{}');
    return new Response(JSON.stringify({error:{code:'BACKEND_UNAVAILABLE',message:'Offline'}}), {status:503});
  };
  try { await assert.rejects(api.createImport('logical-operation'), error=>error.status===503); }
  finally { global.fetch = previous; }
});
