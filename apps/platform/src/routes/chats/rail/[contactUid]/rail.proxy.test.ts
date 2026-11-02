/**
 * The inbox rail's own read — the one endpoint behind the client block, People and Requests.
 *
 * Worth driving directly, because every rule in it is invisible from either end. Each panel is
 * gated on its OWN permission and must be SKIPPED rather than refused (a 403 here would put an
 * error over a thread that loaded perfectly), the adviser's role has to come back as the
 * relationship rather than blank, and both reads must START TOGETHER — the caller hands over
 * both of the client's handles so the request listing never waits on the detail read.
 * A rule this route gets wrong reads as "this client has no advisers and owes nothing", which is a
 * sentence, not an error.
 */
import { describe, expect, it, vi } from 'vitest';
import { GET } from './+server';

type Handler = Parameters<typeof GET>[0];

const DETAIL = {
	// THE CONTACT IS NESTED, which is the whole point of the client-block case below: the person's
	// own columns live under `contact`, and reading them off the root silently yields `undefined`
	// for every one of them.
	contact: {
		id: 42,
		uid: 'seccl_AXF5AC13E',
		type: 'individual' as const,
		sub_type: null,
		service_type: 'advised',
		provider_status: null,
		deleted_at: null,
		// `name` is the ORGANISATION's and is null for a person — the display name is composed.
		name: null,
		title: 'Mrs',
		first_name: 'Rosemary',
		surname: 'Braun',
		email: 'rosemary@example.com',
		mobile: '+447700900123',
		dob: '1968-04-02',
		address: { line1: '12 Bell Street', line2: null, city: 'London', postcode: 'NW1 5BY' }
	},
	accounts: [
		{ uid: 'acc_1', name: 'Oakden SIPP', product_type: 'SIPP', value: 1000 },
		{ uid: 'acc_2', name: 'Oakden ISA', product_type: 'ISA', value: null }
	],
	advisers: [
		{ name: 'Craig Currie', role: 'paraplanner' },
		{ name: 'Mark Ross', role: 'adviser' }
	]
};
const REQUESTS = [{ uid: 'req_1', status: 'pending' }];

/** Runs the handler with the given permissions and reports what it answered and what it asked. */
async function railFor(
	permissions: string[],
	user: unknown = { id: 'u1' },
	query = '?contact_id=42'
) {
	const urls: string[] = [];
	const fetch = vi.fn(async (url: string) => {
		urls.push(url);
		if (url.includes('/crm/contacts/'))
			return new Response(JSON.stringify(DETAIL), { status: 200 });
		if (url.includes('/documents/requests'))
			return new Response(JSON.stringify(REQUESTS), { status: 200 });
		return new Response('{}', { status: 404 });
	});
	const res = await GET({
		locals: { user, accessToken: 'tok', permissions, role: 'adviser' },
		params: { contactUid: '7f3a2b1c9d4e' },
		url: new URL(`http://portal.test/chats/rail/7f3a2b1c9d4e${query}`),
		fetch
	} as unknown as Handler);
	return { body: await res.json(), urls };
}

describe('the inbox rail read', () => {
	it('answers the visible panels in one call', async () => {
		const { body, urls } = await railFor([
			'messages:read',
			'contacts:read',
			'documents:read',
			'tasks:read'
		]);
		expect(body.advisers).toHaveLength(2);
		expect(body.requests).toHaveLength(1);
		expect(body.tasks).toEqual([]);
		// Two handles for one client: the UID addresses the detail, the serial keys requests.
		expect(urls.some((u) => u.includes('/crm/contacts/7f3a2b1c9d4e'))).toBe(true);
		expect(urls.some((u) => u.includes('contact_id=42'))).toBe(true);
		expect(urls.some((u) => u.includes('/tasks'))).toBe(false);
	});

	it('names the client from the NESTED contact, not the root', async () => {
		// The bug this pins: the contact's columns live under `detail.contact`, and reading them off
		// the root made every field `undefined` — nothing threw, and the rail headed itself "Client"
		// (the last fallback in `clientName`) with no email, no phone and no reference.
		const { body } = await railFor(['messages:read', 'contacts:read']);
		// No reference and no status: the rail draws neither, so neither is computed or shipped.
		expect(body.client).toEqual({
			name: 'Mrs Rosemary Braun',
			// Carried for the avatar GLYPH — a person or a building — not to be printed as a row.
			kind: 'individual',
			email: 'rosemary@example.com',
			phone: '+447700900123',
			serviceType: 'Advisory',
			dob: '1968-04-02',
			// The postal line, country deliberately absent — the record resolves the ISO code and
			// owns that formatter; a summary beside a thread wants the address you would post to.
			address: '12 Bell Street, London, NW1 5BY'
		});
	});

	it('summarises the accounts off the same read', async () => {
		// The detail this endpoint already fetches for the advisers carries them, and the status
		// above was already counting them — so the band is a field copy, not a second request.
		const { body } = await railFor(['messages:read', 'contacts:read']);
		expect(body.accounts).toEqual([
			{ uid: 'acc_1', name: 'Oakden SIPP', productType: 'SIPP', value: 1000 },
			{ uid: 'acc_2', name: 'Oakden ISA', productType: 'ISA', value: null }
		]);
	});

	it('has no client block for a reader who may not read contacts', async () => {
		// The detail is the read that is skipped, so the block is absent rather than a card with
		// three blanks in it.
		const { body } = await railFor(['messages:read']);
		expect(body.client).toBeNull();
	});

	it('names each person by their role on the client, adviser first', async () => {
		// The roles arrive as keys from the closed list; the rail shows the words the record shows.
		const { body } = await railFor(['messages:read', 'contacts:read']);
		expect(body.advisers).toEqual([
			{ name: 'Mark Ross', role: 'Adviser' },
			{ name: 'Craig Currie', role: 'Paraplanner' }
		]);
	});

	it('skips a panel the reader has no permission for, rather than refusing the call', async () => {
		const { body, urls } = await railFor(['messages:read', 'contacts:read']);
		expect(body.advisers).toHaveLength(2);
		expect(body.requests).toEqual([]);
		expect(body.tasks).toEqual([]);
		expect(urls.some((u) => u.includes('/documents/requests'))).toBe(false);
		expect(urls.some((u) => u.includes('/tasks'))).toBe(false);
	});

	it('reads the listings even when the detail is not its to read', async () => {
		// The serial arrives from the caller, so the two listings no longer wait on — or depend on —
		// the heaviest read of the three. A reader without `contacts:read` still gets what they owe.
		const { body, urls } = await railFor(['messages:read', 'documents:read']);
		expect(body.advisers).toEqual([]);
		expect(body.requests).toHaveLength(1);
		expect(body.tasks).toEqual([]);
		expect(urls.some((u) => u.includes('/crm/contacts/'))).toBe(false);
	});

	it('skips a listing it was given no serial for, rather than guessing one', async () => {
		const { body, urls } = await railFor(
			['messages:read', 'contacts:read', 'documents:read', 'tasks:read'],
			{ id: 'u1' },
			''
		);
		expect(body.advisers).toHaveLength(2);
		expect(body.requests).toEqual([]);
		expect(body.tasks).toEqual([]);
		expect(urls.some((u) => u.includes('/documents/requests'))).toBe(false);
	});

	it('answers empty for a caller with no messages:read, and for no session at all', async () => {
		const withoutRead = await railFor(['contacts:read']);
		expect(withoutRead.body).toEqual({ advisers: [], requests: [], tasks: [] });
		expect(withoutRead.urls).toEqual([]);
		const signedOut = await railFor(['messages:read', 'contacts:read'], null);
		expect(signedOut.body).toEqual({ advisers: [], requests: [], tasks: [] });
		expect(signedOut.urls).toEqual([]);
	});

	it('leaves a panel empty when its own read fails', async () => {
		// This surface is the CONVERSATION: a side panel that could not be read must not become an
		// error over a thread that loaded fine.
		const fetch = vi.fn(async (url: string) =>
			url.includes('/crm/contacts/')
				? new Response(JSON.stringify(DETAIL), { status: 200 })
				: new Response('nope', { status: 500 })
		);
		const res = await GET({
			locals: {
				user: { id: 'u1' },
				accessToken: 'tok',
				permissions: ['messages:read', 'contacts:read', 'documents:read', 'tasks:read'],
				role: 'adviser'
			},
			params: { contactUid: '7f3a2b1c9d4e' },
			url: new URL('http://portal.test/chats/rail/7f3a2b1c9d4e?contact_id=42'),
			fetch
		} as unknown as Handler);
		const body = await res.json();
		expect(res.status).toBe(200);
		expect(body.advisers).toHaveLength(2);
		expect(body.requests).toEqual([]);
		expect(body.tasks).toEqual([]);
	});
});
