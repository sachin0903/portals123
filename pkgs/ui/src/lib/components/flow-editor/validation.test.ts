import { describe, it, expect, vi, afterEach } from 'vitest';
import {
	createValidationRunner,
	groupIssues,
	nodeHasIssues,
	validateDefinition,
	VALIDATE_ENDPOINT,
	type FlowIssue
} from './validation';

const issue = (node_id: string | null, message: string): FlowIssue => ({ node_id, message });

describe('grouping publish issues', () => {
	it('files each issue against its node', () => {
		const grouped = groupIssues(
			[issue('a', 'no otherwise branch'), issue('b', 'no deadline')],
			['a', 'b']
		);
		expect(grouped.byNode).toEqual({ a: ['no otherwise branch'], b: ['no deadline'] });
		expect(grouped.general).toEqual([]);
		expect(grouped.all).toHaveLength(2);
	});

	it('keeps several issues on one node in the order reported', () => {
		const grouped = groupIssues([issue('a', 'first'), issue('a', 'second')], ['a']);
		expect(grouped.byNode.a).toEqual(['first', 'second']);
	});

	it('puts a flow-wide issue in general', () => {
		const grouped = groupIssues([issue(null, 'this flow has no first step')], ['a']);
		expect(grouped.general).toEqual(['this flow has no first step']);
	});

	it('surfaces an issue naming a deleted node rather than dropping it', () => {
		const grouped = groupIssues([issue('gone', 'leads nowhere')], ['a']);
		expect(grouped.byNode).toEqual({});
		expect(grouped.general).toEqual(['leads nowhere']);
	});

	it('files everything by id when the caller does not say which nodes exist', () => {
		const grouped = groupIssues([issue('anything', 'x')]);
		expect(grouped.byNode).toEqual({ anything: ['x'] });
	});

	it('ignores an empty message rather than badging a node with a blank', () => {
		const grouped = groupIssues([issue('a', '')], ['a']);
		expect(grouped.all).toEqual([]);
		expect(nodeHasIssues(grouped, 'a')).toBe(false);
	});

	it('reads a node with no issues as clean', () => {
		expect(nodeHasIssues(groupIssues([], ['a']), 'a')).toBe(false);
	});

	it('counts one problem once even when it badges several nodes', () => {
		// The engine files an orphan/loop sentence against every node involved so each one badges;
		// the "N problems" count must still say one problem.
		const loop = 'These steps lead back to each other in a loop: “A” → “B” → “A”.';
		const grouped = groupIssues([issue('a', loop), issue('b', loop)], ['a', 'b']);
		expect(grouped.all).toEqual([loop]);
		expect(grouped.byNode).toEqual({ a: [loop], b: [loop] });
	});
});

// ── The fetch half ──────────────────────────────────────────────────────────────────────────

const ok = (issues: FlowIssue[]) =>
	({ ok: true, json: async () => ({ issues }) }) as unknown as Response;
const failed = (status = 500) => ({ ok: false, status }) as unknown as Response;

describe('asking the engine', () => {
	afterEach(() => vi.useRealTimers());

	it('posts the draft and returns the issues', async () => {
		const fetch = vi.fn().mockResolvedValue(ok([issue('a', 'nope')]));
		const result = await validateDefinition('flw_1', { nodes: [] }, { fetch });
		expect(result).toEqual([{ node_id: 'a', message: 'nope' }]);
		const [url, init] = fetch.mock.calls[0];
		expect(url).toBe(VALIDATE_ENDPOINT);
		expect(JSON.parse(init.body)).toEqual({ flow: 'flw_1', definition: { nodes: [] } });
	});

	it('returns null — not an empty list — when it could not find out', async () => {
		// The distinction the badges depend on: [] means "this would publish" and clears the canvas.
		expect(
			await validateDefinition('flw_1', {}, { fetch: vi.fn().mockResolvedValue(failed()) })
		).toBe(null);
		expect(
			await validateDefinition(
				'flw_1',
				{},
				{ fetch: vi.fn().mockRejectedValue(new Error('offline')) }
			)
		).toBe(null);
	});
});

describe('the debounced runner', () => {
	afterEach(() => vi.useRealTimers());

	it('coalesces a burst of edits into one request, carrying the last draft', async () => {
		vi.useFakeTimers();
		const fetch = vi.fn().mockResolvedValue(ok([]));
		const seen: FlowIssue[][] = [];
		const runner = createValidationRunner('flw_1', (i) => seen.push(i), { fetch, delay: 50 });
		runner.request({ v: 1 });
		runner.request({ v: 2 });
		runner.request({ v: 3 });
		expect(fetch).not.toHaveBeenCalled();
		await vi.advanceTimersByTimeAsync(60);
		expect(fetch).toHaveBeenCalledTimes(1);
		expect(JSON.parse(fetch.mock.calls[0][1].body).definition).toEqual({ v: 3 });
		expect(seen).toEqual([[]]);
		runner.dispose();
	});

	it('ignores a slow reply that a newer run has already overtaken', async () => {
		// The bug this prevents: an author fixes a branch, the fast validation of the FIXED draft
		// lands, and then the earlier one arrives and re-badges a node that is now clean.
		const stale = issue('fork', 'no otherwise branch');
		let releaseFirst: (r: Response) => void = () => {};
		const fetch = vi
			.fn()
			.mockImplementationOnce(() => new Promise<Response>((r) => (releaseFirst = r)))
			.mockResolvedValueOnce(ok([]));
		const seen: FlowIssue[][] = [];
		const runner = createValidationRunner('flw_1', (i) => seen.push(i), { fetch, delay: 0 });
		const slow = runner.flush({ v: 1 });
		// The second run resolves first…
		await runner.flush({ v: 2 });
		expect(seen).toEqual([[]]);
		// …and the first one's answer is dropped rather than clobbering it.
		releaseFirst(ok([stale]));
		await slow;
		expect(seen).toEqual([[]]);
		runner.dispose();
	});

	it('leaves the last known badges alone when a check fails', async () => {
		const fetch = vi.fn().mockResolvedValue(failed());
		const seen: FlowIssue[][] = [];
		const runner = createValidationRunner('flw_1', (i) => seen.push(i), { fetch, delay: 0 });
		await runner.flush({});
		expect(seen).toEqual([]);
		runner.dispose();
	});

	it('does nothing after dispose', async () => {
		vi.useFakeTimers();
		const fetch = vi.fn().mockResolvedValue(ok([]));
		const runner = createValidationRunner('flw_1', () => {}, { fetch, delay: 50 });
		runner.request({});
		runner.dispose();
		await vi.advanceTimersByTimeAsync(100);
		expect(fetch).not.toHaveBeenCalled();
	});

	it('reports pending so the builder can show it is checking', async () => {
		const pending: boolean[] = [];
		const fetch = vi.fn().mockResolvedValue(ok([]));
		const runner = createValidationRunner('flw_1', () => {}, {
			fetch,
			delay: 0,
			onpending: (p) => pending.push(p)
		});
		await runner.flush({});
		expect(pending).toEqual([true, false]);
		runner.dispose();
	});
});
