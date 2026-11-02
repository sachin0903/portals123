/**
 * The notification-configuration loader (`GET /firm/event-registry`), a page under Event logs.
 *
 * This used to be the firm's action-event stream; that lives at `/activity` now. The registry
 * returns EVERY declared event with a state each, and the loader passes the rows straight through —
 * it no longer unpicks a catalogue, because most events have no catalogue trigger at all.
 */
import { describe, expect, it, vi } from 'vitest';
import { load } from './+page.server';

type LoadEvent = Parameters<typeof load>[0];

/** An assignable event: a catalogue trigger covers it, so rules can be authored. */
const ASSIGNABLE = {
	event_type: 'document.sent',
	resource: 'document',
	state: 'assignable',
	unavailable_reason: null,
	subject_kinds: ['contact'],
	trigger: {
		key: 'document.shared',
		label: 'A document was shared',
		description: 'A client was sent a document.',
		category: 'documents',
		event_types: ['document.sent'],
		subject_kind: 'contact',
		link_targets: [],
		params: [],
		audiences: []
	},
	rules: [{ uid: 'ruleuid12ab', title: 'Tell the adviser' }],
	automation_count: 2,
	email_count_30d: 4
};

/** A blocked event: it records nobody to notify, so it carries a reason instead of a trigger. */
const BLOCKED = {
	event_type: 'aml.sync_ran',
	resource: 'aml',
	state: 'blocked',
	unavailable_reason: 'This event records no client, account or task.',
	subject_kinds: [],
	trigger: null,
	rules: [],
	automation_count: 0,
	email_count_30d: 0
};

const SUMMARY = { total: 2, assignable: 1, ready: 0, blocked: 1 };

const json = (body: unknown, status = 200) =>
	new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

function event(over: { permissions?: string[]; fetch?: typeof globalThis.fetch } = {}) {
	const fetch =
		over.fetch ??
		vi.fn(async (url: string | URL) => {
			const href = String(url);
			if (href.includes('/firm/event-registry'))
				return json({ items: [ASSIGNABLE, BLOCKED], summary: SUMMARY });
			if (href.includes('/firm/notification-email-templates')) return json([]);
			return json({}, 404);
		});
	return {
		url: new URL('http://portal.test/activity/notifications'),
		locals: {
			user: { id: 'user_1' },
			accessToken: 'token',
			permissions: over.permissions ?? ['notifications:read'],
			role: 'adviser'
		},
		fetch
	} as unknown as LoadEvent & { fetch: ReturnType<typeof vi.fn> };
}

const run = async (e: LoadEvent) =>
	(await load(e)) as Exclude<Awaited<ReturnType<typeof load>>, void>;

describe('the notification-config loader gate', () => {
	it('bounces an unauthenticated caller to /login', async () => {
		const e = event();
		(e.locals as { user: unknown }).user = null;
		await expect(run(e)).rejects.toMatchObject({ status: 303, location: '/login' });
	});

	it('refuses a caller without notifications:read', async () => {
		await expect(run(event({ permissions: [] }))).rejects.toMatchObject({ status: 403 });
	});

	it('accepts notifications:read', async () => {
		const data = await run(event());
		expect(data.events).toHaveLength(2);
	});
});

describe('the loader reads the registry, not the action stream', () => {
	it('asks the registry and the email-template list, once each', async () => {
		const e = event();
		await run(e);
		const urls = e.fetch.mock.calls.map((c) => String(c[0]));
		expect(urls.some((u) => u.includes('/firm/event-registry'))).toBe(true);
		expect(urls.some((u) => u.includes('/firm/notification-email-templates'))).toBe(true);
		expect(urls.some((u) => u.includes('/firm/audit/actions'))).toBe(false);
		expect(e.fetch).toHaveBeenCalledTimes(2);
	});

	it('passes every event through, assignable and not alike', async () => {
		// Listing the unassignable ones is the point: "this happens and cannot notify anyone,
		// because…" is an answer, where a missing row is not.
		const data = await run(event());
		expect(data.events.map((e: { event_type: string }) => e.event_type)).toEqual([
			'document.sent',
			'aml.sync_ran'
		]);
		expect(data.events[1].state).toBe('blocked');
		expect(data.events[1].unavailable_reason).toContain('no client, account or task');
	});

	it('gathers the rule list off the rows that carry one', async () => {
		// The editor takes a flat rule list; only an assignable row has rules to contribute.
		const data = await run(event());
		expect(data.rules).toEqual([{ uid: 'ruleuid12ab', title: 'Tell the adviser' }]);
	});

	it('carries the endpoint own tally for the page subtitle', async () => {
		const data = await run(event());
		expect(data.summary).toEqual(SUMMARY);
	});

	it('degrades to empty lists when the registry refuses, rather than throwing', async () => {
		const fetch = vi.fn(async () => json({ detail: 'nope' }, 500));
		const data = await run(event({ fetch }));
		expect(data.events).toEqual([]);
		expect(data.rules).toEqual([]);
		expect(data.summary).toBeNull();
	});
});
