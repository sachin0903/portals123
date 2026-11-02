/**
 * The Change model drawer’s shape. Source-scanned because a Sheet’s body is portalled and
 * `render()` returns empty markup — same posture as `instruction-sheet.test.ts`.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const source = readFileSync(
	fileURLToPath(new URL('./model-sheet.svelte', import.meta.url)),
	'utf8'
);
const template = source
	.slice(source.indexOf('</script>'))
	.replace(/<!--[\s\S]*?-->/g, '')
	.replace(/\/\/[^\n]*/g, '');

describe('the model drawer', () => {
	it('is a side drawer at the money-movement width', () => {
		expect(template).toContain('<Sheet.Root');
		expect(template).toContain('side="right"');
		expect(template).toContain('sm:max-w-xl');
		expect(template).not.toContain('Dialog');
	});

	it('offers No model so a mandate can be cleared', () => {
		expect(source).toContain("{ value: '', label: NO_MODEL }");
	});

	it('asks why only when the firm requires a reason', () => {
		expect(template).toContain('justificationRequired');
		expect(template).toContain('Why this change');
		expect(source).toContain('justification');
	});

	it('does not portal the picker — a Sheet would steal the click', () => {
		expect(template).toContain('portal={false}');
	});

	it('seeds the draft from the account when the host opens it', () => {
		// `bind:open` from the host does not fire `onOpenChange`, so the drawer opened with an empty
		// draft and the save button armed to clear the mandate.
		expect(source).toMatch(/\$effect\(\(\) => \{\s*if \(open\) untrack\(reset\);/);
	});

	it('states the current model before offering a new one', () => {
		expect(template).toContain('data-testid="current-model"');
		expect(template).toContain('>Current model<');
		expect(template).toContain('>New model<');
		expect(template.indexOf('data-testid="current-model"')).toBeLessThan(
			template.indexOf('<SelectMenu')
		);
	});

	it('says what the save will do with both names, and the button names the act', () => {
		expect(template).toContain('data-testid="model-change"');
		expect(template).toContain('{summary}');
		expect(template).toContain('line-through');
		expect(source).toContain('modelChangeSummary(currentName, nextName, changed)');
		expect(source).toContain('modelChangeAction(currentName, nextName)');
		expect(template).toContain("{saving ? 'Saving…' : action}");
	});
});
