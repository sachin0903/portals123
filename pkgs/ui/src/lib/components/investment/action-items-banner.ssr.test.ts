import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import ActionItemsBanner from './action-items-banner.svelte';
import type { PortalActionItem } from './types.js';

/**
 * SSR pins for the banner action-items look. The behavioural contract is action-items-card's
 * (it imports that component's sortActionItems/dueText), so these tests focus on the parts
 * the banner must not get wrong on its own: the heading counting ALL items while `limit`
 * cuts the rows, urgency surviving the cut, and the hide-when-empty semantics.
 */

const count = (body: string, needle: string): number => body.split(needle).length - 1;

const items: PortalActionItem[] = [
	// Deliberately unsorted: the undated item first, the overdue one last.
	{ uid: 't-undated', title: 'Confirm your contact details', description: null, due_date: null },
	{
		uid: 't-future',
		title: 'Book your annual review',
		description: 'Pick a slot',
		due_date: '2099-01-01'
	},
	{
		uid: 't-overdue',
		title: 'Sign your client agreement',
		description: null,
		due_date: '2020-01-01'
	}
];

const html = (props: Record<string, unknown>): string =>
	render(ActionItemsBanner, { props: { items, ...props } }).body;

describe('ActionItemsBanner (SSR)', () => {
	it('the heading counts ALL items while limit cuts the rows', () => {
		const body = html({ limit: 1 });
		expect(body).toContain('3 things need your attention');
		expect(count(body, 'Done')).toBe(1);
	});

	it('the overdue item survives a limit cut (sorted before slicing), wearing the red due text', () => {
		const body = html({ limit: 1 });
		expect(body).toContain('Sign your client agreement');
		expect(body).toContain('Overdue');
		expect(body).toContain('text-destructive');
		expect(body).not.toContain('Book your annual review');
	});

	it('a single item phrases the heading in the singular', () => {
		const body = render(ActionItemsBanner, { props: { items: items.slice(0, 1) } }).body;
		expect(body).toContain('One thing needs your attention');
	});

	it('renders nothing when empty (the contract default)', () => {
		const body = render(ActionItemsBanner, { props: { items: [] } }).body;
		expect(body).not.toContain('attention');
	});

	it('hideWhenEmpty={false} renders the quiet all-clear band instead', () => {
		const body = render(ActionItemsBanner, { props: { items: [], hideWhenEmpty: false } }).body;
		expect(body).toContain('Nothing needs your attention');
	});

	it('the completing uid shows its pending state', () => {
		const body = html({ completing: 't-overdue', oncompletetask: () => {} });
		expect(body).toContain('Marking…');
	});

	it('wears the attention-panel language, not a glass card', () => {
		const body = html({});
		expect(body).toContain('border-brand/30');
		expect(body).toContain('bg-brand/5');
		expect(body).not.toContain('glass-panel');
	});

	it('an unparseable due date says nothing rather than shouting Overdue (dueText parity)', () => {
		const body = render(ActionItemsBanner, {
			props: {
				items: [{ uid: 't-junk', title: 'Junk-dated task', description: null, due_date: 'junk' }]
			}
		}).body;
		expect(body).toContain('Junk-dated task');
		expect(body).not.toContain('Overdue');
	});
});
