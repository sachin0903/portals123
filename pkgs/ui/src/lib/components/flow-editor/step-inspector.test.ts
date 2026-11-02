import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const source = readFileSync(new URL('./StepInspector.svelte', import.meta.url), 'utf8');

describe('accept_terms inspector', () => {
	it('lets the author write a declaration or choose a PDF', () => {
		expect(source).toContain('Written declaration');
		expect(source).toContain('PDF document');
		expect(source).toContain("setCfg('content'");
		expect(source).toContain('Markdown is supported');
	});
});

describe('verify_identity inspector', () => {
	it('picks the provider from the closed list, not free text', () => {
		// `providerItems` (inspectors/shared.ts) is what supplies the options — both the fixed pair
		// and a retired stored value — so the picker never lets an author type a vendor that isn't
		// one of them, and never loses one that already is.
		expect(source).toContain("providerItems(cfg('provider'))");
		expect(source).toContain("value={cfg('provider') || 'manual'}");
		expect(source).toContain("onValueChange={(v) => setCfg('provider', v)}");
	});
});

describe('aml_check inspector', () => {
	it('has no settings of its own — an AML step is always screened by itself, and a pass does not lapse', () => {
		// No branch at all for aml_check, the same way upload_document and setup_account render nothing.
		expect(source).not.toContain("step.type === 'aml_check'");
	});
});

describe('screening steps', () => {
	it('show no Valid days field — an identity or AML pass does not lapse', () => {
		expect(source).not.toContain('Valid days');
		expect(source).not.toContain('validity_days');
	});
});
