import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const source = readFileSync(new URL('./+page.svelte', import.meta.url), 'utf8');

describe('terms editor submissions', () => {
	it('does not disable a submitter from its click handler before native submission', () => {
		// Svelte flushes the state change quickly enough for `disabled` to cancel the button's
		// default submit. Pending state must begin inside use:enhance, after submission exists.
		expect(source).not.toMatch(/onclick=\{\(\) => \(submitting = '(save|publish|upload)'\)\}/);
		expect(source).toContain('submitting = operation;');
	});

	it('uploads a selected PDF without a second visible action', () => {
		expect(source).toContain('editorForm?.requestSubmit(uploadSubmitter)');
		expect(source).toMatch(/bind:this=\{uploadSubmitter\}[\s\S]*?class="hidden"/);
		expect(source).toContain('Uploading PDF…');
	});

	it('shows the attached draft PDF in the editor', () => {
		expect(source).toContain('<DocumentViewer');
		expect(source).toContain('src="/terms/{item.uid}/file"');
		expect(source).toContain("contentType={item.file_content_type ?? 'application/pdf'}");
	});
});
