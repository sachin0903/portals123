/**
 * The client-record layout loads the shell and NOTHING ELSE: it is the one await every section of
 * the record pays before its first byte. Overview/Details fetch the fat record and the client-book
 * pin inside their streamed record; the unread dot comes on the shell, so this load never calls
 * `/messaging/unread`.
 */
import { describe, expect, it } from 'vitest';
import { load } from './+layout.server';

const SHELL = {
	contact: {
		id: 42,
		uid: 'con_9b2e',
		name: 'Ada Lovelace',
		first_name: 'Ada',
		surname: 'Lovelace',
		email: 'ada@example.test',
		deleted_at: null,
		is_vulnerable: false
	},
	accounts: [
		{ id: 7, uid: 'acc_7', name: 'ISA', value: 1000, product_type: 'isa', is_pension: false }
	],
	advisers: [],
	users: [],
	kyc: [{ kind: 'identity', status: 'passed' }],
	chats_unread: true
};

const locals = {
	user: { id: 'u_1' },
	accessToken: 'tok',
	permissions: ['messages:read', 'contacts:write'],
	role: 'adviser'
} as unknown as App.Locals;

function json(body: unknown, status = 200) {
	return new Response(JSON.stringify(body), {
		status,
		headers: { 'content-type': 'application/json' }
	});
}

describe('the client-record layout', () => {
	it('loads the shell alone — not the client book, the fat record or unread', async () => {
		const urls: string[] = [];
		const fetch = (async (url: string) => {
			urls.push(String(url));
			if (String(url).includes('/crm/contacts/con_9b2e/shell')) return json(SHELL);
			throw new Error(`unexpected fetch: ${url}`);
		}) as unknown as typeof globalThis.fetch;

		const result = (await load({
			locals,
			params: { uid: 'con_9b2e' },
			fetch
		} as never)) as {
			detail: { contact: { id: number; uid: string } };
			canManage: boolean;
			canReadMessages: boolean;
			sidebarFocus: {
				sections?: { label: string; unread?: boolean }[];
				liveUnreadContactId?: string;
			};
		};

		// ONE call. The client-book pin (`/crm/classifications` + `/classification`) used to follow
		// the shell as a second awaited stretch; it belongs to the sections that draw it now.
		expect(urls).toEqual([expect.stringContaining('/crm/contacts/con_9b2e/shell')]);
		expect(urls.some((u) => /\/crm\/contacts\/con_9b2e$/.test(u))).toBe(false);
		expect(urls.some((u) => u.includes('/messaging/unread'))).toBe(false);
		expect(result.detail.contact.id).toBe(42);
		expect(result.detail.contact.uid).toBe('con_9b2e');
		expect(result.canManage).toBe(true);
		expect(result.canReadMessages).toBe(true);
		expect(result.sidebarFocus.sections?.find((s) => s.label === 'Messages')?.unread).toBe(true);
		expect(result.sidebarFocus.liveUnreadContactId).toBe('42');
	});

	it('hides the unread flag when the caller cannot read messages', async () => {
		const fetch = (async (url: string) => {
			if (String(url).includes('/shell')) return json(SHELL);
			throw new Error(`unexpected fetch: ${url}`);
		}) as unknown as typeof globalThis.fetch;

		const result = (await load({
			locals: { ...locals, permissions: [] },
			params: { uid: 'con_9b2e' },
			fetch
		} as never)) as {
			canReadMessages: boolean;
			sidebarFocus: { sections?: { label: string }[] };
		};

		expect(result.canReadMessages).toBe(false);
		expect(result.sidebarFocus.sections?.some((s) => s.label === 'Messages')).toBe(false);
	});
});
