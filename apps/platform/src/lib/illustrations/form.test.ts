import { describe, it, expect } from 'vitest';
import {
	ILLUSTRATION_SECTIONS,
	illustrationFields,
	illustrationFieldsFor,
	PRODUCT_TYPE_DEFAULT,
	illustrationAnswerProblems,
	illustrationTerm,
	retirementAgeWrittenBack,
	toIllustrationPayload
} from './form';

describe('illustrationFields', () => {
	it('has unique field keys and sequential sort order', () => {
		const keys = illustrationFields.map((f) => f.fieldKey);
		expect(new Set(keys).size).toBe(keys.length);
		illustrationFields.forEach((f, i) => expect(f.sortOrder).toBe(i));
	});

	it('gates every Income field on a drawdown basis', () => {
		const gate = {
			conditional: { field: 'quotation_type', operator: 'notEquals', value: 'Accumulation' }
		};
		for (const key of ['drawdown_type', 'withdrawal_amount', 'withdrawal_frequency']) {
			expect(illustrationFields.find((f) => f.fieldKey === key)?.conditions, key).toEqual(gate);
		}
	});

	it('offers a personal contribution as Net only', () => {
		const field = illustrationFields.find((f) => f.fieldKey === 'contribution_net_gross');
		expect(field?.options).toEqual([{ value: 'Net', label: 'Net' }]);
		expect(field?.validation?.defaultValue).toBe('Net');
	});

	it('gates every Contributions field on an accumulation basis', () => {
		// The mirror of Income: a drawdown quote is withdrawals, not regular money going in, so
		// the whole section hides the way Income hides on Accumulation.
		const gate = {
			conditional: { field: 'quotation_type', operator: 'equals', value: 'Accumulation' }
		};
		for (const key of ['contribution_amount', 'contribution_frequency', 'contributor']) {
			expect(illustrationFields.find((f) => f.fieldKey === key)?.conditions, key).toEqual(gate);
		}
		expect(
			illustrationFields.find((f) => f.fieldKey === 'contribution_net_gross')?.conditions
		).toEqual({
			conditional: {
				logic: 'AND',
				conditions: [
					{ field: 'quotation_type', operator: 'equals', value: 'Accumulation' },
					{ field: 'contributor', operator: 'equals', value: 'Personal' }
				]
			}
		});
	});

	it('asks one contribution, and who pays it, on two rows', () => {
		const rows = illustrationFields.filter((f) => f.section === 'Contributions');
		expect(rows.map((f) => f.fieldKey)).toEqual([
			'contribution_amount',
			'contribution_frequency',
			'contributor',
			'contribution_net_gross'
		]);
		expect(rows.find((f) => f.fieldKey === 'contributor')?.options).toEqual([
			{ value: 'Personal', label: 'Personal' },
			{ value: 'Employer', label: 'Employer' }
		]);
		// Halves, so Amount and Frequency sit on one row and Paid by with Net/gross on the next.
		// Width 3 put all four on one row and the segmented controls overflowed the drawer.
		for (const f of rows) expect(f.validation?.width, f.fieldKey).toBe(6);
	});

	it('reads Income left to right as a sentence: how much, how often, on what basis', () => {
		// The drawdown type led until 09 Aug 2026 — the most technical of the three first, and the
		// figure the client actually asked about third.
		const income = illustrationFields.filter((f) => f.section === 'Income');
		expect(income.map((f) => f.fieldKey)).toEqual([
			'withdrawal_amount',
			'withdrawal_frequency',
			'drawdown_type'
		]);
		// Thirds, so the sentence sits on one row.
		for (const f of income) expect(f.validation?.width, f.fieldKey).toBe(4);
	});

	it('asks for a model with a SEARCHABLE picker, reading it from the models data source', () => {
		// A firm's shelf runs to dozens of models with long, deliberately similar names; a plain
		// select makes finding one a scroll.
		const model = illustrationFields.find((f) => f.fieldKey === 'model_id');
		expect(model?.section).toBe('Plan');
		expect(model?.fieldType).toBe('combobox');
		expect(model?.options).toEqual({ source: 'models', valueKey: 'id', labelKey: 'name' });
	});

	it('offers two bases, and sends CTC’s spelling of the drawdown one', () => {
		// The LABEL is the adviser's word and the VALUE is CTC's: `Flexi Access Drawdown` is what
		// reaches the XML (and the filed document's name), while the form calls it "Drawdown"
		// because it is the only drawdown basis on offer. CTC's own `Drawdown` (capped, closed to
		// new members) and `SMPI` (a statutory annual statement) are not quoted from here.
		const basis = illustrationFields.find((f) => f.fieldKey === 'quotation_type');
		expect(basis?.fieldType).toBe('segmented');
		expect(basis?.options).toEqual([
			{ value: 'Accumulation', label: 'Accumulation' },
			{ value: 'Flexi Access Drawdown', label: 'Drawdown' }
		]);
	});

	it('treats the drawdown basis as a drawdown throughout', () => {
		// Everything downstream keys off `!== 'Accumulation'` — Income's condition, Contributions'
		// inverse, and the payload's drawdown/contribution nulling — so relabelling must not have
		// changed the value.
		const out = toIllustrationPayload({
			quotation_type: 'Flexi Access Drawdown',
			drawdown_type: 'Flexible',
			withdrawal_amount: '500',
			withdrawal_frequency: 'Monthly'
		});
		expect(out.quotation_type).toBe('Flexi Access Drawdown');
		expect(out.drawdown_type).toBe('Flexible');
		expect(out.withdrawal_amount).toBe(500);
	});

	it('no longer asks which account, nor which wrapper', () => {
		// Both were replaced by the model: the form is opened from a client rather than an account,
		// and the wrapper picker offered SIPP as effectively its only answer.
		const keys = illustrationFields.map((f) => f.fieldKey);
		expect(keys).not.toContain('account_id');
		expect(keys).not.toContain('product_type');
		expect(keys).not.toContain('product_type_display');
		expect(keys).not.toContain('existing_fund_display');
	});

	it('does not ask for the client at all', () => {
		// Identity is read from the record and shown as text above the form (client-panel.svelte).
		// It was previously a section of read-only `display` fields, which render in a bordered box
		// that reads as a disabled input — and a form that can hold a date of birth is a form that
		// can disagree with the record it is filed against.
		expect(illustrationFields.filter((f) => f.section === 'Client')).toHaveLength(0);
		const keys = illustrationFields.map((f) => f.fieldKey);
		for (const k of ['client_name', 'client_type', 'client_dob', 'client_age'])
			expect(keys).not.toContain(k);
	});

	it('does not ask for a fact the record already holds', () => {
		// The base set is a complete record: retirement age and marital status are shown as text,
		// and a field here would let the illustration disagree with the record it is filed against.
		const keys = illustrationFields.map((f) => f.fieldKey);
		expect(keys).not.toContain('retirement_age');
		expect(keys).not.toContain('marital_status');
		expect(keys).not.toContain('exit_age');
	});

	it('marks every field of an optional section required', () => {
		// The switch is the "do I want this?" question; once it is on, the section's own fields are
		// not individually optional.
		const optionalSections = Object.entries(ILLUSTRATION_SECTIONS)
			.filter(([, m]) => m.optional)
			.map(([name]) => name);
		expect(optionalSections.length).toBeGreaterThan(0);
		for (const f of illustrationFields) {
			if (!f.section || !optionalSections.includes(f.section)) continue;
			expect(f.required, `${f.section} › ${f.fieldKey}`).toBe(true);
		}
	});

	it('offers Weekly for a contribution and not for a withdrawal', () => {
		// Two different sets in the spec, and they were one constant here until 09 Aug 2026: a
		// contribution (CTC1 §d) may be Weekly, a withdrawal (§f) may not. Sharing the list meant
		// adding Weekly for contributions would have offered it as a withdrawal frequency too.
		const values = (key: string) =>
			(illustrationFields.find((f) => f.fieldKey === key)?.options as { value: string }[]).map(
				(o) => o.value
			);
		expect(values('contribution_frequency')).toContain('Weekly');
		expect(values('withdrawal_frequency')).not.toContain('Weekly');
		// A one-off into the plan is the Initial payment section, not a frequency on the regular
		// contribution. A withdrawal may still be a single payment.
		expect(values('contribution_frequency')).not.toContain('Single');
		expect(values('withdrawal_frequency')).toContain('Single');
	});

	it('does not offer capped drawdown', () => {
		// Closed to new members since April 2015, and we send none of the three fields it needs
		// (`current_max_income`, `next_review_date`, `inc_already_taken` — CTC1 §c, capped only), so
		// choosing it produced a request the engine cannot compute properly. The label is the adviser's
		// "Flexi drawdown"; the value stays CTC's Flexible.
		const types = illustrationFields.find((f) => f.fieldKey === 'drawdown_type')?.options as {
			value: string;
			label: string;
		}[];
		expect(types).toEqual([
			{ value: 'Flexible', label: 'Flexi drawdown' },
			{ value: 'UFPLS', label: 'UFPLS' }
		]);
	});

	it('asks crystallised only of a drawdown transfer, then the amount', () => {
		const flag = illustrationFields.find((f) => f.fieldKey === 'transfer_crystallised');
		expect(flag?.section).toBe('Transfer');
		expect(flag?.required).toBe(true);
		expect(flag?.conditions).toEqual({
			conditional: { field: 'quotation_type', operator: 'notEquals', value: 'Accumulation' }
		});
		const amount = illustrationFields.find((f) => f.fieldKey === 'transfer_crystallised_amount');
		expect(amount?.section).toBe('Transfer');
		expect(amount?.required).toBe(true);
		expect(amount?.conditions).toEqual({
			conditional: {
				logic: 'AND',
				conditions: [
					{ field: 'quotation_type', operator: 'notEquals', value: 'Accumulation' },
					{ field: 'transfer_crystallised', operator: 'equals', value: 'Yes' }
				]
			}
		});
		const keys = illustrationFields.map((f) => f.fieldKey);
		expect(keys).not.toContain('existing_fund');
		expect(keys).not.toContain('crystallised_fund');
	});

	it('lays Plan out as the model and the basis', () => {
		// The opening Amount and "Of which crystallised" were the existing fund. An adviser who
		// also entered a transfer illustrated both, so £10,000 plus a £10,000 transfer became
		// £20,000. The fund already held is the client's other SIPPs, shown under the client
		// details, not typed here.
		const plan = illustrationFields.filter((f) => f.section === 'Plan');
		expect(plan.map((f) => f.fieldKey)).toEqual(['model_id', 'quotation_type']);
		for (const f of plan) expect(f.validation?.width, f.fieldKey).toBe(6);
	});

	it('covers every section it renders in the section metadata', () => {
		// A section added without an entry would render with no description and no toggle, silently.
		const sections = new Set(illustrationFields.map((f) => f.section));
		expect([...sections].sort()).toEqual(Object.keys(ILLUSTRATION_SECTIONS).sort());
	});
});

describe('illustrationFieldsFor', () => {
	const complete = { retirement_age: 65, marital_status: 'married' };

	it('always asks for a retirement age, with nothing filled in', () => {
		const fields = illustrationFieldsFor(complete);
		expect(fields.map((f) => f.fieldKey)).toEqual([
			'retirement_age',
			...illustrationFields.map((f) => f.fieldKey)
		]);
		expect(fields[0]).toMatchObject({
			fieldKey: 'retirement_age',
			fieldType: 'number',
			required: true,
			validation: { min: 55, max: 75, width: 6 }
		});
		expect(fields[0]?.validation).not.toHaveProperty('defaultValue');
	});

	it('asks only for the gap, required, ahead of the plan', () => {
		const ageOnly = illustrationFieldsFor({ retirement_age: null, marital_status: 'single' });
		expect(ageOnly[0]).toMatchObject({
			fieldKey: 'retirement_age',
			fieldType: 'number',
			section: 'Plan',
			required: true
		});
		expect(ageOnly.map((f) => f.fieldKey)).not.toContain('marital_status');

		const both = illustrationFieldsFor({ retirement_age: null, marital_status: null });
		expect(both.slice(0, 2).map((f) => f.fieldKey)).toEqual(['retirement_age', 'marital_status']);
		expect(both[1]).toMatchObject({ fieldType: 'marital', required: true, options: null });
		// Still one row: the two facts share it, then the plan pairs follow.
		expect(both[0]?.validation?.width).toBe(6);
		expect(both[1]?.validation?.width).toBe(6);
		expect(both.map((f) => f.sortOrder)).toEqual(both.map((_, i) => i));
	});

	it('treats a blank marital status as a gap', () => {
		const fields = illustrationFieldsFor({ retirement_age: 65, marital_status: '' });
		expect(fields.map((f) => f.fieldKey)).toContain('marital_status');
		expect(fields[0]?.fieldKey).toBe('retirement_age');
	});
});

describe('ILLUSTRATION_SECTIONS', () => {
	it('describes every section', () => {
		for (const [name, meta] of Object.entries(ILLUSTRATION_SECTIONS)) {
			expect(meta.description, `${name} has no description`).toBeTruthy();
		}
	});

	it('makes every optional section switchable, off by default', () => {
		// Transfer is one field and still its own section: a transfer in is a separate decision from
		// the fund already held, and as a lone optional field inside Plan it read as an afterthought.
		// The switch makes "no transfer" something the adviser stated.
		for (const name of ['Initial payment', 'Transfer', 'Contributions', 'Income']) {
			expect(ILLUSTRATION_SECTIONS[name].optional, name).toBe(true);
			expect(ILLUSTRATION_SECTIONS[name].defaultOn, name).toBe(false);
		}
	});

	it('has no Annuity basis section — it is a house assumption, not a client question', () => {
		// Removed 09 Aug 2026. The group is still SENT: the API applies house_annuity_basis() for any
		// caller that supplies none, which is deliberately not here — a portal that carries the
		// constants is a portal that can forget them, and forgetting means the group is omitted and
		// the KFI's projected-income column reads £0 on an accumulation quote.
		expect(ILLUSTRATION_SECTIONS['Annuity basis']).toBeUndefined();
		const annuityKeys = [
			'annuity_frequency',
			'annuity_timing',
			'escalation',
			'escalation_percent',
			'guaranteed_period',
			'spouse_percentage'
		];
		for (const key of annuityKeys) {
			expect(
				illustrationFields.find((f) => f.fieldKey === key),
				key
			).toBeUndefined();
		}
	});

	it('never lets the adviser switch off what the illustration cannot do without', () => {
		// Plan IS the illustration. Adviser charges is always shown with both fees optional — a firm
		// that charges nothing says so by leaving them blank, and a switch would make "we forgot to
		// ask" indistinguishable from "there are none".
		for (const name of ['Plan', 'Adviser charges']) {
			expect(ILLUSTRATION_SECTIONS[name].optional, name).toBeFalsy();
		}
	});
});

describe('toIllustrationPayload', () => {
	// Only what the form still collects. The annuity keys that used to sit here went with the
	// section; the test below passes its own to prove they are ignored rather than echoed.
	const base = { quotation_type: 'Accumulation' };

	it('always sends a prospective illustration — no account, a defaulted wrapper', () => {
		const out = toIllustrationPayload({ ...base }, 25000);
		expect(out.account_id).toBeNull();
		expect(out.product_type).toBe(PRODUCT_TYPE_DEFAULT);
		expect(out.existing_fund_value).toBe(25000);
	});

	it('sends the client’s other SIPPs as the current value, and ignores a typed fund', () => {
		expect(toIllustrationPayload(base).existing_fund_value).toBe(0);
		expect(toIllustrationPayload(base, null).existing_fund_value).toBe(0);
		const out = toIllustrationPayload({ ...base, existing_fund: '999' }, 25000);
		expect(out.existing_fund_value).toBe(25000);
		expect(out.crystallised_fund_value).toBeNull();
	});

	it('sends an initial payment as its own amount, including on a drawdown basis', () => {
		const out = toIllustrationPayload({
			quotation_type: 'Flexi Access Drawdown',
			initial_payment: '10000',
			contribution_amount: '250'
		});
		expect(out.initial_payment).toBe(10000);
		expect(out.member_contribution).toBeNull();
	});

	it('sends a crystallised amount only when a drawdown transfer says yes', () => {
		expect(
			toIllustrationPayload({
				...base,
				transfer_value: '10000',
				transfer_crystallised: 'Yes',
				transfer_crystallised_amount: '4000'
			}).transfer_crystallised_amount
		).toBeNull();
		expect(
			toIllustrationPayload({
				quotation_type: 'Flexi Access Drawdown',
				transfer_value: '10000',
				transfer_crystallised: 'Yes',
				transfer_crystallised_amount: '4000'
			}).transfer_crystallised_amount
		).toBe(4000);
		expect(
			toIllustrationPayload({
				quotation_type: 'Flexi Access Drawdown',
				transfer_value: '10000',
				transfer_crystallised_amount: '4000'
			}).transfer_crystallised_amount
		).toBeNull();
	});

	it('refuses a crystallised amount larger than the transfer', () => {
		expect(
			illustrationAnswerProblems({
				quotation_type: 'Flexi Access Drawdown',
				transfer_crystallised: 'Yes',
				transfer_value: '10000',
				transfer_crystallised_amount: '10001'
			})
		).toEqual(['The crystallised amount cannot be more than the transfer.']);
		expect(
			illustrationAnswerProblems({
				quotation_type: 'Accumulation',
				transfer_crystallised: 'Yes',
				transfer_value: '10000',
				transfer_crystallised_amount: '10001'
			})
		).toEqual([]);
		// Switching Transfer off keeps the typed answers. They are not sent, so they must not refuse.
		expect(
			illustrationAnswerProblems(
				{
					quotation_type: 'Flexi Access Drawdown',
					transfer_crystallised: 'Yes',
					transfer_value: '10000',
					transfer_crystallised_amount: '10001'
				},
				{ transferOn: false }
			)
		).toEqual([]);
	});

	it('sends no client facts the form did not ask for, so the record stands', () => {
		// Null is "the record already holds this". The API uses that value and will not let a
		// request overwrite it.
		expect(toIllustrationPayload(base).exit_age).toBeNull();
		expect(toIllustrationPayload(base).marital_status).toBeNull();
	});

	it('sends a gap the form collected, for the API to write onto the record', () => {
		const out = toIllustrationPayload({
			...base,
			retirement_age: '67',
			marital_status: 'married'
		});
		expect(out.exit_age).toBe(67);
		expect(out.marital_status).toBe('married');
	});

	it('sends the chosen model as a number', () => {
		expect(toIllustrationPayload({ ...base, model_id: '4' }).model_id).toBe(4);
	});

	it('sends no model when none is chosen, rather than 0', () => {
		// 0 is a real id shape; the engine must be able to tell "not chosen" from "model zero".
		expect(toIllustrationPayload(base).model_id).toBeNull();
	});

	it('omits a switched-off section entirely', () => {
		// DynamicForm.collect() drops a disabled section's fields, so they never reach here — the
		// payload then says nothing about contributions, which is different from contributing zero.
		const out = toIllustrationPayload({ ...base }, 1000);
		expect(out.member_contribution).toBeNull();
		expect(out.employer_contribution).toBeNull();
		expect(out.ongoing_adviser_fee_percent).toBeNull();
	});

	it('routes one contribution to the member pair when it is personal', () => {
		const out = toIllustrationPayload({
			...base,
			contributor: 'Personal',
			contribution_amount: '250',
			contribution_frequency: 'Monthly',
			contribution_net_gross: 'Gross'
		});
		expect(out.member_contribution).toBe(250);
		expect(out.member_contribution_frequency).toBe('Monthly');
		expect(out.member_contribution_net_gross).toBe('Gross');
		expect(out.employer_contribution).toBeNull();
		expect(out.employer_contribution_frequency).toBeNull();
	});

	it('routes the same contribution to the employer pair when it is not', () => {
		// The form asks the amount once; only the destination changes. Net/gross goes with the
		// member stream — relief at source is on the member's own money — and the API has no
		// employer equivalent to send it to.
		const out = toIllustrationPayload({
			...base,
			contributor: 'Employer',
			contribution_amount: '400',
			contribution_frequency: 'Quarterly'
		});
		expect(out.employer_contribution).toBe(400);
		expect(out.employer_contribution_frequency).toBe('Quarterly');
		expect(out.member_contribution).toBeNull();
		expect(out.member_contribution_frequency).toBeNull();
		expect(out.member_contribution_net_gross).toBeNull();
	});

	it('leaves no stale member stream behind when the payer is switched to Employer', () => {
		// Two amount fields meant an adviser who filled in Personal, then decided it was the
		// employer, sent both — doubling the contribution with nothing on screen saying so.
		const out = toIllustrationPayload({
			...base,
			contributor: 'Employer',
			contribution_amount: '400',
			contribution_frequency: 'Monthly',
			contribution_net_gross: 'Gross'
		});
		expect(out.member_contribution).toBeNull();
		expect(out.member_contribution_net_gross).toBeNull();
		expect(out.employer_contribution).toBe(400);
	});

	it('sends no annuity keys at all', () => {
		// Explicit nulls would look like an answer. The endpoint distinguishes "no preference"
		// (house basis) from "leave the group out" (include_annuity_basis: false), and the form
		// means the first, so it says nothing.
		const out = toIllustrationPayload({ ...base, escalation: 'RPI', spouse_percentage: '50' });
		for (const key of [
			'annuity_frequency',
			'annuity_timing',
			'escalation',
			'escalation_percent',
			'guaranteed_period',
			'spouse_percentage'
		]) {
			expect(key in out, key).toBe(false);
		}
	});

	it('nulls drawdown fields off a drawdown basis', () => {
		const acc = toIllustrationPayload({
			...base,
			drawdown_type: 'Flexible',
			withdrawal_amount: '500'
		});
		expect(acc.drawdown_type).toBeNull();
		expect(acc.withdrawal_amount).toBeNull();

		const dd = toIllustrationPayload({
			...base,
			quotation_type: 'Drawdown',
			drawdown_type: 'Flexible',
			withdrawal_amount: '500'
		});
		expect(dd.drawdown_type).toBe('Flexible');
		expect(dd.withdrawal_amount).toBe(500);
	});

	it('nulls contributions on a drawdown basis, even if the form still holds them', () => {
		// The section is hidden, but values can linger (the full-page composer posts raw `values`).
		const out = toIllustrationPayload({
			quotation_type: 'Flexi Access Drawdown',
			contributor: 'Personal',
			contribution_amount: '250',
			contribution_frequency: 'Monthly',
			contribution_net_gross: 'Gross'
		});
		expect(out.member_contribution).toBeNull();
		expect(out.member_contribution_frequency).toBeNull();
		expect(out.member_contribution_net_gross).toBeNull();
		expect(out.employer_contribution).toBeNull();
		expect(out.employer_contribution_frequency).toBeNull();
	});

	it('coerces blanks to null, not 0', () => {
		const out = toIllustrationPayload({ ...base, initial_payment: '', ongoing_fee: '  ' });
		expect(out.initial_payment).toBeNull();
		expect(out.crystallised_fund_value).toBeNull();
		expect(out.ongoing_adviser_fee_percent).toBeNull();
	});
});

describe('retirementAgeWrittenBack', () => {
	it('writes a typed age back only when the record has none it can keep', () => {
		expect(retirementAgeWrittenBack(null, '60')).toBe(60);
		expect(retirementAgeWrittenBack(undefined, '60')).toBe(60);
		expect(retirementAgeWrittenBack(52, '60')).toBe(60);
		expect(retirementAgeWrittenBack(65, '60')).toBeNull();
		expect(retirementAgeWrittenBack(75, '60')).toBeNull();
		expect(retirementAgeWrittenBack(null, '')).toBeNull();
		expect(retirementAgeWrittenBack(null, 'nope')).toBeNull();
	});
});

describe('illustrationTerm', () => {
	const on = new Date('2026-10-11T12:00:00Z');
	const retired = { dob: '1940-06-01', retirement_age: 75 };

	it('runs a drawdown illustration forward when retirement age is already past', () => {
		const term = illustrationTerm(retired, 'Flexi Access Drawdown', on);
		expect(term.problem).toBeNull();
		expect(term.note).toBe(
			'Retirement age 75 is already past. This drawdown illustration runs to age 99.'
		);
	});

	it('blocks an accumulation illustration once retirement age is past', () => {
		const term = illustrationTerm(retired, 'Accumulation', on);
		expect(term.note).toBeNull();
		expect(term.problem).toMatch(/already past/);
		expect(term.problem).toMatch(/drawdown/);
	});

	it('says nothing when retirement is still ahead', () => {
		expect(
			illustrationTerm({ dob: '1970-01-01', retirement_age: 67 }, 'Flexi Access Drawdown', on)
		).toEqual({ note: null, problem: null });
	});

	it('measures the term from the age typed, not the one stored', () => {
		const term = illustrationTerm(retired, 'Flexi Access Drawdown', on, '60');
		expect(term.problem).toBeNull();
		expect(term.note).toMatch(/Retirement age 60/);
	});

	it('uses a retirement age typed for a record that does not hold one yet', () => {
		// The column only goes to 75, so an 86-year-old filling the gap can only enter an age
		// already past. Drawdown still illustrates; the typed age is when they retired.
		const term = illustrationTerm(
			{ dob: '1940-06-01', retirement_age: null },
			'Flexi Access Drawdown',
			on,
			'75'
		);
		expect(term.problem).toBeNull();
		expect(term.note).toMatch(/runs to age 99/);
	});
});
