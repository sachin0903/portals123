/**
 * Every form action this portal declares is reachable from something that renders.
 *
 * A form action is a PUBLIC POST endpoint, not a fragment of the page that happens to hold its
 * button. It survives the control that used to trigger it, keeps its permission guard and its API
 * call, and answers anyone who knows the name — while looking retired to everybody reading the
 * screen. Nothing else notices: it type-checks, it lints, and no test renders a button that is not
 * there.
 *
 * The platform portal has the same scan (`routes/clients/[uid]/page.server.test.ts`), written after
 * `?/setEnabled` outlived the Deactivate control by a whole design change. This is the admin half,
 * and portal-wide rather than per-route, because the defect is not specific to one page.
 *
 * TWO WAYS A TRIGGER IS SPELLED, and missing the second is how this test would produce nonsense:
 *
 *   1. a literal `action="?/name"` in markup;
 *   2. the BARE name injected into a shared component that prefixes `?/` itself — the platform
 *      portal passes `{ remove: 'remove' }` into `@graphene/ui`'s automations listing, which
 *      renders `action={`?/${actions.remove}`}`. A literal-only scan called nine such actions
 *      orphaned when every one of them works.
 *
 * So a bare quoted `'name'` anywhere in the markup counts as a reference. That is deliberately
 * LOOSE: it can only ever make this test more permissive, and a scan that wrongly blocks a working
 * action is worse than one that occasionally misses a dead one. `pkgs/ui` is searched as well as
 * this app's own markup, because the shared book surfaces are where most of these buttons live.
 */
import { describe, expect, it } from 'vitest';
import { globSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';

/** Walk up to the workspace root rather than counting `../` — a miscount globs nothing, and a
 *  scan of nothing passes. */
const ROOT = (() => {
	let dir = import.meta.dirname;
	while (!globSync('pnpm-workspace.yaml', { cwd: dir }).length) {
		const up = dirname(dir);
		if (up === dir) throw new Error(`workspace root not found above ${import.meta.dirname}`);
		dir = up;
	}
	return dir;
})();

/**
 * Declared but reachable from nothing, with the reason — the shape the API's own
 * `EXEMPT_MUTATING_ROUTES` list uses. The point of the list is that "there is no way to reach
 * this" becomes a decision somebody wrote down rather than a thing nobody noticed.
 */
const UNREACHABLE: Record<string, string> = {
	productPlatform: 'the products table shows platform refs but offers no control to edit them',
	// `platform.product_platforms` carries each custodian's own reference and name for one shelf
	// item, and the products table SHOWS those refs — `name · code · platform refs · offered` — with
	// no control to edit them. The endpoint is correct and has no caller; it needs a UI, and until it
	// has one this is dead wiring. Found while resolving a rebase conflict inside this very action.
	runQuery:
		'admin dataset detail publishes versions and details; the query-against-a-contact form lives on the platform authoring surface',
	createCategory:
		'admin content edit saves, publishes and archives; creating a category is a control on the platform editor, not copied here'
};

/** `export const <name>: Actions = { … }` — the keys at one level of indentation. */
function declaredActions(source: string): string[] {
	const lines = source.split('\n');
	const names: string[] = [];
	lines.forEach((line, i) => {
		if (!/^export const \w+: Actions = \{/.test(line)) return;
		for (const rest of lines.slice(i + 1)) {
			if (/^\}/.test(rest)) break;
			// One tab exactly: a deeper indent is inside a handler, not another action.
			const key = /^\t([A-Za-z_]\w*)\s*(?::|,\s*$)/.exec(rest);
			// `default` is the unnamed action; it is reached by posting to the page itself.
			if (key && key[1] !== 'default') names.push(key[1]);
		}
	});
	return names;
}

const SERVER_FILES = globSync('apps/admin/src/**/*.ts', { cwd: ROOT }).filter((file) =>
	readFileSync(join(ROOT, file), 'utf8').includes(': Actions = {')
);

const MARKUP = ['apps/admin/src/**/*.svelte', 'pkgs/ui/src/**/*.svelte']
	.flatMap((pattern) => globSync(pattern, { cwd: ROOT }))
	.map((file) => readFileSync(join(ROOT, file), 'utf8'))
	.join('\n');

describe('the admin portal’s form actions', () => {
	it('reaches the sources and the markup at all', () => {
		// Two floors, because each fails differently: no SERVER FILES means the glob or the
		// `: Actions = {` shape moved, and no ACTIONS means the key extraction did. Either way the
		// check below would pass by checking nothing.
		expect(
			SERVER_FILES.length,
			'no file declares actions — the scan has gone stale'
		).toBeGreaterThan(20);
		const all = SERVER_FILES.flatMap((f) => declaredActions(readFileSync(join(ROOT, f), 'utf8')));
		expect(all.length, 'matched files but extracted no action names').toBeGreaterThan(50);
		expect(MARKUP.length, 'found no markup to search').toBeGreaterThan(100_000);
	});

	it('has a trigger for every action, or a recorded reason it has none', () => {
		const orphaned: Record<string, string[]> = {};
		for (const file of SERVER_FILES) {
			const missing = declaredActions(readFileSync(join(ROOT, file), 'utf8')).filter(
				(name) =>
					!MARKUP.includes(`?/${name}`) && !MARKUP.includes(`'${name}'`) && !(name in UNREACHABLE)
			);
			if (missing.length) orphaned[file] = missing;
		}
		expect(orphaned, 'form actions no rendered control can reach').toEqual({});
	});

	it('keeps the exemption list honest', () => {
		const declared = new Set(
			SERVER_FILES.flatMap((f) => declaredActions(readFileSync(join(ROOT, f), 'utf8')))
		);
		const stale = Object.keys(UNREACHABLE).filter((name) => !declared.has(name));
		expect(stale, 'listed as unreachable but no longer declared at all').toEqual([]);
	});
});
