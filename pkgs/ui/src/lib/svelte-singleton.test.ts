import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync, realpathSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * There must be exactly ONE copy of the Svelte runtime (and of @sveltejs/kit) in the workspace.
 *
 * This is not hygiene — a second copy is a silent, total UI failure. bits-ui, runed, svelte-toolbelt,
 * mode-watcher, svelte-sonner, @lucide/svelte, svelte-dnd-action and @xyflow/svelte all declare
 * `svelte` as a PEER dependency, and pnpm happily satisfied those peers with svelte 5.56.0 (+ kit
 * 2.61.1) while the apps and this package ran 5.56.8 (+ kit 2.70.1). Two runtimes means two
 * schedulers: reactive blocks created under one are never driven by the other, so hydration cleared
 * the body of every {#if}/{#each} and nothing ever refilled it. Symptoms, all three portals at once:
 * a page renders for one frame then goes blank, every popover/select (nationality, tax residence) is
 * inert, the theme toggle does nothing, and `use:enhance` never attaches — with NOTHING thrown and a
 * clean console, which is what made it so expensive to diagnose.
 *
 * Guarded in two places: the `svelte`/`@sveltejs/kit` overrides in pnpm-workspace.yaml pin the
 * install graph, and `resolve.dedupe` in each app's vite.config.ts pins the browser bundle. This test
 * fails if either regresses.
 */
function installedVersions(pkg: string): string[] {
	const root = join(import.meta.dirname, '../../../..');
	const out = execFileSync(
		'find',
		[
			'.',
			'-path',
			`*/node_modules/${pkg}/package.json`,
			'-not',
			'-path',
			'*/node_modules/*/node_modules/*/node_modules/*'
		],
		{ cwd: root, encoding: 'utf8', maxBuffer: 8 * 1024 * 1024 }
	);
	const versions = out
		.split('\n')
		.filter(Boolean)
		.map((rel) => {
			try {
				return JSON.parse(readFileSync(join(root, rel), 'utf8')).version as string;
			} catch {
				return '';
			}
		})
		.filter(Boolean);
	return [...new Set(versions)].sort();
}

/**
 * Resolved ONCE, at collection time.
 *
 * Each call shells out to `find` over the whole workspace `node_modules` — a second or two idle,
 * and over vitest's 5s default the moment the machine is busy: under the pre-push hook, which runs
 * the API's whole suite alongside this, the svelte case took 10s and failed as a TIMEOUT. A timeout
 * here reports "the workspace has two Svelte runtimes", which is the most alarming thing this file
 * can say and was not true. The walk belongs outside the timed body.
 */
const RESOLVED: Record<string, string[]> = {
	svelte: installedVersions('svelte'),
	'@sveltejs/kit': installedVersions('@sveltejs/kit'),
	'svelte-sonner': installedVersions('svelte-sonner')
};

describe('workspace has a single Svelte runtime', () => {
	it('resolves exactly one version of svelte', () => {
		const versions = RESOLVED.svelte;
		expect(versions.length, `expected 1 svelte, found: ${versions.join(', ')}`).toBe(1);
	});

	it('resolves exactly one version of @sveltejs/kit', () => {
		const versions = RESOLVED['@sveltejs/kit'];
		expect(versions.length, `expected 1 kit, found: ${versions.join(', ')}`).toBe(1);
	});

	/**
	 * svelte-sonner for the same reason, one layer up. Its `toast` is not a function the caller owns
	 * but a handle on ONE module-level store, and `<Toaster />` renders whatever that store holds — so
	 * two copies means the app writes to a store nobody renders. It fails exactly like a missing
	 * Toaster: the action succeeds, the console is clean, and the user gets no feedback. `@graphene/ui`
	 * is the only declared consumer (index.ts re-exports `toast`, sonner.svelte imports `Toaster`);
	 * `toaster-wiring.test.ts` keeps the apps off it directly.
	 */
	it('resolves exactly one version of svelte-sonner', () => {
		const versions = RESOLVED['svelte-sonner'];
		expect(versions.length, `expected 1 svelte-sonner, found: ${versions.join(', ')}`).toBe(1);
	});
});

describe('one Svelte INSTANCE, not merely one version', () => {
	// The version check above dedupes by version string, which is a weaker property than the one the
	// `overrides` block in pnpm-workspace.yaml exists to guarantee. pnpm keys a store directory on the
	// package AND its resolved peers, so `svelte@5.56.8_@typescript-eslint+types@8.65.0` and
	// `…@8.66.0` are two physical copies of ONE version — two module instances, two schedulers, which
	// is precisely the failure that comment describes: hydration clears every {#if}/{#each} body and
	// nothing refills it, with no error thrown on any portal.
	//
	// Both existed here after a typescript-eslint bump left the older peer variant behind. Nothing
	// linked to it that time, so it was harmless — but the version check would have stayed green
	// either way, which is the hole this closes.
	const CONSUMERS = ['apps/client', 'apps/platform', 'apps/admin', 'pkgs/ui'];

	for (const pkg of ['svelte', '@sveltejs/kit']) {
		it(`every consumer resolves the same physical ${pkg}`, () => {
			const root = join(import.meta.dirname, '../../../..');
			const resolved = new Map<string, string>();
			for (const consumer of CONSUMERS) {
				const entry = join(root, consumer, 'node_modules', pkg, 'package.json');
				if (!existsSync(entry)) continue; // pkgs/ui does not depend on kit
				resolved.set(consumer, realpathSync(entry));
			}
			expect(
				resolved.size,
				`no consumer resolves ${pkg} — the check would be vacuous`
			).toBeGreaterThan(0);
			const distinct = new Set(resolved.values());
			expect(
				distinct.size,
				`${pkg} resolves to ${distinct.size} different copies:\n` +
					[...resolved].map(([c, p]) => `  ${c} -> ${p}`).join('\n')
			).toBe(1);
		});
	}
});
