/**
 * EVERY declared event type must read as English — scanned, not sampled.
 *
 * `describe.ts` derives most sentences from the `resource.verb_past` name, which works until a name
 * breaks one of its assumptions. When it does, the derivation does not fail: it emits the whole name
 * twice, or tacks a determiner on the end. Those rows shipped and sat in the firm's Event logs —
 * "Dismissed a notification all", "Due soon a task due soon", "Reseeded a flow reseeded" — and no
 * test noticed, because every existing case asserts on a type somebody thought to write down.
 *
 * So this reads the vocabulary from `apps/api/app/audit_actions.py`, which is where event types are
 * declared, and checks the sentence for the two shapes the derivation produces when it gives up.
 * A new event with a verb the table does not know now fails here rather than in the log.
 *
 * It reads the Python by filesystem path, the same cross-language pin `block-contract.json` and
 * `flow-contract.json` already use: the vocabulary lives on the other side of a language boundary
 * the project graph cannot see, so the alternative is a second copy that goes stale.
 */

import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { describeActionEvent } from './describe';

const ACTIONS_PY = new URL('../../../../../../apps/api/app/audit_actions.py', import.meta.url);

/** Every `resource.verb` string declared as an EVENT_TYPES value. */
function declaredEventTypes(): string[] {
	const src = readFileSync(ACTIONS_PY, 'utf8');
	const found = new Set<string>();
	for (const m of src.matchAll(/:\s*"([a-z_]+\.[a-z_]+)"/g)) found.add(m[1]!);
	return [...found].sort();
}

/** Words that are fine to repeat in one sentence — articles, prepositions, the usual joins. */
const REPEATABLE = new Set([
	'a',
	'an',
	'the',
	'of',
	'to',
	'their',
	'every',
	'and',
	'for',
	'in',
	'on',
	'at',
	'with',
	'from'
]);

/**
 * A sentence that trails off on one of these is the derivation using a qualifier as a noun.
 *
 * DETERMINERS ONLY. `soon` and `own` were here first and were wrong: "A task is due soon" is
 * perfectly good English, and the thing actually broken about `task.due_soon` was the REPEAT, which
 * the check above catches. A rule that flags correct sentences gets suppressed, and then it catches
 * nothing.
 */
const NOT_A_NOUN = new Set(['all', 'a', 'an', 'the', 'every', 'their']);

describe('the event vocabulary reads as English', () => {
	it('finds the declarations it is meant to be scanning', () => {
		// A scan that scans nothing is not a guard. The count is a floor, not a pin — new events are
		// expected, and only a collapse to near-zero (a moved file, a changed literal style) matters.
		expect(declaredEventTypes().length).toBeGreaterThan(150);
	});

	it('never says the same word twice', () => {
		// "Reseeded a flow reseeded" — the verb was not one the table knows, so the whole name was
		// used as both the verb and the thing it acted on.
		const bad = declaredEventTypes()
			.map((type) => [type, describeActionEvent(type)] as const)
			.filter(([, sentence]) => {
				const words = sentence.toLowerCase().match(/[a-z]+/g) ?? [];
				return words.some((w) => !REPEATABLE.has(w) && words.filter((x) => x === w).length > 1);
			})
			.map(([type, sentence]) => `${type} → "${sentence}"`);
		expect(bad, 'add a LABEL_OVERRIDES entry naming the act').toEqual([]);
	});

	it('never trails off on a determiner', () => {
		// "Dismissed a notification all" — `all_dismissed` puts its qualifier first, and the
		// qualifier is a determiner rather than a noun, so it cannot be the tail of the sentence.
		const bad = declaredEventTypes()
			.map((type) => [type, describeActionEvent(type)] as const)
			.filter(([, sentence]) => {
				const last = (sentence.toLowerCase().match(/[a-z]+/g) ?? []).at(-1) ?? '';
				return NOT_A_NOUN.has(last);
			})
			.map(([type, sentence]) => `${type} → "${sentence}"`);
		expect(bad, 'add a LABEL_OVERRIDES entry naming the act').toEqual([]);
	});

	it('starts every sentence with a capital', () => {
		const bad = declaredEventTypes()
			.map((type) => [type, describeActionEvent(type)] as const)
			.filter(([, sentence]) => sentence.length > 0 && sentence[0] !== sentence[0]!.toUpperCase())
			.map(([type, sentence]) => `${type} → "${sentence}"`);
		expect(bad).toEqual([]);
	});

	it('would catch the regressions it exists for', () => {
		// The three that shipped, proving the checks above are not vacuous. These are the raw
		// derivations; each now has an override, so they are asserted as STRINGS rather than by
		// re-deriving them.
		const shipped = [
			'Dismissed a notification all',
			'Due soon a task due soon',
			'Reseeded a flow reseeded'
		];
		for (const sentence of shipped) {
			const words = sentence.toLowerCase().match(/[a-z]+/g) ?? [];
			const repeats = words.some(
				(w) => !REPEATABLE.has(w) && words.filter((x) => x === w).length > 1
			);
			const trails = NOT_A_NOUN.has(words.at(-1) ?? '');
			expect(repeats || trails, `${sentence} should be caught`).toBe(true);
		}
	});
});

describe('a task event names the task', () => {
	const task = { kind: 'task', id: '7', uid: 'tsk_7', label: 'File the signed declaration' };

	it('says which task is due, not that "a task" is', () => {
		// Four due-soon rows in a column all read the same otherwise, so the name is the only part
		// worth scanning.
		expect(describeActionEvent('task.due_soon', {}, [task])).toBe(
			'File the signed declaration is due soon'
		);
	});

	it('reads as a sentence when a task is assigned, not as a filing instruction', () => {
		// "Assigned File the signed declaration" reads as though somebody assigned a file.
		expect(describeActionEvent('task.assigned', {}, [task])).toBe(
			'Assigned the task File the signed declaration'
		);
		expect(
			describeActionEvent('task.assigned', {}, [
				task,
				{ kind: 'user', id: '3', uid: 'usr_3', label: 'Priya Raman' }
			])
		).toBe('Assigned File the signed declaration to Priya Raman');
	});

	it('falls back to a plain sentence when the task cannot be resolved', () => {
		// A deleted task must still get a sentence rather than a bare id.
		expect(describeActionEvent('task.due_soon', {}, [])).toBe('A task is due soon');
	});

	it('uses the same wording for current and append-only legacy dismiss-all rows', () => {
		expect(describeActionEvent('notification.all_dismissed')).toBe('Dismissed every notification');
		expect(describeActionEvent('action.notifications-dismiss-all-notifications')).toBe(
			'Dismissed every notification'
		);
	});
});
