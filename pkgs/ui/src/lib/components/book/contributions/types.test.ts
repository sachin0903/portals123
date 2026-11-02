/** Guards on the contribution vocabularies the portal shares with the API.
 *
 *  These are the constants the classify panel uses to decide what it may offer: which
 *  categories can be pushed to Morgan Lloyd, which demand a contributor name, and which
 *  frequencies exist. Each has a twin in `apps/api/app/` — a drift shows up as a 400 or a
 *  vendor rejection at the worst moment, so it is pinned here as well as there.
 */
import { describe, expect, it } from 'vitest';
import {
	CONTRIBUTION_CATEGORIES,
	CREATABLE_CONTRIBUTION_CATEGORIES,
	FREQUENCIES,
	PAYMENT_TYPES,
	REGULAR_FREQUENCIES,
	frequencyFor,
	NEEDS_CONTRIBUTOR,
	NEEDS_EMPLOYER,
	OTHER_CONTRIBUTION_OUTCOMES,
	PAID_BY_OPTIONS,
	UNPUSHABLE_CATEGORIES,
	paidByFor,
	paymentTypeFor,
	type ContributionCategory,
	type ContributionFrequency
} from './types';

/** The five categories, as the DB CHECK constraint spells them (0030_contributions_ledger). */
const DB_CATEGORIES = [
	'personal_contribution',
	'employer_contribution',
	'third_party_contribution',
	'transfer_in',
	'not_a_contribution'
] satisfies ContributionCategory[];

/** The five frequencies. Not the old one_off/regular pair — Morgan Lloyd's cam_frequency
 *  needs the period, so "regular" alone cannot be recorded. */
const DB_FREQUENCIES = [
	'single',
	'monthly',
	'quarterly',
	'half_yearly',
	'annually'
] satisfies ContributionFrequency[];

describe('categories', () => {
	it('offers exactly what the database accepts', () => {
		expect(CONTRIBUTION_CATEGORIES.map((c) => c.value).sort()).toEqual([...DB_CATEGORIES].sort());
	});

	it('labels every category', () => {
		for (const option of CONTRIBUTION_CATEGORIES) {
			expect(option.label.length).toBeGreaterThan(0);
			expect(option.label).not.toContain('_');
		}
	});

	it('treats transfers in and non-contributions as unpushable', () => {
		// Coercing either to Morgan Lloyd's OTHER would put a contribution on a client's pension
		// record that never happened. Mirrors app/integrations/morgan_lloyd/contributions.py.
		expect([...UNPUSHABLE_CATEGORIES].sort()).toEqual(['not_a_contribution', 'transfer_in']);
	});

	it('never marks a real contribution unpushable', () => {
		for (const category of [
			'personal_contribution',
			'employer_contribution',
			'third_party_contribution'
		] as const) {
			expect(UNPUSHABLE_CATEGORIES.has(category)).toBe(false);
		}
	});

	it('requires a contributor name for an employer contribution', () => {
		// The `*` rule in cam_createcontribution, and the CHECK on platform.contributions.
		expect(NEEDS_EMPLOYER.has('employer_contribution')).toBe(true);
		expect(NEEDS_EMPLOYER.has('personal_contribution')).toBe(false);
	});
});

describe('frequencies', () => {
	it('offers exactly what the database accepts', () => {
		expect(FREQUENCIES.map((f) => f.value).sort()).toEqual([...DB_FREQUENCIES].sort());
	});

	it('no longer offers the ambiguous "regular"', () => {
		const values: string[] = FREQUENCIES.map((f) => f.value);
		expect(values).not.toContain('regular');
		expect(values).not.toContain('one_off');
	});

	it('defaults sensibly: single is first', () => {
		expect(FREQUENCIES[0].value).toBe('single');
	});
});

describe('payers', () => {
	it('offers exactly what the database accepts', () => {
		expect(PAID_BY_OPTIONS.map((p) => p.value).sort()).toEqual(
			['client', 'employer', 'third_party'].sort()
		);
	});
});

describe('payment type', () => {
	it('is ad-hoc or regular, nothing else', () => {
		expect(PAYMENT_TYPES.map((p) => p.value)).toEqual(['adhoc', 'regular']);
	});

	it('resolves ad-hoc to the single frequency', () => {
		// Ad-hoc is not a frequency of its own: Morgan Lloyd's cam_frequency only knows periods,
		// so a one-off has to arrive as `single` whatever the regular period happens to be set to.
		expect(frequencyFor('adhoc', 'monthly')).toBe('single');
		expect(frequencyFor('adhoc', 'annually')).toBe('single');
	});

	it('resolves regular to the chosen period', () => {
		for (const { value } of REGULAR_FREQUENCIES) {
			expect(frequencyFor('regular', value)).toBe(value);
		}
	});

	it('never offers single as a regular period', () => {
		// It would mean "recurring one-off", and the two-step UI exists precisely to avoid that.
		expect(REGULAR_FREQUENCIES.map((f) => f.value)).not.toContain('single');
	});

	it('covers every database frequency across the two payment types', () => {
		const reachable = new Set([
			frequencyFor('adhoc', 'monthly'),
			...REGULAR_FREQUENCIES.map((f) => frequencyFor('regular', f.value))
		]);
		expect([...reachable].sort()).toEqual([...DB_FREQUENCIES].sort());
	});
});

describe('creatable categories', () => {
	it('is personal and employer only — a transfer in is not a contribution', () => {
		expect(CREATABLE_CONTRIBUTION_CATEGORIES.map((c) => c.value)).toEqual([
			'personal_contribution',
			'employer_contribution'
		]);
	});

	it('stays a subset of what the database accepts', () => {
		const all = CONTRIBUTION_CATEGORIES.map((c) => c.value);
		for (const { value } of CREATABLE_CONTRIBUTION_CATEGORIES) expect(all).toContain(value);
	});

	it('offers nothing the Morgan Lloyd push would have to skip', () => {
		for (const { value } of CREATABLE_CONTRIBUTION_CATEGORIES) {
			expect(UNPUSHABLE_CATEGORIES.has(value)).toBe(false);
		}
	});
});

describe('classification outcomes beyond the two contribution types', () => {
	it('offers every category the database accepts EXCEPT the third-party payer', () => {
		// The two lists are what the UI offers, and between them they have to reach every category
		// an adviser can legitimately choose — a category in neither is one that can never be
		// picked, which is how a credit becomes impossible to dispose of and comes back for ever.
		//
		// `third_party_contribution` is the one deliberate omission, and it is an omission from the
		// CHOICE rather than from the vocabulary: for relief purposes a grandparent's payment into a
		// grandchild's SIPP is the member's own contribution, so the classification is binary
		// (member's money or employer's) and who moved it is a fact about the payment. Rows already
		// classified that way still read back, still render and still push — hence the assertion is
		// on what is OFFERED, and `CONTRIBUTION_CATEGORIES` keeps all five.
		const offered = [
			...CREATABLE_CONTRIBUTION_CATEGORIES.map((c) => c.value),
			...OTHER_CONTRIBUTION_OUTCOMES.map((c) => c.value)
		];
		const accepted = CONTRIBUTION_CATEGORIES.map((c) => c.value);
		expect([...offered].sort()).toEqual(
			accepted.filter((c) => c !== 'third_party_contribution').sort()
		);
		// Still a category, still labelled: a stored row has to render.
		expect(accepted).toContain('third_party_contribution');
	});

	it('never puts the same category in both lists', () => {
		const creatable = new Set(CREATABLE_CONTRIBUTION_CATEGORIES.map((c) => c.value));
		for (const { value } of OTHER_CONTRIBUTION_OUTCOMES) expect(creatable.has(value)).toBe(false);
	});

	it('keeps the disposal routes the queue depends on', () => {
		// `not_a_contribution` is what stops a credit resurfacing (it is written AND cancelled);
		// `transfer_in` is a real thing a payment-in turns out to be.
		const others = OTHER_CONTRIBUTION_OUTCOMES.map((c) => c.value);
		expect(others).toContain('not_a_contribution');
		expect(others).toContain('transfer_in');
	});
});

describe('paidByFor', () => {
	it('derives the payer from the category, so the two cannot disagree', () => {
		expect(paidByFor('personal_contribution')).toBe('client');
		expect(paidByFor('employer_contribution')).toBe('employer');
		expect(paidByFor('third_party_contribution')).toBe('third_party');
	});

	it('only ever answers with a payer the database accepts', () => {
		const allowed = PAID_BY_OPTIONS.map((p) => p.value);
		for (const { value } of CONTRIBUTION_CATEGORIES) {
			expect(allowed).toContain(paidByFor(value));
		}
	});
});

describe('paymentTypeFor', () => {
	it('is the inverse of frequencyFor, so a stored record loads back into the two controls', () => {
		for (const { value } of FREQUENCIES) {
			const type = paymentTypeFor(value);
			expect(frequencyFor(type, value === 'single' ? 'monthly' : value)).toBe(value);
		}
	});

	it('reads a one-off as ad-hoc and every period as regular', () => {
		expect(paymentTypeFor('single')).toBe('adhoc');
		for (const { value } of REGULAR_FREQUENCIES) expect(paymentTypeFor(value)).toBe('regular');
	});
});

describe('NEEDS_CONTRIBUTOR', () => {
	// It no longer drives a form — the classify panel's contributor block is gone, because the
	// employer is a fact about the CLIENT and lives on their record. The distinction it draws is
	// still true of stored rows and of what `cam_createcontribution` asks for.
	it('asks for the payer wherever the payer is not the member', () => {
		expect(NEEDS_CONTRIBUTOR.has('employer_contribution')).toBe(true);
		expect(NEEDS_CONTRIBUTOR.has('third_party_contribution')).toBe(true);
		expect(NEEDS_CONTRIBUTOR.has('personal_contribution')).toBe(false);
	});

	it('is a superset of what is actually REQUIRED', () => {
		// The block is shown for both; only the employer's name is demanded, which is what the API
		// enforces. Demanding a third party's name would block a classification the API accepts.
		for (const value of NEEDS_EMPLOYER) expect(NEEDS_CONTRIBUTOR.has(value)).toBe(true);
		expect(NEEDS_EMPLOYER.has('third_party_contribution')).toBe(false);
	});
});
