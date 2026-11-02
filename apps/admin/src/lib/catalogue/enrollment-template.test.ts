/**
 * The enrolment-template picker lives on the products settings sheet. The SEI firm is
 * already on the tenant's platform enablement — this list is that firm's templates only.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { enrollmentTemplatesForFirm } from './enrollment';
import type { ProviderOption } from './types';

const SOURCE = 'src/lib/catalogue/products-section.svelte';

const option = (value: string, parent: string | null, label = value): ProviderOption => ({
	value,
	label,
	hint: null,
	parent
});

describe('enrollmentTemplatesForFirm', () => {
	const templates = [
		option('tmpl-3102-isa', '3102', 'AIM ISA'),
		option('tmpl-3102-sipp', '3102', 'AIM SIPP'),
		option('tmpl-3106-isa', '3106', 'Other ISA'),
		option('tmpl-orphan', null, 'Unattributed')
	];

	it("keeps only the templates whose parent is this tenant's SEI firm", () => {
		expect(enrollmentTemplatesForFirm(templates, '3102').map((row) => row.value)).toEqual([
			'tmpl-3102-isa',
			'tmpl-3102-sipp'
		]);
	});

	it('is empty when the tenant has not enabled SEI', () => {
		expect(enrollmentTemplatesForFirm(templates, null)).toEqual([]);
	});

	it("does not treat an unattributed template as this firm's", () => {
		expect(enrollmentTemplatesForFirm(templates, '3102').some((row) => row.parent == null)).toBe(
			false
		);
	});
});

describe('the products page load', () => {
	const source = readFileSync('src/routes/tenants/[uid]/platform/products/+page.server.ts', 'utf8');

	it("filters templates by the tenant's enabled SEI firm_id", () => {
		expect(source).toContain('enrollmentTemplatesForFirm');
		expect(source).toContain('config.firm_id');
		expect(source).not.toContain('seiFirms');
	});
});

describe('the SEI enrolment template picker', () => {
	const source = readFileSync(SOURCE, 'utf8');

	it('is a dropdown on the products settings sheet', () => {
		expect(source).toContain('id="product-sei-template"');
		expect(source).toContain('name="account_open_reference"');
		expect(source).toContain('Select.Root');
	});

	it("does not offer a firm picker — the firm is the tenant's enabled SEI connection", () => {
		expect(source).not.toContain('id="product-sei-firm"');
		expect(source).not.toContain('seiFirms');
		expect(source).not.toContain('selectedSeiFirm');
	});

	it('does not fall back to typing a template id', () => {
		expect(source).not.toMatch(/id="product-sei-template"[\s\S]*?<Input/);
	});

	it('does not portal the menu (the form lives in a Sheet)', () => {
		expect(source).toMatch(/portalProps=\{\{\s*disabled:\s*true\s*\}\}/);
	});

	it('records that a SEI row already exists, so an unassigned template does not create one', () => {
		expect(source).toContain('name="sei_mapped"');
		const actions = readFileSync('src/lib/catalogue/actions.ts', 'utf8');
		expect(actions).toContain('hadSeiMapping');
		expect(actions).toContain('reference !== null || hadSeiMapping');
	});
});
