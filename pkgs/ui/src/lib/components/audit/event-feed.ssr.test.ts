/**
 * SSR pins for the compact EventFeed: it humanises each event through the SAME vocabulary as the
 * master-detail EventStream (an actor-led sentence), collapses a burst into a count, attributes a
 * proxied row to "Graphene support" in firm posture, and shows the empty text when there is nothing.
 */
import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import EventFeed from './event-feed.svelte';
import type { ActionEvent } from './describe.js';

let seq = 50;
function ev(over: Partial<ActionEvent> = {}): ActionEvent {
	return {
		id: seq--,
		event_type: 'document.viewed',
		created_at: '2026-08-03T09:00:00Z',
		method: 'GET',
		route: '/documents/{uid}/content',
		status: 200,
		path_params: { uid: 'doc_a' },
		metadata: {},
		actor_id: 'u-1',
		actor_sub: 'wos_alice',
		actor_email: 'alice@firm.test',
		actor_name: 'Alice Adviser',
		tenant_id: 1,
		tenant_uid: 'ten_a',
		tenant_name: 'Firm A',
		target_tenant_id: null,
		target_tenant_uid: null,
		target_tenant_name: null,
		...over
	};
}

describe('EventFeed (SSR)', () => {
	it('keeps the timestamp on the actor line, however long the description', () => {
		// The stamp used to be the last item of the WRAPPING sentence, held right with `ms-auto`. That
		// put it on the actor's line only while the sentence was short enough: a long document name
		// wrapped it onto a third line, right-aligned under the description, so rows were
		// inconsistently two and three lines tall and the times formed no column to scan down.
		//
		// It is now its own trailing column of the row, so this pins the mechanism rather than the
		// pixels: no `ms-auto` on the stamp, and its line box matched to the avatar's so the row's
		// `items-start` aligns the two without a guessed margin.
		const { body } = render(EventFeed, {
			props: {
				events: [
					ev({
						path_params: { uid: 'meridian-wealth-consolidated-statement-2026-Q2-final.pdf' }
					})
				]
			}
		});
		const stamp =
			/<span[^>]*class="([^"]*tabular-nums[^"]*)"[^>]*title="2026-08-03T09:00:00Z"/.exec(body);
		expect(stamp, 'the stamp span should carry the raw timestamp as its title').not.toBeNull();
		expect(stamp?.[1]).not.toContain('ms-auto');
		expect(stamp?.[1]).toContain('leading-5');
	});

	it('renders the actor and a humanised action per event', () => {
		const { body } = render(EventFeed, { props: { events: [ev()] } });
		expect(body).toContain('Alice Adviser');
		expect(body.toLowerCase()).toContain('viewed');
		// The compact glance is humanised only — the raw `action.*` event type is engineer jargon and
		// is deliberately NOT shown (it lives on the full /events surface).
		expect(body).not.toContain('document.viewed');
	});

	it('collapses a burst into a single counted row', () => {
		const { body } = render(EventFeed, {
			props: {
				events: [
					ev({ id: 3, created_at: '2026-08-03T09:20:00Z' }),
					ev({ id: 2, created_at: '2026-08-03T09:10:00Z' }),
					ev({ id: 1, created_at: '2026-08-03T09:00:00Z' })
				]
			}
		});
		expect(body).toContain('(3)');
	});

	it('shows the empty text when there are no events', () => {
		const { body } = render(EventFeed, {
			props: { events: [], emptyText: 'No document activity yet.' }
		});
		expect(body).toContain('No document activity yet.');
	});

	it('renders the Event-type + Actor filter bar when filterable and there is a choice', () => {
		const { body } = render(EventFeed, {
			props: {
				filterable: true,
				events: [
					ev({ id: 30, event_type: 'document.sent', actor_name: 'Ada Adviser' }),
					ev({ id: 29, event_type: 'document.viewed', actor_name: 'Bo Adviser' })
				]
			}
		});
		// Both FilterSelects present (their "all" trigger text), the app-standard control.
		expect(body).toContain('All events');
		expect(body).toContain('All actors');
	});

	it('shows no filter bar by default (opt-in), nor when there is nothing to sift', () => {
		// Off by default — the narrowly scoped feeds (one client, one document) stay filter-free.
		const off = render(EventFeed, { props: { events: [ev(), ev({ id: 40 })] } });
		expect(off.body).not.toContain('All events');
		expect(off.body).not.toContain('All actors');
		// Filterable but a single actor AND a single type: nothing to choose, so no bar.
		const one = render(EventFeed, {
			props: {
				filterable: true,
				events: [ev({ id: 41 }), ev({ id: 42 })] // same type + same actor
			}
		});
		expect(one.body).not.toContain('All events');
		expect(one.body).not.toContain('All actors');
	});

	it('attributes a proxied row to Graphene support in firm posture', () => {
		const { body } = render(EventFeed, {
			props: {
				attribution: 'firm',
				events: [
					ev({
						event_type: 'document.sent',
						target_tenant_id: 9,
						target_tenant_uid: 'ten_b',
						target_tenant_name: 'Firm B'
					})
				]
			}
		});
		expect(body).toContain('Graphene support');
	});
});
