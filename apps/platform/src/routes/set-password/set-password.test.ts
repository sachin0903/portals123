/**
 * Setting a password from the emailed link.
 *
 * The bug this pins: the action pushed onboarding completion with `res.email ?? ''`, but
 * `confirmPasswordReset` returned `{ status: 'ok' }` with no identity, so the push short-circuited
 * on the empty value and `POST /internal/onboarding-complete` never fired. Someone who HAD set
 * their password therefore stayed "pending / never invited" in the mirror until an operator pressed
 * "Sync from WorkOS". A test asserting only the redirect would have missed it entirely, so this
 * asserts the outbound call.
 */

import { describe, expect, it, vi } from 'vitest';
import { MIN_PASSWORD_LENGTH, PASSWORD_HINT } from '@graphene/auth';

vi.mock('$env/dynamic/private', () => ({
	env: { API_URL: 'http://api.test', INTERNAL_API_TOKEN: 'internal-token' }
}));

const { actions, load } = await import('./+page.server');

function event(fetchImpl: ReturnType<typeof vi.fn>, resetImpl?: () => Promise<unknown>) {
	return {
		request: new Request('http://portal.test/set-password', {
			method: 'POST',
			body: new URLSearchParams({
				token: 'invite_tok',
				token_kind: 'invitation',
				password: 'three unrelated words',
				confirm: 'three unrelated words'
			})
		}),
		url: new URL('http://portal.test/set-password?invitation_token=invite_tok'),
		fetch: fetchImpl,
		locals: {
			auth: {
				workos: {
					userManagement: {
						findInvitationByToken: vi.fn(async () => ({
							id: 'inv_42',
							email: 'jo@firm.example',
							state: 'pending'
						})),
						createPasswordReset: vi.fn(async () => ({ passwordResetToken: 'reset_tok' })),
						resetPassword: vi.fn(
							resetImpl ?? (async () => ({ user: { id: 'user_42', email: 'jo@firm.example' } }))
						),
						acceptInvitation: vi.fn(async () => ({ state: 'accepted' }))
					}
				}
			}
		}
	};
}

type ActionArg = Parameters<typeof actions.default>[0];

describe('set password', () => {
	it('tells the API who just onboarded, then redirects to sign in', async () => {
		const doFetch = vi.fn(async () => new Response('{}', { status: 200 }));
		let location = '';
		try {
			await actions.default(event(doFetch) as unknown as ActionArg);
		} catch (e) {
			location = (e as { location: string }).location;
		}
		expect(location).toBe('/login?set=1');
		expect(doFetch).toHaveBeenCalledOnce();
		const [url, init] = doFetch.mock.calls[0] as unknown as [string, RequestInit];
		expect(url).toBe('http://api.test/internal/onboarding-complete');
		expect(JSON.parse(String(init.body))).toEqual({
			workos_user_id: 'user_42',
			email: 'jo@firm.example',
			host: 'portal.test'
		});
		expect((init.headers as Record<string, string>)['x-internal-token']).toBe('internal-token');
	});

	it('still redirects when the push fails — the password is already set', async () => {
		const doFetch = vi.fn(async () => {
			throw new Error('API down');
		});
		let location = '';
		try {
			await actions.default(event(doFetch) as unknown as ActionArg);
		} catch (e) {
			location = (e as { location: string }).location;
		}
		expect(location).toBe('/login?set=1');
	});

	it('does not push when the link was invalid', async () => {
		const doFetch = vi.fn(async () => new Response('{}', { status: 200 }));
		const result = (await actions.default(
			event(doFetch, async () => {
				throw { rawData: { code: 'password_reset_token_expired' } };
			}) as unknown as ActionArg
		)) as { status: number; data: { error: string } };
		expect(result.status).toBe(400);
		expect(doFetch).not.toHaveBeenCalled();
	});
});

describe('the set-password hint', () => {
	type LoadArg = Parameters<typeof load>[0];

	// The only place the 10-character rule is stated before the user types. It is rendered as the
	// SetPasswordForm subtext, and must come from the same constant the action validates against.
	it('states the floor the action enforces', async () => {
		const data = (await load({
			url: new URL('http://portal.test/set-password?invitation_token=tok')
		} as unknown as LoadArg)) as { token: string; hint: string };
		expect(data.token).toBe('tok');
		expect(data.hint).toBe(PASSWORD_HINT);
		expect(data.hint).toContain(`at least ${MIN_PASSWORD_LENGTH} characters`);
	});
});
