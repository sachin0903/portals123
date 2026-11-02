/**
 * The detail-pane value formatters. Pins that big/markup column values (a branding theme jsonb, a
 * GrapesJS email HTML body, a data: URI logo) are summarised for display — and that short, ordinary
 * values (ids, names, statuses) pass through untouched, so the common case is unchanged.
 */
import { describe, expect, it } from 'vitest';
import { capText, previewValue, shortenForJson, omitEmpty } from './format-value.js';

describe('capText', () => {
	it('leaves a short value untouched', () => {
		expect(capText('active')).toBe('active');
	});
	it('truncates a long value with a size hint', () => {
		const out = capText('x'.repeat(500));
		expect(out.startsWith('x'.repeat(140))).toBe(true);
		expect(out).toContain('chars');
		expect(out.length).toBeLessThan(200);
	});
	it('names a data: URI by mime + size instead of showing it', () => {
		const uri = `data:image/png;base64,${'A'.repeat(4000)}`;
		expect(capText(uri)).toBe('image/png data (4 KB)');
	});
});

describe('previewValue', () => {
	it('returns the empty glyph for null/undefined', () => {
		expect(previewValue(null)).toBe('∅');
		expect(previewValue(undefined)).toBe('∅');
	});
	it('shows a small object whole', () => {
		expect(previewValue({ a: 1 })).toBe('{"a":1}');
	});
	it('summarises a big object by shape + size, not a wall', () => {
		const theme = { colours: 'x'.repeat(300), logo: 'y'.repeat(300) };
		const out = previewValue(theme);
		expect(out).toContain('2 fields');
		expect(out).not.toContain('xxxxxxxxxx');
	});
	it('summarises a big array by count', () => {
		expect(previewValue(Array.from({ length: 50 }, (_, i) => i))).toContain('50 items');
	});
	it('caps a long scalar string', () => {
		expect(previewValue('z'.repeat(400))).toContain('chars');
	});
});

describe('shortenForJson', () => {
	it('shortens a data: URI deep in the structure but keeps the shape', () => {
		const out = shortenForJson({
			theme: { icon_url: `data:image/svg+xml;base64,${'A'.repeat(2000)}`, mode: 'dark' }
		}) as { theme: { icon_url: string; mode: string } };
		expect(out.theme.mode).toBe('dark');
		expect(out.theme.icon_url).toContain('image/svg+xml data');
		expect(out.theme.icon_url).not.toContain('AAAA');
	});
	it('truncates a long string but leaves short ones', () => {
		const out = shortenForJson({ body: 'h'.repeat(1000), name: 'Suitability' }) as {
			body: string;
			name: string;
		};
		expect(out.name).toBe('Suitability');
		expect(out.body).toContain('chars');
		expect(out.body.length).toBeLessThan(400);
	});
	it('keeps nulls (an old→new of null is meaningful)', () => {
		expect(shortenForJson({ changed: { x: { old: null, new: 'y' } } })).toEqual({
			changed: { x: { old: null, new: 'y' } }
		});
	});
});

describe('omitEmpty', () => {
	it('drops null/undefined top-level keys (the always-empty target_tenant_* noise)', () => {
		const out = omitEmpty({
			id: 1,
			tenant_name: 'Firm A',
			target_tenant_id: null,
			target_tenant_uid: null,
			tenant_icon_url: null
		});
		expect(out).toEqual({ id: 1, tenant_name: 'Firm A' });
	});
	it('also drops explicitly named keys', () => {
		expect(omitEmpty({ a: 1, b: 2 }, ['b'])).toEqual({ a: 1 });
	});
});
