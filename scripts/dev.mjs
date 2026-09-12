/** Development static host and streaming reverse proxy only. No domain endpoints or mock responses. */
import http from 'node:http';
import https from 'node:https';
import { readFile, stat } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { resolve, extname, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
const root = fileURLToPath(new URL('../', import.meta.url));
if (existsSync(resolve(root, '.env'))) process.loadEnvFile(resolve(root, '.env'));
const frontend = resolve(root, 'frontend');
const port = Number(process.env.PORT || 5173);
const backend = new URL(process.env.BACKEND_ORIGIN || 'http://127.0.0.1:8001');
if (!['http:', 'https:'].includes(backend.protocol) || backend.pathname !== '/' || backend.username || backend.password) {
  throw new Error('BACKEND_ORIGIN must be an HTTP(S) origin without a path or credentials.');
}
const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml' };
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname.startsWith('/api/')) {
    const origin = req.headers.origin;
    if (origin && origin !== `http://${req.headers.host}`) {
      res.writeHead(403, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: { code: 'ORIGIN_REJECTED', message: 'Use the local frontend origin.' } }));
      return;
    }
    const transport = backend.protocol === 'https:' ? https : http;
    const headers = { ...req.headers, host: backend.host };
    delete headers.connection;
    const upstream = transport.request(new URL(req.url, backend), { method: req.method, headers }, response => {
      res.writeHead(response.statusCode || 502, { ...response.headers, 'cache-control': 'no-store' });
      response.pipe(res);
    });
    upstream.setTimeout(120000, () => upstream.destroy(new Error('Backend request timed out')));
    upstream.on('error', () => {
      if (res.headersSent) { res.destroy(); return; }
      res.writeHead(503, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
      res.end(JSON.stringify({ error: { code: 'BACKEND_UNAVAILABLE', message: 'The backend is not connected. Start the Python service and check BACKEND_ORIGIN.', retryable: true } }));
    });
    req.on('aborted', () => upstream.destroy());
    req.pipe(upstream);
    return;
  }
  if (!['GET', 'HEAD'].includes(req.method)) { res.writeHead(405); res.end(); return; }
  try {
    const path = resolve(frontend, `.${decodeURIComponent(url.pathname === '/' ? '/index.html' : url.pathname)}`);
    if (!path.startsWith(frontend + sep) || !(await stat(path)).isFile()) throw new Error('not found');
    const body = await readFile(path);
    res.writeHead(200, { 'Content-Type': mime[extname(path)] || 'application/octet-stream', 'Cache-Control': 'no-store' });
    res.end(req.method === 'HEAD' ? undefined : body);
  } catch { res.writeHead(404); res.end('Not found'); }
});
server.listen(port, '127.0.0.1', () => console.log(`Frontend: http://127.0.0.1:${server.address().port}\nAPI proxy: ${backend.origin}/api/v1\nNo backend or dataset is bundled.`));
