/**
 * Nationality and tax residency are this control. The mark beside an option is the shared
 * MultiSelect's tick; a checkbox here is a second multi-select.
 */
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const source = readFileSync(new URL('./multi-select-input.svelte', import.meta.url), 'utf8');
const multiSelect = readFileSync(
	new URL('../../components/ui/multi-select/multi-select.svelte', import.meta.url),
	'utf8'
);

describe('form multi-select', () => {
	it('is the shared MultiSelect', () => {
		expect(source).toContain("from '../../components/ui/multi-select/index.js'");
		expect(source).toContain('<MultiSelect');
		expect(source).not.toContain('Checkbox');
	});

	it('marks a chosen option with a tick', () => {
		expect(multiSelect).toContain('<CheckIcon');
		expect(multiSelect).toContain('[&>.cn-command-item-indicator]:hidden');
	});
});
