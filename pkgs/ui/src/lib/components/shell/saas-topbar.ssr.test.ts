/**
 * SSR pins for the topbar's two independent controls.
 *
 * The trail says where a page SITS; the back arrow says where the reader CAME FROM. They answer
 * different questions and render together — an earlier cut of this had the arrow replace the trail,
 * which threw away the map to show the history. The "trail alone" case is the guard against an
 * admin regression: the admin portal and the branding preview pass no `back` and must render
 * exactly as they always have.
 */
import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import SaasTopbar from './saas-topbar.svelte';

const trail = [{ label: 'Tasks', href: '/tasks' }, { label: 'Chase the LOA' }];
const back = { href: '/', label: 'Dashboard' };

describe('SaasTopbar', () => {
	it('renders the back arrow beside the trail, not instead of it', () => {
		const { body } = render(SaasTopbar, { props: { back, breadcrumb: trail } });
		expect(body).toContain('aria-label="Back to Dashboard"');
		expect(body).toContain('href="/"');
		expect(body).toContain('aria-label="breadcrumb"');
		expect(body).toContain('Tasks');
		expect(body).toContain('Chase the LOA');
	});

	it('names the arrow by its destination rather than spelling it out beside the trail', () => {
		// Icon only: "Back to Clients | Clients / Mr Max Oakden" would repeat most of the trail. The
		// destination has to survive somewhere a screen reader can reach it.
		const { body } = render(SaasTopbar, { props: { back, breadcrumb: trail } });
		expect(body).toContain('aria-label="Back to Dashboard"');
		expect(body).not.toContain('>Back to Dashboard<');
	});

	it('renders the trail alone when there is no history to offer', () => {
		// The admin portal, the branding preview, and the platform on a hard reload.
		const { body } = render(SaasTopbar, { props: { breadcrumb: trail } });
		expect(body).toContain('aria-label="breadcrumb"');
		expect(body).toContain('Tasks');
		expect(body).not.toContain('Back to');
	});

	it('renders the arrow alone when a page has history but no trail', () => {
		const { body } = render(SaasTopbar, { props: { back } });
		expect(body).toContain('aria-label="Back to Dashboard"');
		expect(body).not.toContain('aria-label="breadcrumb"');
	});

	it('renders neither when the page has no back target and no trail', () => {
		const { body } = render(SaasTopbar, { props: {} });
		expect(body).not.toContain('Back to');
		expect(body).not.toContain('aria-label="breadcrumb"');
	});

	it('typesets the last crumb as the page title, since no page renders a heading', () => {
		// The listings dropped their PageHeader, so this is the only thing naming the page. A trail
		// of uniformly muted `text-sm` gave the page's own name no more weight than the section
		// above it.
		const { body } = render(SaasTopbar, { props: { breadcrumb: trail } });
		const leaf = body.slice(body.indexOf('breadcrumb-page'));
		expect(leaf).toContain('text-base');
		expect(leaf).toContain('font-semibold');
	});

	it('keeps the page name on a phone and collapses only the ancestors', () => {
		// While a heading existed, hiding the whole trail below `sm` cost the map but kept the name.
		// With no heading it left the page anonymous, so the leaf must never be the thing that hides.
		const { body } = render(SaasTopbar, { props: { breadcrumb: trail } });
		const ancestor = body.slice(body.indexOf('breadcrumb-item'), body.indexOf('breadcrumb-page'));
		expect(ancestor).toContain('sm:inline-flex');
		expect(body.slice(body.indexOf('breadcrumb-page'))).not.toContain('hidden');
	});
});
