/**
 * SSR pins for the signed W-9 / W-8 slot on the Compliance card's form: it appears only for a W-9
 * or a W-8, its dropzone posts the file with the card's own Save, and the file on record shows only
 * while the answer on the form is still the saved one.
 */
import { describe, expect, it } from 'vitest';
import type { ComponentProps } from 'svelte';
import { render } from 'svelte/server';
import FatcaDocumentField from './fatca-document-field.svelte';

const signed = { uid: 'doc_w9', name: 'w9-signed.pdf' };
const href = (uid: string) => `/documents/${uid}`;

const body = (props: ComponentProps<typeof FatcaDocumentField>) =>
	render(FatcaDocumentField, { props }).body;

describe('FatcaDocumentField (SSR)', () => {
	it('offers a dropzone named for the form under a W-9 or a W-8', () => {
		const w9 = body({ status: 'w9', savedStatus: null, document: null });
		expect(w9).toContain('Signed W-9');
		expect(w9).toContain('Upload the signed W-9');
		expect(w9).toContain('name="fatca_document"');
		expect(w9).toContain('data-slot="file-dropzone"');
		// One file: the slot replaces, it does not collect.
		expect(w9).not.toContain('multiple');

		const w8 = body({ status: 'w8', savedStatus: 'w8', document: null });
		expect(w8).toContain('Signed W-8');
		expect(w8).toContain('name="fatca_document"');
	});

	it('shows nothing for a blank answer or another stored code', () => {
		for (const status of ['', 'w8_cure']) {
			const html = body({ status, savedStatus: status, document: signed, href });
			expect(html, status).not.toContain('fatca_document');
			expect(html, status).not.toContain('Signed');
			expect(html, status).not.toContain('w9-signed.pdf');
		}
	});

	it('links the file on record while the answer on the form is the saved one', () => {
		const html = body({ status: 'w9', savedStatus: 'w9', document: signed, href });
		expect(html).toContain('href="/documents/doc_w9"');
		expect(html).toContain('w9-signed.pdf');
		expect(html).toContain('Replace the signed W-9');
	});

	it('names the file without a link where the app has no document route', () => {
		const html = body({ status: 'w9', savedStatus: 'w9', document: signed });
		expect(html).toContain('w9-signed.pdf');
		expect(html).not.toContain('href=');
	});

	it('drops the file on record once the answer changes, because the save unlinks it', () => {
		const html = body({ status: 'w8', savedStatus: 'w9', document: signed, href });
		expect(html).toContain('Signed W-8');
		expect(html).not.toContain('w9-signed.pdf');
		expect(html).toContain('Upload the signed W-8');
	});
});
