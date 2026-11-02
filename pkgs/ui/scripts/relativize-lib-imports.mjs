// Rewrites `$lib/...` import specifiers in pkgs/ui to real relative paths.
//
// shadcn-svelte's CLI emits `$lib/...` imports. That alias is fine inside a
// SvelteKit app, but @graphene/ui is consumed as SOURCE by the apps, where each
// app's `$lib` points at ITS OWN src/lib — not this package's. So after every
// `shadcn-svelte add`, run this to convert `$lib/<x>` to the correct path
// relative to each file. Idempotent.
//
//   node pkgs/ui/scripts/relativize-lib-imports.mjs

import { readdirSync, statSync, readFileSync, writeFileSync } from 'node:fs';
import { join, relative, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const LIB = fileURLToPath(new URL('../src/lib', import.meta.url));

let changed = 0;
function walk(dir) {
	for (const entry of readdirSync(dir)) {
		const p = join(dir, entry);
		if (statSync(p).isDirectory()) walk(p);
		else if (/\.(svelte|ts|js)$/.test(entry)) fix(p);
	}
}

function fix(file) {
	const src = readFileSync(file, 'utf8');
	const out = src.replace(/(['"])\$lib\/([^'"]*)\1/g, (_m, q, sub) => {
		let rel = relative(dirname(file), join(LIB, sub)).replace(/\\/g, '/');
		if (!rel.startsWith('.')) rel = './' + rel;
		return `${q}${rel}${q}`;
	});
	if (out !== src) {
		writeFileSync(file, out);
		changed++;
	}
}

walk(LIB);
console.log(`relativized $lib imports in ${changed} file(s)`);
