/** All domain and upload requests go through this versioned contract. No offline fixtures. */
export class ApiError extends Error {
  constructor(message, status = 0, code = 'NETWORK_ERROR', details = []) {
    super(message); this.status = status; this.code = code; this.details = details;
  }
}
export async function request(path, { method = 'GET', body, idempotencyKey, timeout = 15000 } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  const multipart = body instanceof FormData;
  const headers = { Accept: 'application/json' };
  if (body !== undefined && !multipart) headers['Content-Type'] = 'application/json';
  if (idempotencyKey) headers['Idempotency-Key'] = idempotencyKey;
  try {
    const response = await fetch(`/api/v1${path}`, {
      method, headers, signal: controller.signal,
      body: body === undefined ? undefined : multipart ? body : JSON.stringify(body),
    });
    let value;
    try { value = await response.json(); }
    catch { throw new ApiError('The backend returned an invalid JSON response. Check the API contract.', response.status, 'INVALID_RESPONSE'); }
    if (!response.ok) throw new ApiError(value.error?.message || 'The backend could not complete this request.', response.status, value.error?.code, value.error?.details);
    return value;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError(method === 'GET'
      ? 'Cannot reach the backend. Check the service and retry.'
      : 'The result was not confirmed. Refresh the server state before retrying; do not assume the operation failed.', 0, 'OUTCOME_UNKNOWN');
  } finally { clearTimeout(timer); }
}
export const api = {
  overview: () => request('/overview'),
  problems: filters => request(`/problems?${new URLSearchParams(filters)}`),
  detail: id => request(`/problems/${encodeURIComponent(id)}`),
  reports: (id, page = 1) => request(`/problems/${encodeURIComponent(id)}/reports?page=${page}`),
  mutate: (id, action, body, key) => request(`/problems/${encodeURIComponent(id)}/${action}`, { method: 'POST', body, idempotencyKey: key }),
  imports: () => request('/imports?limit=10'),
  importDetail: id => request(`/imports/${encodeURIComponent(id)}`),
  createImport: key => request('/imports', { method: 'POST', body: {}, idempotencyKey: key }),
  upload: (id, role, file, revision, key) => {
    const body = new FormData(); body.append('file', file); body.append('revision', String(revision));
    return request(`/imports/${encodeURIComponent(id)}/files/${role}`, { method: 'PUT', body, idempotencyKey: key, timeout: 120000 });
  },
  runImport: (id, revision, key) => request(`/imports/${encodeURIComponent(id)}/run`, { method: 'POST', body: { revision }, idempotencyKey: key }),
};
