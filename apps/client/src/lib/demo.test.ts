/**
 * The `$lib/demo` re-export shim, and the boundary around it.
 *
 * The audit's point was not that `formatDate` was broken — it is that importing it from a module
 * called `demo` makes real-vs-fabricated invisible at the call site, so nobody notices when the
 * invented holdings, activity and adviser next door start being rendered to a signed-in client. The
 * real formatters now live in `$lib/format`, but `demo.ts` still re-exports them so the call sites
 * keep compiling mid-migration, which means the trap is still reachable from every one of them.
 *
 * Two things are pinned here, both about that boundary:
 *
 * 1. The shim is a shim: every re-exported symbol is the *same function* as `$lib/format`'s, so
 *    nobody can quietly fork a second `formatDate` — or, worse, a second countdown helper measuring
 *    against `DEMO_NOW` — back into the fixture module.
 * 2. The importer list cannot GROW. Deliberately one-directional: migrating a file is fine (the list
 *    shrinks), adding a new `$lib/demo` import is not.
 */

import { readdirSync, readFileSync } from 'node:fs';
import { join, relative, sep } from 'node:path';
import { describe, expect, it } from 'vitest';
import * as demo from './demo';
import * as format from './format';

const SRC = join(process.cwd(), 'src');

/** Every source file under `src/` that imports from `$lib/demo`. */
function demoImporters(): string[] {
	const found: string[] = [];
	const walk = (dir: string): void => {
		for (const entry of readdirSync(dir, { withFileTypes: true })) {
			const full = join(dir, entry.name);
			if (entry.isDirectory()) {
				walk(full);
				continue;
			}
			if (!/\.(svelte|ts)$/.test(entry.name)) continue;
			if (/^demo(\.test)?\.ts$/.test(entry.name)) continue; // the module itself + this test
			if (readFileSync(full, 'utf8').includes("'$lib/demo'")) {
				found.push(relative(SRC, full).split(sep).join('/'));
			}
		}
	};
	walk(SRC);
	return found.sort();
}

/**
 * The files still importing through the shim, as of this change. Each needs a one-line import
 * rewrite (`$lib/demo` → `$lib/format`) and all of them belong to other feature areas (documents,
 * requests, share), so they are handed off rather than churned here. Shrink this list as they
 * migrate; never add to it.
 */
const REMAINING = [
	'lib/documents.ts',
	'lib/format.test.ts',
	'routes/transfer/[kind]/+page.server.ts',
	'routes/transfer/transfer.test.ts'
];

describe('the demo shim only re-exports; it never forks a second copy', () => {
	const shimmed = ['formatDateTime', 'daysUntil', 'daysUntilDue', 'daysUntilExpiry'] as const;

	it.each(shimmed)('%s is the identical binding from $lib/format', (name) => {
		expect(demo[name]).toBeDefined();
		expect(demo[name]).toBe(format[name]);
	});

	it('keeps the fixed demo clock out of the real module', () => {
		// DEMO_NOW is why the countdowns moved: a share link's real `expiresAt` measured against a
		// hardcoded 2026-06-22 is silently wrong rather than visibly broken.
		expect(demo.DEMO_NOW).toBeInstanceOf(Date);
		expect('DEMO_NOW' in format).toBe(false);
		// And the helpers that used to subtract from it now default to the real clock.
		const inFive = new Date(Date.now() + 5 * 86_400_000).toISOString();
		expect(format.daysUntil(inFive)).toBe(5);
	});

	it('leaves the fixture data where it is', () => {
		for (const fixture of ['holdings', 'activity', 'adviser', 'documents']) {
			expect(fixture in demo, `${fixture} is fixture data and belongs in demo.ts`).toBe(true);
			expect(fixture in format, `${fixture} must not be in $lib/format`).toBe(false);
		}
	});
});

describe('the $lib/demo import boundary', () => {
	it('has no importer that is not already on the known list', () => {
		const unexpected = demoImporters().filter((f) => !REMAINING.includes(f));
		expect(unexpected, 'New code must import formatters from $lib/format, not $lib/demo').toEqual(
			[]
		);
	});

	it('actually looks at files (so the check cannot pass by finding nothing)', () => {
		expect(demoImporters().length).toBeGreaterThan(0);
	});
});
