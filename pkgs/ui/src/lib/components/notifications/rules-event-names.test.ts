/**
 * The Event box in the rule builder names the EVENT, not its friendly sentence.
 *
 * Read off the source rather than rendered, for the reason `switch-columns.test.ts` records: this
 * is a markup decision with no behaviour to observe, so nothing else in the suite can see it change
 * back. The two things worth pinning are that the option prints the trigger's own key, and that the
 * sentence it replaced still appears somewhere — dropping the label outright would leave a list of
 * seventeen bare identifiers as the only guidance an author gets, since the builder shows the
 * description nowhere else.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const source = readFileSync(join(import.meta.dirname, 'notification-rules.svelte'), 'utf8');

/** The Event `<select>` and nothing else — the Notify box below it renders audience labels, which
 *  are a different question and legitimately stay friendly. */
const eventSelect = (() => {
	const start = source.indexOf('id="nr-trigger"');
	const end = source.indexOf('id="nr-audience"');
	expect(start, 'the Event select is still identified as nr-trigger').toBeGreaterThan(-1);
	expect(end).toBeGreaterThan(start);
	return source.slice(start, end);
})();

describe('the rule builder names the event', () => {
	it('prints the trigger key in the option, not its label', () => {
		// In sentence case: a raw `document.sent` was the one lowercase option in the builder.
		expect(eventSelect).toContain('<option value={t.key}>{sentenceCase(t.key)}</option>');
		expect(
			eventSelect.includes('{t.label}'),
			'the Event options are back to the friendly sentence'
		).toBe(false);
	});

	it('keeps the sentence and the raw event types beneath the box', () => {
		// Demoted, not deleted. The description is the only explanation of a trigger anywhere in the
		// builder, and `event_types` — the audit events a rule actually sits on — was exposed by the
		// API for this and rendered nowhere.
		expect(eventSelect).toContain('selectedTrigger.description');
		expect(eventSelect).toContain('selectedTrigger.event_types');
	});

	it('resolves the selected trigger without assuming a draft is open', () => {
		// `draft` is null whenever the panel is closed, and this derivation runs regardless.
		expect(source).toContain(
			'const selectedTrigger = $derived(draft ? triggerByKey.get(draft.trigger_key) : undefined);'
		);
	});
});
