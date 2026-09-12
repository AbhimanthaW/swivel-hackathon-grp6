/** Dependency-free generator for the JSON Schema constructs used by this contract. */
import { readFileSync, writeFileSync } from 'node:fs';
const source = JSON.parse(readFileSync(new URL('../contracts/openapi.json', import.meta.url), 'utf8'));
function type(schema) {
  if (schema.$ref) return schema.$ref.split('/').at(-1);
  if (schema.const !== undefined) return JSON.stringify(schema.const);
  if (schema.enum) return schema.enum.map(value => JSON.stringify(value)).join(' | ');
  if (schema.anyOf) return schema.anyOf.map(type).join(' | ');
  if (schema.type === 'array') return `Array<${type(schema.items)}>`;
  if (schema.type === 'object') return `{\n${Object.entries(schema.properties).map(([key, value]) => `  ${key}${schema.required?.includes(key) ? '' : '?'}: ${type(value)};`).join('\n')}\n}`;
  if (schema.type === 'integer' || schema.type === 'number') return 'number';
  if (['string', 'boolean', 'null'].includes(schema.type)) return schema.type;
  throw new Error(`Unsupported schema construct: ${JSON.stringify(schema)}`);
}
const output = '// Generated from contracts/openapi.json. Run npm run types after contract changes.\n\n' + Object.entries(source.components.schemas).map(([name, schema]) => `export type ${name} = ${type(schema)};`).join('\n\n') + '\n';
const target = new URL('../frontend/domain.d.ts', import.meta.url);
if (process.argv.includes('--check')) {
  if (readFileSync(target, 'utf8') !== output) throw new Error('domain.d.ts is out of sync. Run npm run types.');
} else writeFileSync(target, output);
