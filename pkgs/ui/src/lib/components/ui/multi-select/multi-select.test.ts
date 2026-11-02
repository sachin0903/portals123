import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const source = readFileSync(
	fileURLToPath(new URL('./multi-select.svelte', import.meta.url)),
	'utf8'
);

describe('MultiSelect', () => {
	it('can render its panel inline so a Dialog focus trap still receives clicks', () => {
		// Popover.Content portals to document.body by default. Inside a Dialog or Sheet that
		// panel sits outside the modal's focus trap and never receives a click — the reason the
		// vulnerability form used to be 24 checkboxes. `portal={false}` disables the portal.
		expect(source).toContain('portal = true');
		expect(source).toContain('portalProps={portal ? undefined : { disabled: true }}');
	});
});
