/**
 * The email-template delete confirm's usage map — fetched when the confirm opens, not on first
 * paint of `/templates?kind=emails`.
 */
import { describe, expect, it } from 'vitest';
import { buildTemplateUsage } from '$lib/email-template-usage';
import { GET } from './+server';
import { harness, json, staff, withPermissions } from '../harness';

const RULES = [
	{
		uid: 'r1',
		trigger_key: 'task.assigned',
		audience: 'staff',
		email_code: 'invite',
		enabled: true
	}
];
const NOTIF_CATALOGUE = [
	{
		key: 'task.assigned',
		label: 'Task assigned',
		audiences: [{ key: 'staff', label: 'The adviser', default_email_code: null }]
	}
];

type Handler = Parameters<typeof GET>[0];

async function usageFor(permissions: string[], over: (u: string) => Response | null = () => null) {
	const { fetch, calls } = harness(
		(u) =>
			over(u) ??
			(u.includes('/firm/notification-rules')
				? json(RULES)
				: u.includes('/firm/notification-catalogue')
					? json(NOTIF_CATALOGUE)
					: json([]))
	);
	const res = await GET({
		locals: withPermissions(permissions),
		fetch
	} as unknown as Handler);
	return { status: res.status, body: await res.json(), calls };
}

describe('GET /templates/emails/usage', () => {
	it('refuses a signed-in user without email_templates:read', async () => {
		const out = await usageFor([]);
		expect(out.status).toBe(403);
		expect(out.calls).toEqual([]);
	});

	it('maps each enabled rule onto the template code it actually renders', async () => {
		const { fetch } = harness((u) =>
			u.includes('/firm/notification-rules')
				? json([
						...RULES,
						{ uid: 'r2', trigger_key: 'task.assigned', audience: 'staff', enabled: true },
						{ uid: 'r3', trigger_key: 'task.assigned', audience: 'staff', enabled: false }
					])
				: json(NOTIF_CATALOGUE)
		);
		const res = await GET({ locals: staff, fetch } as unknown as Handler);
		expect(await res.json()).toEqual({
			invite: ['Task assigned (The adviser)'],
			notification: ['Task assigned (The adviser)']
		});
	});

	it('returns an empty map when the usage calls fail — a warning is not a prerequisite', async () => {
		const out = await usageFor(['email_templates:write'], () => json({}, 403));
		expect(out.status).toBe(200);
		expect(out.body).toEqual({});
	});
});

describe('buildTemplateUsage', () => {
	it('falls back to the wrapper when a rule names no template of its own', () => {
		expect(
			buildTemplateUsage(
				[{ ...RULES[0], email_code: null, enabled: true }] as never,
				NOTIF_CATALOGUE as never
			)
		).toEqual({ notification: ['Task assigned (The adviser)'] });
	});
});
