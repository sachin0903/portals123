/**
 * SSR coverage for the client-record Documents tab, pinning the alignment with the shared library
 * (`@graphene/ui/book/documents`'s DocumentsLibrary — the reference implementation these two
 * near-duplicate surfaces are kept in step with).
 *
 * What SSR can pin, and why each of these is worth a test:
 *  - the columns are SORTABLE HEADERS, not a Sort dropdown: every data column is a button named by
 *    its own text, the initial sort is Created-descending, and the inactive columns still render
 *    their (dimmed) chevron — the affordance must not be hover-only, or a touch user never sees it;
 *  - the rows sit in TYPE FOLDERS by default, with no pager;
 *  - the removed controls stay removed: the kind filter, the Sort select, the Status column, the
 *    per-row preview eye and — since the documents rework — the Live/Archived switch and the row
 *    Send button. A regression here is a silent re-divergence from the reference;
 *  - the row's client-facing action is NOTIFY: a document filed under a client is auto-linked
 *    (0096, `role='subject'`) and already in their portal, so a Send button on their own record
 *    offered to grant access they had. What is left to do is tell them it is there;
 *  - a byte-less document is NOT an ordinary row: dropping the Status column would otherwise make a
 *    failed render indistinguishable from a filed, downloadable one, so the badge + reason + retry
 *    moved inline under the name;
 *  - Delivery is Read/Unread alone (see the component's own note on why "Sent" no longer says
 *    anything a filed document does not already say).
 * Clicking a header to flip the direction is client-side and out of SSR's reach; the initial
 * direction and the `aria-sort`/label wiring are what the markup can prove.
 */
import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import type { ApiDocument, ApiFile } from '@graphene/ui/book/documents';
import ClientDocuments, {
	clientDocumentsFilterDefs,
	rowMenuItems,
	type DocumentsView
} from './client-documents.svelte';

const doc = (over: Partial<ApiDocument> = {}): ApiDocument => ({
	uid: 'doc_1',
	client_contact_uid: null,
	name: 'Suitability report.pdf',
	document_type: 'suitability',
	source: 'generated',
	status: 'active',
	content_type: 'application/pdf',
	size_bytes: 2048,
	created_at: '2026-07-01T09:00:00Z',
	generated_at: '2026-07-01T09:00:00Z',
	template_version: 2,
	template_name: 'Suitability',
	template_uid: 'tpl_9',
	created_by: 'Ada Adviser',
	client: 'Max Oakden',
	client_contact_id: null,
	recipients: null,
	client_viewed: false,
	client_viewed_at: null,
	sent_to_client: false,
	sent_at: null,
	error: null,
	...over
});

const file = (over: Partial<ApiFile> = {}): ApiFile => ({
	uid: 'file_1',
	name: 'passport.jpg',
	attachment_type: 'passport',
	content_type: 'image/jpeg',
	size_bytes: 4096,
	contact_id: 8,
	contact_name: 'Max Oakden',
	created_at: '2026-06-01T09:00:00Z',
	created_by: 'Max Oakden',
	...over
});

const html = (
	over: { documents?: ApiDocument[]; files?: ApiFile[] } = {},
	canSend = false,
	canWrite = false,
	view: DocumentsView = 'table'
): string =>
	render(ClientDocuments, {
		props: {
			data: {
				client: { id: 8, uid: 'con_9b2e', name: 'Max Oakden' },
				clientOption: { contact_id: 8, name: 'Max Oakden', email: 'max@firm.test' },
				documents: over.documents ?? [doc()],
				files: over.files ?? [],
				library: [],
				events: [],
				canReadActivity: false,
				documentTypes: [],
				forms: []
			},
			canSend,
			canWrite,
			view
		}
	}).body;

describe('client Documents tab — sortable column headers', () => {
	it('renders every column as a sort button, marks only the active one, and always shows the chevron', () => {
		const body = html();
		for (const label of ['Document', 'Created', 'Author', 'Type', 'Delivery', 'Size']) {
			expect(body).toContain(`<span>${label}</span>`);
		}
		// The dropdown this replaced: its two options were the whole of the sorting on offer.
		expect(body).not.toContain('Sort: Newest');
		expect(body).not.toContain('Sort: Name');
		expect(body).not.toContain('aria-label="Sort documents"');
		expect(body).not.toContain('aria-sort="none"');
		expect(body.match(/aria-sort=/g)).toHaveLength(1);
		expect(body.match(/lucide-chevrons-up-down/g)).toHaveLength(5);
		expect(body).toContain('opacity-30');
		expect(body).toContain('group-hover:opacity-70');
	});

	it('starts on Created descending — exactly one column claims the sort', () => {
		const body = html();
		expect(body.match(/aria-sort="descending"/g)).toHaveLength(1);
		expect(body).not.toContain('aria-sort="ascending"');
		const created = body.indexOf('<span>Created</span>');
		expect(created).toBeGreaterThan(-1);
		expect(body.slice(body.lastIndexOf('<th', created), created)).toContain(
			'aria-sort="descending"'
		);
		// …and the rows come out newest-first, so the comparator and its seed direction really ran.
		const rows = html({
			documents: [
				doc({ uid: 'old', name: 'Older report.pdf', created_at: '2026-01-01T09:00:00Z' }),
				doc({ uid: 'new', name: 'Newer report.pdf', created_at: '2026-07-01T09:00:00Z' })
			]
		});
		expect(rows.indexOf('Newer report.pdf')).toBeLessThan(rows.indexOf('Older report.pdf'));
	});
});

describe('client Documents tab — type folders', () => {
	it('puts the toolbar in the card above the table, bands rows by type, and drops the pager', () => {
		const body = html({
			documents: [doc(), doc({ uid: 'doc_2', name: 'Second report.pdf' })],
			files: [file()]
		});
		const search = body.indexOf('placeholder="Search by name…"');
		expect(search).toBeGreaterThan(body.indexOf('data-slot="card-content"'));
		expect(body.indexOf('aria-label="Documents"')).toBeGreaterThan(search);
		expect(body).toContain('<span class="truncate">Suitability report</span>');
		expect(body).toContain('<span class="truncate">Passport</span>');
		expect(body.match(/font-normal">2</g)).toHaveLength(1);
		expect(body).toMatch(/aria-selected="true"[^>]*aria-label="Show as a table"/);
		expect(body).not.toContain('data-slot="listing-pagination"');
	});
});

describe('client Documents tab — the controls that were removed', () => {
	it('has no kind filter, no Status column and no per-row preview', () => {
		const body = html({ documents: [doc()], files: [file()] });
		expect(body).not.toContain('Documents &amp; files');
		expect(body).not.toContain('Documents only');
		expect(body).not.toContain('Files only');
		expect(body).not.toContain('aria-label="Filter by kind"');
		// Both kinds still list together — the Type column is what says which is which.
		expect(body).toContain('Suitability report.pdf');
		expect(body).toContain('passport.jpg');
		expect(body).toContain('Passport');
		// Eight header cells, not nine — asserted structurally, because SSR wraps a component's text
		// children in `<!---->` markers, so a `>Status<` string match would pass even if the column
		// came back.
		expect(body.match(/data-slot="table-head"/g)).toHaveLength(8);
		expect(body).not.toContain('Status');
		expect(body).not.toContain('Active');
		expect(body).not.toContain('aria-label="Preview Suitability report.pdf"');
		expect(body).not.toContain('Preview');
	});
});

describe('client Documents tab — filters', () => {
	it('offers Type and Author, and NOT a client filter', () => {
		// The panel is behind a shut popover and renders nothing server-side, so the defs are what
		// SSR can pin. A client filter is absent: this tab IS one client.
		const defs = clientDocumentsFilterDefs({ typeOptions: [], authorOptions: [] });
		expect(defs.map((f) => f.key)).toEqual(['type', 'author']);
		const body = html({ documents: [doc()], files: [file()] });
		expect(body).toContain('aria-label="Show filters"');
		expect(body).not.toContain('All clients');
		expect(body).not.toContain('aria-label="Filter by client"');
	});

	it('names a supporting file’s type with the FILE vocabulary', () => {
		// `other` is a legal value on both sides and means different things: "Other" as a document
		// type, "Other file" as an attachment type. The option label is resolved off the rows that
		// carry the slug, so a file's type is not humanised with the document vocabulary.
		const body = html({ documents: [doc()], files: [file()] });
		expect(body).toContain('Passport');
	});
});

describe('client Documents tab — a byte-less document is still not an ordinary row', () => {
	it('shows the render state, its reason and a retry inline under the name', () => {
		const failed = { documents: [doc({ status: 'failed', error: 'Template variable missing' })] };
		const body = html(failed, false, true);
		expect(body).toContain('Failed');
		expect(body).toContain('Template variable missing');
		expect(body).toContain('aria-label="Retry rendering Suitability report.pdf"');
		// A retry is a write: a reader sees the failure, not the retry.
		const reader = html(failed);
		expect(reader).toContain('Template variable missing');
		expect(reader).not.toContain('aria-label="Retry rendering');
	});

	it('badges a queued render without offering a retry (nothing has failed yet)', () => {
		const body = html({ documents: [doc({ status: 'queued' })] });
		expect(body).toContain('Queued');
		expect(body).not.toContain('aria-label="Retry rendering Suitability report.pdf"');
	});

	it('says nothing extra on a document that rendered', () => {
		const body = html();
		expect(body).not.toContain('Failed');
		expect(body).not.toContain('Queued');
	});
});

describe('client Documents tab — Delivery', () => {
	it('is Read/Unread alone: the Sent pill is gone', () => {
		const unread = html();
		expect(unread).toContain('Unread');
		// Sending no longer decides what the client can see (a document filed against them is
		// auto-linked), so a Sent/Not sent pill distinguished nothing. Matched on the bare labels,
		// which appear nowhere else — the row's send button says "Send to", never "Sent".
		expect(unread).not.toContain('Sent');
		expect(unread).not.toContain('Not sent');
		const read = html({
			documents: [doc({ client_viewed: true, client_viewed_at: '2026-07-02T10:00:00Z' })]
		});
		expect(read).toContain('Read');
		expect(read).toContain('Max Oakden viewed this');
	});

	it('renders an em dash for a supporting file, which has nothing to deliver', () => {
		const body = html({ documents: [], files: [file()] });
		expect(body).not.toContain('Unread');
		expect(body).toContain('—');
	});
});

describe('client Documents tab — no client-facing document act on a row', () => {
	it('offers neither a notify nor a send — the row is drafting only', () => {
		// This block pinned a per-row Notify button that replaced a per-row Send. Both are gone: a
		// document filed under a client is already in their portal, so "send" would offer access they
		// hold, and telling them about it was replaced by a document REQUEST — which asks them to act
		// and records whether they did. What reaches the client from this tab is "Request from client",
		// in the Add-document menu, where it is one deliberate act rather than a per-row nudge.
		const body = html({}, true);
		expect(body).not.toContain('aria-label="Tell Max Oakden about Suitability report.pdf"');
		expect(body).not.toContain('aria-label="Send Suitability report.pdf to Max Oakden"');
		expect(body).not.toContain('Send to Max Oakden');
		expect(body).not.toContain('Resend to Max Oakden');
		// …while the row's own actions stay, now behind one kebab (the four the library offers). The
		// TRIGGER is what SSR can see; the items live in a closed DropdownMenu.Content and are pinned by
		// the source scan below, for the reason this file already gives about Select/Popover panels.
		expect(body).toContain('aria-label="Actions for Suitability report.pdf"');
	});

	// NOT asserted here: that "Request from client" is offered on documents:send. It sits inside a
	// DropdownMenu.Content, which renders nothing at SSR because the menu is closed — the same reason
	// a Select/Popover panel is invisible to these tests. Its gate is one `{#if canSend}` beside the
	// item, and inventing an assertion that passes for the wrong reason would be worse than none.
});

describe('client Documents tab — no Live/Archived switch', () => {
	it('has no Live/Archived switch — the only tablist is the view switch', () => {
		const body = html({}, true);
		expect(body.match(/role="tablist"/g)).toHaveLength(1);
		expect(body).toContain('role="tablist" aria-label="View"');
		expect(body).not.toContain('Archived');
	});
});

describe('client Documents tab — the kebab', () => {
	// The menu is closed at SSR, so its items are pinned through `rowMenuItems`.
	it.each([
		[{ isFile: false }, false, ['view', 'download']],
		[{ isFile: true }, false, ['download']],
		[{ isFile: false }, true, ['view', 'download', 'delete']],
		[{ isFile: true }, true, ['download', 'delete']],
		[{ isFile: false, deletable: false }, true, ['view', 'download']]
	])('rowMenuItems(%j, canWrite=%s) -> %j', (doc, canWrite, expected) => {
		expect(rowMenuItems(doc, canWrite)).toEqual(expected);
	});
});

const twoDocsAndAFile = {
	documents: [doc(), doc({ uid: 'doc_2', name: 'Second report.pdf' })],
	files: [file()]
};
const NAMES = ['Suitability report.pdf', 'Second report.pdf', 'passport.jpg'];

describe('client Documents tab — select boxes', () => {
	it('gates select boxes behind documents:write, laid out as one indented cell per row', () => {
		const writer = html(twoDocsAndAFile, false, true).replace(/<!--.*?-->/g, '');
		const head = writer.slice(writer.indexOf('<thead'), writer.indexOf('</thead>'));
		expect(head).toContain('aria-label="Select all"');
		expect(writer).toContain('aria-label="Select all in Passport"');
		for (const name of NAMES) expect(writer).toContain(`aria-label="Select ${name}"`);
		expect(writer.match(/data-slot="table-head"/g)).toHaveLength(9);
		const box = writer.indexOf('aria-label="Select Second report.pdf"');
		expect(writer.slice(writer.lastIndexOf('<td', box), box)).toContain('colspan="3"');

		const reader = html(twoDocsAndAFile);
		expect(reader).not.toContain('aria-label="Select all');
		expect(reader).not.toContain('aria-label="Select Suitability report.pdf"');
	});
});

describe('client Documents tab — toolbar', () => {
	it('shows Add document and Table/Grid as named icons, and Sort only in grid mode', () => {
		const body = html();
		expect(body).toContain('title="Add document"');
		expect(body).not.toMatch(/>\s*Add document\s*</);
		for (const name of ['Show as a table', 'Show as a grid']) {
			expect(body).toContain(`title="${name}"`);
		}
		expect(body).not.toContain('aria-label="Sort"');
		expect(html({}, false, false, 'grid')).toContain('aria-label="Sort"');
	});
});

describe('client Documents tab — grid', () => {
	it('renders a card per document with no table, boxes gated behind documents:write', () => {
		const writer = html(twoDocsAndAFile, false, true, 'grid');
		expect(writer).not.toContain('<table');
		expect(writer.match(/data-slot="document-thumb"/g)).toHaveLength(3);
		for (const name of NAMES) expect(writer).toContain(`aria-label="Select ${name}"`);
		expect(writer).toContain('href="/clients/con_9b2e/documents/doc_2"');

		const reader = html(twoDocsAndAFile, false, false, 'grid');
		expect(reader).not.toContain('aria-label="Select');
		expect(reader).toContain('aria-label="Actions for Suitability report.pdf"');
	});

	it('previews a queued render as rendering and a failed one as failed', () => {
		const documents = [
			doc({ uid: 'doc_q', status: 'queued', size_bytes: null }),
			doc({ uid: 'doc_f', status: 'failed', error: 'Template error' })
		];
		const body = html({ documents }, false, false, 'grid');
		expect(body).toContain('data-status="rendering"');
		expect(body).toContain('data-status="failed"');
	});

	it('shows the same empty state as the table', () => {
		const body = html({ documents: [], files: [] }, false, true, 'grid');
		expect(body).toContain('No documents yet for this client.');
	});
});
