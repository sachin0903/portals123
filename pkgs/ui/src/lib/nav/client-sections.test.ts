/** The client record's section list and the `?tab=` deep links that must keep resolving to it. */
import { describe, expect, it } from 'vitest';
import { readdirSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
	CLIENT_TAB_ANCHORS,
	CLIENT_TAB_ROUTES,
	clientSections,
	clientSidebarFocus,
	clientTabTarget
} from './client-sections';
import { FOCUS_ICONS, parseFocus } from './sidebar-focus';

const repoRoot = fileURLToPath(new URL('../../../../../', import.meta.url));

/** Section directories under a client-record route. Overview is the record page itself. */
function sectionDirs(relative: string): string[] {
	return readdirSync(join(repoRoot, relative), { withFileTypes: true })
		.filter((e) => e.isDirectory())
		.map((e) => e.name);
}

const ME = {
	uid: 'con_9b2e',
	name: 'Mr Max Oakden',
	email: 'max@example.com',
	canReadDocuments: true,
	canSeeIllustrations: true,
	canReadActivity: true,
	canReadCompliance: true,
	canReadJourneys: true
};

describe('clientSections', () => {
	it('points every section at this client and highlights Overview only on the record itself', () => {
		const sections = clientSections(ME);
		expect(sections.every((s) => s.href.startsWith('/clients/con_9b2e'))).toBe(true);
		const overview = sections.find((s) => s.label === 'Overview')!;
		// Without `exact`, Overview stays highlighted on every subpage of the record.
		expect(overview).toMatchObject({ href: '/clients/con_9b2e', exact: true });
		expect(sections.filter((s) => s.exact)).toHaveLength(1);
	});

	it('places Details immediately under Overview', () => {
		const labels = clientSections(ME).map((s) => s.label);
		expect(labels.indexOf('Details')).toBe(labels.indexOf('Overview') + 1);
		expect(clientSections(ME).find((s) => s.label === 'Details')).toMatchObject({
			href: '/clients/con_9b2e/details',
			icon: 'details'
		});
	});

	it('asks only for icons the shell actually has', () => {
		// An unknown key degrades to no icon at all, which reads as a half-loaded sidebar.
		for (const s of clientSections({ ...ME, canReadMessages: true }))
			expect(FOCUS_ICONS[s.icon!], s.label).toBeTruthy();
	});

	it('gives Emails and Activity DIFFERENT glyphs', () => {
		// The defect this rules out: both sections once asked for `activity`, so the outbound-email
		// ledger and the action trail wore one symbol a foot apart in the same sidebar. They are
		// different surfaces, and one glyph for two meanings is how a reader learns to distrust the
		// icons. (The pair used to be labelled Activity and Logs; the names changed, the rule did
		// not.)
		const sections = clientSections({ ...ME, canReadMessages: true });
		const emails = sections.find((s) => s.label === 'Emails')!;
		const activity = sections.find((s) => s.label === 'Activity')!;
		expect(emails.icon).toBe('emails');
		expect(activity.icon).toBe('activity');
		expect(FOCUS_ICONS.emails).not.toBe(FOCUS_ICONS.activity);
	});

	it('offers Journeys immediately after Details, gated on cases:read', () => {
		expect(clientSections({ ...ME, canReadJourneys: false }).map((s) => s.label)).not.toContain(
			'Journeys'
		);
		const labels = clientSections(ME).map((s) => s.label);
		expect(labels.indexOf('Journeys')).toBe(labels.indexOf('Details') + 1);
		expect(clientSections(ME).find((s) => s.label === 'Journeys')).toMatchObject({
			href: '/clients/con_9b2e/journeys',
			icon: 'journeys'
		});
		expect(clientTabTarget('con_9b2e', 'journeys')).toEqual({
			redirect: '/clients/con_9b2e/journeys'
		});
	});

	it('offers Compliance, gated on screening:read, at /compliance', () => {
		expect(clientSections({ ...ME, canReadCompliance: false }).map((s) => s.label)).not.toContain(
			'Compliance'
		);
		const section = clientSections(ME).find((s) => s.label === 'Compliance')!;
		expect(section).toMatchObject({
			href: '/clients/con_9b2e/compliance',
			icon: 'compliance'
		});
		expect(clientSections(ME).map((s) => s.label)).not.toContain('Screening');
	});

	it('gates Documents on documents:read and Events on activity:read, each on its own', () => {
		// Each section lands on a page that refuses without its permission; Events reads the action
		// stream (activity:read) now, not the document trail, so it has its OWN gate.
		const noDocs = clientSections({ ...ME, canReadDocuments: false }).map((s) => s.label);
		expect(noDocs).not.toContain('Documents');
		expect(noDocs).toContain('Overview');
		const noEvents = clientSections({ ...ME, canReadActivity: false }).map((s) => s.label);
		expect(noEvents).not.toContain('Activity');
		const all = clientSections(ME).map((s) => s.label);
		expect(all).toContain('Documents');
		expect(all).toContain('Activity');
	});

	it('lands every section on a route that exists', () => {
		// The nav and the route tree are two lists that have to agree; a section with no directory is a
		// row that 404s on click. Read from disk rather than restated, so adding one to the nav and
		// forgetting the page — or retiring a page and leaving its row — fails here.
		const trees = [
			'apps/platform/src/routes/clients/[uid]',
			'apps/admin/src/routes/tenants/[uid]/operate/clients/[clientUid]'
		];
		for (const s of clientSections({ ...ME, canReadMessages: true })) {
			const tail = s.href.replace('/clients/con_9b2e', '').replace(/^\//, '');
			if (!tail) continue; // the overview IS the record's own +page.svelte
			for (const tree of trees) expect(sectionDirs(tree), `${tree} ${s.label}`).toContain(tail);
		}
	});

	it('offers no People section — the advisers and the logins are popouts on the record', () => {
		// The page that held both is gone: the record's Team menu and portal-access badge open
		// them in place. Its route is deleted, so a row here would 404 on click and `?tab=people` has
		// to fall through to the record rather than redirect to nothing.
		expect(clientSections({ ...ME, canReadMessages: true }).map((s) => s.label)).not.toContain(
			'People'
		);
		expect(CLIENT_TAB_ROUTES.people).toBeUndefined();
		expect(clientTabTarget('con_9b2e', 'people')).toEqual({ redirect: '/clients/con_9b2e' });
	});

	it('offers Illustrations between Documents and Compliance when the reader may see them', () => {
		const labels = clientSections(ME).map((s) => s.label);
		expect(labels.indexOf('Illustrations')).toBe(labels.indexOf('Documents') + 1);
		expect(clientTabTarget('con_9b2e', 'illustrations')).toEqual({
			redirect: '/clients/con_9b2e/illustrations'
		});
		expect(
			clientSections({ ...ME, canReadDocuments: false, canSeeIllustrations: false }).map(
				(s) => s.label
			)
		).not.toContain('Illustrations');
		expect(
			clientSections({ ...ME, canReadDocuments: false, canSeeIllustrations: true }).map(
				(s) => s.label
			)
		).toContain('Illustrations');
	});

	it('shows Messages only with messages:read, and places it after Events', () => {
		expect(clientSections(ME).map((s) => s.label)).not.toContain('Messages');
		const labels = clientSections({ ...ME, canReadMessages: true }).map((s) => s.label);
		expect(labels).toContain('Messages');
		expect(labels.indexOf('Messages')).toBe(labels.indexOf('Activity') + 1);
	});

	it("carries this client's own unread dot on Messages, defaulting to false", () => {
		const chats = (chatsUnread?: boolean) =>
			clientSections({ ...ME, canReadMessages: true, chatsUnread }).find(
				(s) => s.label === 'Messages'
			);
		expect(chats(true)?.unread).toBe(true);
		expect(chats(false)?.unread).toBe(false);
		expect(chats(undefined)?.unread).toBe(false);
	});
});

describe('clientSidebarFocus', () => {
	it('survives the shell’s own validation of page data', () => {
		// The chrome treats `page.data.sidebarFocus` as untrusted input and drops a half-formed focus
		// back to the global nav — so what we publish has to pass its parser, not just type-check.
		const focus = parseFocus(clientSidebarFocus(ME));
		expect(focus).toMatchObject({
			eyebrow: 'Client',
			title: 'Mr Max Oakden',
			subtitle: 'max@example.com',
			back: { href: '/', label: 'Dashboard' }
		});
		expect(focus!.sections).toHaveLength(clientSections(ME).length);
	});

	it('omits the subtitle rather than publishing a blank one when the client has no email', () => {
		const focus = clientSidebarFocus({ ...ME, email: null });
		expect(focus.subtitle).toBeUndefined();
		expect(parseFocus(focus)!.title).toBe('Mr Max Oakden');
	});

	it('publishes a live-poll key for the Messages dot only when the section itself would show', () => {
		// No `messages:read` ⇒ no Messages section ⇒ nothing for the root layout to poll for.
		expect(clientSidebarFocus({ ...ME, contactId: '42' }).liveUnreadContactId).toBeUndefined();
		// `messages:read` but no contact id (a caller that never resolved one) ⇒ same: no key to poll.
		expect(
			clientSidebarFocus({ ...ME, canReadMessages: true }).liveUnreadContactId
		).toBeUndefined();
		const focus = clientSidebarFocus({ ...ME, canReadMessages: true, contactId: '42' });
		expect(focus.liveUnreadContactId).toBe('42');
		expect(parseFocus(focus)!.liveUnreadContactId).toBe('42');
	});
});

describe('clientTabTarget — the old ?tab= deep links', () => {
	it('sends every tab that became a route to a section that exists', () => {
		// A tab pointing at a route nobody serves is a dead link the type-checker cannot see.
		const hrefs = new Set(clientSections(ME).map((s) => s.href));
		for (const tab of Object.keys(CLIENT_TAB_ROUTES)) {
			expect(clientTabTarget('con_9b2e', tab), tab).toEqual({ redirect: expect.any(String) });
			const { redirect } = clientTabTarget('con_9b2e', tab) as { redirect: string };
			expect(hrefs.has(redirect), tab).toBe(true);
		}
	});

	it('keeps the dashboard’s documents link working', () => {
		// routes/+page.svelte's action-items rail links to /clients/{id}?tab=documents.
		expect(clientTabTarget('con_8b4f', 'documents')).toEqual({
			redirect: '/clients/con_8b4f/documents'
		});
		expect(clientTabTarget('con_8b4f', ' Documents ')).toEqual({
			redirect: '/clients/con_8b4f/documents'
		});
	});

	it('sends the old screening tab to Compliance', () => {
		expect(clientTabTarget('con_9b2e', 'screening')).toEqual({
			redirect: '/clients/con_9b2e/compliance'
		});
	});

	it('resolves the tabs that folded into the landing view to a band on it, not a redirect', () => {
		// Accounts and Notes are bands on Overview. Redirecting would either loop or throw the
		// reader at the top of the page; the page scrolls to the band instead. Details became its
		// own route.
		expect(clientTabTarget('con_9b2e', 'accounts')).toEqual({ anchor: 'accounts' });
		expect(clientTabTarget('con_9b2e', 'notes')).toEqual({ anchor: 'notes' });
	});

	it('sends the old details tab to the Details route', () => {
		expect(clientTabTarget('con_9b2e', 'details')).toEqual({
			redirect: '/clients/con_9b2e/details'
		});
	});

	it('never names both a route and a band for one tab', () => {
		for (const tab of Object.keys(CLIENT_TAB_ANCHORS))
			expect(CLIENT_TAB_ROUTES[tab], tab).toBeUndefined();
	});

	it('lands an unknown, empty or retired tab on the record rather than a 404', () => {
		// `overview` is retired as a value: it means "the record", which is where we send it.
		for (const tab of ['overview', 'holdings', '', null])
			expect(clientTabTarget('con_9b2e', tab), String(tab)).toEqual({
				redirect: '/clients/con_9b2e'
			});
	});
});
