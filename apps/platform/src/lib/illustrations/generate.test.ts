import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { filedDocumentPath, type GenerateResult } from './generate';

const here = dirname(fileURLToPath(import.meta.url));

const filed = (over: Partial<GenerateResult> = {}): GenerateResult => ({
	document_uid: 'doc_abc',
	stored: true,
	placeholder: false,
	placeholder_reason: null,
	request_xml: null,
	errors: [],
	...over
});

describe('filedDocumentPath', () => {
	it('names the client-scoped document record', () => {
		expect(filedDocumentPath('con_9b2e', filed())).toBe('/clients/con_9b2e/documents/doc_abc');
	});

	it('is null when the PDF was produced but not filed', () => {
		// Nothing to open: the engine answered and storage did not. The form stays up so the
		// adviser can see that, rather than being sent to a 404.
		expect(filedDocumentPath('con_9b2e', filed({ stored: false }))).toBeNull();
	});

	it('is null when a stored flag arrives without a document uid', () => {
		expect(filedDocumentPath('con_9b2e', filed({ document_uid: null }))).toBeNull();
	});

	it('keeps indicative output and engine errors in the review panel', () => {
		expect(filedDocumentPath('con_9b2e', filed({ placeholder: true }))).toBeNull();
		expect(
			filedDocumentPath('con_9b2e', filed({ errors: ['Projection used a fallback.'] }))
		).toBeNull();
	});
});

describe('after a filed generate, the form gets out of the way', () => {
	const drawer = readFileSync(join(here, 'illustration-drawer.svelte'), 'utf8');
	// The route file is a `Streamed` wrapper now; the form and its submit live in the composer
	// beside it, which is what these rules are about.
	const composer = readFileSync(
		join(here, '../../routes/clients/[uid]/illustrations/new/illustration-composer.svelte'),
		'utf8'
	);

	it('the drawer disables Generate for the whole trip, then opens the document', () => {
		// A paid engine: the button is disabled from the click (`if (busy) return` is the
		// double-click that lands before the re-render), Escape/outside-click cannot dismiss and
		// reset it mid-request, and it stays disabled through the navigation.
		expect(drawer).toMatch(/if \(busy\) return/);
		expect(drawer).toMatch(/disabled=\{busy\}/);
		expect(drawer).toContain('const dismissLocked = $derived(submitting && !navigating)');
		expect(drawer).toMatch(/if \(!next && dismissLocked\) \{\s*open = true;\s*return;/);
		expect(drawer).toMatch(/function reset\(\) \{\s*if \(busy\) return;/);
		expect(drawer).toContain("escapeKeydownBehavior={dismissLocked ? 'ignore' : 'close'}");
		expect(drawer).toContain("interactOutsideBehavior={dismissLocked ? 'ignore' : 'close'}");
		expect(drawer).toContain('showCloseButton={!dismissLocked}');
		expect(drawer).toContain('navigating = true');
		expect(drawer).toContain('open = false');
		expect(drawer).toContain('filedDocumentPath');
		expect(drawer).toContain('await tick()');
		expect(drawer).toContain('await goto(href)');
	});

	it('does not refresh the client record before opening the document', () => {
		// `invalidateAll` on this page races goto and wins: the drawer shuts and the adviser stays
		// on the client. The host used to fire it from `ongenerated` before the navigate.
		const fromHref = drawer.indexOf('const href = filedDocumentPath');
		const toStay = drawer.indexOf('ongenerated?.(generated)');
		expect(fromHref).toBeGreaterThan(-1);
		expect(toStay).toBeGreaterThan(fromHref);
		const navigate = drawer.slice(fromHref, toStay);
		expect(navigate).toContain('await goto(href)');
		expect(navigate).toContain('return;');
		expect(navigate).not.toContain('ongenerated');
		expect(navigate).not.toContain('invalidateAll');
		const mount = readFileSync(
			join(here, '../components/clients/client-record-page.svelte'),
			'utf8'
		);
		const drawerMount = mount.slice(mount.indexOf('<IllustrationDrawer'));
		expect(drawerMount).not.toContain('invalidateAll');
	});

	it('the full-page composer takes the same door', () => {
		expect(composer).toMatch(/if \(busy\) return/);
		expect(composer).toMatch(/disabled=\{busy\}/);
		expect(composer).toContain('filedDocumentPath');
		expect(composer).toContain('await goto(href)');
	});

	it('both surfaces recover to the filed outcome if navigation fails', () => {
		for (const source of [drawer, composer]) {
			expect(source).toMatch(/catch \{[\s\S]*?navigating = false;/);
			expect(source).toContain('The illustration was filed, but its document could not be opened.');
			expect(source).toContain('result = generated');
			expect(source).toContain('submitting = false');
		}
	});
});
