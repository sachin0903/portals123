import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

describe('client onboarding terms', () => {
	const source = readFileSync(new URL('./+page.svelte', import.meta.url), 'utf8');

	it('indexes the open declaration by definition order, including agreed ones', () => {
		expect(source).toContain('termsAgreeIndex(termsAll, agreeStep?.key)');
		const termsFilter = source.slice(
			source.indexOf('const termsAll'),
			source.indexOf('const agreeIndex')
		);
		expect(termsFilter).toContain("s.step_type === 'accept_terms'");
		expect(termsFilter).not.toContain('s.request_item_uid');
	});

	it('closes the identity dialog when the client asks for a person', () => {
		expect(source).toMatch(/await refreshJourney\(\);[\s\S]*?finally \{[\s\S]*?idvOpen = false/);
	});

	it('seeds each manual-evidence file binding before rendering the document rows', () => {
		const loader = source.slice(
			source.indexOf('async function loadIdentityDocuments'),
			source.indexOf('async function uploadIdentityDoc')
		);
		expect(loader).toContain('idvDocFiles[document.item_uid] ?? []');
		expect(loader.indexOf('idvDocFiles =')).toBeLessThan(loader.indexOf('idvDocs = documents'));
	});

	it('clips the terms PDF instead of letting zoom widen the dialog', () => {
		expect(source).toContain('zoomable={false}');
		expect(source).toContain('overflow-hidden');
	});

	it('does not highlight a single next form when several fact-finds are ready', () => {
		expect(source).toContain('readyForms.length > 1 ? null');
	});

	it('treats a successful load with no blob as an acknowledgement, not a failed PDF', () => {
		expect(source).toContain('This is an acknowledgement');
		expect(source).toContain('disabled={!agreeDoc || !agreeChecked || agreeing || agreeLoading}');
	});
});
