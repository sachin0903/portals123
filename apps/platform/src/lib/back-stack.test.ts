/**
 * The back trail.
 *
 * The bug these pin: a back link was the route's declared PARENT, so a task opened from the
 * dashboard's action queue offered "Back to Tasks" — a page the user had never visited. The tests
 * below cover the trail itself and, specifically, the loop: visit a page you have already been on
 * and Back must skip past it rather than bounce you between the two.
 */

import { describe, expect, it } from 'vitest';
import { CAP, advance, pageKey, resolveBack, top, type BackEntry } from './back-stack';

const dashboard: BackEntry = { href: '/', label: 'Dashboard' };
const tasks: BackEntry = { href: '/tasks', label: 'Tasks' };
const task: BackEntry = { href: '/tasks/t1', label: 'Chase the LOA' };

describe('advance', () => {
	it('builds a trail as you go deeper', () => {
		let stack = advance([], null, '/');
		expect(stack).toEqual([]); // the page you are ON is never in the stack
		stack = advance(stack, dashboard, '/tasks/t1');
		expect(stack).toEqual([dashboard]);
		stack = advance(stack, task, '/clients/8');
		expect(stack).toEqual([dashboard, task]);
		expect(top(stack)).toEqual(task);
	});

	it('collapses a loop: x -> y -> z, then z -> y, and Back from y goes to x', () => {
		// The case the whole design turns on. Pushing z here would leave [x, y, z] and Back from y
		// would return to z, bouncing the user between two pages forever.
		const stack = advance([dashboard, tasks], task, '/tasks');
		expect(stack).toEqual([dashboard]);
		expect(top(stack)).toEqual(dashboard);
	});

	it('empties the stack when you arrive back at its bottom', () => {
		expect(advance([dashboard, tasks], task, '/')).toEqual([]);
		expect(top([])).toBeNull();
	});

	it('ignores a query-only change, because that is a filter and not a journey', () => {
		// Every listing pages and filters with goto('?' + qs), which fires the same navigation
		// lifecycle as a real page change. Recording those would make Back walk the user back through
		// their own filter history one click at a time.
		expect(advance([dashboard], { href: '/tasks?status=open', label: 'Tasks' }, '/tasks?page=2')) //
			.toEqual([dashboard]);
	});

	it('collapses a loop even when the query differs', () => {
		// Identity is the pathname, so returning to a listing with different filters is still a
		// return to that listing.
		const stack = advance([{ href: '/tasks?page=2', label: 'Tasks' }], task, '/tasks?status=open');
		expect(stack).toEqual([]);
	});

	it('stores the query but compares without it, so Back restores your filters', () => {
		// Both halves in one test: dropping the query from the STORED href would send the user back to
		// an unfiltered listing, and keeping it in the COMPARISON would re-break the case above.
		const filtered: BackEntry = { href: '/tasks?status=open&page=3', label: 'Tasks' };
		const stack = advance([], filtered, '/tasks/t1');
		expect(stack).toEqual([filtered]);
		expect(top(stack)?.href).toBe('/tasks?status=open&page=3');
		expect(pageKey(top(stack)!.href)).toBe('/tasks');
	});

	it('drops the oldest entry past the cap and keeps the newest', () => {
		let stack: BackEntry[] = [];
		for (let i = 0; i < CAP + 2; i++) {
			stack = advance(stack, { href: `/p${i}`, label: `P${i}` }, `/p${i + 1}`);
		}
		expect(stack).toHaveLength(CAP);
		expect(stack[0].href).toBe('/p2');
		expect(top(stack)?.href).toBe(`/p${CAP + 1}`);
	});

	it('records nothing when there is nothing to record', () => {
		// A page the breadcrumb cannot name (a 404, /notifications) is skipped rather than recorded as
		// "Untitled" — the hop is dropped, and Back still points at the page before it.
		expect(advance([dashboard], null, '/tasks/t1')).toEqual([dashboard]);
	});
});

describe('resolveBack', () => {
	const url = (href: string) => new URL(href, 'https://portal.example');

	it('prefers where you actually came from over what the link claimed', () => {
		expect(resolveBack(dashboard, url('/forms/f1?from=/templates&fromLabel=Templates'))) //
			.toEqual(dashboard);
	});

	it('falls back to the origin spelt into the link when there is no history', () => {
		expect(resolveBack(null, url('/forms/f1?from=/templates/t1&fromLabel=Suitability'))).toEqual({
			href: '/templates/t1',
			label: 'Suitability'
		});
	});

	it('names an unlabelled origin "previous" rather than leaving the control blank', () => {
		expect(resolveBack(null, url('/forms/f1?from=/templates'))).toEqual({
			href: '/templates',
			label: 'previous'
		});
	});

	it('refuses anything that is not a same-origin relative path', () => {
		// A crafted ?from must not turn a back link into an open redirect.
		for (const from of ['//evil.example', 'https://evil.example', 'http:/x', 'evil', '']) {
			expect(resolveBack(null, url(`/forms/f1?from=${encodeURIComponent(from)}`))).toBeNull();
		}
	});

	it('is null when there is neither history nor a declared origin', () => {
		expect(resolveBack(null, url('/tasks/t1'))).toBeNull();
	});
});
