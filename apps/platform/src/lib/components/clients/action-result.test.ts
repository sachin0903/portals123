import { describe, expect, it } from 'vitest';
import { outcomeOf } from './action-result';

describe('outcomeOf', () => {
	it('reads a failure that arrived with HTTP 200', () => {
		// This is the whole point: SvelteKit sends `fail(403)` back as HTTP 200 with the failure in
		// the body, so archive/delete checking `res.ok` saw every refusal as a success.
		const o = outcomeOf({
			type: 'failure',
			status: 403,
			data: { error: 'You do not have permission to archive documents.' }
		});
		expect(o.ok).toBe(false);
		expect(o.error).toBe('You do not have permission to archive documents.');
	});

	it('falls back to the caller’s message when the failure carries none', () => {
		expect(outcomeOf({ type: 'failure', status: 500, data: {} }, 'Couldn’t archive that.')).toEqual(
			{
				ok: false,
				error: 'Couldn’t archive that.'
			}
		);
	});

	it('treats an unexpected error as a failure, not a success', () => {
		expect(outcomeOf({ type: 'error', error: new Error('boom') }, 'nope').ok).toBe(false);
	});

	it('treats a redirect out of the action as a lost session', () => {
		const o = outcomeOf({ type: 'redirect', status: 303, location: '/login' });
		expect(o.ok).toBe(false);
		expect(o.error).toContain('sign in again');
	});

	it('accepts a success', () => {
		expect(outcomeOf({ type: 'success', status: 200, data: { ok: true } })).toEqual({ ok: true });
		expect(outcomeOf({ type: 'success', status: 204 })).toEqual({ ok: true });
	});
});
