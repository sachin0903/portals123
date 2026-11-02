import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { defaultComposeFields } from './default-content.js';

/** "Load default" fills the compose strip too, not just the canvas — the API returns `preheader` for every shipped default and the editor used to read only `subject`, silently publishing no inbox preview text. Scans the real component source rather than calling the function alone, because `onLoadDefault` fires from a dynamic-imported GrapesJS click that can't be driven in node, and `preheader` appearing anywhere in the file would pass on the broken version too. */
const page = readFileSync(join(import.meta.dirname, 'email-template-editor-page.svelte'), 'utf8');

describe('defaultComposeFields', () => {
	it('returns BOTH the subject and the preheader of the shipped default', () => {
		expect(
			defaultComposeFields({
				subject: 'You’re invited to {{ brand.name }}',
				preheader: 'Set a password to activate your account.',
				html: '<p>hi</p>',
				css: ''
			})
		).toEqual({
			subject: 'You’re invited to {{ brand.name }}',
			preheader: 'Set a password to activate your account.'
		});
	});

	it('returns null when there is no shipped default', () => {
		// A custom code 404s the endpoint, so null means the affordance isn't offered and the author's own copy is left alone.
		expect(defaultComposeFields(null)).toBeNull();
		expect(defaultComposeFields(undefined)).toBeNull();
	});

	it('turns an absent preheader into an empty line, never the string "undefined"', () => {
		// A portal on an API older than this field would otherwise put the literal "undefined" into the input and mail it as the preview.
		const legacy = { subject: 'S', html: '<p>hi</p>', css: '' } as Parameters<
			typeof defaultComposeFields
		>[0];
		expect(defaultComposeFields(legacy)).toEqual({ subject: 'S', preheader: '' });
	});
});

describe('the editor page applies it', () => {
	it('writes both compose fields from the rule, and binds each input to that state', () => {
		expect(page).toMatch(
			/onLoadDefault=\{\(\) => \{[\s\S]*?defaultComposeFields\(defaultContent\)/
		);
		expect(page).toMatch(/subject = fields\.subject;\s*preheader = fields\.preheader;/);
		// Matched across the element, not adjacent lines: the inputs sit in the email's own head with a `form="et-save"` attribute between them now. `[^>]*` can't leave the opening tag, so this still proves the binding is on THAT input.
		expect(page).toMatch(/name="subject"[^>]*bind:value=\{subject\}/);
		expect(page).toMatch(/name="preheader"[^>]*bind:value=\{preheader\}/);
	});

	it('overwrites, deliberately, and treats the two fields alike', () => {
		// Both fields overwrite unconditionally, deliberately — no "only if empty" guard on either; a future guard belongs on BOTH.
		expect(page).not.toMatch(/if \(!subject\)/);
		expect(page).not.toMatch(/if \(!preheader\)/);
	});
});
