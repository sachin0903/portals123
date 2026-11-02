/**
 * The email-template delete confirm's usage map — fetched when the confirm opens, not on first
 * paint of the emails list.
 */
import { describe, expect, it } from 'vitest';
import { GET } from './+server';
import { API, FIRM, locals, okJson, tenantApi } from '../proxy-harness';

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

describe('GET /tenants/[uid]/email-templates/usage', () => {
	it('refuses without tenants:read', async () => {
		const api = tenantApi(FIRM, () => okJson([]));
		const res = await GET({
			params: { uid: FIRM.uid },
			locals: locals([]),
			fetch: api.fetch
		} as never);
		expect(res.status).toBe(403);
		expect(api.made()).toEqual([]);
	});

	it('maps each enabled rule onto the template code it actually renders', async () => {
		const api = tenantApi(FIRM, (url) => {
			if (url.includes('/notification-rules')) return okJson(RULES);
			if (url.includes('/notification-catalogue')) return okJson(NOTIF_CATALOGUE);
			return okJson([]);
		});
		const res = await GET({
			params: { uid: FIRM.uid },
			locals: locals(['tenants:read']),
			fetch: api.fetch
		} as never);
		expect(res.status).toBe(200);
		expect(await res.json()).toEqual({
			invite: ['Task assigned (The adviser)']
		});
		expect(api.made()).toEqual([
			`GET ${API}/admin/tenants/${FIRM.uid}/notification-rules`,
			`GET ${API}/admin/notification-catalogue`
		]);
	});
});
