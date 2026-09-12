import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { canRun } from '../frontend/imports.js';
const spec = JSON.parse(readFileSync(new URL('../contracts/openapi.json', import.meta.url)));

test('all OpenAPI references resolve and operations have unique IDs', () => {
  const ids = new Set();
  for (const path of Object.values(spec.paths)) for (const op of Object.values(path)) {
    assert.ok(!ids.has(op.operationId)); ids.add(op.operationId);
    for (const [code,response] of Object.entries(op.responses)) assert.ok(response.content['application/json'].schema, `Missing response for ${code}`);
  }
  function visit(value) {
    if (!value || typeof value !== 'object') return;
    if (value.$ref) assert.ok(spec.components.schemas[value.$ref.split('/').at(-1)], value.$ref);
    Object.values(value).forEach(visit);
  }
  visit(spec);
});
test('LLM output cannot directly set assignment, priority or status', () => {
  const schema = JSON.parse(readFileSync(new URL('../contracts/llm-structuring.schema.json', import.meta.url)));
  const candidate = schema.properties.candidates.items;
  assert.equal(candidate.additionalProperties, false);
  for (const key of ['assignment','status','priority','assignedCrew']) assert.ok(!(key in candidate.properties));
  assert.ok(candidate.required.includes('sourceReportIds'));
});
test('all three real upload roles must be ready before running', () => {
  assert.equal(canRun(null),false);
  assert.equal(canRun({status:'draft',files:[]}),false);
  const files = ['reports','assets','jobs_history'].map(role=>({role,status:'ready'}));
  assert.equal(canRun({status:'draft',files}),true);
  assert.equal(canRun({status:'processing',files}),false);
  assert.equal(canRun({status:'completed',files}),false);
  assert.equal(canRun({status:'failed',files}),true);
  assert.equal(canRun({status:'draft',files:files.map((f,i)=>i===1?{...f,status:'invalid'}:f)}),false);
});
test('generated types match the canonical contract', () => {
  execFileSync(process.execPath, [new URL('../scripts/generate-types.mjs', import.meta.url).pathname,'--check']);
});
test('no exported CSVs, databases, LLM credentials or sample-data folders ship with the frontend', () => {
  const names = readdirSync(new URL('../frontend/', import.meta.url));
  assert.ok(!names.some(name=>/fixture|mock|sample|\.csv$|\.db$|\.sqlite$|\.env/.test(name)));
  for (const file of names.filter(name=>name.endsWith('.js'))) {
    const source = readFileSync(new URL('../frontend/'+file,import.meta.url),'utf8');
    assert.ok(!/reporter_contact|reporter_name|received_at|location_text/.test(source));
  }
});
