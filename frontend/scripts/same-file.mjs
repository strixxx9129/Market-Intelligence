// Fail (exit 1) when two files differ; always delete the second (a temporary check copy).
// Used by `pnpm gen:api:check` in CI: the committed client must match openapi.json.
import { readFileSync, rmSync } from 'node:fs';

const [committed, fresh] = process.argv.slice(2);
const same = readFileSync(committed, 'utf8') === readFileSync(fresh, 'utf8');
rmSync(fresh);
if (!same) {
  console.error(`${committed} is out of date: run \`pnpm gen:api\` (after \`make openapi\`).`);
  process.exit(1);
}
console.log(`${committed} matches openapi.json`);
