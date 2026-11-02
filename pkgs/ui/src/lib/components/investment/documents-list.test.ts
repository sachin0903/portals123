import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import DocumentsList from './documents-list.svelte';
import type { ClientDocument, DocumentCategory } from './types.js';

const doc = (id: string, category: string, name: string): ClientDocument => ({
	id,
	// The wire type says DocumentCategory, but the server can drift ahead of this build —
	// exactly the case under test.
	category: category as DocumentCategory,
	name,
	date: '5 Apr 2026',
	size: '412 KB',
	format: 'PDF'
});

const known = [
	doc('stmt-1', 'Statements', 'Quarterly Statement'),
	doc('corr-1', 'Correspondence', 'Suitability Report')
];
const junk = doc('mystery-1', 'Compliance Pack', 'MiFID Costs Bundle');

describe('DocumentsList (SSR)', () => {
	it("files an unknown category under 'Other', rendered last in the grouped view", () => {
		// Unknown categories used to vanish from the grouped view entirely — server drift
		// silently hid documents from the client.
		const body = render(DocumentsList, { props: { documents: [...known, junk] } }).body;
		expect(body).toContain('Other');
		expect(body).toContain('MiFID Costs Bundle');
		expect(body.indexOf('Other')).toBeGreaterThan(body.indexOf('Correspondence'));
	});

	it("shows no 'Other' bucket when every category is known", () => {
		const body = render(DocumentsList, { props: { documents: known } }).body;
		expect(body).not.toContain('Other');
	});

	it('renders an unknown category in the flat view instead of throwing', () => {
		// The flat view rendered `icons[doc.category]` — undefined for a drifted category, which
		// threw at render. The fallback icon keeps the row alive.
		const body = render(DocumentsList, {
			props: { documents: [...known, junk], grouped: false }
		}).body;
		expect(body).toContain('MiFID Costs Bundle');
		expect(body).toContain('Compliance Pack · 5 Apr 2026');
	});

	it('sheds row details by CONTAINER width, not viewport (no sm: variants left)', () => {
		// The size/format details used sm: viewport variants — wrong in both directions when the
		// block's grid span diverges from the screen (see the table components).
		for (const grouped of [true, false]) {
			const body = render(DocumentsList, { props: { documents: known, grouped } }).body;
			expect(body).toContain('@container');
			expect(body).toContain('@lg:inline');
			expect(body).not.toMatch(/[^@a-z-]sm:/);
		}
	});
});
