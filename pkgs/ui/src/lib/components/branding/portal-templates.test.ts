import { describe, expect, it } from 'vitest';
import {
	assignTemplateClassifications,
	assignTemplateGroups,
	assignTemplateRoles,
	duplicateTemplate,
	previewTemplateBranding,
	templateFromBranding,
	withWorkingPages
} from './portal-templates.js';

describe('portal design sets', () => {
	it('creates an independent design from the current portal', () => {
		const source = {
			logo_url: 'https://assets.example/logo.svg',
			colors: { light: { primary: '#123456' } },
			pages: null
		};
		const template = templateFromBranding(source, 'Family Office');
		expect(template.id).toBe('family_office');
		expect(template.pages?.length).toBeGreaterThan(1);
		expect(template).not.toHaveProperty('logo_url');
		expect(template.colors).not.toBe(source.colors);
	});

	it('carries card designs with the design set, independently', () => {
		const source = { card_designs: { ISA: { finish: 'paper' as const } } };
		const template = templateFromBranding(source, 'HNW');
		expect(template.card_designs).toEqual({ ISA: { finish: 'paper' } });
		expect(template.card_designs).not.toBe(source.card_designs);
		const preview = previewTemplateBranding(
			{ card_designs: { ISA: { finish: 'ink' as const } } },
			{ id: 'd2c', name: 'D2C' }
		);
		expect(preview.card_designs).toBeUndefined();
	});

	it('keeps each classification on one design set', () => {
		const templates = [
			{ id: 'advised', name: 'Advised', classification_slugs: ['advised', 'standard'] },
			{ id: 'd2c', name: 'D2C', classification_slugs: ['d2c'] }
		];
		const next = assignTemplateClassifications(templates, 'd2c', ['d2c', 'standard']);
		expect(next[0]?.classification_slugs).toEqual(['advised']);
		expect(next[1]?.classification_slugs).toEqual(['d2c', 'standard']);
	});

	it('keeps each client group on one design set', () => {
		const templates = [
			{ id: 'workplace', name: 'Workplace', group_slugs: ['workplace', 'charity'] },
			{ id: 'charity', name: 'Charity', group_slugs: [] }
		];
		const next = assignTemplateGroups(templates, 'charity', ['charity']);
		expect(next[0]?.group_slugs).toEqual(['workplace']);
		expect(next[1]?.group_slugs).toEqual(['charity']);
	});

	it('keeps each role on one design set', () => {
		const templates = [
			{ id: 'standard', name: 'Standard', role_slugs: ['client', 'trustee'] },
			{ id: 'family', name: 'Family', role_slugs: ['family-client'] }
		];
		const next = assignTemplateRoles(templates, 'family', ['trustee', 'family-client']);
		expect(next[0]?.role_slugs).toEqual(['client']);
		expect(next[1]?.role_slugs).toEqual(['trustee', 'family-client']);
	});

	it('duplicates appearance without duplicating role assignments', () => {
		const copy = duplicateTemplate(
			{
				id: 'family',
				name: 'Family',
				role_slugs: ['trustee'],
				colors: { light: { primary: '#123456' } }
			},
			['family', 'family_copy']
		);
		expect(copy.id).toBe('family_copy_2');
		expect(copy.role_slugs).toEqual([]);
		expect(copy.classification_slugs).toEqual([]);
		expect(copy.colors?.light?.primary).toBe('#123456');
	});

	it('previews template appearance while retaining shared firm identity', () => {
		const preview = previewTemplateBranding(
			{
				logo_url: 'https://assets.example/logo.svg',
				colors: { light: { primary: '#111111' } },
				finish: 'ledger'
			},
			{
				id: 'standard',
				name: 'Standard',
				colors: { light: { primary: '#225588' } }
			}
		);
		expect(preview.logo_url).toBe('https://assets.example/logo.svg');
		expect(preview.colors?.light?.primary).toBe('#225588');
		expect(preview.finish).toBeUndefined();
	});

	it('stamps working pages onto the selected design and the default root', () => {
		const pages = [
			{
				id: 'home',
				slug: 'home',
				label: 'Home',
				layouts: { default: [{ id: 'accounts', type: 'accounts' }] }
			}
		];
		const next = withWorkingPages(
			{
				pages: null,
				default_template_id: 'trustees',
				templates: [
					{ id: 'trustees', name: 'Trustees', role_slugs: ['trustee'] },
					{ id: 'family', name: 'Family', role_slugs: ['family-client'] }
				]
			},
			pages,
			'trustees'
		);
		expect(next.pages).toEqual(pages);
		expect(next.templates?.[0]?.pages).toEqual(pages);
		expect(next.templates?.[1]?.pages).toBeUndefined();
	});

	it('leaves the root page set alone when editing a non-default design', () => {
		const pages = [
			{
				id: 'home',
				slug: 'home',
				label: 'Home',
				layouts: { default: [{ id: 'holdings', type: 'holdings' }] }
			}
		];
		const next = withWorkingPages(
			{
				pages: null,
				default_template_id: 'trustees',
				templates: [
					{ id: 'trustees', name: 'Trustees' },
					{ id: 'family', name: 'Family' }
				]
			},
			pages,
			'family'
		);
		expect(next.pages).toBeNull();
		expect(next.templates?.[1]?.pages).toEqual(pages);
		expect(next.templates?.[0]?.pages).toBeUndefined();
	});

	it('does not freeze an empty working set (untouched studio keeps pages null)', () => {
		const client = { pages: null, templates: [{ id: 'trustees', name: 'Trustees' }] };
		expect(withWorkingPages(client, null, 'trustees')).toBe(client);
		expect(withWorkingPages(client, [], 'trustees')).toBe(client);
	});
});
