/**
 * The detail panel opens on demand. The workbench has no deep link to seed a selection from, so
 * the only way in is a picked row, and that is client-side: SSR can prove the panel is absent,
 * not that a click brings it in. Esc and the X put it back.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';
import TransfersWorkbench from './transfers-workbench.svelte';
import type { ApiTransfer } from './api.js';

// jsdom has no layout, so it has no `scrollIntoView`. The workbench brings the picked row back into
// view after the panel reflows the table.
Element.prototype.scrollIntoView ??= () => {};

const TRANSFER: ApiTransfer = {
	id: 1,
	uid: 'trf_1',
	account_id: 10,
	contact_id: 5,
	expectation_id: 7,
	transaction_id: null,
	ceding_provider: 'Aviva',
	policy_number: 'AV-9912',
	transfer_type: 'pension',
	basis: 'cash',
	extent: 'full',
	crystallised_value: null,
	uncrystallised_value: null,
	total_value: 25_000,
	crystallisation: null,
	pension_type: null,
	safeguarded_benefits: null,
	currency: 'GBP',
	status: 'in_progress',
	ml_status: 2,
	ml_status_label: 'Awaiting action from ceding scheme',
	ml_status_at: '2026-06-10',
	ceding_status: null,
	ml_transfer_id: null,
	ml_recorded_at: null,
	expected_date: null,
	completed_at: null,
	note: null,
	created_at: '2026-06-01T09:00:00Z',
	updated_at: '2026-06-01T09:00:00Z',
	account_name: "Sophie's SIPP",
	account_uid: 'acc_1',
	product_type: 'SIPP',
	client_name: 'Sophie Halloway',
	contact_uid: 'con_5',
	adviser: 'Dan Reeve',
	expectation_uid: 'exp_1',
	expectation_status: 'instructed',
	expectation_due_date: '2026-06-20',
	expectation_amount: 25_000,
	expectation_matched_at: null,
	matched_by_hand: null,
	transaction_amount: null,
	transaction_date: null,
	transaction_source: null,
	transaction_reference: null,
	reconciliation: { status: 'pending', legs: [], variance: null, reason: null }
};

const panel = () => document.querySelector('aside[aria-label="Selected transfer"]');
const closeButton = () =>
	document.querySelector<HTMLButtonElement>('button[aria-label="Close panel"]');
const row = () => document.querySelector<HTMLTableRowElement>('tbody tr[role="button"]');

/** From the body by default: with nothing focused, that is where a browser sends the key. */
function escape(from: EventTarget = document.body): void {
	from.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
	flushSync();
}

describe('the transfers workbench panel', () => {
	let app: ReturnType<typeof mount> | null = null;

	beforeEach(async () => {
		app = mount(TransfersWorkbench, {
			target: document.body,
			props: { transfers: [TRANSFER], onnote: vi.fn() }
		});
		flushSync();
		await tick();
	});

	afterEach(() => {
		if (app) unmount(app);
		app = null;
		document.body.innerHTML = '';
	});

	it('opens for a picked row and marks that row', () => {
		expect(panel()).toBeNull();
		row()!.click();
		flushSync();
		expect(panel()).not.toBeNull();
		expect(closeButton()).not.toBeNull();
		expect(row()!.className).toContain('transfer-selected');
		// Below `xl` the panel takes the queue's place; the queue stays mounted under it.
		expect(row()!.closest('.max-xl\\:hidden')).not.toBeNull();
	});

	it('closes on the X', () => {
		row()!.click();
		flushSync();
		closeButton()!.click();
		flushSync();
		expect(panel()).toBeNull();
		expect(row()!.className).not.toContain('transfer-selected');
	});

	it('closes on Esc, but not while a note is being typed', () => {
		row()!.click();
		flushSync();
		const note = panel()!.querySelector('textarea');
		expect(note).not.toBeNull();
		escape(note!);
		expect(panel()).not.toBeNull();
		escape();
		expect(panel()).toBeNull();
	});
});
