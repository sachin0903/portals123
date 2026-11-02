/**
 * The booking drawer's SHAPE — the questions it asks and, more importantly, the ones it does not.
 *
 * Three controls were removed because each asked an adviser to state something we either already
 * know or cannot yet know:
 *   - the CONTRIBUTION DATE: on a book-ahead the payment has not been made, so a date typed here is
 *     a guess. `app/matching.py::link` stamps the real one off the credit when it lands, and
 *     `create_contribution` now stores NULL rather than today's date.
 *   - PAID BY: it restated the category. A personal contribution is paid by the client and an
 *     employer contribution by the employer, so a separate control could only ever disagree with
 *     the category beside it.
 *   - the TAX YEAR: derived from the date, so choosing one before the date is known could only
 *     contradict it. `contributions.tax_year_of` does it server-side.
 *
 * A form that grows one of them back can record something untrue, so it is pinned. A SOURCE SCAN,
 * because the drawer's body renders through a portal that SSR does not run: `render()` of a Sheet
 * returns empty markup, which makes every "does not contain" assertion pass for the wrong reason.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { CREATABLE_CONTRIBUTION_CATEGORIES, PAYMENT_TYPES, REGULAR_FREQUENCIES } from './types.js';

const source = readFileSync(
	fileURLToPath(new URL('./contribution-form.svelte', import.meta.url)),
	'utf8'
);
/** Comments stripped: this file's own header names the removed fields, and a scan over the raw
 *  text would report the explanation as the defect. */
const template = source
	.slice(source.indexOf('</script>'))
	.replace(/<!--[\s\S]*?-->/g, '')
	.replace(/\/\/[^\n]*/g, '');
/** The fetch body — what is actually sent, as distinct from what is on screen. */
const payload = source.slice(
	source.indexOf('body: JSON.stringify('),
	source.indexOf('const body =')
);

describe('the contribution drawer asks', () => {
	it('for the amount, as a money field', () => {
		// Not a bare `<Input inputmode="decimal">`, which is what it was: no currency symbol, no
		// grouping, so 1234500 was typed and read back as an undifferentiated run of digits.
		expect(template).toContain('<MoneyInput id="con-amount"');
	});

	it('when the money is expected by', () => {
		// The due date survives: it is a forecast, and the custodian's Anticipated Cash Receipt
		// needs one. It is the CONTRIBUTION date — a statement about money that has moved — that
		// cannot be known at booking.
		expect(template).toContain('id="con-due"');
		expect(template).toContain('Expected by');
		expect(payload).toContain('due_date');
	});

	it('the category, as a button group over the two creatable ones', () => {
		expect(template).toContain('options={CREATABLE_CONTRIBUTION_CATEGORIES}');
		// A Segmented, not a Select: two options whose whole content is "which of these two".
		expect(template).toMatch(/<Segmented[\s\S]*?CREATABLE_CONTRIBUTION_CATEGORIES/);
		expect(CREATABLE_CONTRIBUTION_CATEGORIES).toHaveLength(2);
		// Content-width: Segmented is already `inline-flex`. `w-full` stretched the pill across the
		// drawer (classify-panel.test.ts pins the same rule).
		expect(template).not.toMatch(/<Segmented[\s\S]{0,160}class="w-full"/);
	});

	it('ad-hoc or regular, with the period as a second step', () => {
		expect(template).toContain('options={PAYMENT_TYPES}');
		expect(PAYMENT_TYPES.map((p) => p.value)).toEqual(['adhoc', 'regular']);
		// The period is behind the choice, not beside it: asking for one on a one-off is how
		// "recurring one-off" gets recorded, and Morgan Lloyd's cam_frequency has no such value.
		expect(template).toMatch(/\{#if paymentType === 'regular'\}[\s\S]*?REGULAR_FREQUENCIES/);
		expect(REGULAR_FREQUENCIES.map((f) => f.value)).not.toContain('single');
	});

	it('and sends one frequency, resolved from the two controls', () => {
		expect(source).toContain('frequencyFor(paymentType, regular)');
		expect(payload).toContain('frequency');
	});
});

describe('the contribution drawer never asks', () => {
	it('for the contribution date', () => {
		expect(template).not.toContain('con-date');
		expect(template).not.toContain('Contribution date');
		// Nor sends one. The API leaves the column NULL until a credit is matched.
		expect(payload).not.toContain('contribution_date');
	});

	it('who paid', () => {
		expect(template).not.toContain('con-paid-by');
		expect(template).not.toContain('Paid by');
		// `paid_by` is still SENT — the wire field is real and Morgan Lloyd needs it. It is derived
		// from the category rather than asked, which is the whole point.
		expect(payload).toContain('paid_by: paidBy');
		expect(source).toContain("category === 'employer_contribution' ? 'employer' : 'client'");
	});

	it('for the tax year', () => {
		expect(template).not.toContain('con-tax-year');
		expect(template).not.toContain('Tax year');
		expect(payload).not.toContain('tax_year');
	});

	it('for HMRC eligibility checks', () => {
		expect(template).not.toContain('HMRC checks');
		expect(template).not.toContain('EligibilityList');
		expect(source).not.toContain('eligibilityChecks');
		expect(source).not.toContain('isBlocked');
	});
});

describe('the contribution drawer', () => {
	it('is the money-movement form width, not the illustration floor', () => {
		// It used to force `!w-[46vw] !max-w-none`, then sat uncapped on `sm:w-[58vw]` after that
		// was dropped — same width as the SIPP illustration, next to Payment / Withdraw at xl.
		expect(template).toContain('sm:max-w-xl');
		expect(template).not.toContain('46vw');
		expect(template).not.toMatch(/Sheet\.Content[^>]*!w-/);
	});

	it('keeps the employer block behind the category that needs it', () => {
		// Morgan Lloyd requires the contributor's details for an employer contribution and has no
		// use for them on a personal one.
		expect(template).toMatch(/\{#if needsEmployer\}/);
		expect(source).toContain('NEEDS_EMPLOYER.has(category)');
	});
});
