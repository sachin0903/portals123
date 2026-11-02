/**
 * The team member page: name, job title, and whether they may advise.
 *
 * The listing used to own this as a drawer. Main's Team UI is a person record at `/team/[uid]`,
 * so the save lives here — without it, assigning a book 409s because nobody is `is_adviser`.
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it, vi } from 'vitest';
import { actions } from './+page.server';

const PROFILE = { user: { id: 'user_1', uid: 'memberuid123', types: ['staff'] } };

function manageLocals() {
	return {
		user: { id: 'user_1', email: 'adviser@firm.test' },
		accessToken: 'token',
		permissions: ['members:write'],
		role: 'adviser'
	};
}

function fetchStub() {
	return vi.fn<(url: string | URL, init?: RequestInit) => Promise<Response>>(async (url) => {
		if (String(url).includes('/members/by-uid/'))
			return new Response(JSON.stringify(PROFILE), { status: 200 });
		return new Response(JSON.stringify({}), { status: 200 });
	});
}

describe('the staff-profile save', () => {
	/**
	 * The rule the catalogue's model form learned the hard way: a form that posts its whole shape
	 * clears every control it did not render. Saving a model's NAME wiped the VAT flag the platform
	 * had set, because the action sent `vatable` with no control behind it.
	 *
	 * So the test is on what the BODY carries, not on the response — an absent key must not appear
	 * at all, and an EMPTY box must appear as `null`, because "nobody touched this" and "clear it"
	 * are different intentions.
	 */
	function editEvent(fields: Record<string, string>) {
		const fetch = fetchStub();
		return {
			fetch,
			event: {
				request: new Request('http://portal.test/team/memberuid123?/updateUser', {
					method: 'POST',
					body: new URLSearchParams(fields)
				}),
				params: { uid: 'memberuid123' },
				locals: manageLocals()
			}
		};
	}

	type EditArg = Parameters<typeof actions.updateUser>[0];

	async function bodyOf(fields: Record<string, string>) {
		const { fetch, event } = editEvent(fields);
		await actions.updateUser({ ...event, fetch } as unknown as EditArg);
		const put = fetch.mock.calls.find(([, init]) => init?.method === 'PUT');
		return JSON.parse(String(put?.[1]?.body ?? '{}'));
	}

	it('sends only the fields the form actually carried', async () => {
		const body = await bodyOf({ job_title: 'Chartered Financial Planner' });
		expect(body.job_title).toBe('Chartered Financial Planner');
		expect('bio' in body).toBe(false);
		expect('fca_reference' in body).toBe(false);
		expect('type' in body).toBe(false);
	});

	it('sends an emptied box as null, so a blank CLEARS rather than storing ""', async () => {
		const body = await bodyOf({ job_title: '   ' });
		expect(body.job_title).toBeNull();
	});

	it('does not send is_adviser, so a name save cannot clear the adviser flag', async () => {
		const body = await bodyOf({ first_name: 'Ada', is_adviser: 'on', fca_reference: 'ABC123' });
		expect(body.first_name).toBe('Ada');
		expect('is_adviser' in body).toBe(false);
		expect('fca_reference' in body).toBe(false);
	});

	it('addresses the person from the route, not a posted user id', async () => {
		const { fetch, event } = editEvent({ job_title: 'Partner' });
		await actions.updateUser({ ...event, fetch } as unknown as EditArg);
		const put = fetch.mock.calls.find(([, init]) => init?.method === 'PUT');
		expect(String(put?.[0])).toContain('/firm/members/user_1');
		expect(String(put?.[0])).not.toContain('memberuid123');
	});
});

describe('the adviser section', () => {
	type AdviserArg = Parameters<typeof actions.setAdviser>[0];

	async function adviserBody(fields: Record<string, string>) {
		const fetch = vi.fn<(url: string | URL, init?: RequestInit) => Promise<Response>>(
			async (url) => {
				if (String(url).includes('/members/by-uid/'))
					return new Response(JSON.stringify(PROFILE), { status: 200 });
				return new Response(JSON.stringify({}), { status: 200 });
			}
		);
		await actions.setAdviser({
			request: new Request('http://portal.test/team/memberuid123?/setAdviser', {
				method: 'POST',
				body: new URLSearchParams(fields)
			}),
			params: { uid: 'memberuid123' },
			locals: manageLocals(),
			fetch
		} as unknown as AdviserArg);
		const put = fetch.mock.calls.find(([, init]) => init?.method === 'PUT');
		return JSON.parse(String(put?.[1]?.body ?? '{}'));
	}

	it('sends the switch on its own, and does not clear the FCA reference', async () => {
		const off = await adviserBody({ is_adviser: '' });
		expect(off.is_adviser).toBe(false);
		expect('fca_reference' in off).toBe(false);
		expect('seccl_node' in off).toBe(false);
		expect('sei_dsr' in off).toBe(false);

		const on = await adviserBody({ is_adviser: 'on' });
		expect(on.is_adviser).toBe(true);
		expect('fca_reference' in on).toBe(false);
		expect('seccl_node' in on).toBe(false);
	});

	it('sends the chosen node and DSR, and a blank clears them', async () => {
		const body = await adviserBody({ is_adviser: 'on', seccl_node: '21', sei_dsr: '59282' });
		expect(body.seccl_node).toBe('21');
		expect(body.sei_dsr).toBe('59282');
		const cleared = await adviserBody({ is_adviser: 'on', seccl_node: '', sei_dsr: '  ' });
		expect(cleared.seccl_node).toBeNull();
		expect(cleared.sei_dsr).toBeNull();
	});

	it('sends an emptied FCA box as null', async () => {
		const body = await adviserBody({ is_adviser: 'on', fca_reference: '   ' });
		expect(body.is_adviser).toBe(true);
		expect(body.fca_reference).toBeNull();
	});
});

describe('the custodian mapping', () => {
	type RefArg = Parameters<typeof actions.setProviderRef>[0];

	function refEvent(fields: Record<string, string>) {
		const fetch = fetchStub();
		return {
			fetch,
			event: {
				request: new Request('http://portal.test/team/memberuid123?/setProviderRef', {
					method: 'POST',
					body: new URLSearchParams(fields)
				}),
				params: { uid: 'memberuid123' },
				locals: manageLocals()
			}
		};
	}

	it('posts the chosen id to that provider’s own route', async () => {
		const { fetch, event } = refEvent({
			provider: 'sei',
			external_id: ' 57817496 '
		});
		await actions.setProviderRef({ ...event, fetch } as unknown as RefArg);
		const put = fetch.mock.calls.find(([, init]) => init?.method === 'PUT');
		expect(String(put?.[0])).toContain('/firm/members/user_1/provider-ref/sei');
		expect(JSON.parse(String(put?.[1]?.body))).toEqual({ external_id: '57817496' });
	});

	it('sends a blank id through, because that is how a mapping is cleared', async () => {
		const { fetch, event } = refEvent({ provider: 'sei', external_id: '' });
		await actions.setProviderRef({ ...event, fetch } as unknown as RefArg);
		const put = fetch.mock.calls.find(([, init]) => init?.method === 'PUT');
		expect(JSON.parse(String(put?.[1]?.body))).toEqual({ external_id: '' });
	});

	it('refuses without a provider rather than guessing one', async () => {
		const { fetch, event } = refEvent({ external_id: '1' });
		const out = await actions.setProviderRef({ ...event, fetch } as unknown as RefArg);
		expect(fetch).not.toHaveBeenCalled();
		expect((out as { status?: number }).status).toBe(400);
	});
});

describe('the staff-profile form', () => {
	it('keeps adviser off the name form, and saves the switch on its own', () => {
		const source = readFileSync(join(import.meta.dirname, '+page.svelte'), 'utf8');
		expect(source).toContain('action="?/updateUser"');
		expect(source).toContain('action="?/setAdviser"');
		expect(source).toContain('title="Adviser"');
		expect(source).toContain('aria-label="Adviser"');
		expect(source).toContain('name="fca_reference"');
		expect(source).toContain('name="seccl_node"');
		expect(source).toContain('name="sei_dsr"');
		const switchForm = source.slice(source.indexOf('bind:this={adviserForm}'));
		expect(switchForm).not.toContain('name="seccl_node"');
		expect(switchForm).not.toContain('name="sei_dsr"');
		// The switch asks first only when this person is a client's ADVISER somewhere: turning it
		// off moves those rows to Support, and the dialog says so.
		expect(source).toContain("staff_access.some((c) => c.role === 'adviser')");
		expect(source).toContain("stay on each client's team as Support");
		expect(source).not.toContain('Those assignments stay');
		const nameForm = source.slice(
			source.indexOf('action="?/updateUser"'),
			source.indexOf('title="Adviser"')
		);
		expect(nameForm).not.toContain('name="is_adviser"');
		expect(nameForm).not.toContain('name="fca_reference"');
		expect(source).not.toContain('May advise clients');
		expect(source).not.toContain('name="qualifications"');
		expect(source).not.toContain('name="bio"');
		expect(source).not.toContain('name="photo_url"');
		expect(source).not.toContain('name="direct_phone"');
		expect(source).not.toContain('type_staff');
		expect(source).not.toContain('Also a client of the firm');
	});
});
