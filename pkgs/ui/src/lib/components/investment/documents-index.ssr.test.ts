import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import DocumentsIndex, { groupDocuments } from './documents-index.svelte';
import type { ClientDocument, DocumentCategory } from './types.js';

/**
 * SSR pins for the index documents look: the register's category headers (same buckets +
 * 'Other' drift rule as documents-list) and the ruled rows' fields.
 */

const doc = (id: string, category: string, name: string): ClientDocument => ({
	id,
	// The wire type says DocumentCategory, but the server can drift ahead of this build.
	category: category as DocumentCategory,
	name,
	date: '5 Apr 2026',
	size: '412 KB',
	format: 'PDF'
});

const known = [
	doc('stmt-1', 'Statements', 'Quarterly Statement'),
	doc('agr-1', 'Agreements', 'Terms of Business'),
	doc('corr-1', 'Correspondence', 'Suitability Report')
];
const junk = doc('mystery-1', 'Compliance Pack', 'MiFID Costs Bundle');

describe('groupDocuments', () => {
	it('buckets in the canonical order with unknown categories filed last under Other', () => {
		const groups = groupDocuments([...known, junk]);
		expect(groups.map((g) => g.category)).toEqual([
			'Statements',
			'Agreements',
			'Correspondence',
			'Other'
		]);
		expect(groups.at(-1)!.items).toEqual([junk]);
	});

	it('emits no Other bucket when every category is known', () => {
		expect(groupDocuments(known).map((g) => g.category)).not.toContain('Other');
	});
});

describe('DocumentsIndex (SSR)', () => {
	it("renders small-caps category headers including 'Other', last", () => {
		const body = render(DocumentsIndex, { props: { documents: [...known, junk] } }).body;
		expect(body).toContain('Other');
		expect(body).toContain('MiFID Costs Bundle');
		expect(body.indexOf('Other')).toBeGreaterThan(body.indexOf('Correspondence'));
		expect(body).toContain('uppercase');
	});

	it('each row carries name, date, size and the format tag, linked to the document', () => {
		const body = render(DocumentsIndex, { props: { documents: known } }).body;
		expect(body).toContain('href="/documents/stmt-1"');
		expect(body).toContain('Quarterly Statement');
		expect(body).toContain('5 Apr 2026');
		expect(body).toContain('412 KB');
		expect(body).toContain('PDF');
	});

	it('is the ruled register — divided hairline rows, size shed by container width', () => {
		const body = render(DocumentsIndex, { props: { documents: known } }).body;
		expect(body).toContain('divide-y');
		expect(body).toContain('@container');
		expect(body).toContain('@lg:inline');
		// Zero chrome: no panels, no badges.
		expect(body).not.toContain('glass-panel');
	});
});
