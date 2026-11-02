/**
 * SSR coverage for the automations surfaces.
 *
 * What SSR can pin — and what matters for this surface's contract:
 *
 *  - **links are INJECTED**: every href comes from the `links` builders, never a hardcoded
 *    platform path (a second mount lives under a different prefix, so a remembered href is a
 *    broken page);
 *  - **no `actions` ⇒ no write affordances**: the read-only posture renders no New/Edit/Reorder/
 *    Pause control and no arm toggle. This is the "narrowed role" case — a principal with
 *    `automations:read` alone must not be shown a control that would 403, and a PARTIAL actions
 *    object (pause but not write, the incident-responder posture) must show exactly the one it has;
 *  - **the paused banner is unmissable**, because while it is up nothing on the page runs however
 *    armed it looks;
 *  - the table renders the human summaries rather than raw JSON, and the empty state explains what
 *    automations are for.
 *
 * Interaction (opening the sheet, dragging the order, submitting) is client-side and out of SSR's
 * reach; the model round trip behind the sheet is unit-tested in `conditions.test.ts`.
 */
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import AutomationsListing from './automations-listing.svelte';
import AutomationDetail from './automation-detail.svelte';
import RunLog from './run-log.svelte';
import type {
	AutomationActions,
	AutomationLinks,
	AutomationRow,
	AutomationSettings,
	DeliveryRow
} from './types.js';

// Deliberately NOT the platform's own routes — the assertions below prove the components render
// whatever the app injects rather than remembering where they were written.
const links: AutomationLinks = {
	index: '/x/automations',
	automation: (uid) => `/x/automations/${uid}`
};

const noopEnhance = (() => ({ destroy() {} })) as AutomationActions['enhance'];

const fullActions: AutomationActions = {
	enhance: noopEnhance,
	create: 'create',
	update: 'update',
	remove: 'remove',
	toggle: 'toggle',
	reorder: 'reorder',
	pause: 'pause'
};

const row = (over: Partial<AutomationRow> = {}): AutomationRow => ({
	uid: 'aut_1',
	name: 'Chase an unsigned fact find',
	trigger_key: 'client.form_submitted',
	trigger_label: 'A client submits a form',
	trigger_source: 'action',
	subject_kind: 'contact',
	supported: true,
	conditions: { field: 'contact.type', operator: 'equals', value: 'individual' },
	action: { type: 'apply_tag', config: { tag: 'chase-sent' } },
	config: {},
	priority: 100,
	enabled: true,
	rearm_minutes: null,
	schedule: null,
	allow_automation_source: false,
	flow_uid: null,
	flow_code: null,
	flow_name: null,
	flow_status: null,
	last_fired_at: '2026-07-30T09:00:00Z',
	last_outcome: 'dispatched',
	last_detail: 'Tag applied',
	...over
});

const settings = (over: Partial<AutomationSettings> = {}): AutomationSettings => ({
	paused: false,
	paused_reason: null,
	timezone: 'Europe/London',
	effective: { default_rearm_minutes: 1440, executions_per_minute: 60 },
	defaults: {},
	ceilings: {},
	max_rules: 200,
	preflight_available: false,
	...over
});

const delivery = (over: Partial<DeliveryRow> = {}): DeliveryRow => ({
	id: 1,
	event_id: 99,
	event_key: 'action:99',
	subject_kind: 'contact',
	subject_id: '7',
	case_uid: null,
	outcome: 'skipped_conditions',
	detail: null,
	error: null,
	chain_depth: 0,
	attempts: 1,
	retry_after: null,
	created_at: '2026-07-30T09:00:00Z',
	...over
});

describe('AutomationsListing', () => {
	it('renders each rule as a sentence, not as JSON', () => {
		const { body } = render(AutomationsListing, {
			props: { automations: [row()], settings: settings(), links, actions: fullActions }
		});
		expect(body).toContain('Chase an unsigned fact find');
		expect(body).toContain('A client submits a form');
		expect(body).toContain('Client type is individual');
		expect(body).toContain('Apply a tag: chase-sent');
		expect(body).not.toContain('"operator"');
	});

	it('states that order is precedence, and numbers the rows in that order', () => {
		const { body } = render(AutomationsListing, {
			props: {
				automations: [row({ uid: 'a', name: 'First' }), row({ uid: 'b', name: 'Second' })],
				settings: settings(),
				links,
				actions: fullActions
			}
		});
		expect(body).toContain('first match wins');
		expect(body.indexOf('First')).toBeLessThan(body.indexOf('Second'));
	});

	it('builds every href from the injected links', () => {
		const { body } = render(AutomationsListing, {
			props: { automations: [row()], settings: settings(), links, actions: fullActions }
		});
		expect(body).toContain('/x/automations/aut_1');
		expect(body).not.toContain('href="/automations');
	});

	it('badges a trigger nothing emits yet', () => {
		const { body } = render(AutomationsListing, {
			props: {
				automations: [row({ supported: false, trigger_label: 'A journey completes' })],
				settings: settings(),
				links,
				actions: fullActions
			}
		});
		expect(body).toContain('Not live yet');
	});

	it('explains what automations are for when there are none', () => {
		const { body } = render(AutomationsListing, {
			props: { automations: [], settings: settings(), links, actions: fullActions }
		});
		expect(body).toContain('No automations yet');
		expect(body).toContain('New automation');
	});

	// ── the narrowed-role postures ─────────────────────────────────────────

	it('READ-ONLY: no actions ⇒ no write affordance anywhere', () => {
		const { body } = render(AutomationsListing, {
			props: {
				automations: [row(), row({ uid: 'b', name: 'Second' })],
				settings: settings(),
				links
			}
		});
		expect(body).toContain('Chase an unsigned fact find');
		expect(body).not.toContain('New automation');
		expect(body).not.toContain('Reorder');
		expect(body).not.toContain('Pause all automations');
		// The armed state still READS — it just isn't a control.
		expect(body).toContain('Armed');
		expect(body).not.toContain('?/toggle');
	});

	it('READ-ONLY: the empty state offers no call to action it cannot honour', () => {
		const { body } = render(AutomationsListing, {
			props: { automations: [], settings: settings(), links }
		});
		expect(body).toContain('No automations yet');
		expect(body).not.toContain('New automation');
	});

	it('PAUSE-ONLY: the incident responder gets the switch and nothing else', () => {
		const { body } = render(AutomationsListing, {
			props: {
				automations: [row()],
				settings: settings(),
				links,
				actions: { enhance: noopEnhance, pause: 'pause' }
			}
		});
		expect(body).toContain('Pause all automations');
		expect(body).toContain('?/pause');
		expect(body).not.toContain('New automation');
		expect(body).not.toContain('?/toggle');
		expect(body).not.toContain('?/reorder');
	});

	it('posts to the APP’S OWN action names, not remembered ones', () => {
		const { body } = render(AutomationsListing, {
			props: {
				automations: [row()],
				settings: settings(),
				links,
				actions: { ...fullActions, pause: 'setPaused', toggle: 'setArmed' }
			}
		});
		expect(body).toContain('?/setPaused');
		expect(body).toContain('?/setArmed');
	});

	// ── the paused banner ──────────────────────────────────────────────────

	it('makes a paused firm unmissable, and repeats the reason given', () => {
		const { body } = render(AutomationsListing, {
			props: {
				automations: [row()],
				settings: settings({ paused: true, paused_reason: 'Investigating a loop' }),
				links,
				actions: fullActions
			}
		});
		expect(body).toContain('Automations are paused for this firm');
		expect(body).toContain('Investigating a loop');
		expect(body).toContain('Resume automations');
		// …and the bottom pause card is gone, because it is already paused.
		expect(body).not.toContain('Pause all automations');
	});

	it('says so when the rule cap is reached', () => {
		const { body } = render(AutomationsListing, {
			props: {
				automations: [row()],
				settings: settings({ max_rules: 1 }),
				links,
				actions: fullActions
			}
		});
		expect(body).toContain('limit of 1 automations');
	});

	it('surfaces a load failure rather than rendering a convincing empty table', () => {
		const { body } = render(AutomationsListing, {
			props: { automations: [], settings: null, links, loadFailed: true }
		});
		expect(body).toContain('couldn’t be loaded');
	});
});

describe('AutomationDetail', () => {
	it('reads the rule back as when / only if / then', () => {
		const { body } = render(AutomationDetail, {
			props: { automation: row(), settings: settings(), links, actions: fullActions }
		});
		expect(body).toContain('A client submits a form');
		expect(body).toContain('Client type is individual');
		expect(body).toContain('Apply a tag: chase-sent');
		expect(body).toContain('/x/automations'); // the injected back link
	});

	it('states the re-arm window and the self-trigger posture in words', () => {
		const { body } = render(AutomationDetail, {
			props: { automation: row(), settings: settings(), links, actions: fullActions }
		});
		expect(body).toContain('Won’t re-fire for the same subject within 1 day');
		expect(body).toContain('don’t trigger it');
	});

	it('warns when the rule is off, and when the whole firm is paused', () => {
		const off = render(AutomationDetail, {
			props: { automation: row({ enabled: false }), settings: settings(), links }
		});
		expect(off.body).toContain('switched off');

		const paused = render(AutomationDetail, {
			props: { automation: row(), settings: settings({ paused: true }), links }
		});
		expect(paused.body).toContain('paused for this firm');
	});

	it('READ-ONLY: no edit, no delete, and the dry run explains itself away', () => {
		const { body } = render(AutomationDetail, {
			props: { automation: row(), settings: settings(), links }
		});
		expect(body).not.toContain('?/remove');
		expect(body).toContain('don’t have permission to dry-run');
	});

	it('offers the dry run only with the app’s test action, and promises it performs nothing', () => {
		const { body } = render(AutomationDetail, {
			props: {
				automation: row(),
				settings: settings(),
				links,
				actions: fullActions,
				testActionName: 'dryRun'
			}
		});
		expect(body).toContain('?/dryRun');
		expect(body).toContain('never performs the action');
	});

	it('renders a dry-run verdict per condition, with the value it actually read', () => {
		const { body } = render(AutomationDetail, {
			props: {
				automation: row(),
				settings: settings(),
				links,
				actions: fullActions,
				testActionName: 'dryRun',
				dryRun: {
					performed: false,
					would_fire: false,
					matched: false,
					blockers: [],
					subject: { kind: 'contact', id: 7, found: true },
					trigger: {
						key: 'client.form_submitted',
						label: 'A client submits a form',
						source: 'action'
					},
					target: { kind: 'action' },
					facts: {},
					explanation: {
						kind: 'leaf',
						field: 'contact.type',
						operator: 'equals',
						value: 'individual',
						actual: 'organisation',
						passed: false
					}
				}
			}
		});
		expect(body).toContain('This would not fire');
		expect(body).toContain('Nothing was performed');
		expect(body).toContain('Client type is individual');
		expect(body).toContain('organisation');
	});
});

describe('RunLog', () => {
	it('explains an empty log rather than showing a bare "no rows"', () => {
		const { body } = render(RunLog, { props: { deliveries: [] } });
		expect(body).toContain('hasn’t been considered');
	});

	it('leads with the outcome in the firm’s words, and falls back to the hint for detail', () => {
		const { body } = render(RunLog, {
			props: {
				deliveries: [delivery(), delivery({ id: 2, outcome: 'dispatched', detail: 'Tag applied' })]
			}
		});
		expect(body).toContain('No match');
		expect(body).toContain('the conditions didn’t hold');
		expect(body).toContain('Ran');
		expect(body).toContain('Tag applied');
	});

	it('shows a failure’s error and a chain’s depth', () => {
		const { body } = render(RunLog, {
			props: {
				deliveries: [delivery({ outcome: 'failed', error: 'the target exploded', chain_depth: 2 })]
			}
		});
		expect(body).toContain('Failed');
		expect(body).toContain('the target exploded');
		expect(body).toContain('chain depth 2');
	});
});

/** The placeholder is the only place a map-shaped key's shape is stated before an author is refused,
 *  and the textarea branch passed none through — a declared placeholder rendered a blank box. */
describe('the action config form', () => {
	const source = readFileSync(new URL('./action-config.svelte', import.meta.url), 'utf8');

	it('passes a placeholder to a textarea, not only to a text input', () => {
		const textareaBlock = source.slice(
			source.indexOf("{#if field.kind === 'textarea'}"),
			source.indexOf("{:else if field.kind === 'choice'")
		);
		expect(textareaBlock).toContain('placeholder={field.placeholder}');
	});
});
