import { globSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

/**
 * `CardHeader` is a GRID, so the flexbox idiom that used to right-align its trailing control is
 * dead code that still type-checks.
 *
 * shadcn-svelte 1.x rebuilt the header as `grid auto-rows-min …
 * has-data-[slot=card-action]:grid-cols-[1fr_auto]`. `class="flex-row items-center justify-between"`
 * only sets `flex-direction`/`align-items`/`justify-content` — none of which do anything without
 * `display: flex` — so the header stays a single-column grid and the trailing element wraps onto a
 * second ROW, left-aligned under the title. That is exactly how the account record's Address, Bank
 * details and Fees pencils came to sit under their headings instead of opposite them.
 *
 * Nothing else can see this: the markup is valid, the classes are real Tailwind, svelte-check and
 * eslint pass, and an SSR test that asserts on the class list would pass too — the failure is what
 * the browser DOES with those classes. So the rule is written on the source.
 *
 * The fix is `<CardAction>` (`data-slot="card-action"`), which both switches the grid to two
 * columns and places itself in the right-hand one.
 */

/** The workspace root, named ONCE: this file sits seven directories down from it, and counting
 *  `../` per entry is how the first version of this scan silently missed three of the four trees. */
const WORKSPACE = fileURLToPath(new URL('../../../../../../../', import.meta.url));
/** pkgs/ui + the three portals — the rule is one rule, and a copy per app is a copy to forget. */
const ROOTS = ['pkgs/ui/src', 'apps/client/src', 'apps/platform/src', 'apps/admin/src'];

/** Every `<CardHeader …>` opening tag in the workspace, as `{file, tag}`. */
function cardHeaders(): { file: string; tag: string }[] {
	const found: { file: string; tag: string }[] = [];
	for (const root of ROOTS) {
		for (const file of globSync(`${WORKSPACE}${root}/**/*.svelte`)) {
			const source = readFileSync(file, 'utf8');
			for (const [tag] of source.matchAll(/<CardHeader[^>]*>/g)) {
				found.push({ file: file.replaceAll('\\', '/'), tag });
			}
		}
	}
	return found;
}

/** Flexbox properties, which need a `display: flex` to mean anything. */
const FLEX_PROPERTIES = /\b(flex-row|justify-between|space-y-0)\b/;
/** The `flex` class itself, matched whole — `\bflex\b` would also see it inside `flex-row`. */
const SETS_DISPLAY_FLEX = /["\s]flex(?=["\s])/;

/** Flexbox properties with no `display: flex` to act on — dead classes on the header's grid. */
const deadOnTheGrid = (tag: string) => FLEX_PROPERTIES.test(tag) && !SETS_DISPLAY_FLEX.test(tag);

describe('CardHeader is a grid, not a flex row', () => {
	it('finds the headers it is meant to be scanning', () => {
		// The guard on the guard: a moved directory must fail loudly, not pass vacuously. There are
		// ~90 across the four trees; the floor only has to be high enough that losing a whole tree
		// (the bug this scan shipped with) cannot slip through.
		expect(cardHeaders().length).toBeGreaterThan(60);
	});

	it('no header carries flexbox properties with nothing to act on', () => {
		const offenders = cardHeaders()
			.filter(({ tag }) => deadOnTheGrid(tag))
			.map(({ file, tag }) => `${file.slice(file.indexOf('/src/') + 1)} → ${tag}`);
		expect(offenders).toEqual([]);
	});

	it('would catch the regression it exists for', () => {
		// The exact shape that shipped on the account record's three panels.
		expect(
			deadOnTheGrid('<CardHeader class="flex-row items-center justify-between space-y-0 pb-3">')
		).toBe(true);
		for (const good of [
			// What replaced it: padding and gap are real on a grid.
			'<CardHeader class="pb-3">',
			'<CardHeader class="gap-2 pb-2">',
			'<CardHeader>',
			// And asking for `display: flex` OUTRIGHT is not the bug — the properties then have
			// something to act on. `client-record.svelte` does this deliberately, so the rule is
			// "no dead classes", not "never flex".
			'<CardHeader class="flex flex-row items-center justify-between gap-3 pb-3">'
		]) {
			expect(deadOnTheGrid(good), good).toBe(false);
		}
	});
});
