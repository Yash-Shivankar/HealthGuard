/**
 * Locale parity check.
 *
 * A half-translated page is worse than an untranslated one when the subject is
 * someone's symptoms: a reader sees their own language and trusts it, then hits
 * an English fragment mid-sentence and cannot tell whether the meaning survived.
 *
 * So every locale must carry every key, and the plural forms i18next v21+
 * expects (_one/_other) must be present wherever the base file uses them. This
 * runs in `npm run build`, so a missing key fails the build rather than
 * appearing in a clinic.
 */

import { readFileSync } from 'node:fs';

const DIR = new URL('../src/utils/i18n/locales/', import.meta.url);
const BASE = 'en';
const OTHERS = ['hi', 'mr'];

const load = (code) => JSON.parse(readFileSync(new URL(`${code}.json`, DIR), 'utf8'));

function flatten(value, prefix = '') {
  return Object.entries(value).flatMap(([key, child]) =>
    child !== null && typeof child === 'object'
      ? flatten(child, `${prefix}${key}.`)
      : [`${prefix}${key}`],
  );
}

const base = new Set(flatten(load(BASE)));
let failed = false;

for (const code of OTHERS) {
  const keys = new Set(flatten(load(code)));
  const missing = [...base].filter((key) => !keys.has(key));
  const extra = [...keys].filter((key) => !base.has(key));

  if (missing.length || extra.length) {
    failed = true;
    console.error(`\n${code}.json is out of step with ${BASE}.json:`);
    if (missing.length) console.error(`  missing: ${missing.join(', ')}`);
    if (extra.length) console.error(`  not in ${BASE}: ${extra.join(', ')}`);
  } else {
    console.log(`${code}.json: ${keys.size} keys, matches ${BASE}.json`);
  }
}

// i18next v21 renamed the plural suffix from _plural to _one/_other. A file
// still using the old form silently renders the singular for every count.
for (const code of [BASE, ...OTHERS]) {
  const stale = flatten(load(code)).filter((key) => key.endsWith('_plural'));
  if (stale.length) {
    failed = true;
    console.error(`\n${code}.json uses the removed _plural suffix: ${stale.join(', ')}`);
    console.error('  Use _one and _other instead (i18next v21+).');
  }
}

if (failed) process.exit(1);
console.log('Locales are consistent.');
