/**
 * The staff-assigned step form, rendered.
 *
 * This page carries anything the firm authored behind a `fill_form` step — from three questions to
 * a whole fact-find with a dozen sections. It was laid out for the short case only: a fixed
 * `max-w-3xl` column with the form inside a Card, and no way to move about a long one but to
 * scroll. A fact-find is then a single unbroken column, narrower than the screen it is being filled
 * in on, with the submit button an unknown distance below.
 *
 * The two things pinned here are the decisions this page makes, because both are silent when wrong:
 * a rail that never appears looks like a form that simply has no sections, and a rail on a
 * one-section form is 13rem of empty gutter. The rail's own behaviour (anchors, scroll-spy,
 * responsive collapse) belongs to DynamicForm and is pinned in
 * pkgs/ui/src/lib/forms/dynamic-form.ssr.test.ts — not restated here.
 */
import { readFileSync } from 'node:fs';
import { SECTION_NAV_MIN_FIELDS } from '@graphene/ui';
import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import Page from './+page.svelte';
import type { ApiFormField } from '$lib/forms';
import type { StepForm } from './+page.server';

const field = (
	key: string,
	section: string | null,
	over: Partial<ApiFormField> = {}
): ApiFormField => ({
	uid: `f-${key}`,
	field_key: key,
	field_type: 'text',
	label: `Field ${key.toUpperCase()}`,
	section,
	description: null,
	required: false,
	conditions: null,
	validation: null,
	options: null,
	sort_order: 0,
	...over
});

const stepForm = (fields: ApiFormField[], over: Partial<StepForm> = {}): StepForm => ({
	step_key: 'fact_find',
	title: 'Fact find',
	status: 'pending',
	form_uid: 'form-1',
	name: 'Fact find',
	description: null,
	fields,
	answers: null,
	editable: true,
	...over
});

/** Render the page the way its loader feeds it. `data` is shaped structurally: the generated
 *  `PageData` carries SvelteKit's own layout fields, which this page never reads. */
function renderPage(form: StepForm, over: Record<string, unknown> = {}) {
	const data = {
		form,
		countries: null,
		journeyUid: 'app-1',
		caseUid: 'case-1',
		participant: null,
		formContext: null,
		previousForm: null,
		nextForm: null,
		canManage: true,
		...over
	};
	return render(Page, { props: { data, form: null } as never });
}

/** A fact-find: three sections that open more than a screen long (`SECTION_NAV_MIN_FIELDS`). */
const LONG = [
	field('a', 'Personal details'),
	field('b', 'Employment', { sort_order: 1 }),
	field('c', 'Objectives', { sort_order: 2 }),
	...Array.from({ length: SECTION_NAV_MIN_FIELDS - 3 }, (_, i) =>
		field(`x${i}`, 'Objectives', { sort_order: 3 + i })
	)
];
const SHORT = [field('a', 'Personal details'), field('b', 'Personal details', { sort_order: 1 })];
/** Several sections, but only a few questions: quicker to scroll than to navigate. */
const FEW = [
	field('a', 'Personal details'),
	field('b', 'Employment', { sort_order: 1 }),
	field('c', 'Objectives', { sort_order: 2 })
];

describe('journey step form — layout', () => {
	it('gives a long multi-section form its section rail', () => {
		const { body } = renderPage(stepForm(LONG));
		expect(body).toContain('aria-label="Form sections"');
		for (const section of ['Personal details', 'Employment', 'Objectives']) {
			expect(body).toContain(section);
		}
	});

	it('keeps the applicant and purpose visible while the form is being completed', () => {
		const { body } = renderPage(stepForm(SHORT, { title: 'Pension information' }), {
			formContext: {
				subject: 'John Pavey',
				purpose: 'Pension and retirement information',
				owner: 'Alex Morgan'
			}
		});
		expect(body).toContain('This form is for');
		expect(body).toContain('For John Pavey');
		expect(body).toContain('John Pavey');
		expect(body).toContain('Assigned to Alex Morgan');
		expect(body).toContain('Pension and retirement information');
	});

	it('gives a one-section form no rail', () => {
		// A rail listing a single destination navigates nowhere and costs the form 13rem.
		const { body } = renderPage(stepForm(SHORT));
		expect(body).not.toContain('aria-label="Form sections"');
	});

	it('needs more than one section AND a screen of fields', () => {
		// One section navigates nowhere; three sections over three questions are quicker to scroll.
		expect(renderPage(stepForm(SHORT)).body).not.toContain('aria-label="Form sections"');
		expect(renderPage(stepForm(FEW)).body).not.toContain('aria-label="Form sections"');
		expect(renderPage(stepForm(LONG)).body).toContain('aria-label="Form sections"');
	});

	it('keeps a short multi-section form in the narrow column, with no rail to fill a wide one', () => {
		const { body } = renderPage(stepForm(FEW));
		expect(body).toContain('max-w-3xl');
		expect(body).not.toContain('max-w-6xl');
	});

	it('ignores hidden sidecar fields when counting sections', () => {
		// A `hidden` field renders no control (its value is owned by another typed field), so a
		// section holding only those would put a rail entry above an empty heading.
		const { body } = renderPage(
			stepForm([
				field('a', 'Personal details'),
				field('national_identifier_type', 'Derived', { validation: { hidden: true } })
			])
		);
		expect(body).not.toContain('aria-label="Form sections"');
	});

	it('widens the column for a long form and leaves a short one alone', () => {
		expect(renderPage(stepForm(LONG)).body).toContain('max-w-6xl');
		expect(renderPage(stepForm(SHORT)).body).toContain('max-w-3xl');
		expect(renderPage(stepForm(SHORT)).body).not.toContain('max-w-6xl');
	});

	it('stays a fluid single column, so the wider layout does not overflow a narrow screen', () => {
		// `max-w-*` on a `w-full` container caps the width without setting one — the page still
		// collapses to one column on a laptop or a phone (DynamicForm hides the rail below `lg`).
		const { body } = renderPage(stepForm(LONG));
		expect(body).toContain('mx-auto w-full');
	});

	it('clears the sticky topbar when a section is jumped to', () => {
		// `--form-scroll-offset` is spent by both the rail's stick point and each section's
		// scroll-margin. Without it a jumped-to heading lands underneath the shell's topbar; the
		// height comes from the `--topbar-h` token the shell publishes, never a hardcoded number.
		const { body } = renderPage(stepForm(LONG));
		expect(body).toContain('--form-scroll-offset: calc(var(--topbar-h, 4rem) + 1rem)');
	});

	it('renders the empty state in a card when the form has no questions', () => {
		const { body } = renderPage(stepForm([]));
		expect(body).toContain('This form has no questions yet');
		expect(body).not.toContain('aria-label="Form sections"');
	});

	it('offers the builder rather than describing it — and only to somebody who may use it', () => {
		// It said "Add fields to the form in the Forms builder, then come back" and left the reader
		// to go and find it. The form's own uid is on the payload, so the route is known.
		expect(renderPage(stepForm([]), { canEditForm: true }).body).toContain('Open the form builder');
		// THE GATE IS `forms:write`, NOT `cases:write`. It was `canManage` — which is `cases:write`
		// (`+page.server.ts`) — while `/forms/{uid}/edit` needs a forms permission and returns
		// `form: null` without one. A case worker with no forms permission was offered a button that
		// lands on "Form not found": a refusal dressed as a missing record.
		expect(renderPage(stepForm([]), { canManage: true }).body).not.toContain(
			'Open the form builder'
		);
	});

	it('allows amendments until handoff and then makes the form read-only', () => {
		expect(renderPage(stepForm(LONG)).body).toContain('Save form');
		expect(renderPage(stepForm(LONG, { status: 'fulfilled' })).body).toContain('Save changes');
		expect(renderPage(stepForm(LONG, { status: 'fulfilled', editable: false })).body).not.toContain(
			'Save changes'
		);
		expect(renderPage(stepForm(LONG), { canManage: false }).body).not.toContain('Save form');
	});

	it('links directly through the applicable form sequence', () => {
		const { body } = renderPage(stepForm(LONG), {
			previousForm: {
				href: '/journeys/app-1/steps/case-1/core',
				title: 'Applicant',
				status: 'fulfilled'
			},
			nextForm: {
				href: '/journeys/app-1/steps/case-1/pension',
				title: 'Pension',
				status: 'pending'
			}
		});
		expect(body).toContain('Previous · Applicant');
		expect(body).toContain('Save and continue');
		expect(body).toContain('/journeys/app-1/steps/case-1/pension');
	});
});

describe('journey step form — overflow', () => {
	const source = readFileSync(new URL('./step-form-body.svelte', import.meta.url), 'utf8');

	it('keys the live form by case and participant, not only form type', () => {
		// Two applicants share `form_uid` and `step_key`. Identity that omits `caseUid` kept
		// applicant 1's in-memory answers on applicant 2 after Prev/Next (and submitted them).
		expect(source).toContain(
			'stepFormIdentity(data.caseUid, data.form.step_key, data.participant)'
		);
		expect(source).not.toContain('${data.form.form_uid}:${data.form.step_key}');
	});

	it('never wraps the form in something that clips it', () => {
		// AddressLookup's suggestion list is absolutely positioned rather than portalled (a Popover
		// panel lands outside a Sheet's focus trap), so ANY ancestor that hides overflow cuts it off
		// at that ancestor's edge — `z-50` cannot help. The form's own section Cards declare
		// `overflow-visible`; what this page must not do is add a clip above them.
		expect(source).not.toContain('overflow-hidden');
		expect(source).not.toContain('overflow-y-auto');
	});

	it('does not nest the form inside a card of its own', () => {
		// DynamicForm renders every section in a Card. A wrapper here put a card inside a card —
		// unnoticeable on a one-section form, a box inside a box on each section of a long one.
		// The empty state is the other Card.
		expect(source).toContain('<DynamicForm');
		const beforeForm = source.slice(0, source.indexOf('<DynamicForm'));
		const lastOpen = beforeForm.lastIndexOf('<Card');
		const lastClose = beforeForm.lastIndexOf('</Card>');
		expect(lastClose).toBeGreaterThan(lastOpen);
	});

	it('places applied fees in the form’s Fees section instead of above the form', () => {
		const mount = source.slice(
			source.indexOf('<DynamicForm'),
			source.indexOf('/>', source.indexOf('<DynamicForm'))
		);
		expect(mount).toContain('sectionMeta');
		expect(mount).toContain('Fees:');
		expect(mount).toContain('lead: appliedDisplayFees.length ? appliedFeesLead : undefined');
		expect(source.indexOf('{#snippet appliedFeesLead()}')).toBeLessThan(
			source.indexOf('<DynamicForm')
		);
	});

	it('leaves the empty state card unclipped too', () => {
		const { body } = renderPage(stepForm([]));
		const cards = [...body.matchAll(/data-slot="card"[^>]*class="([^"]*)"/g)].map((m) => m[1]);
		expect(cards.length).toBeGreaterThan(0);
		for (const cls of cards) expect(cls).toContain('overflow-visible');
	});
});
