import { describe, expect, it } from 'vitest';
import { asPreviewDocument } from './as-preview-document.js';

describe('asPreviewDocument', () => {
	it('wraps a fragment in a standards-mode document', () => {
		const out = asPreviewDocument('<table width="100%"><tr><td>Hi</td></tr></table>');
		expect(out.startsWith('<!DOCTYPE html>')).toBe(true);
		expect(out).toContain('<table width="100%"><tr><td>Hi</td></tr></table>');
	});

	it('leaves a full document alone', () => {
		const html = '<!DOCTYPE html><html><body><p>Hi</p></body></html>';
		expect(asPreviewDocument(html)).toBe(html);
	});
});
