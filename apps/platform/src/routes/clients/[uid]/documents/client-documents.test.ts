/**
 * The client-record "Documents" tab loader. Pins the re-sourced recent-activity fetch: this client's
 * DOCUMENT events off the action stream (`/firm/audit/actions?contact=<uid>&document_family=true`),
 * the `{items}` envelope unwrap into `docsData.events`, and that a failed activity fetch degrades to
 * an empty rail rather than a page error — the events glance is `activity:read`-gated, distinct from
 * the `documents:read` this tab needs, so a user without it must still see their documents. Without
 * activity:read the fetch is SKIPPED (not fired-then-403'd) and the rail hidden (`canReadActivity`).
 *
 * It also pins an absence: the archived slice is no longer fetched. The tab's Live/Archived switch is
 * gone (archiving reads as DELETING, and a client's record is not where a firm browses its
 * deletions), so that round trip had no consumer left.
 */
import { describe, expect, it, vi } from 'vitest';
import { load } from './+page.server';

const json = (data: unknown, status = 200): Response =>
	new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });

type Handler = (url: string) => Response;

function harness(handler: Handler) {
	const calls: string[] = [];
	const fetch = vi.fn(async (input: unknown) => {
		const url = String(input);
		calls.push(url);
		return handler(url);
	});
	return { fetch, calls };
}

function loadEvent(
	fetch: ReturnType<typeof harness>['fetch'],
	permissions: string[] = ['documents:read', 'activity:read']
) {
	return {
		params: { uid: 'con_9b2e' },
		locals: { user: { id: 'u1' }, permissions, role: null, accessToken: 'tok' },
		fetch,
		parent: async () => ({
			detail: { contact: { id: 8, email: 'max@firm.test' } },
			canReadDocuments: true,
			clientName: 'Max Oakden'
		}),
		depends: () => {}
	} as never;
}

type LoadData = Exclude<Awaited<ReturnType<typeof load>>, void>;
const run = async (e: unknown) => {
	const loaded = (await load(e as Parameters<typeof load>[0])) as LoadData;
	return { ...loaded, ...(await loaded.library) };
};

describe('client documents loader — recent activity', () => {
	it("fetches this client's DOCUMENT events off the action stream and unwraps {items}", async () => {
		const ev = {
			id: 1,
			event_type: 'document.sent',
			created_at: '2026-08-11T09:00:00Z',
			path_params: { uid: 'doc_1' },
			metadata: {},
			actor_name: 'Craig Currie'
		};
		const { fetch, calls } = harness((url) => {
			if (url.includes('/firm/audit/actions')) return json({ items: [ev] });
			return json([]); // docs / files / archived / library / types / forms
		});
		const data = await run(loadEvent(fetch));
		const audit = calls.find((u) => u.includes('/firm/audit/actions'));
		expect(audit).toBeDefined();
		// Scoped to THIS client and to the document family — not the whole firm stream.
		// The client's UID: `?contact=` resolves a uid, and the serial (which `?contact_id=` on the
		// documents fetch beside it legitimately takes) matches none — narrowing nothing, so this
		// rail showed the whole firm's document activity under one client's name.
		expect(audit).toContain('contact=con_9b2e');
		expect(audit).toContain('document_family=true');
		expect(data.docsData.events).toHaveLength(1);
		expect(data.docsData.events[0].id).toBe(1);
		expect(data.docsData.canReadActivity).toBe(true);
	});

	it('skips the fetch and hides the rail without activity:read', async () => {
		const { fetch, calls } = harness(() => json([]));
		const data = await run(loadEvent(fetch, ['documents:read']));
		// No action-stream call at all — not a fired-then-403'd fetch.
		expect(calls.find((u) => u.includes('/firm/audit/actions'))).toBeUndefined();
		expect(data.docsData.events).toEqual([]);
		expect(data.docsData.canReadActivity).toBe(false);
		expect(data.loadError).toBe(false);
	});

	it('does NOT ask for this client’s archived documents any more', async () => {
		const { fetch, calls } = harness(() => json([]));
		const data = await run(loadEvent(fetch));
		expect(calls.some((u) => u.includes('status=archived'))).toBe(false);
		expect(data.docsData).not.toHaveProperty('archivedDocuments');
		// …and the live slice is still both halves of the record: documents plus supporting files.
		expect(calls.some((u) => u.includes('/documents?contact_id=8'))).toBe(true);
		expect(calls.some((u) => u.includes('/files?contact_id=8'))).toBe(true);
	});

	it('degrades a failed activity fetch to an empty rail, not a page error', async () => {
		const { fetch } = harness((url) => {
			if (url.includes('/firm/audit/actions')) return json({ detail: 'nope' }, 403);
			return json([]);
		});
		const data = await run(loadEvent(fetch));
		expect(data.docsData.events).toEqual([]);
		// The rail's activity:read gate is independent of the tab's documents:read — a 403 there is not
		// a page failure (loadError tracks only the documents + files fetches).
		expect(data.loadError).toBe(false);
	});
});
