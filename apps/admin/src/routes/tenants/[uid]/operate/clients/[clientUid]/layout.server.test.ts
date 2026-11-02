import { existsSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { load } from './+layout.server';

const TENANT = 'ten_abc123';
const CLIENT = 'Xq7kP2mN9vRt';

function fakeFetch(): typeof globalThis.fetch {
	return (async () =>
		new Response(
			JSON.stringify({
				contact: {
					id: 7,
					uid: CLIENT,
					type: 'individual',
					first_name: 'Ada',
					surname: 'Lovelace',
					email: 'ada@example.com'
				},
				accounts: [],
				chats_unread: true
			}),
			{ status: 200, headers: { 'content-type': 'application/json' } }
		)) as typeof globalThis.fetch;
}

async function run() {
	const event = {
		locals: { user: { id: 'u' }, accessToken: 't', permissions: [], role: null },
		params: { uid: TENANT, clientUid: CLIENT },
		fetch: fakeFetch(),
		parent: async () => ({ tenant: { uid: TENANT, name: 'Firm' }, writing: false })
	};
	return (await load(event as never)) as unknown as {
		sidebarFocus: {
			title: string;
			back: { href: string };
			sections: { label: string; href: string; unread?: boolean }[];
		};
	};
}

describe('operate client record sidebar', () => {
	it('publishes the platform client sections under the operate prefix', async () => {
		const { sidebarFocus } = await run();
		const base = `/tenants/${TENANT}/operate/clients/${CLIENT}`;
		expect(sidebarFocus.title).toBe('Ada Lovelace');
		expect(sidebarFocus.back.href).toBe(`/tenants/${TENANT}/operate`);
		expect(sidebarFocus.sections.map((s) => s.label)).toEqual([
			'Overview',
			'Details',
			'Journeys',
			'Transactions',
			'Documents',
			'Illustrations',
			'Compliance',
			'Emails',
			'Activity',
			'Messages'
		]);
		for (const section of sidebarFocus.sections) expect(section.href.startsWith(base)).toBe(true);
		expect(sidebarFocus.sections.find((s) => s.label === 'Messages')?.unread).toBe(true);
	});

	it('has a route behind every section', async () => {
		const { sidebarFocus } = await run();
		const prefix = `/tenants/${TENANT}/operate/clients/${CLIENT}`;
		for (const { href } of sidebarFocus.sections) {
			const suffix = href.slice(prefix.length);
			const dir = `src/routes/tenants/[uid]/operate/clients/[clientUid]${suffix}`;
			expect(existsSync(`${dir}/+page.svelte`), href).toBe(true);
		}
	});
});
