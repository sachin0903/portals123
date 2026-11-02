import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * The toast pattern is two halves that must stay wired together, and both failure modes are SILENT.
 *
 * `toast.success(…)` only ever writes to a store; the DOM is produced by the `<Toaster />` mounted in
 * an app's root layout. Call the one without mounting the other and the action still succeeds, the
 * console stays clean and the user simply gets no feedback — there is nothing to notice in review, in
 * a type-check or in a component unit test (the component under test renders its own Toaster).
 *
 * Two invariants, both static:
 *
 *  1. An app that calls `toast` mounts `<Toaster />` in its ROOT layout — root, so it covers every
 *     route including the unauthenticated branch, and once, so there is a single host.
 *  1b. An app that mounts `<NotificationBell />` mounts `<Toaster />` too. The bell pops a card when
 *     a notification arrives (`arrivals.ts`), and it raises it from INSIDE this package — so unlike
 *     invariant 1 there is no `toast.` call anywhere in the app's own source to notice. A portal
 *     that adds the bell and no Toaster silently drops every arrival pop-up while the badge and the
 *     dropdown keep working perfectly, which is the hardest version of this bug to spot.
 *  2. Nothing outside this package imports `svelte-sonner` directly. `@graphene/ui` owns the
 *     dependency and re-exports `toast` (index.ts) beside `Toaster` (./sonner.svelte); an app
 *     reaching past that is an undeclared dependency under pnpm's strict node_modules and — where it
 *     resolves at all — is the standard route to a SECOND module instance, i.e. a toast store nobody
 *     renders. See ../../../svelte-singleton.test.ts for why one instance is not negotiable.
 *
 * Honest limit: this pins the WIRING, not the bundling. It cannot see a dev-server dep-optimiser
 * that hands the page two copies of one module — that needs a browser, which this repo has no
 * harness for.
 */
const ROOT = join(import.meta.dirname, '../../../../../../..');
const APPS = join(ROOT, 'apps');

/** Every .svelte/.ts source file under a directory, recursively. */
function sourceFiles(dir: string): string[] {
	const found: string[] = [];
	for (const entry of readdirSync(dir)) {
		if (entry === 'node_modules' || entry === '.svelte-kit') continue;
		const path = join(dir, entry);
		if (statSync(path).isDirectory()) found.push(...sourceFiles(path));
		else if (/\.(svelte|ts)$/.test(entry)) found.push(path);
	}
	return found;
}

/** The SvelteKit apps — read from disk so a new portal is covered the day it is added. */
const portals = readdirSync(APPS).filter((name) => {
	try {
		return statSync(join(APPS, name, 'src', 'routes', '+layout.svelte')).isFile();
	} catch {
		return false;
	}
});

// A bare `toast(…)` is legal too, but every call site in the repo uses a method and matching the
// bare form would collide with local variables named `toast`.
const CALLS_TOAST =
	/\btoast\.(success|error|info|warning|message|promise|loading|custom|dismiss)\(/;
const IMPORTS_SONNER = /\bfrom\s+['"]svelte-sonner['"]/;

// The bell raises its arrival pop-ups from inside @graphene/ui, so an app that mounts it depends on
// a Toaster without containing a single `toast.` call of its own.
// Either bell: the streamed wrapper mounts the live one, and raises the same cards.
const MOUNTS_BELL = /<(Streamed)?NotificationBell[\s/>]/;

/**
 * Each portal's sources, walked and READ once at collection time.
 *
 * Every assertion below used to re-walk the tree and re-read every file — four cases across three
 * portals is twelve full walks of each portal's `src`, thousands of synchronous `statSync`/`readFileSync`
 * calls each. Fast enough alone, and over vitest's 5s default timeout the moment the machine is
 * busy: the pre-push hook runs the API's whole suite alongside this, and these cases took 13-19s
 * and failed as timeouts — which reads as "the portals dropped their Toaster", not as "the disk
 * was busy". Hoisting the work out of the timed body is the fix; the cases now do string matching
 * on memory.
 */
const SOURCES: Record<string, { path: string; text: string }[]> = Object.fromEntries(
	portals.map((portal) => [
		portal,
		sourceFiles(join(APPS, portal, 'src')).map((path) => ({
			path,
			text: readFileSync(path, 'utf8')
		}))
	])
);

/** A portal's root layout with comments stripped — a mount that has been commented out is exactly
 *  the regression these cases exist to catch. */
const LAYOUTS: Record<string, string> = Object.fromEntries(
	portals.map((portal) => [
		portal,
		readFileSync(join(APPS, portal, 'src/routes/+layout.svelte'), 'utf8')
			.replace(/<!--[\s\S]*?-->/g, '')
			.replace(/\/\*[\s\S]*?\*\//g, '')
	])
);

describe('toast is wired to a Toaster in every portal', () => {
	it('finds the portals to check', () => {
		// Guards the guard: a bad ROOT would silently make every case below vacuous.
		expect(portals.length).toBeGreaterThanOrEqual(3);
	});

	it.each(portals)('%s mounts <Toaster /> if it calls toast', (portal) => {
		const callers = SOURCES[portal].filter((f) => CALLS_TOAST.test(f.text));
		if (callers.length === 0) return;

		expect(
			/<Toaster[\s/>]/.test(LAYOUTS[portal]),
			`${portal} calls toast in ${callers.length} file(s) but its root layout mounts no <Toaster /> — ` +
				`every one of those toasts is silently dropped`
		).toBe(true);
	});

	it.each(portals)('%s mounts <Toaster /> if it mounts <NotificationBell />', (portal) => {
		if (!SOURCES[portal].some((f) => MOUNTS_BELL.test(f.text))) return;

		expect(
			/<Toaster[\s/>]/.test(LAYOUTS[portal]),
			`${portal} mounts <NotificationBell /> but its root layout mounts no <Toaster /> — the ` +
				`badge and dropdown still work, so nothing looks wrong, but every arrival pop-up is dropped`
		).toBe(true);
	});

	it.each(portals)('%s imports toast from @graphene/ui, never svelte-sonner', (portal) => {
		const offenders = SOURCES[portal]
			.filter((f) => IMPORTS_SONNER.test(f.text))
			.map((f) => f.path.slice(ROOT.length + 1));
		expect(offenders, `import { toast } from '@graphene/ui' instead`).toEqual([]);
	});
});

describe('the toast stack clears the topbar', () => {
	// The bell lives in the top-right of the chrome and a top-positioned stack defaults to 24px
	// from the edge, so an arriving notification sat on top of the bell and its badge — covering
	// the control it exists to point at. Static, because the overlap only shows in a browser at a
	// real viewport, which this repo has no harness for.
	const wrapper = readFileSync(join(import.meta.dirname, 'sonner.svelte'), 'utf8');

	it('offsets the top edge past the topbar height the shells declare', () => {
		const offset = wrapper.match(/const CLEAR_TOPBAR = \{([^}]*)\}/)?.[1] ?? '';
		expect(offset, 'sonner.svelte should define a CLEAR_TOPBAR offset').not.toBe('');
		const top = offset.match(/top:\s*'([\d.]+)rem'/)?.[1];
		expect(top, 'the offset should name a rem top edge').toBeDefined();
		// The shells declare `--topbar-h: 4rem` (pinned by shell/chrome.test.ts). Anything at or
		// under that puts the stack back over the bar.
		expect(Number(top)).toBeGreaterThan(4);
	});

	it('names only the top edge, so the other stacks do not move', () => {
		const offset = wrapper.match(/const CLEAR_TOPBAR = \{([^}]*)\}/)?.[1] ?? '';
		// Sonner fills an unnamed edge with its own default. Naming the sides or the bottom here
		// would shift the platform and admin action toasts, which sit in a bottom corner and have
		// nothing to do with the topbar.
		for (const edge of ['right', 'bottom', 'left']) {
			expect(offset, `CLEAR_TOPBAR should not name ${edge}`).not.toContain(`${edge}:`);
		}
	});
});
