/**
 * The seat a reader takes on the queue, how long a case has waited, whose move it is, what
 * approving means for each kind of hold, which case opens next, and which record card a details
 * hold belongs to.
 */
import { describe, expect, it } from 'vitest';
import {
	defaultReviewSeat,
	detailCardOf,
	matchingReviews,
	nextReviewFor,
	reviewApproveCopy,
	reviewChangeDetail,
	reviewDecidesSentence,
	reviewDetailCards,
	reviewRejectCopy,
	reviewSeatCounts,
	reviewSeatOf,
	reviewsForCard,
	reviewsOffTheCards,
	reviewUndecidable,
	reviewWaiting,
	seatMatches,
	waitingLabel,
	waitingTone
} from './reviews';
import type { ReviewRow } from './types';

const T0 = Date.parse('2026-10-22T18:00:00Z');
const HOUR = 3_600_000;

const row = (over: Partial<ReviewRow> = {}): ReviewRow => ({
	uid: 'rev_1',
	case_no: 3,
	action_key: 'client.details_changed',
	label: 'Edit client details',
	summary: 'Edit client details',
	before_label: 'Current details',
	after_label: 'Change W-9 or W-8',
	event_type: 'client.details_updated',
	subject_type: 'contact',
	subject_id: 'con_1',
	contact_uid: 'con_1',
	contact_name: 'Mark Black',
	account_uid: null,
	account_name: null,
	justification: '',
	reviewer_role_id: 1,
	reviewer_role_slug: 'adviser',
	reviewer_role_name: 'Adviser',
	approver_roles: ['Adviser', 'Administrator'],
	eligible_approvers: 3,
	proposed_by: 'user-1',
	proposed_by_name: 'Mark Alford',
	proposed_at: new Date(T0 - 9 * HOUR).toISOString(),
	status: 'pending',
	decided_by: null,
	decided_by_name: null,
	decided_at: null,
	decision_note: null,
	applied_at: null,
	is_mine: false,
	can_decide: true,
	payload: {
		contact_id: 1,
		fields: {
			us_person: false,
			fatca_status: null,
			source_of_funds: 'personal_savings',
			source_of_wealth: 'salary'
		}
	},
	before: {
		us_person: false,
		fatca_status: 'w8',
		source_of_funds: 'personal_savings',
		source_of_wealth: 'salary'
	},
	...over
});

describe('the seats', () => {
	const forMe = row({ uid: 'a', can_decide: true });
	const mine = row({ uid: 'b', can_decide: false, is_mine: true });
	const theirs = row({ uid: 'c', can_decide: false });
	const done = row({ uid: 'd', status: 'approved', can_decide: false });

	it('reads a seat off the URL and nothing else', () => {
		expect(reviewSeatOf('for_me')).toBe('for_me');
		expect(reviewSeatOf('settled')).toBe('settled');
		expect(reviewSeatOf('inbox')).toBeNull();
		expect(reviewSeatOf(null)).toBeNull();
	});

	it('sorts the rows into the four seats', () => {
		expect(seatMatches(forMe, 'for_me')).toBe(true);
		expect(seatMatches(mine, 'for_me')).toBe(false);
		expect(seatMatches(mine, 'mine')).toBe(true);
		expect(seatMatches(row({ status: 'approved', is_mine: true, can_decide: false }), 'mine')).toBe(
			true
		);
		expect(seatMatches(theirs, 'waiting')).toBe(true);
		expect(seatMatches(done, 'waiting')).toBe(false);
		expect(seatMatches(done, 'settled')).toBe(true);
	});

	it('counts the three working seats and leaves Settled uncounted', () => {
		expect(reviewSeatCounts([forMe, mine, theirs, done])).toEqual({
			for_me: 1,
			mine: 1,
			waiting: 3,
			settled: null
		});
	});

	it('opens on For me when there is something to decide, else on everything waiting', () => {
		expect(defaultReviewSeat([forMe, theirs])).toBe('for_me');
		expect(defaultReviewSeat([mine, theirs])).toBe('waiting');
		expect(defaultReviewSeat([])).toBe('waiting');
	});

	it('filters by seat, and only the Settled seat honours the outcome filter', () => {
		const all = [forMe, mine, theirs, done, row({ uid: 'e', status: 'rejected' })];
		const base = { submitted_by: [], action_key: '', status: '', q: '' };
		expect(matchingReviews(all, { ...base, seat: 'for_me' }).map((r) => r.uid)).toEqual(['a']);
		expect(matchingReviews(all, { ...base, seat: 'waiting' }).map((r) => r.uid)).toEqual([
			'a',
			'b',
			'c'
		]);
		expect(matchingReviews(all, { ...base, seat: 'settled' }).map((r) => r.uid)).toEqual([
			'd',
			'e'
		]);
		expect(
			matchingReviews(all, { ...base, seat: 'settled', status: 'rejected' }).map((r) => r.uid)
		).toEqual(['e']);
		// an outcome filter under a working seat is ignored rather than emptying the queue
		expect(
			matchingReviews(all, { ...base, seat: 'waiting', status: 'rejected' }).map((r) => r.uid)
		).toEqual(['a', 'b', 'c']);
		// with no seat the older rule holds: unset is the waiting queue
		expect(matchingReviews(all, base).map((r) => r.uid)).toEqual(['a', 'b', 'c']);
	});
});

describe('how long a case has waited', () => {
	it('says a duration the way a queue does, coarser as it grows', () => {
		expect(waitingLabel(25 * 60_000)).toBe('25m');
		expect(waitingLabel(9 * HOUR)).toBe('9h');
		expect(waitingLabel(33 * HOUR)).toBe('1d 9h');
		expect(waitingLabel(10 * 24 * HOUR)).toBe('10d');
		expect(waitingLabel(-1)).toBe('—');
	});

	it('is plain under a day, amber past it and red past three', () => {
		expect(waitingTone(9 * HOUR)).toBe('none');
		expect(waitingTone(24 * HOUR)).toBe('warning');
		expect(waitingTone(72 * HOUR)).toBe('danger');
	});

	it('measures a pending case to now and a settled one to its decision', () => {
		expect(reviewWaiting(row(), T0)).toMatchObject({ label: '9h', tone: 'none', settled: false });
		const old = row({ proposed_at: new Date(T0 - 33 * HOUR).toISOString() });
		expect(reviewWaiting(old, T0)).toMatchObject({ label: '1d 9h', tone: 'warning' });
		const done = row({
			status: 'approved',
			decided_at: new Date(T0 - 7 * HOUR).toISOString()
		});
		expect(reviewWaiting(done, T0)).toMatchObject({ label: '2h', tone: 'none', settled: true });
	});
});

describe('whose move it is', () => {
	it('tells the reader they hold the role, who it waits on, or that nobody can decide', () => {
		expect(reviewDecidesSentence(row())).toBe('You hold Adviser · Administrator');
		expect(reviewDecidesSentence(row({ can_decide: false }))).toBe(
			'Waiting on Adviser · Administrator · 3 people'
		);
		expect(reviewDecidesSentence(row({ can_decide: false, eligible_approvers: 1 }))).toBe(
			'Waiting on Adviser · Administrator · 1 person'
		);
		const stuck = row({ can_decide: false, eligible_approvers: 0, approver_roles: ['Compliance'] });
		expect(reviewUndecidable(stuck)).toBe(true);
		expect(reviewDecidesSentence(stuck)).toBe('Nobody else holds Compliance');
		expect(reviewUndecidable(row({ status: 'approved', eligible_approvers: 0 }))).toBe(false);
	});

	it('falls back to the first role on an older row', () => {
		expect(reviewDecidesSentence(row({ approver_roles: undefined, can_decide: false }))).toBe(
			'Waiting on Adviser · 3 people'
		);
	});
});

describe('what approving means', () => {
	it('names the card for a details edit', () => {
		const copy = reviewApproveCopy(row());
		expect(copy.title).toBe('Approve this change?');
		expect(copy.description).toContain("compliance write to the record as Mark Alford's save");
	});

	it('says that a held payment is SENT, with the figure', () => {
		const copy = reviewApproveCopy(
			row({
				action_key: 'transaction.payment',
				label: 'Payment',
				source: 'action',
				input: { kind: 'expected_in', amount: 25000 },
				payload: null,
				before: null
			})
		);
		expect(copy.title).toBe('Approve and send £25,000.00 now?');
		expect(copy.description).toContain('goes to the custodian');
		expect(
			reviewRejectCopy(row({ source: 'action', action_key: 'transaction.payment' })).description
		).toContain('Nothing is sent');
	});

	it('warns that a bank change moves future withdrawals', () => {
		const copy = reviewApproveCopy(row({ action_key: 'contact.bank_details_changed' }));
		expect(copy.title).toBe('Approve this bank change?');
		expect(copy.description).toContain('Future withdrawals will go to this account');
	});

	it('says a model change is pushed to the custodian after the save', () => {
		expect(reviewApproveCopy(row({ action_key: 'account.model_changed' })).description).toContain(
			'goes to the custodian as its own run'
		);
	});
});

describe('the next case', () => {
	it('opens the next case the reader may decide, wrapping to the first, else nothing', () => {
		const rows = [
			row({ uid: 'a' }),
			row({ uid: 'b', can_decide: false }),
			row({ uid: 'c' }),
			row({ uid: 'd' })
		];
		expect(nextReviewFor(rows, 'a')).toBe('c');
		expect(nextReviewFor(rows, 'd')).toBe('a');
		expect(nextReviewFor([row({ uid: 'a' })], 'a')).toBeNull();
	});
});

describe("the record's cards", () => {
	it('knows which card each contact column lives on', () => {
		expect(detailCardOf('fatca_status')).toBe('compliance');
		expect(detailCardOf('first_name')).toBe('contact');
		expect(detailCardOf('retirement_age')).toBe('pension');
		expect(detailCardOf('employer_name')).toBe('employer');
		expect(detailCardOf('dob')).toBe('personal');
		expect(detailCardOf('made_up')).toBeNull();
	});

	it('places a details hold on the cards it touches', () => {
		expect(reviewDetailCards(row())).toEqual(['compliance']);
		const two = row({ payload: { fields: { first_name: 'M', dob: '1970-01-01' } } });
		expect(reviewDetailCards(two)).toEqual(['contact', 'personal']);
		expect(reviewsForCard([row(), two], 'personal').map((r) => r.uid)).toEqual([two.uid]);
		expect(reviewsOffTheCards([row({ payload: { fields: { made_up: 1 } } })])).toHaveLength(1);
		expect(reviewsOffTheCards([row()])).toHaveLength(0);
		expect(reviewDetailCards(row({ action_key: 'contact.bank_details_changed' }))).toEqual([]);
	});

	it('says in one line how much of the card moves', () => {
		expect(reviewChangeDetail(row())).toBe('1 of 4 fields on the compliance');
		expect(reviewChangeDetail(row({ justification: 'Client asked' }))).toBe(
			'1 of 4 fields on the compliance · “Client asked”'
		);
		expect(
			reviewChangeDetail(
				row({
					action_key: 'transaction.payment',
					source: 'action',
					input: { kind: 'expected_in', amount: 100 },
					account_name: 'Wilson GIA',
					payload: null
				})
			)
		).toBe('Expected in · Wilson GIA');
	});
});
