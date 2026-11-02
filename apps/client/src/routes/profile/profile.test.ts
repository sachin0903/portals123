/**
 * Resolving the signed-in client's OWN CRM record (`+page.server.ts`).
 *
 * This page renders a name, phone number and correspondence address under "your details", so
 * picking the wrong party is a privacy failure, not a cosmetic one. The `contact_ids` claim covers
 * every party a login is linked to through `crm.contact_users` — whatever the capacity — so a
 * client holding power of attorney for a relative legitimately sees two parties here.
 *
 * The discriminator is `is_self` on GET /crm/contacts/mine, which the API computes from the token
 * user (`auth.uid()`) and capacity 'self'. It is not the session email: a WorkOS impersonation
 * session keeps that user id while `user.email` can differ from the mirrored login address, and
 * matching on the address showed "we couldn't find your record" for a person whose book was
 * otherwise loading. A party they only act for is never a fallback.
 *
 * A staff view-as session is different on purpose. Its token user is the adviser, so `is_self` is
 * false for the client on screen, and the shell's stand-in identity is only that client's name.
 * The session names the client (`contactId`); that record is the page, and it is never a reason
 * for an ordinary login to accept some other party.
 */

import { describe, expect, it, vi } from 'vitest';
import { load } from './+page.server';

const LOGIN = 'jo.baxter@example.test';

type Row = Record<string, unknown>;

/** A party as the two endpoints return it: the list projection's fields, plus the address and the
 *  `crm.contact_users` links the detail projection adds. */
type Party = {
	id: number;
	/** What every `/crm/contacts/{contact_uid}` path takes. The serial is still on the row (the
	 *  `?contact_id=` filters take it), which is exactly why sending the wrong one type-checks. */
	uid: string;
	name: string | null;
	first_name: string | null;
	surname: string | null;
	email: string | null;
	mobile: string | null;
	phone: string | null;
	address: Record<string, string> | null;
	users: { user_id: string; capacity: string; name: string | null; email: string | null }[];
};

/** The relative the caller holds power of attorney for: listed first (surname order), and with no
 *  email of their own — exactly the shape that defeated the old email-match-or-first rule. */
const RELATIVE: Party = {
	id: 41,
	uid: 'con_41',
	name: 'Arthur Baxter',
	first_name: 'Arthur',
	surname: 'Baxter',
	email: null,
	mobile: '020 7000 0001',
	phone: '0113 000 0001',
	address: { line1: '2 Elm Row', city: 'Leeds', postcode: 'LS1 1AA' },
	users: [
		{ user_id: 'u-relative', capacity: 'self', name: 'Arthur Baxter', email: 'arthur@other.test' },
		{ user_id: 'u-me', capacity: 'power_of_attorney', name: 'Jo Baxter', email: LOGIN }
	]
};

/** The caller's own party. Their CRM email is a joint mailbox, so it does NOT match their login. */
const OWN: Party = {
	id: 77,
	uid: 'con_77',
	name: 'Jo Baxter',
	first_name: 'Jo',
	surname: 'Baxter',
	email: 'household@example.test',
	mobile: '07700 900123',
	phone: '0117 900 0000',
	address: { line1: '9 Beech Lane', city: 'Bristol', postcode: 'BS1 4ST' },
	users: [{ user_id: 'u-me', capacity: 'self', name: 'Jo Baxter', email: LOGIN }]
};

const listRow = (c: Party) => ({
	id: c.id,
	uid: c.uid,
	name: c.name,
	first_name: c.first_name,
	surname: c.surname,
	email: c.email,
	mobile: c.mobile
});

/** A fetch stub over the two endpoints the loader uses, recording the calls it makes.
 *
 *  `is_self` is what the API publishes from the token user. The stub derives it from a 'self'
 *  link whose `user_id` is the caller (`u-me`), which is the same fact — and deliberately not
 *  from either email. A detail registered as a status code (the lookup failed) cannot say, so
 *  pass `self` for that uid when the test still needs the loader to ask for it. */
function stubFetch(options: {
	list?: Row[] | number;
	details?: Record<string, Row | number>;
	self?: string[];
}): {
	fetch: typeof globalThis.fetch;
	calls: string[];
} {
	const calls: string[] = [];
	const respond = (body: Row | Row[] | number): Response =>
		typeof body === 'number'
			? ({ ok: false, status: body } as unknown as Response)
			: ({ ok: true, status: 200, json: async () => body } as unknown as Response);

	const fetch = vi.fn(async (input: RequestInfo | URL) => {
		const url = String(input);
		calls.push(url);
		if (url.includes('/crm/contacts/mine')) {
			if (typeof options.list === 'number') return respond(options.list);
			const contacts = (options.list ?? []).map((row) => {
				const detail = options.details?.[String(row.uid)];
				const users =
					detail !== undefined && typeof detail !== 'number'
						? ((detail.users as Row[] | undefined) ?? [])
						: [];
				const linked = users.some((u) => u.capacity === 'self' && u.user_id === 'u-me');
				return {
					id: row.id,
					uid: row.uid,
					name: row.name,
					is_self: linked || (options.self ?? []).includes(String(row.uid))
				};
			});
			return respond({ contacts });
		}
		// Any final segment, not just digits: a serial sent here must MISS, the way it misses
		// `crm.contacts.uid` in the real resolver, rather than quietly matching a fixture.
		const detail = /\/crm\/contacts\/([^/?]+)$/.exec(url);
		if (detail) {
			const found = options.details?.[detail[1]];
			if (found === undefined) return respond(404);
			return typeof found === 'number' ? respond(found) : respond({ contact: found, ...found });
		}
		return respond(404);
	});
	return { fetch: fetch as unknown as typeof globalThis.fetch, calls };
}

/** The detail projection: `{ contact, users, ... }`. */
const detailOf = (c: Party): Row => ({
	contact: {
		id: c.id,
		uid: c.uid,
		name: c.name,
		first_name: c.first_name,
		surname: c.surname,
		email: c.email,
		mobile: c.mobile,
		phone: c.phone,
		address: c.address
	},
	users: c.users
});

// The loader's signature is SvelteKit's ServerLoadEvent; the stub carries only what it reads.
type LoadArg = Parameters<typeof load>[0];

const run = (
	fetch: typeof globalThis.fetch,
	email: string | null = LOGIN,
	viewAsContactId?: number
) =>
	load({
		locals: {
			user: email === null ? { id: 'u-me' } : { id: 'u-me', email },
			accessToken: 'tok',
			viewAs: viewAsContactId == null ? null : { contactId: viewAsContactId }
		},
		fetch,
		url: new URL('http://localhost/profile')
	} as unknown as LoadArg) as Promise<{
		profile: {
			firstName: string | null;
			mobilePhone: string | null;
			phone: string | null;
		} | null;
		loadError: boolean;
		unresolved: boolean;
	}>;

describe("resolving the caller's own record", () => {
	it("never renders another party's details, even when no CRM email matches the login", async () => {
		const { fetch } = stubFetch({
			list: [listRow(RELATIVE), listRow(OWN)],
			details: { con_41: detailOf(RELATIVE), con_77: detailOf(OWN) }
		});
		const data = await run(fetch);
		expect(data.profile?.firstName).toBe('Jo');
		expect(data.profile?.mobilePhone).toBe('07700 900123');
		// The relative's numbers must never appear — the old `?? list.data[0]` returned exactly this,
		// and the other-numbers array is the same leak in a second column.
		expect(data.profile?.mobilePhone).not.toBe(RELATIVE.mobile);
		expect(data.profile?.phone).toBe(OWN.phone);
		expect(data.profile?.phone).not.toBe(RELATIVE.phone);
		expect(data.unresolved).toBe(false);
		expect(data.loadError).toBe(false);
	});

	it("loads only the caller's own record, never the other party in scope", async () => {
		const own = { ...OWN, email: LOGIN };
		const { fetch, calls } = stubFetch({
			list: [listRow(RELATIVE), listRow(own)],
			details: { con_41: detailOf(RELATIVE), con_77: detailOf(own) }
		});
		const data = await run(fetch);
		expect(data.profile?.firstName).toBe('Jo');
		expect(calls[0]).toMatch(/\/crm\/contacts\/mine$/);
		expect(calls).toEqual([
			expect.stringMatching(/\/crm\/contacts\/mine$/),
			expect.stringMatching(/\/crm\/contacts\/con_77$/)
		]);
	});

	it('says so honestly when no party is linked to this login as themselves', async () => {
		// Only the relative is in scope: the caller acts FOR them and has no record of their own.
		const { fetch } = stubFetch({
			list: [listRow(RELATIVE)],
			details: { con_41: detailOf(RELATIVE) }
		});
		const data = await run(fetch);
		expect(data.profile).toBeNull();
		expect(data.unresolved).toBe(true);
		expect(data.loadError).toBe(false);
	});

	it('rejects a capacity that is not self even when the emails line up', async () => {
		// A delegate link carrying the caller's own email is still not "this party is me".
		const delegated = {
			...RELATIVE,
			users: [{ user_id: 'u-me', capacity: 'delegate', name: 'Jo Baxter', email: LOGIN }]
		};
		const { fetch } = stubFetch({
			list: [listRow(delegated)],
			details: { con_41: detailOf(delegated) }
		});
		const data = await run(fetch);
		expect(data.profile).toBeNull();
		expect(data.unresolved).toBe(true);
	});

	it('reads no phone as null however the API spells it', async () => {
		for (const phone of [null, undefined]) {
			const party = { ...OWN, phone } as Party;
			const { fetch } = stubFetch({ list: [listRow(party)], details: { con_77: detailOf(party) } });
			const data = await run(fetch);
			expect(data.profile?.phone).toBeNull();
		}
	});

	it('resolves the caller when the session email does not match the mirrored login', async () => {
		// WorkOS impersonation keeps the impersonated user's id on the token and can present a
		// different address on the sealed session (the dashboard user's, or a name where an email
		// should be). The old email join failed closed here and the page showed that session
		// identity with no phone or address. The record is theirs because `is_self` says so.
		const drifted: Party = {
			...OWN,
			users: [{ user_id: 'u-me', capacity: 'self', name: 'Jo Baxter', email: 'jo.baxter@old.test' }]
		};
		const { fetch } = stubFetch({
			list: [listRow(RELATIVE), listRow(drifted)],
			details: { con_41: detailOf(RELATIVE), con_77: detailOf(drifted) }
		});
		const data = await run(fetch, 'ops@graphene.test');
		expect(data.profile?.firstName).toBe('Jo');
		expect(data.profile?.mobilePhone).toBe('07700 900123');
		expect(data.unresolved).toBe(false);
		expect(data.loadError).toBe(false);
		// And specifically NOT the relative, who does carry a 'self' link of their own — accepting
		// "the only self link in scope" would render their name, phone and address as the caller's.
		const relativeOnly = stubFetch({
			list: [listRow(RELATIVE)],
			details: { con_41: detailOf(RELATIVE) }
		});
		expect((await run(relativeOnly.fetch, 'ops@graphene.test')).profile).toBeNull();
	});

	it('loads the client a member of staff is viewing as, whose login this is not', async () => {
		// The token user is the adviser, so `is_self` is true for Jo (u-me) and false for Arthur.
		// The session is Arthur's portal. Accepting `is_self` here would show Jo's phone and
		// address on a screen captioned with Arthur's name; showing nobody would fall back to the
		// shell stand-in, which is only the client's name in both the first-name and email slots.
		const { fetch, calls } = stubFetch({
			list: [listRow(RELATIVE), listRow(OWN)],
			details: { con_41: detailOf(RELATIVE), con_77: detailOf(OWN) }
		});
		const data = await run(fetch, 'Craig Currie', RELATIVE.id);
		expect(data.profile?.firstName).toBe('Arthur');
		expect(data.profile?.mobilePhone).toBe(RELATIVE.mobile);
		expect(data.profile?.phone).toBe(RELATIVE.phone);
		expect(data.unresolved).toBe(false);
		expect(data.loadError).toBe(false);
		expect(calls).toEqual([
			expect.stringMatching(/\/crm\/contacts\/mine$/),
			expect.stringMatching(/\/crm\/contacts\/con_41$/)
		]);
	});

	it('does not show a different party when the view-as client is not in scope', async () => {
		const { fetch, calls } = stubFetch({
			list: [listRow(RELATIVE)],
			details: { con_41: detailOf(RELATIVE) }
		});
		const data = await run(fetch, LOGIN, 999);
		expect(data.profile).toBeNull();
		expect(data.unresolved).toBe(true);
		expect(calls.some((url) => url.endsWith('/crm/contacts/con_41'))).toBe(false);
	});

	it('still resolves when the session carries no email at all', async () => {
		const { fetch } = stubFetch({
			list: [listRow(RELATIVE), listRow(OWN)],
			details: { con_41: detailOf(RELATIVE), con_77: detailOf(OWN) }
		});
		const data = await run(fetch, '');
		expect(data.profile?.firstName).toBe('Jo');
		expect(data.unresolved).toBe(false);
	});

	it('is an empty-but-honest page when the firm holds no parties for this login', async () => {
		const { fetch } = stubFetch({ list: [] });
		const data = await run(fetch);
		expect(data.profile).toBeNull();
		expect(data.unresolved).toBe(true);
		expect(data.loadError).toBe(false);
	});
});

describe('load failures', () => {
	it("surfaces a retryable error when the caller's parties cannot be fetched", async () => {
		vi.spyOn(console, 'error').mockImplementation(() => {});
		const { fetch } = stubFetch({ list: 500 });
		const data = await run(fetch);
		expect(data).toEqual({ profile: null, loadError: true, unresolved: false });
	});

	it("surfaces a retryable error when the caller's own record cannot be fetched", async () => {
		vi.spyOn(console, 'error').mockImplementation(() => {});
		const { fetch } = stubFetch({
			list: [listRow(RELATIVE), listRow(OWN)],
			details: { con_41: detailOf(RELATIVE), con_77: 500 },
			self: ['con_77']
		});
		const data = await run(fetch);
		expect(data.profile).toBeNull();
		expect(data.loadError).toBe(true);
		// Not "we couldn't find your record" — we don't know that; the lookup broke.
		expect(data.unresolved).toBe(false);
	});

	it('does not ask for a party that is not the caller, even when that lookup would fail', async () => {
		vi.spyOn(console, 'error').mockImplementation(() => {});
		const { fetch, calls } = stubFetch({
			list: [listRow(RELATIVE), listRow(OWN)],
			details: { con_41: 500, con_77: detailOf(OWN) }
		});
		const data = await run(fetch);
		expect(data.profile?.firstName).toBe('Jo');
		expect(data.loadError).toBe(false);
		expect(calls.some((url) => url.endsWith('/crm/contacts/con_41'))).toBe(false);
	});
});
