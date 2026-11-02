import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

describe('DocumentUploadDialog', () => {
	it('is a reusable callback-driven document uploader', () => {
		const source = readFileSync(
			'src/lib/components/book/documents/document-upload-dialog.svelte',
			'utf8'
		);
		expect(source).toContain('onupload: (input:');
		expect(source).toContain('Upload documents');
		expect(source).toContain("value: 'file', label: 'Supporting file'");
		expect(source).not.toContain('$app/');
		expect(source).not.toContain('?/upload');
	});
});
