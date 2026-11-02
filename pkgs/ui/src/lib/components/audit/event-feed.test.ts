/**
 * `groupEvents` collapses a burst of repeated events into one count-bearing row — the behaviour the
 * retired documents.audit_log feed had (`groupActivity`), now over the action-event stream. Pins the
 * key (type · actor · primary subject), the 30-min window chaining, that different subjects/actors do
 * NOT merge, and that the stream's newest-first order survives.
 */
import { describe, expect, it } from 'vitest';
import { groupEvents } from './event-feed.js';
import type { ActionEvent } from './describe.js';

let seq = 100;
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

describe('groupEvents', () => {
	it('collapses same type · actor · subject within the window into one counted row', () => {
		const groups = groupEvents([
			ev({ id: 3, created_at: '2026-08-03T09:20:00Z' }),
			ev({ id: 2, created_at: '2026-08-03T09:10:00Z' }),
			ev({ id: 1, created_at: '2026-08-03T09:00:00Z' })
		]);
		expect(groups).toHaveLength(1);
		expect(groups[0].count).toBe(3);
		expect(groups[0].event.id).toBe(3); // the newest is the representative
		expect(groups[0].members.map((m) => m.id)).toEqual([3, 2, 1]);
	});

	it('does NOT merge different documents', () => {
		const groups = groupEvents([
			ev({ id: 2, path_params: { uid: 'doc_a' } }),
			ev({ id: 1, path_params: { uid: 'doc_b' } })
		]);
		expect(groups).toHaveLength(2);
		expect(groups.every((g) => g.count === 1)).toBe(true);
	});

	it('does NOT merge different actors on the same document', () => {
		const groups = groupEvents([
			ev({ id: 2, actor_sub: 'wos_alice' }),
			ev({ id: 1, actor_sub: 'wos_bo' })
		]);
		expect(groups).toHaveLength(2);
	});

	it('does NOT merge across the 30-minute window', () => {
		const groups = groupEvents([
			ev({ id: 2, created_at: '2026-08-03T10:00:00Z' }),
			ev({ id: 1, created_at: '2026-08-03T09:00:00Z' }) // an hour earlier
		]);
		expect(groups).toHaveLength(2);
	});

	it('keys a shares- event separately from a documents- event on the same subject', () => {
		const groups = groupEvents([
			ev({ id: 2, event_type: 'document_share.created' }),
			ev({ id: 1, event_type: 'document.viewed' })
		]);
		expect(groups).toHaveLength(2);
	});

	it('collapses ONLY views — identical non-view events each stay their own row', () => {
		// Two identical sends in the window: a send is a distinct act, so both must show (never one
		// "Sent … (2)" line that hides a real delivery).
		const sends = groupEvents([
			ev({ id: 2, event_type: 'document.sent', created_at: '2026-08-03T09:10:00Z' }),
			ev({ id: 1, event_type: 'document.sent', created_at: '2026-08-03T09:00:00Z' })
		]);
		expect(sends).toHaveLength(2);
		expect(sends.every((g) => g.count === 1)).toBe(true);
		// …but two identical views in the window DO collapse.
		const views = groupEvents([
			ev({ id: 2, event_type: 'document.viewed', created_at: '2026-08-03T09:10:00Z' }),
			ev({ id: 1, event_type: 'document.viewed', created_at: '2026-08-03T09:00:00Z' })
		]);
		expect(views).toHaveLength(1);
		expect(views[0].count).toBe(2);
	});

	it('preserves newest-first order across groups', () => {
		const groups = groupEvents([
			ev({ id: 3, path_params: { uid: 'doc_a' } }),
			ev({ id: 2, path_params: { uid: 'doc_b' } }),
			ev({ id: 1, path_params: { uid: 'doc_a' }, created_at: '2026-08-03T09:05:00Z' })
		]);
		// doc_a's two events are within the window → one group led by id 3; doc_b is its own.
		expect(groups.map((g) => g.event.id)).toEqual([3, 2]);
		expect(groups[0].count).toBe(2);
	});

	it('returns [] for no events', () => {
		expect(groupEvents([])).toEqual([]);
	});

	it('falls back to the first entity when there is no uid/id path param', () => {
		const withEntity = (id: string): Partial<ActionEvent> => ({
			path_params: {},
			entities: [{ kind: 'contact', id, label: 'X' }]
		});
		const merged = groupEvents([
			ev({ id: 2, ...withEntity('55') }),
			ev({ id: 1, ...withEntity('55') })
		]);
		expect(merged).toHaveLength(1);
		const split = groupEvents([
			ev({ id: 2, ...withEntity('55') }),
			ev({ id: 1, ...withEntity('66') })
		]);
		expect(split).toHaveLength(2);
	});
});
