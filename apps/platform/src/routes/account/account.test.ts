/**
 * The profile page.
 *
 * It used to validate the three fields and return `{ saved: true }` with nothing persisted anywhere
 * — the page then rendered "Your details have been saved." above a footnote admitting changes were
 * not persisted. There is no endpoint for this yet (`/me` is read-only), so the page is read-only
 * and the action fails closed. The first assertion below fails against the old version.
 */

import { describe, expect, it } from 'vitest';
import { _PROFILE_READ_ONLY_MESSAGE, actions } from './+page.server';

type ActionArg = Parameters<typeof actions.default>[0];

function event(fields: Record<string, string>) {
	return {
		request: new Request('http://portal.test/account', {
			method: 'POST',
			body: new URLSearchParams(fields)
		})
	};
}

describe('profile save', () => {
	it('never claims to have saved anything', async () => {
		const result = (await actions.default(
			event({
				firstName: 'Jo',
				lastName: 'Bloggs',
				email: 'jo@firm.test'
			}) as unknown as ActionArg
		)) as { status: number; data: { error: string } };
		expect(result.status).toBe(503);
		expect(result.data.error).toBe(_PROFILE_READ_ONLY_MESSAGE);
		expect(result.data).not.toHaveProperty('saved');
	});

	it('says who can change these details', () => {
		expect(_PROFILE_READ_ONLY_MESSAGE).toMatch(/administrator/i);
	});
});
