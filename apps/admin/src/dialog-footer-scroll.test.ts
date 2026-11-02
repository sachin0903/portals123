/**
 * `Dialog.Footer` must never live inside a scrolling element.
 *
 * It carries `-mx-4 -mb-4` (pkgs/ui `dialog-footer.svelte`), negative margins that deliberately
 * break out of `Dialog.Content`'s `p-4` so the muted bar reaches the dialog's edges and its
 * `rounded-b-xl` meets the corners. That only resolves against Content's padding. Put it inside an
 * `overflow-y-auto` element and the same margins make it 2rem wider than the content box, which
 * overflows — and because `overflow-y: auto` computes `overflow-x` from `visible` to `auto`, the
 * dialog grows a HORIZONTAL scrollbar. Measured on the real page: with the footer inside the
 * scroller the box went `scrollWidth 645` against `clientWidth 633`; moved out, `648 == 648`.
 *
 * Reported as "requesting a file from client — shows horizontal/vertical scrollbars on popup box",
 * and both bars are the same defect compounding: the footer inside the scroller also adds its own
 * height, pushing the content past `max-h` so the vertical bar appears, which narrows the content
 * box by ~15px and guarantees the horizontal one.
 *
 * A source scan, like `lib/catalogue/switch-columns.test.ts`, because nothing here is a type error
 * and the dialog still renders — it just overflows. The fix is structural: the form (or a plain
 * wrapper) is the flex column, the FIELDS scroll inside it, and the footer is a sibling of the
 * scrolling element. `overflow-x-hidden` is not the fix; it hides the bar by clipping the bar the
 * footer is trying to draw.
 */
import { describe, expect, it } from 'vitest';
import { globSync, readFileSync } from 'node:fs';

// Inlined rather than imported from a shared scan helper: this file is the whole of what needs
// them here, and the alternative is a module whose other exports police an addressing convention
// that has nothing to do with a dialog's footer.
/** The portals, relative to `apps/admin` (where the scan runs). */
const PORTALS = ['client', 'platform', 'admin'] as const;
const portalRoot = (app: string): string => `../${app}/src`;

/** Every component that renders a `Dialog.Footer`, across the three portals and the shared kit. */
const SOURCES = [
	...PORTALS.flatMap((app) => globSync(`${portalRoot(app)}/**/*.svelte`)),
	...globSync('../../pkgs/ui/src/**/*.svelte')
].filter((path) => readFileSync(path, 'utf8').includes('Dialog.Footer'));

/** Any utility that makes an element a scroll container on the block axis. */
const SCROLLS = /\boverflow(-y)?-(auto|scroll)\b/;

/**
 * The MARKUP alone: no `<script>`, no `<style>`, no comments.
 *
 * Dropping the script block is what makes the nesting walk below trustworthy rather than merely
 * plausible. TypeScript generics are angle-bracketed, so `Record<string, unknown>`, `Snippet<[]>`
 * and `as unknown as X<Y>` all parse as element opens — on its first run this scan reported four
 * false positives with ancestor chains containing `void` and `string, unknown[]`, because the
 * corrupted stack meant `<script>` never closed and every element appeared nested under it.
 *
 * Comments go for the reason `switch-columns.test.ts` records: the FIXED dialog carries a comment
 * explaining this very rule, naming `overflow-y-auto` and `-mx-4` in prose, and a scan over raw text
 * reports the explanation as the defect.
 */
function markupOnly(source: string): string {
	return source
		.replace(/<script[\s\S]*?<\/script>/gi, '')
		.replace(/<style[\s\S]*?<\/style>/gi, '')
		.replace(/<!--[\s\S]*?-->/g, '');
}

/**
 * The tags open at the point `Dialog.Footer` is rendered, innermost last.
 *
 * A deliberately small parser: it walks the markup counting element opens and closes, so what it
 * returns is the real ancestor chain rather than "a scroll class appears somewhere above". Svelte
 * blocks (`{#if}` / `{#each}`) are not elements and do not affect nesting.
 */
function ancestorsOfFooter(markup: string): string[][] {
	const found: string[][] = [];
	// `{ name, attrs }`, NOT a joined string: unwinding a close tag looks the name up, and a stack of
	// `"div class=…"` entries can never match `"div"` — so nothing ever closed, the stack only grew,
	// and every element in the file counted as an ancestor. That is what produced this scan's first
	// five "failures", every one of them a file whose footer is correctly placed.
	const stack: { name: string; attrs: string }[] = [];
	const tag = /<(\/?)([A-Za-z][\w.-]*)((?:[^>"']|"[^"]*"|'[^']*')*?)(\/?)>/g;
	for (const m of markup.matchAll(tag)) {
		const [, closing, name, attrs, selfClosing] = m;
		if (name === 'Dialog.Footer' && !closing) {
			found.push(stack.map((e) => `${e.name}${e.attrs}`));
		}
		if (closing) {
			const at = stack.findLastIndex((e) => e.name === name);
			if (at !== -1) stack.length = at;
		} else if (!selfClosing && !/^(br|hr|img|input|meta|link)$/i.test(name)) {
			stack.push({ name, attrs });
		}
	}
	return found;
}

describe('Dialog.Footer is never inside a scroll container', () => {
	it('finds the dialogs that have a footer at all', () => {
		// A scan that silently matches nothing passes for the wrong reason.
		expect(SOURCES.length).toBeGreaterThan(5);
	});

	it.each(SOURCES)('%s', (path) => {
		const markup = markupOnly(readFileSync(path, 'utf8'));
		for (const ancestors of ancestorsOfFooter(markup)) {
			const scrolling = ancestors.filter((a) => SCROLLS.test(a));
			expect(
				scrolling,
				`Dialog.Footer is inside a scrolling element in ${path}. Its -mx-4/-mb-4 breakout ` +
					`overflows a scroll box, which shows as a horizontal scrollbar. Make the fields ` +
					`scroll and leave the footer as their sibling.`
			).toEqual([]);
		}
	});
});
