/**
 * SSR pins for the inline fact sheet under an expanded event row: a body field that is prose
 * takes the whole row, a short one shares a row with the other facts.
 */
import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import EventDetail from './event-detail.svelte';
import type { ActionEvent } from './describe.js';

function ev(over: Partial<ActionEvent> = {}): ActionEvent {
	return {
		id: 7,
		event_type: 'client.note_added',
		created_at: '2026-08-03T09:00:00Z',
		method: 'POST',
		route: '/firm/contacts/{uid}/notes',
		status: 201,
		path_params: { uid: 'ct_a' },
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

/** The grid item that holds the `<dt>` with this label. */
function cellFor(html: string, label: string): string | null {
	const at = html.indexOf(`>${label}<`);
	if (at < 0) return null;
	const open = html.lastIndexOf('<div class="', at);
	return html.slice(open, html.indexOf('</dd>', at));
}

describe('EventDetail inline (SSR)', () => {
	it('gives a note body the whole row, and keeps a short field in the grid', () => {
		// A note's body in a quarter-width cell ran as a column of broken words down the side of
		// the actor's email, and an unbroken run inside it (a URL) crossed into the next cell.
		const long =
			'Spoke to Sophie about the pension transfer; she wants to wait for the Q3 statement before ' +
			'deciding and asked for the illustration to be re-run at £300 a month.';
		const { body } = render(EventDetail, {
			props: {
				variant: 'inline',
				event: ev({ metadata: { body: { body: long, pinned: 'yes' } } })
			}
		});
		const note = cellFor(body, 'Body');
		expect(note).not.toBeNull();
		expect(note).toContain('col-span-2');
		expect(note).toContain('whitespace-pre-wrap');
		expect(note).toContain('Spoke to Sophie');

		const pinned = cellFor(body, 'Pinned');
		expect(pinned).not.toBeNull();
		expect(pinned).not.toContain('col-span-2');
	});

	it('resets the host table cell’s nowrap so a short body wraps inside its column', () => {
		// `Table.Cell` is `whitespace-nowrap`; the inline grid inherited it, so a 70-character note
		// never wrapped at its spaces and ran under the Actor column.
		const { body } = render(EventDetail, {
			props: {
				variant: 'inline',
				event: ev({ metadata: { body: { body: 'a short note that still has to wrap' } } })
			}
		});
		expect(body).toMatch(/<div class="[^"]*whitespace-normal[^"]*"/);
	});
});
