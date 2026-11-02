/**
 * @vitest-environment jsdom
 *
 * EVERY FIELD KIND PUTS ITS ANSWER IN `values` AS IT IS TYPED.
 *
 * This is the rule the fact-find broke, and it is invisible to every other kind of test. An SSR
 * test renders the control correctly; a unit test of the input component asserts on the `onchange`
 * it was given; `svelte-check` is happy. The defect only exists in the gap between the DOM and the
 * form's state — `date` committed on `change` alone, so between typing a date and leaving the
 * control the form did not hold it, and anything that re-rendered the form in that window (a
 * dropdown pick, a `computedFrom` field resolving, a conditional section appearing) left the
 * answer on screen and out of the payload. What that looks like to an adviser is a filled-in box
 * that Save calls empty.
 *
 * So the sweep is the test: mount the real `DynamicForm`, type into each kind the way a browser
 * does, and read back what the form actually has. A new field kind is covered by adding one line
 * to `KINDS` — and a kind that is NOT in the list fails `covers every offered field kind`, so the
 * next control cannot be added without answering this question.
 */
import { afterEach, beforeAll, describe, expect, it } from 'vitest';
import type { Component } from 'svelte';
import { FORM_FIELD_TYPES, type FormField } from './types.js';
import { SECTION_NAV_MIN_FIELDS } from './section-nav.js';

let mount: typeof import('svelte').mount;
let flushSync: typeof import('svelte').flushSync;
let tick: typeof import('svelte').tick;
let Harness: Component<Record<string, unknown>>;

beforeAll(async () => {
	// The section rail's scroll-spy is a real browser feature jsdom does not implement. Stubbed
	// here rather than guarded in the component: an absent IntersectionObserver is a property of
	// this test environment, and making the rail defensive about it would hide a genuinely broken
	// observer in a browser that does have one.
	if (!('IntersectionObserver' in globalThis)) {
		class NoopObserver {
			observe() {}
			unobserve() {}
			disconnect() {}
			takeRecords() {
				return [];
			}
			root = null;
			rootMargin = '';
			thresholds = [];
		}
		Object.defineProperty(globalThis, 'IntersectionObserver', {
			writable: true,
			configurable: true,
			value: NoopObserver
		});
	}
	// `scrollIntoView` likewise: jsdom does not lay anything out, so it has no implementation.
	if (!Element.prototype.scrollIntoView) {
		Element.prototype.scrollIntoView = function scrollIntoView() {};
	}
	({ mount, flushSync, tick } = await import('svelte'));
	Harness = (await import('./field-entry-harness.test.svelte'))
		.default as unknown as typeof Harness;
}, 120_000);

// Every mount lands in `document.body` and every field id is derived from the field's uid, so
// without this the second test's `#fld-a` finds the FIRST test's input — which is how this file
// first "failed" on a fix that was working.
afterEach(() => {
	document.body.innerHTML = '';
});

const field = (over: Partial<FormField>): FormField =>
	({
		uid: 'a',
		fieldKey: 'q',
		label: 'Question',
		fieldType: 'text',
		section: 'Section',
		sortOrder: 1,
		required: false,
		options: null,
		validation: {},
		description: null,
		...over
	}) as FormField;

/** Typed, not pasted: the value goes in and ONE `input` event follows, as a browser sends. */
function typeInto(el: HTMLInputElement | HTMLTextAreaElement, text: string) {
	el.value = text;
	el.dispatchEvent(new InputEvent('input', { bubbles: true }));
	flushSync();
}

function mountField(f: FormField, dataSources: Record<string, unknown[]> = {}) {
	const target = document.createElement('div');
	document.body.append(target);
	const app = mount(Harness, { target, props: { fields: [f], dataSources } });
	flushSync();
	const read = () => (app as unknown as { read: () => Record<string, unknown> }).read();
	return { target, read };
}

/** One entry per control the form offers. `typed` is what a browser leaves in `input.value`. */
const KINDS: { type: string; typed: string; expected?: unknown }[] = [
	{ type: 'text', typed: 'Wetherington', expected: 'Wetherington' },
	{ type: 'textarea', typed: 'Some notes', expected: 'Some notes' },
	{ type: 'number', typed: '42', expected: 42 },
	{ type: 'email', typed: 'theo@caldwell.test', expected: 'theo@caldwell.test' },
	{ type: 'phone', typed: '07700900123' },
	{ type: 'money', typed: '1000', expected: '1000' },
	{ type: 'percentage', typed: '5', expected: '5' },
	// The one this file exists for.
	{ type: 'date', typed: '1984-03-21', expected: '1984-03-21' },
	{ type: 'sortCode', typed: '123456' },
	{ type: 'bankAccountNumber', typed: '12345678', expected: '12345678' },
	{ type: 'nationalIdentifier', typed: 'AB123456C', expected: 'AB123456C' }
];

describe('a typed answer reaches the form', () => {
	for (const { type, typed, expected } of KINDS) {
		it(`${type} commits on input`, () => {
			const { target, read } = mountField(field({ fieldType: type }));
			const el = target.querySelector('input, textarea') as HTMLInputElement | null;
			expect(el, `${type} rendered no control`).not.toBeNull();
			typeInto(el!, typed);
			const answer = read().q;
			expect(
				answer,
				`${type} left its answer in the DOM and not in the form — DOM="${el!.value}"`
			).not.toBeUndefined();
			if (expected !== undefined) expect(answer).toEqual(expected);
		});
	}

	it('the date survives another field changing mid-entry', () => {
		// The race as reported: the answer is typed, something else on the form moves, and the date
		// has to still be there afterwards. Before the fix `values.dob` was `undefined` at this
		// point while the box on screen read 1984-03-21.
		const target = document.createElement('div');
		document.body.append(target);
		const app = mount(Harness, {
			target,
			props: {
				fields: [
					field({ uid: 'a', fieldKey: 'dob', fieldType: 'date', sortOrder: 1 }),
					field({ uid: 'b', fieldKey: 'notes', sortOrder: 2 })
				]
			}
		});
		flushSync();
		const dob = target.querySelector('#fld-a') as HTMLInputElement;
		typeInto(dob, '1984-03-21');
		(app as unknown as { set: (k: string, v: unknown) => void }).set('notes', 'picked something');
		flushSync();
		const values = (app as unknown as { read: () => Record<string, unknown> }).read();
		expect(values.dob).toBe('1984-03-21');
		expect(dob.value).toBe('1984-03-21');
	});
});

describe('the sweep covers what the builder offers', () => {
	/**
	 * A kind is either swept above or listed here with the reason it cannot be.
	 *
	 * Without this the sweep silently stops covering the form: a new control is added to
	 * `FORM_FIELD_TYPES`, nobody adds a line to `KINDS`, and the file keeps passing while saying
	 * nothing about the field that was just introduced — which is precisely how `date` went
	 * uncovered.
	 */
	const NOT_TYPED_INTO: Record<string, string> = {
		checkbox: 'a tick, not typing — covered by the required-marker test below',
		select: 'chosen from a menu; commits through `onValueChange`',
		segmented: 'chosen from a button group; commits through `onValueChange`',
		combobox: 'chosen from a list; its own text box is a SEARCH, not the answer',
		multiSelect: 'chosen from a list',
		address: 'a composite of sub-inputs with its own lookup, not one control',
		beneficiaries: 'a repeating sub-form',
		phoneNumbers: 'a repeating sub-form; its label is a menu and its number a masked input',
		repeating: 'a repeating group of scalar columns; not one control to type into',
		display: 'read-only; it is not an answer and never reaches the payload',
		title: 'chosen from the shared title list; commits through `onValueChange`',
		gender: 'chosen from the shared gender list; commits through `onValueChange`',
		marital: 'chosen from the shared marital list; commits through `onValueChange`',
		employment: 'chosen from the shared employment list; commits through `onValueChange`',
		// The two aliases the canonical `kind` folds onto a swept type.
		dateOfBirth: 'alias of `date`',
		phoneNumber: 'alias of `phone`',
		currency: 'alias of `money`'
	};

	it('every offered field type is swept or excused', () => {
		const swept = new Set(KINDS.map((k) => k.type));
		const missing = FORM_FIELD_TYPES.map((t) => t.value).filter(
			(t) => !swept.has(t) && !(t in NOT_TYPED_INTO)
		);
		expect(missing, `add these to KINDS (or to NOT_TYPED_INTO with a reason)`).toEqual([]);
	});

	it('the excuses are real types, not stale names', () => {
		const offered = new Set<string>(FORM_FIELD_TYPES.map((t) => t.value));
		expect(Object.keys(NOT_TYPED_INTO).filter((t) => !offered.has(t))).toEqual([]);
	});
});

describe('a refused submit says which fields, and where', () => {
	/** A form whose required answer sits in the second section, below the fold of a real page —
	 *  long enough (`SECTION_NAV_MIN_FIELDS`) that it gets its section nav at all. */
	function twoSections() {
		const target = document.createElement('div');
		document.body.append(target);
		const app = mount(Harness, {
			target,
			props: {
				sectionNav: true,
				fields: [
					field({ uid: 'a', fieldKey: 'given_name', label: 'First name', section: 'About you' }),
					...Array.from({ length: SECTION_NAV_MIN_FIELDS - 2 }, (_, i) =>
						field({
							uid: `x${i}`,
							fieldKey: `extra_${i}`,
							label: `Extra ${i}`,
							section: 'About you'
						})
					),
					field({
						uid: 'b',
						fieldKey: 'earnings',
						label: 'Annual income',
						section: 'Employment',
						sortOrder: 2,
						required: true
					})
				]
			}
		});
		flushSync();
		return { target, app };
	}

	it('moves to the first field that failed', async () => {
		const { target, app } = twoSections();
		const earnings = target.querySelector('#fld-b') as HTMLInputElement;
		expect(document.activeElement).not.toBe(earnings);
		(app as unknown as { submit: () => boolean }).submit();
		flushSync();
		// `focusFirstError` awaits a tick of its own, so the errors are painted before it moves.
		await tick();
		await tick();
		expect(document.activeElement).toBe(earnings);
	});

	it('counts the failures against the section that holds them', () => {
		const { target, app } = twoSections();
		const rail = () => target.querySelector('nav[aria-label="Form sections"]')!.textContent ?? '';
		expect(rail()).not.toMatch(/\d/);
		(app as unknown as { submit: () => boolean }).submit();
		flushSync();
		// The count sits on Employment, which is where the missing answer is — not on About you.
		const employment = [...target.querySelectorAll('nav[aria-label="Form sections"] button')].find(
			(b) => (b.textContent ?? '').includes('Employment')
		);
		const about = [...target.querySelectorAll('nav[aria-label="Form sections"] button')].find((b) =>
			(b.textContent ?? '').includes('About you')
		);
		expect(employment?.textContent).toContain('1');
		expect(about?.textContent).not.toMatch(/\d/);
	});

	it('the count at the foot of the form is a control, not a sentence', () => {
		// It used to be a `<p>`: true, and no help at all on a form where the missing answer is three
		// sections above the button you just pressed.
		const { target, app } = twoSections();
		(app as unknown as { submit: () => boolean }).submit();
		flushSync();
		const jump = [...target.querySelectorAll('button')].find((b) =>
			(b.textContent ?? '').includes('need attention')
		);
		expect(jump).toBeTruthy();
		expect(jump!.tagName).toBe('BUTTON');
	});

	it('a required CHECKBOX is marked the way every other field is', () => {
		// This branch carried no marker at all, so a required tick and an optional one looked
		// identical on a form whose convention is "unmarked means you must answer it".
		const target = document.createElement('div');
		document.body.append(target);
		mount(Harness, {
			target,
			props: {
				fields: [
					field({
						uid: 'a',
						fieldKey: 'terms',
						fieldType: 'checkbox',
						label: 'I agree',
						required: true
					}),
					field({
						uid: 'b',
						fieldKey: 'news',
						fieldType: 'checkbox',
						label: 'Send me news',
						sortOrder: 2
					})
				]
			}
		});
		flushSync();
		const rows = [...target.querySelectorAll('label')].map(
			(l) => l.closest('div')?.textContent ?? ''
		);
		const required = rows.find((t) => t.includes('I agree')) ?? '';
		const optional = rows.find((t) => t.includes('Send me news')) ?? '';
		expect(required).not.toContain('Optional');
		expect(optional).toContain('Optional');
	});
});

describe('an optional dropdown can be cleared', () => {
	const taxForm = (required: boolean) =>
		field({
			fieldKey: 'fatca_status',
			fieldType: 'select',
			label: 'Does this client have a W-9 or a W-8?',
			required,
			options: {
				options: [
					{ value: 'w9', label: 'W-9' },
					{ value: 'w8', label: 'W-8' }
				]
			}
		});

	/** Open the menu the way the keyboard does. bits-ui opens on a left-button pointerdown, which
	 *  a synthetic click is not. */
	async function openMenu(required: boolean) {
		const target = document.createElement('div');
		document.body.append(target);
		const app = mount(Harness, {
			target,
			props: { fields: [taxForm(required)], initial: { fatca_status: 'w8' } }
		});
		flushSync();
		const trigger = target.querySelector('#fld-a') as HTMLElement;
		expect(trigger.textContent).toContain('W-8');
		trigger.focus();
		trigger.dispatchEvent(
			new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true, cancelable: true })
		);
		const deadline = Date.now() + 3000;
		while (!document.querySelector('[data-slot="select-item"]') && Date.now() < deadline) {
			await tick();
			await new Promise((resolve) => setTimeout(resolve, 20));
		}
		const items = [...document.querySelectorAll<HTMLElement>('[data-slot="select-item"]')];
		const read = () => (app as unknown as { read: () => Record<string, unknown> }).read();
		return { trigger, items, read };
	}

	it('offers a blank first, and choosing it clears the answer', async () => {
		const { trigger, items, read } = await openMenu(false);
		expect(items.map((i) => i.textContent?.trim())).toEqual(['—', 'W-9', 'W-8']);
		items[0].dispatchEvent(new PointerEvent('pointerup', { bubbles: true, cancelable: true }));
		flushSync();
		await tick();
		expect(read().fatca_status).toBe('');
		expect(trigger.textContent).toContain('—');
		expect(trigger.textContent).not.toContain('W-8');
	});

	it('offers no blank on a required question', async () => {
		const { items } = await openMenu(true);
		expect(items.map((i) => i.textContent?.trim())).toEqual(['W-9', 'W-8']);
	});
});
