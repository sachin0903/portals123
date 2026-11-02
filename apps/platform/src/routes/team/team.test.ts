/**
 * The firm's Team page (invite + resend).
 *
 * Two defects, both about telling the adviser the truth:
 *
 * 1. **It rendered a live credential.** The invite response's `invitation_url` is a set-password
 *    link — anyone holding it can set that person's password and sign in as them. The action read
 *    it out of the response and the page put it in an `<Input>` plus an "Open link" button, so the
 *    credential landed in the page HTML, the action payload, and every proxy log and browser
 *    history in between. It is no longer read at all.
 * 2. **It claimed the email had been sent.** With no link to show it fell through to "An invitation
 *    email has been sent." unconditionally — including when Resend was unconfigured and nothing had
 *    left the building, leaving the adviser waiting for a person who could never get in. The action
 *    now surfaces the API's own `email_delivered`.
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it, vi } from 'vitest';
import { actions } from './+page.server';

/** A live set-password link, as `provisioning.grant_portal_access(reveal_link=True)` returns it. */
const LIVE_LINK = 'https://firm.portal.test/set-password?token=a-live-password-set-token';

function event(
	action: 'addMember' | 'resendInvite',
	fields: Record<string, string>,
	body: unknown
) {
	const fetch = vi.fn(async () => new Response(JSON.stringify(body), { status: 200 }));
	return {
		fetch,
		event: {
			request: new Request(`http://portal.test/team?/${action}`, {
				method: 'POST',
				body: new URLSearchParams(fields)
			}),
			locals: {
				user: { id: 'user_1', email: 'adviser@firm.test' },
				accessToken: 'token',
				// `members:write` — otherwise requireManage redirects before the action runs.
				permissions: ['members:write'],
				role: 'adviser'
			}
		}
	};
}

type AddArg = Parameters<typeof actions.addMember>[0];
type ResendArg = Parameters<typeof actions.resendInvite>[0];

const INVITE = { email: 'newbie@firm.test', role_slugs: 'adviser' };

describe('inviting a member', () => {
	it('sends the complete staff role set, not a portal-access boolean', async () => {
		const e = event(
			'addMember',
			{ email: 'newbie@firm.test', role_slugs: 'adviser' },
			{ email_delivered: true }
		);
		await actions.addMember({ ...e.event, fetch: e.fetch } as unknown as AddArg);
		const [, init] = e.fetch.mock.calls[0] as unknown as [string, { body: string }];
		expect(JSON.parse(init.body)).toMatchObject({ role_slugs: ['adviser'] });
		expect(init.body).not.toContain('portal');
	});

	it('never puts the set-password link in the action payload, even if the API returns one', async () => {
		// Belt and braces: the API withholds `invitation_url` unless `reveal_link` is asked for, and
		// this endpoint does not ask. This asserts the page is safe even if that ever changes —
		// the credential must not survive anywhere in what the action hands to the browser.
		const { fetch, event: e } = event('addMember', INVITE, {
			invitation_sent: true,
			email_delivered: true,
			invitation_url: LIVE_LINK
		});
		const result = await actions.addMember({ ...e, fetch } as unknown as AddArg);
		const serialised = JSON.stringify(result);
		expect(serialised).not.toContain(LIVE_LINK);
		expect(serialised).not.toContain('set-password');
		expect(serialised).not.toContain('a-live-password-set-token');
		expect(result).not.toHaveProperty('invitationUrl');
		expect(result).not.toHaveProperty('invitation_url');
		// ...and it does not ask for the link either.
		const [, init] = fetch.mock.calls[0] as unknown as [string, { body: string }];
		expect(init.body).not.toContain('reveal');
	});

	it('says the invitation was emailed only when it actually was', async () => {
		const sent = await actions.addMember({
			...event('addMember', INVITE, { invitation_sent: true, email_delivered: true }).event,
			fetch: event('addMember', INVITE, { invitation_sent: true, email_delivered: true }).fetch
		} as unknown as AddArg);
		expect(sent).toMatchObject({
			action: 'addMember',
			ok: true,
			invitedEmail: 'newbie@firm.test',
			invitationSent: true,
			emailDelivered: true
		});

		const undelivered = event('addMember', INVITE, {
			invitation_sent: true,
			email_delivered: false
		});
		const result = await actions.addMember({
			...undelivered.event,
			fetch: undelivered.fetch
		} as unknown as AddArg);
		expect(result).toMatchObject({ ok: true, invitationSent: true, emailDelivered: false });
	});

	it('treats a response it cannot parse as "not delivered" rather than assuming success', async () => {
		const e = event('addMember', INVITE, null);
		const fetch = vi.fn(async () => new Response('not json', { status: 200 }));
		const result = await actions.addMember({
			...e.event,
			fetch
		} as unknown as AddArg);
		expect(result).toMatchObject({ ok: true, emailDelivered: false });
	});
});

describe('resending an invitation', () => {
	it('reports the delivery outcome so the toast cannot claim a resend that failed', async () => {
		const delivered = event('resendInvite', { user_id: 'u1' }, { email_delivered: true });
		expect(
			await actions.resendInvite({
				...delivered.event,
				fetch: delivered.fetch
			} as unknown as ResendArg)
		).toMatchObject({ action: 'resendInvite', ok: true, userId: 'u1', emailDelivered: true });

		const failed = event('resendInvite', { user_id: 'u1' }, { email_delivered: false });
		expect(
			await actions.resendInvite({
				...failed.event,
				fetch: failed.fetch
			} as unknown as ResendArg)
		).toMatchObject({ ok: true, emailDelivered: false });
	});

	it('does not leak the freshly minted link either', async () => {
		const e = event(
			'resendInvite',
			{ user_id: 'u1' },
			{
				email_delivered: true,
				invitation_url: LIVE_LINK
			}
		);
		const result = await actions.resendInvite({
			...e.event,
			fetch: e.fetch
		} as unknown as ResendArg);
		expect(JSON.stringify(result)).not.toContain('set-password');
	});
});

describe('the directory row', () => {
	it('carries adviser and job title so the listing can sort and badge without a second read', () => {
		const source = readFileSync(join(import.meta.dirname, '+page.svelte'), 'utf8');
		expect(source).toContain('isAdviser:');
		expect(source).toContain('jobTitle:');
		expect(source).not.toContain('action="?/updateUser"');
		expect(source).not.toContain('name="is_adviser"');
	});
});
