/** SSR pins for the listing, duplicated per portal — anything hardcoded here is wrong for one mount. Every href comes from the injected `links` builder (`/email-templates/{uid}/edit` firm vs `/tenants/{uid}/email-templates/{uid}/edit` ops — a literal 404s on one, invisible to svelte-check); `actions` absent renders no action, keeping `email_templates:write` (platform) and `model:write`/`tenants:write` (admin) app-side. */
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import EmailTemplatesListing from './email-templates-listing.svelte';
import type { EmailTemplateRow, EmailTemplatesListingActions } from './types';

/** The kebab menu and both dialogs are bits-ui portals: closed, they render NOTHING server-side, so `not.toContain('Delete permanently')` would pass whether or not the gate exists — these rules are pinned on the source instead, comments stripped first since they quote the very strings being scanned for. */
const SOURCE = readFileSync(new URL('./email-templates-listing.svelte', import.meta.url), 'utf8')
	.replace(/<!--[\s\S]*?-->/g, '')
	.replace(/\/\*[\s\S]*?\*\//g, '')
	.replace(/^\s*\/\/.*$/gm, '');

const row = (over: Partial<EmailTemplateRow> = {}): EmailTemplateRow => ({
	uid: 'eml_1',
	code: 'invite',
	name: 'Client invitation',
	description: null,
	version_count: 1,
	current_version_number: 1,
	current_version_status: 'draft',
	...over
});

const catalog = [
	{
		code: 'invite',
		label: 'Invitation',
		category: 'transactional',
		description: 'The first email.'
	}
];

const links = {
	edit: (uid: string) => `/email-templates/${uid}/edit`,
	versions: (uid: string) => `/email-templates/${uid}/versions`
};

const writes: EmailTemplatesListingActions = {
	enhance: () => ({ destroy() {} }),
	deserialize: () => ({ type: 'success' as const }),
	create: '?/create',
	update: '?/update',
	delete: '?/delete',
	onmutated: () => {},
	navigate: () => {}
};

const base = {
	title: 'Email templates',
	description: 'Any email you haven’t published here is sent with Graphene’s default.',
	deleteConsequence: 'Any notification that used it goes out with Graphene’s own design.',
	links,
	// Every case below states its own rows; an empty list is the default so the base is a complete
	// prop set rather than one the spread has to finish.
	templates: [] as EmailTemplateRow[]
};

const body = (props: Record<string, unknown>) =>
	render(EmailTemplatesListing, { props: { ...base, ...props } }).body;

describe('EmailTemplatesListing — links are injected', () => {
	it('builds every row href from `links`, and renders the OTHER portal’s shape from the same code', () => {
		const firm = body({ templates: [row(), row({ uid: 'eml_2', name: 'Password reset' })] });
		expect(firm).toContain('href="/email-templates/eml_1/edit"');
		expect(firm).toContain('href="/email-templates/eml_2/edit"');
		expect(firm).toContain('Client invitation');

		// If this component ever grows a literal href, one of these two halves fails.
		const ops = body({
			links: {
				edit: (uid: string) => `/tenants/ten_firm/email-templates/${uid}/edit`,
				versions: (uid: string) => `/tenants/ten_firm/email-templates/${uid}/versions`
			},
			templates: [row()]
		});
		expect(ops).toContain('href="/tenants/ten_firm/email-templates/eml_1/edit"');
		expect(ops).not.toContain('href="/email-templates/eml_1/edit"');
	});
});

describe('EmailTemplatesListing — an absent callback renders no action', () => {
	it('read-only shows no create form; write mode posts to the app’s own action name', () => {
		const readOnly = body({ templates: [row()], catalog });
		expect(readOnly).not.toContain('New email template');
		expect(readOnly).not.toContain('?/create');
		expect(readOnly).toContain('View');
		expect(readOnly).not.toContain('Edit');

		const writeMode = body({ templates: [row()], catalog, actions: writes });
		expect(writeMode).toContain('New email template');
		expect(writeMode).toContain('Edit');
	});

	it('shows Load defaults only when that action is passed — the one real asymmetry', () => {
		expect(body({ templates: [row()], actions: writes })).not.toContain('Load defaults');
		const ops = body({
			templates: [row()],
			actions: { ...writes, loadDefaults: '?/loadDefaults' }
		});
		expect(ops).toContain('Load defaults');
		expect(ops).toContain('?/loadDefaults');
	});
});

it('draws no heading with title absent, shows the kebab only once there is something in it', () => {
	const noTitle = { title: undefined, description: undefined, templates: [row()], catalog };
	const withActs = body({ ...noTitle, actions: writes });
	expect(withActs).not.toContain('<h1');
	expect(withActs).toContain('aria-label="Listing actions"');
	expect(body(noTitle)).not.toContain('aria-label="Listing actions"');
	expect(body({ templates: [row()], catalog })).toContain('<h1');
});

describe('EmailTemplatesListing — the copy that must not be flattened', () => {
	it('renders the caller’s eyebrow, back link and row codes, and omits each when absent', () => {
		// `back` is operations-only. `showCodes` is opt-in and BOTH portals pass it: the code is what a
		// notification rule names and what the cheat sheet is keyed by, so an author needs to see it.
		const ops = body({
			title: 'Emails',
			eyebrow: 'Internal operations',
			templates: [row()],
			catalog,
			showCodes: true,
			back: { href: '/tenants/ten_firm', label: 'Caldwell Wealth' }
		});
		expect(ops).toContain('Internal operations');
		expect(ops).toContain('Emails');
		expect(ops).toContain('href="/tenants/ten_firm"');
		expect(ops).toContain('Caldwell Wealth');
		expect(ops).toContain('ID:');
		expect(ops).toContain('>invite<');

		const firm = body({ templates: [row()], catalog });
		expect(firm).not.toContain('href="/tenants/ten_firm"');
		expect(firm).not.toContain('ID:');
	});
});

describe('EmailTemplatesListing — the rules that live inside closed portals', () => {
	it('scans the real source (a guard that matches nothing is not a guard), for the shared DataTable', () => {
		expect(SOURCE.length).toBeGreaterThan(2000);
		expect(SOURCE).toContain('DropdownMenu.Item');
		expect(SOURCE).toContain('deleteConsequence');
		// Not a hand-built table.
		expect(SOURCE).toContain('<DataTable');
		expect(SOURCE).not.toMatch(/<table[\s>]/);
		expect(SOURCE).not.toContain('<thead');
		expect(SOURCE).not.toContain('empty="No rows."');
		expect(SOURCE).toMatch(/empty="[^"]*email templates[^"]*"/);
	});

	it('hardcodes no URL — every href and endpoint comes from `links` or `actions`', () => {
		// Neither `/email-templates/…` nor `/tenants/…` may appear here, nor an assumed `action="?/…"` name — each is right for one portal and wrong for the other.
		expect(SOURCE).not.toMatch(/["'`]\/email-templates/);
		expect(SOURCE).not.toMatch(/["'`]\/tenants/);
		expect(SOURCE).not.toMatch(/action="\?\//);
	});

	it('gates Settings and Delete on `writes`; Version history is NOT gated', () => {
		// Version history is the only way back from an unwanted edit, so a read-only reader is offered it (the dialog withholds its own Restore without `canManage`).
		const kebab = SOURCE.slice(
			SOURCE.indexOf('<DropdownMenu.Content'),
			SOURCE.indexOf('</DropdownMenu.Content>')
		);
		expect(kebab).toBeTruthy();
		const gate = kebab.indexOf('{#if writes}');
		expect(gate).toBeGreaterThan(-1);
		expect(kebab.indexOf('Version history')).toBeLessThan(gate);
		for (const gated of ['Settings', 'Delete permanently'])
			expect(kebab.indexOf(gated), gated).toBeGreaterThan(gate);
	});

	it('offers no archived view, and no restore of a deleted template', () => {
		// Nothing archives an email template: the row goes, its version history goes, and the code is free again. A Live/Archived switch or a Restore item would promise a state the API cannot return.
		//
		// The word `archiv` IS the whole guard. This also used to forbid `Segmented` outright, as a
		// proxy for the same thing — too broad now the listing legitimately has a mode toggle, and it
		// was always the archived STATE that is unreachable rather than the control.
		expect(SOURCE).not.toMatch(/archiv/i);
	});

	it('switches between the two kinds of email rather than stacking them', () => {
		// System email is the platform's, always on, and a firm only re-skins it; notifications are
		// raised by a firm's own features. Stacked, the second sat below a scroll and read as an
		// afterthought — so choosing between them is a view, on the sanctioned mode control.
		expect(SOURCE).toContain('Segmented');
		const toggle = SOURCE.slice(
			SOURCE.indexOf('<Segmented'),
			SOURCE.indexOf('/>', SOURCE.indexOf('<Segmented'))
		);
		// Driven by the groups that HAVE rows, so a firm holding one kind is never shown an empty tab
		// for the other — and with one group left the control does not render at all.
		expect(toggle).toContain('groupedRows.map');
		expect(SOURCE).toContain('{#if groupedRows.length > 1}');
	});

	it('confirms destructively, naming what goes and that the code is freed', () => {
		const confirm = SOURCE.slice(
			SOURCE.indexOf('<ConfirmDialog'),
			SOURCE.indexOf('</ConfirmDialog>')
		);
		expect(confirm).toContain('destructive');
		expect(confirm).toContain('confirmLabel="Delete permanently"');
		expect(confirm).toContain('description={deleteDescription}');
		expect(SOURCE).toMatch(/version history are deleted for good/);
		expect(SOURCE).toMatch(/becomes free to use again/);
		// There is nothing to restore, so the copy may not offer a way back.
		expect(confirm).not.toMatch(/restore/i);
	});

	it('surfaces the API’s own 409 refusal instead of a generic failure', () => {
		// The refusal NAMES the published journey or the unknown code to fix; flattening it to "could not delete" leaves the author with nothing to act on.
		expect(SOURCE).toMatch(/result\.data\?\.error as string\) : undefined/);
		expect(SOURCE).toMatch(/toast\.error\(refusal \?\?/);
	});

	it('states no surface-specific consequence of its own, and mounts settings only in write mode', () => {
		// "a new firm clones the preset" is a fact about the model tenant the firm's portal must never show, and vice versa — so it arrives as a prop.
		expect(SOURCE).toContain('${deleteConsequence}');
		expect(SOURCE).not.toMatch(/clones the preset/);
		expect(SOURCE).not.toMatch(/built-in default/);
		expect(SOURCE).toMatch(/\{#if writes\}\s*<TemplateSettingsDialog/);
		expect(SOURCE).toContain('updateAction={writes.update}');
	});

	it('appends that consequence only for a code the send path knows', () => {
		// The mount's sentence says what is sent INSTEAD of this template, which presumes there is
		// something to send. A code the catalogue does not carry has nothing behind it and nothing
		// that sends it, so appending it to a firm's `test` template promised a substitute that does
		// not exist. Pinned at the source because a ConfirmDialog's description is not in the SSR
		// body, and scoped to the derivation so an unrelated occurrence cannot satisfy it.
		const at = SOURCE.indexOf('const deleteDescription');
		expect(at).toBeGreaterThan(-1);
		const derivation = SOURCE.slice(at, at + 600);
		expect(derivation).toContain('knownCodes.has(deleteTarget.code)');
		expect(derivation).toContain('${deleteConsequence}');
	});
});

it('wires the shared DataTable: accessible label, Email/Status sortable (not actions), row link in a cell', () => {
	const html = body({ templates: [row()], catalog, actions: writes });
	expect(html).toContain('aria-label="System email templates"');
	// Catalogue order, so no column claims a sort: Email and Status both show the idle chevron.
	expect(html.match(/lucide-chevrons-up-down/g)).toHaveLength(2);
	expect(html).not.toContain('aria-sort=');
	// A real `<a href>` (middle-click, new tab), not `onRowClick`'s `role="button"`.
	expect(html).toContain('href="/email-templates/eml_1/edit"');
	expect(html).not.toContain('role="button"');
});

describe('EmailTemplatesListing — the honest vocabulary reaches BOTH mounts', () => {
	it('calls a draft on top of earlier versions "Unpublished changes", never plain Draft', () => {
		// The admin copy read `status === 'published' ? … : 'Draft'`, so a template published for months with one draft on top said "Draft" — wrong answer to the question this list is asked.
		expect(
			body({ templates: [row({ current_version_status: 'draft', version_count: 4 })] })
		).toContain('Unpublished changes');
	});

	it('warns, naming the code, about a row no send site will ever send', () => {
		const html = body({ templates: [row({ code: 'not_wired' })], catalog });
		expect(html).toContain('Not in use');
		expect(html).toContain('not_wired');
	});

	it('states a failed read instead of claiming the tenant has no templates', () => {
		const failed = body({ templates: [], loadError: true });
		expect(failed).toContain("Couldn't load these email templates.");
		expect(failed).not.toContain('No email templates yet');

		expect(body({ templates: [], emptyTitle: 'No model email templates yet' })).toContain(
			'No model email templates yet'
		);
	});
});
