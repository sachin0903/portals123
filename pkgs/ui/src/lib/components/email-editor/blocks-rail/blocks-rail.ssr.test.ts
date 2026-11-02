import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { createRawSnippet } from 'svelte';
import { render } from 'svelte/server';
import BlocksRail from './blocks-rail.svelte';

/** The palette rail. WHAT THESE MOSTLY PIN IS AN ABSENCE: the rail drew its own tiles once, with a
 *  hand-rolled drag, and it cost two rounds of broken drag-and-drop. It renders GrapesJS's panel only. */
const raw = readFileSync(join(import.meta.dirname, 'blocks-rail.svelte'), 'utf8');
/** Comments stripped: this file explains at length what it deliberately does NOT do. */
const source = raw.replace(/<!--[\s\S]*?-->/g, '').replace(/(^|[^:'"`\w])\/\/[^\n]*/g, '$1');

const palette = createRawSnippet(() => ({ render: () => '<div data-testid="palette"></div>' }));
const FIELDS = [
	{ group: 'Recipient', fields: [{ label: 'First name', token: '{{ first_name }}' }] }
];

describe('the rail', () => {
	it('renders the palette mount, always', () => {
		// GrapesJS mounts into it ONCE, at init; there is no second mount if the element goes away.
		const { body } = render(BlocksRail, { props: { paletteMount: palette } });
		expect(body).toContain('data-testid="palette"');
		expect(body).toContain('aria-label="Blocks"');
	});

	it('hides the blocks flyout rather than removing it', () => {
		const { body } = render(BlocksRail, { props: { paletteMount: palette } });
		expect(body).toMatch(/class="[^"]*\bhidden\b[^"]*"/);
	});

	it('scopes the palette with the class its re-theme keys off', () => {
		// `create.ts` styles the panel by `.gjs-email-palette`, so the two editors' sheets cannot collide.
		const { body } = render(BlocksRail, { props: { paletteMount: palette } });
		expect(body).toContain('gjs-email-palette');
	});

	it('offers merge fields only with both a vocabulary and somewhere to insert', () => {
		const withBoth = render(BlocksRail, {
			props: { paletteMount: palette, mergeFields: FIELDS, oninsertField: () => {} }
		});
		expect(withBoth.body).toContain('aria-label="Merge fields"');

		const noCallback = render(BlocksRail, {
			props: { paletteMount: palette, mergeFields: FIELDS }
		});
		expect(noCallback.body).not.toContain('aria-label="Merge fields"');

		const noFields = render(BlocksRail, {
			props: { paletteMount: palette, oninsertField: () => {} }
		});
		expect(noFields.body).not.toContain('aria-label="Merge fields"');
	});
});

/** The rail is the second door to the context panel — the flyout is a short scoped list, the panel
 *  answers where the details come from and what ticking another context would add. An author asking
 *  "where does this come from?" is looking at the flyout when they ask it, so the door has to be
 *  there and not only in the page's ... menu. */
describe('the door to the context panel', () => {
	it('is offered from the fields flyout when the caller has a panel to open', () => {
		const withPanel = render(BlocksRail, {
			props: {
				paletteMount: palette,
				mergeFields: FIELDS,
				oninsertField: () => {},
				onreference: () => {},
				open: 'fields'
			}
		});
		expect(withPanel.body).toContain('What this email is about');
	});

	it('is absent for a mount with no panel', () => {
		const noPanel = render(BlocksRail, {
			props: {
				paletteMount: palette,
				mergeFields: FIELDS,
				oninsertField: () => {},
				open: 'fields'
			}
		});
		expect(noPanel.body).not.toContain('What this email is about');
	});
});

describe('the drag is GrapesJS’s, not ours', () => {
	it('draws no BLOCK tiles and drives no block drag', () => {
		// THE RULE THIS FILE EXISTS FOR: any of these means the rail has begun reimplementing the block drag.
		expect(source).not.toMatch(/onpointerdown/);
		expect(source).not.toMatch(/dragStart|dragStop/);
		expect(source).not.toMatch(/getMedia|getLabel/);
		expect(source).toMatch(/\{@render paletteMount\(\)\}/);
	});

	it('drives no drag at all, fields included', () => {
		expect(source).not.toMatch(/draggable/);
		expect(source).not.toMatch(/ondragstart|ondragend|dataTransfer/);
	});

	it('says the tiles are clicked, because a palette does not usually mean that', () => {
		// Prettier wraps the sentence, so match on its two halves rather than one line.
		expect(source).toContain('Click one to insert it where you are');
	});

	it('says WHO fills the listed fields, before how to use them', () => {
		// An author looking at a list they did not write needs to know who fills it, or a field reads as
		// a promise nobody made.
		expect(source).toMatch(/The platform fills these in when the email is sent/);
		expect(source).toMatch(/Only when a notification rule is what sends this email/);
	});

	it('does not portal the flyout', () => {
		// `Popover.Content` renders into `document.body` — the standing pkgs/ui gotcha.
		expect(source).not.toMatch(/from '[^']*popover/);
		expect(source).toMatch(/absolute top-2 left-full/);
	});

	it('adds no document listeners and no outside-click dismissal', () => {
		// A drag that leaves the flyout IS an outside pointerdown; dismissing on one would unmount mid-drag.
		expect(source).not.toMatch(/addEventListener/);
		expect(source).not.toMatch(/clickOutside|onclickoutside/i);
	});
});
