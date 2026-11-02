import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * Two rules the rules LISTING has to keep, asserted over the source: the component needs a live
 * `Switch`, a catalogue and a save endpoint, so a render here would only ever show the shell.
 *
 * Both came out of a QA pass over the whole notification surface.
 */
const source = readFileSync(join(import.meta.dirname, 'notification-rules.svelte'), 'utf8').replace(
	/<!--[\s\S]*?-->/g,
	''
);

describe('a row says WHICH email it sends', () => {
	it('names the design on the row, not only inside the edit sheet', () => {
		// "Does this send an email, and which one" is the question the task-notice consolidation was
		// about. A row with an Email switch and nothing else cannot answer it without opening every
		// rule in turn — which is what a QA pass reported having to do.
		expect(source).toMatch(/function templateLabel\(code: string \| null\)/);
		expect(source).toMatch(/Email design:/);
		expect(source).toMatch(/\{@const design = templateLabel\(row\.email_code\)\}/);
	});

	it('names the generic wrapper rather than leaving a blank code implied', () => {
		// A blank `email_code` means the shared design — a real answer, and the one an author is most
		// likely to want to change.
		expect(source).toMatch(/design \?\? 'General notification'/);
	});

	it('says it only where an email is actually sent', () => {
		expect(source).toMatch(/\{#if row\.email\}/);
	});
});

describe('Load defaults is offered once', () => {
	it('is withheld from the header while the list is empty', () => {
		// The empty state owns the action then — it is the only thing on screen and it explains what
		// defaults are. Two buttons for one act is how a QA pass came to click one and see nothing.
		expect(source).toMatch(/\{#if onLoadDefaults && displayRows\.length\}/);
	});

	it('still offers it from the empty state, which is the other half of that', () => {
		// The gate is on the `action` PROP, not an `{#if}` inside the snippet: a snippet is always
		// truthy, so EmptyState's own `{#if action}` fires and it paints an empty wrapper whenever
		// the inner condition is false. This used to assert the inner spelling.
		expect(source).toMatch(
			/action=\{canManage && onLoadDefaults \? loadDefaultsAction : undefined\}/
		);
		// Both doors run the same handler, so they cannot behave differently.
		expect(source.match(/onclick=\{loadDefaults\}/g)?.length).toBe(2);
	});
});
