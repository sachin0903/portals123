/**
 * The registry pin — the TypeScript half of the cross-language contract.
 *
 * The API mirrors `flow-contract.json` in Python (it ships without pkgs/ui and cannot read the
 * JSON at runtime); this side reads it directly, so what needs pinning here is not the DATA but
 * the DERIVATIONS: the picker menus, the operator polarity pairs and the config descriptors are
 * all computed, and a contract edit that quietly empties one of them would ship a blank dropdown
 * rather than fail anything.
 *
 * The technique is `branding/portal-slugs.test.ts`'s: read the JSON from disk independently of the
 * module's own import, so the assertion cannot be satisfied by the module agreeing with itself.
 */
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import {
	ACTION_BY_TYPE,
	ACTION_TYPES,
	AI_ACTIONS,
	BASE_OPERATORS,
	CAPS,
	FIELDS,
	HIDDEN_ACTIONS,
	LISTENER_SOURCES,
	LISTENER_SUBJECTS,
	OFFERED_ACTION_TYPES,
	OPEN_PREFIXES,
	OPERATORS,
	OPERATOR_SPECS,
	PARKED_ACTIONS,
	TRIGGERS,
	TRIGGER_BY_KEY,
	actionPermissionNote,
	aiActionNote,
	configFields,
	isAIAction,
	isActionAvailable,
	isListenable,
	isSupported,
	listenerRefusal,
	triggerGroups
} from './contract.js';

const contract = JSON.parse(
	readFileSync(new URL('../book/journeys/flow-contract.json', import.meta.url), 'utf8')
);

describe('the trigger registry pins to flow-contract.json', () => {
	it('exposes every trigger, with its label, source and subject', () => {
		expect(TRIGGERS.map((t) => t.key)).toEqual(Object.keys(contract.triggers));
		for (const trigger of TRIGGERS) {
			const spec = contract.triggers[trigger.key];
			expect(trigger.label).toBe(spec.label);
			expect(trigger.source).toBe(spec.source);
			expect(trigger.subject).toBe(spec.subject);
			expect(trigger.event).toBe(spec.event ?? null);
		}
	});

	it('groups every trigger exactly once, and never into a blank menu', () => {
		const groups = triggerGroups();
		expect(groups.length).toBeGreaterThan(1);
		const keys = groups.flatMap((g) => g.triggers.map((t) => t.key));
		expect(new Set(keys).size).toBe(keys.length);
		expect(keys.sort()).toEqual(Object.keys(contract.triggers).sort());
		expect(groups.every((g) => g.label && g.triggers.length > 0)).toBe(true);
	});

	it('marks domain-sourced triggers as not live yet, and action/schedule ones as live', () => {
		// Mirrors app/flows/triggers.py::SUPPORTED_SOURCES. A trigger the engine cannot fire is
		// authorable but badged — refusing it would blank half the menu, accepting it silently would
		// leave a firm believing a rule is armed.
		expect(isSupported('contact.created')).toBe(true);
		expect(isSupported('schedule.recurring')).toBe(true);
		expect(isSupported('case.completed')).toBe(false);
		expect(isSupported('tag.applied')).toBe(false);
		expect(isSupported('nonsense.key')).toBe(false);
	});

	it('resolves a trigger by key', () => {
		expect(TRIGGER_BY_KEY['contact.created']?.label).toBe('A client record is created');
		expect(TRIGGER_BY_KEY['nope']).toBeUndefined();
	});
});

describe('listener deliverability — what a wait_for_event node may wait for', () => {
	it('reads its two sets straight from the contract', () => {
		expect([...LISTENER_SOURCES]).toEqual(contract.listeners.deliverableSources);
		expect([...LISTENER_SUBJECTS]).toEqual(contract.listeners.scopableSubjects);
	});

	it('refuses a schedule — the rule-tier predicate said yes and that was the bug', () => {
		// `isSupported` is the RULE tier's question and answers TRUE for a schedule, which is
		// correct there and catastrophic here: a schedule is not an event, so a case parked
		// waiting for one is never released. The builder used to gate this picker on it.
		expect(isSupported('schedule.recurring')).toBe(true);
		expect(isListenable('schedule.recurring')).toBe(false);
		expect(listenerRefusal('schedule.recurring')).toContain('schedule');
	});

	it('refuses domain triggers until something delivers them', () => {
		for (const key of ['case.completed', 'case.step_completed', 'screening.completed']) {
			expect(isListenable(key)).toBe(false);
		}
	});

	it('refuses task triggers — a journey is never about a task', () => {
		expect(LISTENER_SUBJECTS.has('task')).toBe(false);
		expect(isListenable('task.completed')).toBe(false);
		expect(listenerRefusal('task.completed')).toContain('task');
	});

	it('still allows the ones the contract’s own example needs', () => {
		for (const key of ['client.form_submitted', 'client.document_uploaded']) {
			expect(isListenable(key)).toBe(true);
			expect(listenerRefusal(key)).toBeNull();
		}
	});

	it('gives an unknown key its own reason rather than a subject one', () => {
		expect(listenerRefusal('nonsense.key')).toContain('know');
	});
});

describe('the action registry pins to flow-contract.json', () => {
	it('exposes every action type with its config keys and permission', () => {
		expect(ACTION_TYPES.map((a) => a.type)).toEqual(Object.keys(contract.actionTypes));
		for (const action of ACTION_TYPES) {
			expect(action.config).toEqual(contract.actionTypes[action.type].config);
			expect(action.permission).toBe(contract.actionTypes[action.type].permission);
			expect(action.label).toBeTruthy();
		}
	});

	it('emits one editable field per contract config key, in contract order', () => {
		// The descriptor is what makes "add a config key to the contract and it appears in the
		// sheet" true. A key with no descriptor entry still renders — as text — rather than
		// silently going missing from the form.
		for (const [type, spec] of Object.entries(contract.actionTypes) as [
			string,
			{ config: string[] }
		][]) {
			expect(configFields(type).map((f) => f.key)).toEqual(spec.config);
			expect(configFields(type).every((f) => f.label && f.kind)).toBe(true);
		}
		expect(configFields('not-an-action')).toEqual([]);
	});

	it('resolves an action by type', () => {
		expect(ACTION_BY_TYPE['apply_tag']?.label).toBe('Apply a tag');
	});

	it('picks a team role from the closed list, Adviser by default — never free text', () => {
		// The API refuses a role outside the four (422), and `lead` was migrated away, so a text box
		// here would let an author save an action that can only ever fail.
		const role = configFields('assign_adviser').find((f) => f.key === 'role');
		expect(role?.kind).toBe('choice');
		expect(role?.choices?.map((c) => c.value)).toEqual([
			'adviser',
			'paraplanner',
			'administrator',
			'support'
		]);
		expect(role?.defaultValue).toBe('adviser');
	});
});

describe('the condition vocabulary pins to flow-contract.json', () => {
	it('exposes every operator', () => {
		expect(OPERATORS).toEqual(contract.conditions.operators);
	});

	it('gives every operator a spec, so no dropdown entry can render blank', () => {
		for (const op of OPERATORS) expect(OPERATOR_SPECS[op]).toBeDefined();
	});

	it('pairs every negated operator with its exact positive twin', () => {
		// The builder renders eight TESTS plus an is/is-not toggle, not sixteen entries. That only
		// works if the pairing is total and symmetric — a one-way twin would produce a row whose
		// polarity toggle sends it somewhere it cannot come back from.
		for (const [op, spec] of Object.entries(OPERATOR_SPECS)) {
			if (!spec.twin) continue;
			const twin = OPERATOR_SPECS[spec.twin];
			expect(twin, `${op} names a twin that doesn't exist`).toBeDefined();
			expect(twin?.twin).toBe(op);
			expect(twin?.negate).toBe(!spec.negate);
			// Both sides share one label; the polarity is what differs.
			expect(twin?.label).toBe(spec.label);
			expect(twin?.value).toBe(spec.value);
		}
	});

	it('offers only the positive side of each pair as a test', () => {
		expect(BASE_OPERATORS.every((spec) => !spec.negate)).toBe(true);
		expect(BASE_OPERATORS.map((s) => s.op)).toContain('equals');
		expect(BASE_OPERATORS.map((s) => s.op)).not.toContain('notEquals');
	});

	it('splits the closed field registry from the open namespaces', () => {
		const all = Object.values(contract.conditions.fieldScopes).flat() as string[];
		expect(FIELDS).toEqual(all.filter((f) => !f.endsWith('*')));
		// memory.* and event.subject.* are AUTHORED keys — the picker offers them as a prefix, not
		// as a closed list, or a firm's own memory key becomes unreachable from its own builder.
		expect(OPEN_PREFIXES).toContain('memory.');
		expect(OPEN_PREFIXES).toContain('event.subject.');
	});
});

describe('caps', () => {
	it('pins to the contract', () => {
		expect(CAPS).toEqual(contract.caps);
		expect(CAPS.maxRulesPerTenant).toBeGreaterThan(0);
	});
});

describe('action availability', () => {
	it('parks only real contract actions — a typo would park nothing and say nothing', () => {
		for (const type of PARKED_ACTIONS) expect(ACTION_BY_TYPE[type]).toBeDefined();
	});

	it('reads an unparked contract action as available and anything else as not', () => {
		expect(isActionAvailable('apply_tag')).toBe(true);
		expect(isActionAvailable('not_a_real_action')).toBe(false);
		for (const type of PARKED_ACTIONS) expect(isActionAvailable(type)).toBe(false);
	});

	it('names the permission an action asserts, from the contract', () => {
		// The point of the line: an author sees that "send an email" exercises the firm's own
		// client-facing release authority, on the screen where they choose it.
		expect(actionPermissionNote('send_email')).toContain('documents:send');
		expect(actionPermissionNote('raise_task')).toContain('tasks:write');
		expect(actionPermissionNote('nope')).toBe(null);
	});
});

describe('the AI gates are flagged as such', () => {
	it('names every AI action in the contract, and nothing that is not one', () => {
		// The set drives the inspector's warning. A typo here would silently stop telling an author
		// that choosing this action sends a slice of a client's text to a third party.
		for (const type of AI_ACTIONS) expect(contract.actionTypes[type]).toBeDefined();
		expect([...AI_ACTIONS].sort()).toEqual(['classify', 'draft_message']);
		expect(isAIAction('send_email')).toBe(false);
	});

	it('says what each one does and does not do', () => {
		// AI ROUTES AND DRAFTS — it never decides and it never sends. That has to be on the screen
		// where the choice is made, not only in the docs.
		expect(aiActionNote('classify')).toContain('decides nothing');
		expect(aiActionNote('draft_message')).toContain('Nothing is sent to a client');
		expect(aiActionNote('apply_tag')).toBe(null);
	});

	it('gates a draft on tasks:write, never on client-facing release', () => {
		// A draft goes on a task for a person to review. Gating it on documents:send would let a
		// firm that has granted nobody release authority acquire it through a model.
		expect(contract.actionTypes['draft_message'].permission).toBe('tasks:write');
		expect(actionPermissionNote('draft_message')).toContain('tasks:write');
	});
});

describe('withdrawn actions', () => {
	it('offers neither AI action anywhere', () => {
		// Off by default twice server-side, so publish already refuses them — a palette entry that always fails is a promise we do not keep.
		const offered = OFFERED_ACTION_TYPES.map((a) => a.type);
		expect(offered).not.toContain('classify');
		expect(offered).not.toContain('draft_message');
	});

	it('withdraws them from AUTHORING only — the contract and the executor still know them', () => {
		// Authoring only: an existing flow that already carries one must still parse, validate and run.
		expect(ACTION_TYPES.map((a) => a.type)).toContain('classify');
		expect(isActionAvailable('classify')).toBe(true);
		expect(ACTION_BY_TYPE.draft_message).toBeDefined();
	});

	it('is the only difference between the two lists', () => {
		const hidden = ACTION_TYPES.filter((a) => !OFFERED_ACTION_TYPES.includes(a)).map((a) => a.type);
		expect(new Set(hidden)).toEqual(HIDDEN_ACTIONS);
	});

	it('does not overload PARKED_ACTIONS, which means something else', () => {
		// Parked = declared but unimplemented, and shown WITH a badge; conflating the two badges a working action as broken.
		for (const type of HIDDEN_ACTIONS) expect(PARKED_ACTIONS.has(type)).toBe(false);
	});
});

/** `variables` fills an approved template's merge fields in: without it every field the case facts did not carry rendered empty, so "Your {{ document_name }} is ready" delivered "Your  is ready". A blank-filler, not a second place to author copy — the API bounds the count and length and refuses a name shadowing a real scope (`effects.parse_email_variables`). */
describe('send_email fills in its template', () => {
	it('offers a variables field', () => {
		const keys = configFields('send_email').map((f) => f.key);
		expect(keys).toContain('variables');
	});

	it('is labelled for what it does, not for the config key', () => {
		const field = configFields('send_email').find((f) => f.key === 'variables');
		expect(field?.label).toBe('Fill in the template');
	});

	it('is a textarea holding a mapping, like the other two map-shaped keys', () => {
		// `request_mapping`/`response_mapping` are already textareas; a bespoke editor for the third would give an author two ways to write one shape.
		const field = configFields('send_email').find((f) => f.key === 'variables');
		expect(field?.kind).toBe('textarea');
		expect(configFields('lookup').find((f) => f.key === 'request_mapping')?.kind).toBe('textarea');
	});

	it('shows the shape in its placeholder', () => {
		// The API's refusal names the shape too; an author should not have to be refused first.
		const field = configFields('send_email').find((f) => f.key === 'variables');
		expect(field?.placeholder).toContain('document_name');
	});
});
