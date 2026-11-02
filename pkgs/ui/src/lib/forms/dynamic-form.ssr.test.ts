import { createRawSnippet } from 'svelte';
import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import DynamicForm from './dynamic-form.svelte';
import { sectionAnchorId } from './editor/compute';
import { SECTION_NAV_MIN_FIELDS } from './section-nav.js';
import type { FormField } from './types.js';

/**
 * SSR pins for section descriptions and the optional-section switch.
 *
 * The switch is not decoration: a section switched off is excluded from `collect()`, so it
 * decides what the request says. "No contributions" and "contributions of zero" are different
 * claims, and the second one is the one that silently changes a projection.
 */

const field = (key: string, section: string, over: Partial<FormField> = {}): FormField => ({
	uid: `f-${key}`,
	fieldKey: key,
	fieldType: 'text',
	label: `Field ${key.toUpperCase()}`,
	section,
	description: null,
	required: false,
	conditions: null,
	validation: null,
	options: null,
	sortOrder: 0,
	...over
});

const FIELDS: FormField[] = [
	field('a', 'Always'),
	field('b', 'Maybe', { sortOrder: 1 }),
	field('c', 'Maybe', { sortOrder: 2 })
];

describe('DynamicForm — section metadata (SSR)', () => {
	it('renders a section description under its heading', () => {
		const { body } = render(DynamicForm, {
			props: {
				fields: FIELDS,
				sectionMeta: { Always: { description: 'What this section is for.' } }
			}
		});
		expect(body).toContain('What this section is for.');
	});

	it('renders no description when a section declares none', () => {
		const { body } = render(DynamicForm, { props: { fields: FIELDS } });
		expect(body).toContain('Always');
		expect(body).not.toContain('What this section is for.');
	});

	it('renders a section lead inside that section before its fields', () => {
		const lead = createRawSnippet(() => ({
			render: () => '<p data-testid="section-lead">Applied fees</p>'
		}));
		const { body } = render(DynamicForm, {
			props: { fields: FIELDS, sectionMeta: { Always: { lead } } }
		});
		const section = body.slice(body.indexOf('>Always<'), body.indexOf('>Maybe<'));
		expect(section).toContain('data-testid="section-lead"');
		expect(section.indexOf('Applied fees')).toBeLessThan(section.indexOf('Field A'));
	});

	it('hides an optional section’s fields until it is switched on', () => {
		const off = render(DynamicForm, {
			props: { fields: FIELDS, sectionMeta: { Maybe: { optional: true, defaultOn: false } } }
		});
		// The heading stays — the adviser has to be able to find the switch — but the fields go.
		expect(off.body).toContain('Maybe');
		expect(off.body).not.toContain('Field B');

		const on = render(DynamicForm, {
			props: {
				fields: FIELDS,
				sectionMeta: { Maybe: { optional: true, defaultOn: false } },
				enabledSections: { Maybe: true }
			}
		});
		expect(on.body).toContain('Field B');
	});

	it('defaultOn opens the section without the host having to seed enabledSections', () => {
		const { body } = render(DynamicForm, {
			props: { fields: FIELDS, sectionMeta: { Maybe: { optional: true, defaultOn: true } } }
		});
		expect(body).toContain('Field B');
	});

	it('an explicit enabledSections entry beats defaultOn', () => {
		const { body } = render(DynamicForm, {
			props: {
				fields: FIELDS,
				sectionMeta: { Maybe: { optional: true, defaultOn: true } },
				enabledSections: { Maybe: false }
			}
		});
		expect(body).not.toContain('Field B');
	});

	it('never renders a switch for a section that is not optional', () => {
		// A switch on a mandatory section would offer an adviser a request the engine will reject.
		const { body } = render(DynamicForm, {
			props: { fields: FIELDS, sectionMeta: { Always: { description: 'Required.' } } }
		});
		expect(body).not.toContain('aria-label="Include Always"');
	});

	it('labels the switch with the section it governs', () => {
		const { body } = render(DynamicForm, {
			props: { fields: FIELDS, sectionMeta: { Maybe: { optional: true } } }
		});
		expect(body).toContain('aria-label="Include Maybe"');
	});

	it('leaves a form with no sectionMeta exactly as it was', () => {
		// Every existing caller passes none; none of them should gain a switch or lose a field.
		const { body } = render(DynamicForm, { props: { fields: FIELDS } });
		expect(body).toContain('Field A');
		expect(body).toContain('Field B');
		expect(body).not.toContain('aria-label="Include');
	});

	it('does not clip a section, so an inline overlay can escape the card', () => {
		// AddressLookup's suggestion list is absolutely positioned rather than portalled (a Popover
		// panel lands outside a Sheet's focus trap). An overflow-hidden ancestor crops it.
		const { body } = render(DynamicForm, { props: { fields: FIELDS } });
		const cards = [...body.matchAll(/data-slot="card"[^>]*class="([^"]*)"/g)].map((m) => m[1]);
		expect(cards.length).toBeGreaterThan(0);
		for (const cls of cards) {
			expect(cls).toContain('overflow-visible');
			expect(cls).not.toContain('overflow-hidden');
		}
	});

	it('does not render a derived hidden sidecar as an editable field', () => {
		const { body } = render(DynamicForm, {
			props: {
				fields: [
					field('national_identifier', 'Personal details', {
						fieldType: 'nationalIdentifier',
						label: 'National identifier'
					}),
					field('national_identifier_type', 'Personal details', {
						label: 'Identifier type',
						validation: { hidden: true }
					})
				]
			}
		});
		expect(body).toContain('National identifier');
		expect(body).not.toContain('Identifier type');
	});
});

/**
 * The section rail. `sectionNav` is what a long multi-section form is navigated by (the journey
 * step page and the illustration drawer both turn it on), and the two halves of it have to agree:
 * the rail's targets are ids, and the sections have to actually carry them or every jump is a no-op
 * that reports nothing. The scroll-spy itself is client-only and out of reach here — what SSR can
 * pin is the wiring, the opt-in and the responsive collapse.
 */
describe('DynamicForm — section nav (SSR)', () => {
	/** The rail's own entries. Scoped to the `<nav>` element rather than scanned across the whole
	 *  body, which swept up the form's Submit button — the same false positive scoping fixes in
	 *  switch-columns.test.ts. */
	/** Long enough for a section nav (`wantsSectionNav`): two sections that open a screen long. */
	const LONG: FormField[] = [
		field('a', 'Always'),
		...Array.from({ length: SECTION_NAV_MIN_FIELDS - 1 }, (_, i) =>
			field(`m${i}`, 'Maybe', { sortOrder: i + 1 })
		)
	];

	const navButtons = (body: string): string[] => {
		const nav = /<nav[^>]*aria-label="Form sections"[\s\S]*?<\/nav>/.exec(body)?.[0] ?? '';
		return [...nav.matchAll(/<button[^>]*>([\s\S]*?)<\/button>/g)].map((m) =>
			m[1].replace(/<[^>]*>/g, '').trim()
		);
	};

	it('is off by default, so a short form is unchanged', () => {
		// Every pre-existing caller passes nothing and must not grow a rail.
		const { body } = render(DynamicForm, { props: { fields: LONG } });
		expect(body).not.toContain('aria-label="Form sections"');
		expect(body).not.toContain('@4xl:grid-cols-[13rem_minmax(0,1fr)]');
	});

	it('stays off on a short form even when switched on', () => {
		// Two sections over three fields is quicker to scroll than to navigate, so neither the rail
		// nor the dropdown that stands in for it on a narrow screen is drawn.
		const { body } = render(DynamicForm, { props: { fields: FIELDS, sectionNav: true } });
		expect(body).not.toContain('aria-label="Form sections"');
		expect(body).not.toContain('Jump to section');
		expect(body).not.toContain('@4xl:grid-cols-[13rem_minmax(0,1fr)]');
	});

	it('counts an optional section that opens switched off as not there', () => {
		// The SIPP illustration's shape: a long form on paper, a short one when it opens.
		const { body } = render(DynamicForm, {
			props: { fields: LONG, sectionNav: true, sectionMeta: { Maybe: { optional: true } } }
		});
		expect(body).not.toContain('aria-label="Form sections"');
	});

	it('lists every visible section once when switched on', () => {
		const { body } = render(DynamicForm, { props: { fields: LONG, sectionNav: true } });
		expect(body).toContain('aria-label="Form sections"');
		// Two sections over twelve fields — the rail lists sections, not fields.
		expect(navButtons(body)).toEqual(['Always', 'Maybe']);
	});

	it('points each rail entry at an id the section actually carries', () => {
		// The jump is `getElementById`, so a label that does not resolve to a rendered id is a
		// silently dead nav item. `sectionAnchorId` is the single spelling of that id on both sides.
		const { body } = render(DynamicForm, { props: { fields: LONG, sectionNav: true } });
		for (const section of ['Always', 'Maybe']) {
			expect(body).toContain(`id="${sectionAnchorId(section)}"`);
		}
	});

	it('honours --form-scroll-offset on the section itself, not just the rail', () => {
		// A jumped-to heading otherwise lands underneath the app's sticky topbar. The host sets the
		// variable; the scroll-margin here is what spends it.
		const { body } = render(DynamicForm, { props: { fields: LONG, sectionNav: true } });
		expect(body).toContain('scroll-mt-[var(--form-scroll-offset,1rem)]');
	});

	it('collapses to a single column until the form itself is wide enough', () => {
		// The rail follows the form's width, not the window: a narrow dialog on a desktop must not
		// spend 13rem on a rail, and must not lay twelve field columns into what is left.
		const { body } = render(DynamicForm, { props: { fields: LONG, sectionNav: true } });
		expect(body).toContain('@4xl:grid-cols-[13rem_minmax(0,1fr)]');
		expect(body).toContain('class="hidden @4xl:block"');
		// The phone gets the same list as one control, stuck to the top of the form.
		expect(body).toContain('aria-label="Jump to section"');
		// With a visible label: the trigger shows only the current section's name.
		expect(body).toMatch(
			/aria-hidden="true"[^>]*>(?:(?!<\/span>)[\s\S])*Jump to section\s*<\/span>/
		);
		expect(body).toContain('@2xl:grid-cols-12');
		expect(body).not.toContain('grid-column: span');
	});

	it('keeps the jump bar as wide as the sections unless the host asks it to bleed', () => {
		// A fixed `-mx-6` fitted one dialog's padding and stuck out 24px either side on a page. The
		// bleed is the host's padding, so only the host can say it; on a page it is zero. The bar
		// also stops under the shell's topbar rather than sliding beneath it.
		const { body } = render(DynamicForm, { props: { fields: LONG, sectionNav: true } });
		expect(body).toContain('-mx-[var(--form-nav-bleed,0px)]');
		expect(body).toContain('top-[var(--topbar-h,0px)]');
		expect(body).not.toMatch(/sm:-mx-6|\s-mx-4\s/);
		// Flat at rest; a shadow only once stuck, so it lifts off the card scrolling under it.
		expect(body).toContain('data-stuck:shadow-md');
		expect(body).not.toContain('data-stuck=');
	});

	it('marks one entry current from the first render, before any scroll-spy has run', () => {
		// The observer is client-only; without a fallback the rail would render with nothing active.
		const { body } = render(DynamicForm, { props: { fields: LONG, sectionNav: true } });
		expect([...body.matchAll(/aria-current="true"/g)]).toHaveLength(1);
	});

	it('renders no rail for a form with no visible sections', () => {
		const { body } = render(DynamicForm, { props: { fields: [], sectionNav: true } });
		expect(body).not.toContain('aria-label="Form sections"');
		expect(body).toContain('This form has no visible fields.');
	});
});

describe('field-set change', () => {
	it('clears submitted errors when the field identity changes', async () => {
		const { readFile } = await import('node:fs/promises');
		const source = await readFile(new URL('./dynamic-form.svelte', import.meta.url), 'utf8');
		expect(source).toContain('const fieldIdentity = $derived');
		expect(source).toMatch(/submitted = false/);
		expect(source).toMatch(/errors = \{\}/);
	});
});
