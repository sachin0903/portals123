import { describe, it, expect, vi } from 'vitest';
import { attachStepAnswers, blankStep, parseStepAnswers, stepStatusTone } from './flows.js';

describe('attachStepAnswers', () => {
	it('writes the collected answers onto the body synchronously', () => {
		// The regression this guards: the page used to render the payload into a `$state`-backed
		// hidden input and call requestSubmit() in the same tick, so enhance built the body from the
		// STALE input and the API stored `{}` — silent data loss on the staff fulfilment path.
		const body = new FormData();
		body.set('participant', '42');
		const ok = attachStepAnswers(body, {
			validate: () => true,
			collect: () => ({ first_name: 'Ada', attitude: 'balanced' })
		});
		expect(ok).toBe(true);
		expect(body.get('answers')).toBe('{"first_name":"Ada","attitude":"balanced"}');
		expect(body.get('participant')).toBe('42');
	});

	it('replaces any earlier answers entry rather than appending a second one', () => {
		const body = new FormData();
		body.append('answers', '{}');
		attachStepAnswers(body, { validate: () => true, collect: () => ({ a: 1 }) });
		expect(body.getAll('answers')).toEqual(['{"a":1}']);
	});

	it('refuses (and collects nothing) when the form does not validate', () => {
		const collect = vi.fn(() => ({ a: 1 }));
		const body = new FormData();
		expect(attachStepAnswers(body, { validate: () => false, collect })).toBe(false);
		expect(body.get('answers')).toBeNull();
		expect(collect).not.toHaveBeenCalled();
	});

	it('refuses when the form component has not mounted yet', () => {
		const body = new FormData();
		expect(attachStepAnswers(body, undefined)).toBe(false);
		expect(attachStepAnswers(body, null)).toBe(false);
		expect(body.get('answers')).toBeNull();
	});
});

describe('parseStepAnswers', () => {
	// The regression this guards: with JavaScript disabled the fulfilment form posts natively with no
	// `answers` entry at all (the questions are rendered by a component outside the posting <form>,
	// so there are no native inputs to fall back on). The action used to default to '{}', which the
	// API accepts for an all-optional form — completing a fact-find with nothing recorded.
	it('reports an ABSENT field rather than treating it as an empty submission', () => {
		const body = new FormData();
		body.set('participant', '42');
		expect(parseStepAnswers(body.get('answers'))).toEqual({ ok: false, reason: 'absent' });
	});

	it('accepts an object, including a deliberately empty one', () => {
		expect(parseStepAnswers('{"first_name":"Ada"}')).toEqual({
			ok: true,
			answers: { first_name: 'Ada' }
		});
		// '{}' IS a submission — an all-optional form may legitimately be submitted with nothing.
		expect(parseStepAnswers('{}')).toEqual({ ok: true, answers: {} });
	});

	it('refuses anything that is not a JSON object', () => {
		for (const raw of ['', 'not json', 'null', '[]', '"a string"', '7']) {
			expect(parseStepAnswers(raw)).toEqual({ ok: false, reason: 'malformed' });
		}
	});

	it('round-trips what attachStepAnswers writes', () => {
		const body = new FormData();
		attachStepAnswers(body, { validate: () => true, collect: () => ({ a: 1, b: [2] }) });
		expect(parseStepAnswers(body.get('answers'))).toEqual({ ok: true, answers: { a: 1, b: [2] } });
	});
});

describe('blankStep', () => {
	it('uses the step type defaults, not a hardcoded assignee/scope', () => {
		expect(blankStep('fill_form', 's1')).toMatchObject({
			id: 's1',
			type: 'fill_form',
			assignee: 'client',
			scope: 'participant',
			required: true,
			depends_on: []
		});
		expect(blankStep('setup_account', 's2')).toMatchObject({
			assignee: 'staff',
			scope: 'account'
		});
	});
});

describe('stepStatusTone', () => {
	it('maps unknown statuses to neutral rather than throwing', () => {
		expect(stepStatusTone('completed')).toBe('success');
		expect(stepStatusTone('who_knows')).toBe('neutral');
	});
});
