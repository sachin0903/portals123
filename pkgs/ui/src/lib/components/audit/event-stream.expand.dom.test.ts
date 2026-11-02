/**
 * The trail's rows OPEN AND CLOSE, which is the half of this change no SSR test can see.
 *
 * The surface was master-detail, so a click only ever SELECTED: clicking the row you were already
 * reading did nothing, because the pane had to show something. In place that is wrong twice over —
 * a row that expands and cannot be closed wedges a screenful of detail into a list somebody is
 * scanning, and the only way to shrink it is to open a DIFFERENT event.
 *
 * So there are three rules, all run-time: a click on a closed row OPENS it; a click on the OPEN row
 * CLOSES it; and only one is ever open. A server render shows the newest row already open and can
 * prove none of them, so this mounts the real component and clicks it.
 *
 * No fake timers: per the pkgs/ui note in CLAUDE.md, faking `setTimeout` freezes vitest's worker
 * heartbeat and the run dies looking like a hang.
 */
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import type { Component } from 'svelte';
import type { ActionEvent } from './describe.js';

let mount: typeof import('svelte').mount;
let unmount: typeof import('svelte').unmount;
let flushSync: typeof import('svelte').flushSync;
let EventStream: Component<{ events: ActionEvent[] }>;

beforeAll(async () => {
	// bits-ui's floating layer (the filter dropdowns in the toolbar above the table) reaches for
	// browser APIs jsdom does not implement. Scaffolding for the mount, never part of what is tested.
	vi.stubGlobal(
		'ResizeObserver',
		class {
			observe() {}
			unobserve() {}
			disconnect() {}
		}
	);
	if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {};

	({ mount, unmount, flushSync } = await import('svelte'));
	EventStream = (await import('./event-stream.svelte')).default as typeof EventStream;
}, 120_000);

afterEach(() => {
	document.body.innerHTML = '';
});

const event = (id: number, actor: string): ActionEvent => ({
	id,
	event_type: 'client.created',
	created_at: '2026-08-03T09:15:00Z',
	method: 'POST',
	route: '/firm/contacts',
	status: 200,
	path_params: {},
	metadata: { correlation_id: `corr_${id}` },
	actor_id: `u-${id}`,
	actor_sub: `wos_${id}`,
	actor_email: `${actor.toLowerCase()}@firm.test`,
	actor_name: actor,
	tenant_id: 1,
	tenant_uid: 'ten_firm',
	tenant_name: 'Feed Firm',
	target_tenant_id: null,
	target_tenant_uid: null,
	target_tenant_name: null
});

// Newest first, the order the stream arrives in.
const EVENTS = [event(3, 'Alice'), event(2, 'Bo'), event(1, 'Cass')];

function mounted() {
	const target = document.createElement('div');
	document.body.append(target);
	const component = mount(EventStream, { target, props: { events: EVENTS } });
	return {
		target,
		/** The DATA rows — the disclosure rows, which are the ones carrying `aria-expanded`. */
		rows: () => [...target.querySelectorAll<HTMLElement>('tbody tr[aria-expanded]')],
		/** How many details are rendered. One `<td colspan>` per open row. */
		openDetails: () => target.querySelectorAll('tbody td[colspan]').length,
		dispose: () => unmount(component)
	};
}

const click = (el: HTMLElement) => {
	el.dispatchEvent(new MouseEvent('click', { bubbles: true }));
	flushSync();
};

const press = (el: HTMLElement, key: string) => {
	el.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
	flushSync();
};

const expandedFlags = (ui: ReturnType<typeof mounted>) =>
	ui.rows().map((r) => r.getAttribute('aria-expanded'));

describe('the event trail expands in place', () => {
	it('lands with NOTHING open', () => {
		// The master-detail pane opened the newest row because a pane showing nothing is a hole in
		// the layout. An unexpanded row is not, and a surface that opens one for you has answered a
		// question nobody asked while pushing the rest of the list down a screen.
		const ui = mounted();
		expect(expandedFlags(ui)).toEqual(['false', 'false', 'false']);
		expect(ui.openDetails()).toBe(0);
		ui.dispose();
	});

	it('closes the open row when it is clicked again', () => {
		// The defect this rules out: a row you can open and cannot close. Master-detail could not have
		// it, because the pane always showed something.
		const ui = mounted();
		click(ui.rows()[0]!);
		expect(expandedFlags(ui)).toEqual(['true', 'false', 'false']);
		click(ui.rows()[0]!);
		expect(expandedFlags(ui)).toEqual(['false', 'false', 'false']);
		expect(ui.openDetails()).toBe(0);
		// …and it opens again from there, rather than latching shut.
		click(ui.rows()[0]!);
		expect(expandedFlags(ui)).toEqual(['true', 'false', 'false']);
		ui.dispose();
	});

	it('opens one row at a time — an accordion, not a stack', () => {
		// A trail is SCANNED. Two open details put the next row the reader wants a screen and a half
		// further down, and the order — the only thing a trail has — stops being legible.
		const ui = mounted();
		click(ui.rows()[1]!);
		expect(expandedFlags(ui)).toEqual(['false', 'true', 'false']);
		expect(ui.openDetails()).toBe(1);
		click(ui.rows()[2]!);
		expect(expandedFlags(ui)).toEqual(['false', 'false', 'true']);
		expect(ui.openDetails()).toBe(1);
		ui.dispose();
	});

	it('renders the detail under the row it belongs to, spanning the whole table', () => {
		const ui = mounted();
		click(ui.rows()[1]!);
		const open = ui.rows()[1]!;
		const detailRow = open.nextElementSibling as HTMLElement;
		expect(detailRow.querySelector('td')?.getAttribute('colspan')).toBe('4');
		// It really is this event's detail — Bo's email is in it, and nobody else's.
		expect(detailRow.textContent).toContain('bo@firm.test');
		expect(detailRow.textContent).not.toContain('alice@firm.test');
		ui.dispose();
	});

	it('toggles from the keyboard, with Enter and with Space', () => {
		// The row is the control (`role="button"`), so both keys must work and Space must not scroll
		// the page — DataTable's `preventDefault` — or the reader loses their place on every toggle.
		const ui = mounted();
		press(ui.rows()[1]!, 'Enter');
		expect(expandedFlags(ui)).toEqual(['false', 'true', 'false']);
		press(ui.rows()[1]!, ' ');
		expect(expandedFlags(ui)).toEqual(['false', 'false', 'false']);
		ui.dispose();
	});

	it('leaves no aria-controls behind on a row it has closed', () => {
		// A dangling `aria-controls` names an element that no longer exists, which a screen reader
		// reports as a broken relationship rather than as a collapsed row.
		const ui = mounted();
		click(ui.rows()[0]!);
		expect(ui.rows().some((r) => r.hasAttribute('aria-controls'))).toBe(false);
		ui.dispose();
	});
});
