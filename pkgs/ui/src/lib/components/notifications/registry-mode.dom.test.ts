/**
 * The rule builder MOUNTS in both groupings.
 *
 * This file exists because an SSR test could not catch the bug that shipped: SSR does not evaluate
 * two-way bindings, so `bind:open={openSections[section.key]}` rendered fine on the server and threw
 * `props_invalid_value` the moment the component hydrated — taking the whole page down. The section
 * keys depend on the grouping (two audiences vs one per category) and the seeded map only covered
 * the first, so the registry bound `undefined` to a prop with a fallback.
 *
 * Mounting is the only way to see that. Per CLAUDE.md: `setTimeout` stays REAL (faking it kills
 * vitest's worker heartbeat) and the import gets a generous timeout, because pulling bits-ui through
 * Vite's cold transform beats the 10s default on Windows.
 */
import { describe, expect, it } from 'vitest';
import { mount, tick, unmount } from 'svelte';
import NotificationRules from './notification-rules.svelte';
import type { CatalogueTrigger, NotificationRule } from './rule-types.js';

const audience = () => ({
	key: 'client',
	label: 'The client',
	builtin: true,
	default_title: 'Your report is ready',
	default_body: 'A new document is waiting.',
	default_link: null,
	default_in_app: true,
	default_email: true,
	default_email_code: 'document_sent'
});

const trigger = (over: Partial<CatalogueTrigger> = {}): CatalogueTrigger => ({
	key: 'document.shared',
	label: 'A document is shared with a client',
	description: 'Fires when a document is sent.',
	category: 'documents',
	event_types: ['document.sent'],
	subject_kind: 'contact',
	link_targets: [],
	params: [],
	audiences: [audience()],
	...over
});

const registryEvent = (over: Record<string, unknown> = {}) => ({
	event_type: 'document.sent',
	resource: 'document',
	state: 'assignable' as const,
	unavailable_reason: null,
	subject_kinds: ['contact'],
	trigger: trigger(),
	rules: [] as NotificationRule[],
	automation_count: 0,
	email_count_30d: 0,
	...over
});

const stored: NotificationRule = {
	uid: 'nrule_1',
	trigger_key: 'document.shared',
	audience: 'client',
	role_slug: null,
	label: 'Document sent',
	title: 'Your report is ready',
	body: null,
	link: null,
	priority: 'normal',
	default_in_app: true,
	default_email: true,
	mandatory: false,
	firm_editable: true,
	authored_by: 'graphene',
	enabled: true,
	email_code: 'document_sent'
};

function mounted(props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.append(target);
	const component = mount(NotificationRules, { target, props });
	return { target, dispose: () => unmount(component) };
}

describe('mounting the rule builder', () => {
	it('mounts the REGISTRY grouping — several categories, a stored rule and a built-in', () => {
		// The regression this suite exists for: Collapsible bound to an unseeded category key
		// (`bind:open={undefined}`), which threw on hydration and took the page down. One mount
		// carries it, so long as it holds what a real catalogue holds — more than one category, a
		// stored rule, a shipped built-in, and an event with no trigger at all.
		const { target, dispose } = mounted({
			events: [
				registryEvent(),
				registryEvent({
					event_type: 'task.created',
					resource: 'task',
					trigger: trigger({ key: 'task.assigned', label: 'A task is assigned', category: 'tasks' })
				}),
				registryEvent({
					event_type: 'aml.sync_ran',
					resource: 'aml',
					state: 'blocked',
					trigger: null,
					subject_kinds: [],
					unavailable_reason: 'This event records no client, account or task.'
				})
			],
			rules: [stored],
			groupBy: 'event',
			firmMode: true
		});
		expect(target.textContent).toContain('A document is shared with a client');
		expect(target.textContent).toContain('A task is assigned');
		// The shipped default renders as a row marked `default`, which is the registry's whole
		// point: a firm receiving it saw nothing configured before.
		expect(target.textContent).toContain('default');
		dispose();
	});
});

describe('the editor opened from a registry row', () => {
	/**
	 * Needs a MOUNT, not an SSR render: the dialog exists only once a draft does, so its markup is
	 * behind a click. An SSR assertion over the select would have passed vacuously — the body simply
	 * does not contain it.
	 */
	// `await tick()` after the click: Svelte 5 flushes on a microtask, so the dialog is not in the
	// DOM on the line after `.click()`. Without it the query returns null and the test reads as
	// "the editor never opened".
	async function openAssign(events: Record<string, unknown>[]) {
		const { target, dispose } = mounted({
			events,
			rules: [],
			groupBy: 'event',
			canManage: true,
			onSave: () => {}
		});
		// ONE control per event, whatever is attached: the editor manages the whole event.
		const assign = [...target.querySelectorAll('button')].find(
			(b) => (b.textContent ?? '').trim() === 'Configure'
		);
		assign?.click();
		await tick();
		return { target, dispose, assign };
	}

	it('offers a control to author a notification on an assignable row', async () => {
		const { assign, dispose } = await openAssign([registryEvent()]);
		expect(assign).toBeTruthy();
		dispose();
	});

	// The editor's own markup is asserted in `registry-mode.ssr.test.ts` by scanning the source:
	// bits-ui's Dialog does not mount under jsdom here (the click fires, the draft is set, and the
	// portalled content never appears), so a mount test over it would assert nothing. The scan is
	// what `rules-event-names.test.ts` already does for the same reason.
});

describe('a trigger that covers several events', () => {
	/**
	 * The real shape of the data, and the one every other fixture here missed: a catalogue trigger
	 * may name MORE THAN ONE event. `message.received_by_client` fires on four message types, so the
	 * registry returns four rows carrying the same trigger.
	 *
	 * That crashed the editor. The derived catalogue was `events.map(e => e.trigger)`, so the trigger
	 * appeared four times, and the dialog's Event select is a keyed `{#each}` over it —
	 * `each_key_duplicate` threw during render, which opened the overlay and drew no content. It read
	 * as "the button does nothing".
	 *
	 * **This test does not catch that, and cannot.** The dialog is portalled and does not mount under
	 * jsdom, so the offending `{#each}` never runs — checked by removing the fix, under which this
	 * still passes. The guard is `describe.test.ts::triggersFrom`, which is pure and does fail. What
	 * is worth pinning HERE is the half that does render: two events sharing one trigger are two
	 * rows, not one.
	 */
	const shared = {
		key: 'message.received_by_client',
		label: 'When an adviser sends a message',
		description: '',
		category: 'messages',
		event_types: ['message.sent', 'message.attachment_sent'],
		subject_kind: 'contact',
		link_targets: [],
		params: [],
		audiences: [audience()]
	};

	const rowsSharingATrigger = [
		registryEvent({ event_type: 'message.sent', resource: 'message', trigger: shared }),
		registryEvent({ event_type: 'message.attachment_sent', resource: 'message', trigger: shared })
	];

	it('lists one row per event, not one per trigger', () => {
		const { target, dispose } = mounted({
			events: rowsSharingATrigger,
			rules: [],
			groupBy: 'event',
			canManage: true,
			onSave: () => {}
		});
		// Both events are listed — the trigger is shared, the ROWS are not.
		expect(target.textContent).toContain('message.sent');
		expect(target.textContent).toContain('message.attachment_sent');

		// Each row offers its own control — the shared trigger must not collapse them into one.
		const act = [...target.querySelectorAll('button')].filter(
			(b) => (b.textContent ?? '').trim() === 'Configure'
		);
		expect(act.length, 'one control per event row').toBe(2);
		dispose();
	});
});

describe('the switches on a registry line', () => {
	/**
	 * A registry row is an EVENT, which has no channels of its own — the switches belong to the
	 * notifications attached to it, and an event may carry several. They are bound to the same
	 * optimistic mirror the builder's rows use, so a toggle moves at once and is persisted by the
	 * batched Save bar rather than a round trip per flick.
	 */
	function switches(target: HTMLElement) {
		return [...target.querySelectorAll('[role="switch"]')] as HTMLElement[];
	}

	it('gives every attached notification its own three switches, named by the column', () => {
		const { target, dispose } = mounted({
			events: [
				registryEvent({
					trigger: trigger({
						audiences: [
							audience(),
							{ ...audience(), key: 'adviser', label: "The client's adviser" }
						]
					})
				})
			],
			rules: [stored],
			groupBy: 'event',
			canManage: true,
			onSave: () => {},
			onSaveMany: () => {}
		});
		// The HEADER names them — a bare switch in a named column, not a label repeated on the line.
		for (const name of ['In-app', 'Email', 'Mandatory']) {
			expect(target.textContent).toContain(name);
		}
		// Three per audience, and no `enabled`: a notification that should not fire is one to
		// REMOVE, and an off switch beside the two channels reads as a third channel while meaning
		// something else. Two audiences on this event, so six.
		expect(switches(target).length).toBe(6);
		dispose();
	});

	it('offers the batched Save bar once one is toggled, and not before', async () => {
		const { target, dispose } = mounted({
			events: [registryEvent()],
			rules: [stored],
			groupBy: 'event',
			canManage: true,
			onSave: () => {},
			onSaveMany: () => {}
		});
		expect(target.textContent).not.toContain('unsaved');

		// `mandatory` is false on the fixture, so this is a real change rather than a no-op.
		const mandatory = switches(target).at(-1);
		expect(mandatory, 'a mandatory switch is rendered').toBeTruthy();
		mandatory?.click();
		await tick();
		expect(target.textContent).toContain('unsaved');
		dispose();
	});

	it('does not offer them to a reader who cannot manage rules', () => {
		const { target, dispose } = mounted({
			events: [registryEvent()],
			rules: [stored],
			groupBy: 'event',
			canManage: false
		});
		// Rendered but inert — a switch that silently does nothing is worse than a disabled one.
		for (const el of switches(target)) {
			expect(el.hasAttribute('disabled') || el.getAttribute('aria-disabled') === 'true').toBe(true);
		}
		dispose();
	});
});

describe('who a notification reaches', () => {
	/**
	 * Only `client` leaves the firm. A notice to a client goes to a real customer, and is the one to
	 * look twice at before switching a channel on — so the line marks it, beside the words rather
	 * than instead of them.
	 */
	const bothAudiences = registryEvent({
		trigger: trigger({
			audiences: [audience(), { ...audience(), key: 'adviser', label: "The client's adviser" }]
		})
	});

	it('marks the client line apart from the staff line', () => {
		const { target, dispose } = mounted({
			events: [bothAudiences],
			rules: [],
			groupBy: 'event',
			canManage: true,
			onSave: () => {}
		});
		const marks = [...target.querySelectorAll('[title="Goes to the client"]')];
		const staff = [...target.querySelectorAll('[title="Goes to firm staff"]')];
		expect(marks.length, 'the client audience is marked').toBe(1);
		expect(staff.length, 'the internal audience is marked as internal').toBe(1);
		dispose();
	});
});
