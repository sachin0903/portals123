import { describe, expect, it } from 'vitest';
import {
	asRegularMovements,
	draftsFrom,
	emptyMovementDraft,
	movementFrequencyLabel,
	movementScheduleCaption,
	movementsFromDrafts,
	scheduleFrequencyChoices,
	SCHEDULE_FREQUENCIES
} from './regular.js';

describe('asRegularMovements', () => {
	it('reads the register array this card renders', () => {
		expect(
			asRegularMovements([
				{ amount: 500, frequency: 'monthly', method: 'direct_debit', next_date: '2026-08-01' }
			])
		).toEqual([
			{
				amount: 500,
				frequency: 'monthly',
				next_date: '2026-08-01',
				method: 'direct_debit',
				reference: null
			}
		]);
	});

	it('reads onboarding’s regular_payments object, mapping date onto next_date', () => {
		expect(
			asRegularMovements({
				initial_cash_deposit: { investment_amount: 10_000 },
				regular_payments: { amount: 250, frequency: 'monthly', date: '2026-09-01' }
			})
		).toEqual([
			{
				amount: 250,
				frequency: 'monthly',
				next_date: '2026-09-01',
				method: null,
				reference: null
			}
		]);
	});

	it('reads a bare onboarding withdrawal object', () => {
		expect(asRegularMovements({ amount: 100, frequency: 'quarterly', date: '2026-10-06' })).toEqual(
			[
				{
					amount: 100,
					frequency: 'quarterly',
					next_date: '2026-10-06',
					method: null,
					reference: null
				}
			]
		);
	});

	it('does not invent a movement from an initial deposit alone', () => {
		expect(asRegularMovements({ initial_cash_deposit: { investment_amount: 10_000 } })).toEqual([]);
	});

	it('treats null, a string, and an empty object as none set up', () => {
		expect(asRegularMovements(null)).toEqual([]);
		expect(asRegularMovements('monthly')).toEqual([]);
		expect(asRegularMovements({})).toEqual([]);
	});
});

describe('drafts round-trip', () => {
	it('seeds one empty row so the form is ready to fill, not an empty list', () => {
		expect(draftsFrom([])).toEqual([emptyMovementDraft()]);
	});

	it('drops empty amounts on the way out, so clearing the last row is a clear', () => {
		expect(
			movementsFromDrafts([
				emptyMovementDraft(),
				{ amount: 0, frequency: 'monthly', method: '', next_date: '', reference: '' }
			])
		).toEqual([]);
		expect(
			movementsFromDrafts([
				{
					amount: 500,
					frequency: 'monthly',
					method: 'direct_debit',
					next_date: '2026-08-01',
					reference: 'SIPP'
				}
			])
		).toEqual([
			{
				amount: 500,
				frequency: 'monthly',
				next_date: '2026-08-01',
				method: 'direct_debit',
				reference: 'SIPP'
			}
		]);
	});
});

describe('schedule frequencies', () => {
	it('offers monthly, quarterly and yearly — not half-yearly', () => {
		expect(SCHEDULE_FREQUENCIES.map((item) => item.value)).toEqual([
			'monthly',
			'quarterly',
			'annually'
		]);
		expect(SCHEDULE_FREQUENCIES.map((item) => item.label)).toEqual([
			'Monthly',
			'Quarterly',
			'Yearly'
		]);
	});

	it('keeps a stored half-yearly choice on an update, and only then', () => {
		expect(scheduleFrequencyChoices(null).map((item) => item.value)).toEqual([
			'monthly',
			'quarterly',
			'annually'
		]);
		expect(scheduleFrequencyChoices('half_yearly').map((item) => item.label)).toContain(
			'Half-yearly'
		);
	});

	it('names frequency and the next date, and never a dash for a method nobody stored', () => {
		const format = (iso: string) => iso;
		expect(movementScheduleCaption('monthly', '2026-10-01', format)).toBe(
			'Monthly · next 2026-10-01'
		);
		expect(movementScheduleCaption(null, '2026-10-01', format)).toBe('Next 2026-10-01');
		expect(movementScheduleCaption('quarterly', null, format)).toBe('Quarterly');
		expect(movementScheduleCaption(null, null, format)).toBeNull();
		expect(movementScheduleCaption('monthly', '2026-10-01', format)).not.toContain('—');
	});

	it('prints annually as Yearly and still names a stored half-yearly row', () => {
		expect(movementFrequencyLabel('annually')).toBe('Yearly');
		expect(movementFrequencyLabel('yearly')).toBe('Yearly');
		expect(movementFrequencyLabel('half_yearly')).toBe('Half-yearly');
		expect(movementFrequencyLabel('monthly')).toBe('Monthly');
		expect(movementFrequencyLabel(null)).toBe('—');
		// A stored value with no label is offered in the dropdown, so it is still sentence case.
		expect(movementFrequencyLabel('every_four_weeks')).toBe('Every four weeks');
	});
});
