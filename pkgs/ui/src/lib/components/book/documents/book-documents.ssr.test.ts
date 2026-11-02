/**
 * SSR coverage for the shared documents surfaces (the portal waterfall: the platform pages
 * became thin routes over these, and the admin portal mounts them next over its own loaders).
 *
 * What SSR can pin — and what matters for the extraction contract:
 *  - links are INJECTED: every href in the markup comes from the `links` builders, never a
 *    hardcoded platform path (the admin routes differ, so a hardcoded href is a broken page);
 *  - actions are OPTIONAL: no callbacks ⇒ the affordance is absent, and the surface renders
 *    read-only (the admin default) — no upload/generate on the library, no
 *    rename/delete on the record;
 *  - the fixture rows render (table, feed, sidebar) exactly as the platform pages did;
 *  - SEND IS GONE and NOTIFY replaced it. Filing a document under a client auto-grants them access
 *    (0096, `role='subject'`), so a send affordance implied that visibility was still ours to give;
 *    what is left to offer is telling the client the document is there. Asserted as an absence as
 *    well as a presence, because reintroducing "Send" would read as a harmless label;
 *  - THERE IS NO LIVE/ARCHIVED SWITCH, and `archived` reads as DELETED wherever it still shows.
 * Interaction (filtering, sorting, menus, dialogs) is client-side and out of SSR's reach.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it, vi } from 'vitest';
import { render } from 'svelte/server';
import DocumentsLibrary, { type DocumentsLibraryActions } from './documents-library.svelte';
import DocumentsLibrarySkeleton from './documents-library-skeleton.svelte';
import DocumentDetail, { type DocumentDetailActions } from './document-detail.svelte';
import type { LibraryRow } from './documents.js';
import type { ActionEvent } from '../../audit/describe.js';
import type { DocumentDetailLinks, DocumentsLibraryLinks } from './types.js';

// Deliberately NOT the platform's routes — the assertions below prove the components render
// whatever the app injects rather than remembering where they were extracted from.
const libraryLinks: DocumentsLibraryLinks = {
	document: (uid) => `/x/documents/${uid}`,
	documentContent: (uid) => `/x/documents/${uid}/content`,
	fileDownload: (uid) => `/x/files/${uid}/download`,
	requests: '/x/requests',
	templates: '/x/templates'
};

const detailLinks: DocumentDetailLinks = {
	documents: '/x/documents',
	template: (uid) => `/x/templates/${uid}`
};

const row = (over: Partial<LibraryRow> = {}): LibraryRow => ({
	error: null,
	uid: 'doc_1',
	name: 'Suitability report.pdf',
	category: 'suitability',
	documentType: 'pdf',
	source: 'generated',
	status: 'active',
	scope: 'team',
	sizeBytes: 2048,
	createdAt: '2026-07-01T09:00:00Z',
	createdBy: 'Ada Adviser',
	client: 'Bo Client',
	clientContactId: null,
	recipients: '',
	version: 'v2',
	templateName: 'Suitability',
	templateUid: 'tpl_9',
	clientViewed: false,
	...over
});

// Recent activity now draws from the action-event stream — the same ActionEvent the /events page
// reads, humanised by the shared EventFeed (the documents.audit_log feed is retired).
const events: ActionEvent[] = [
	{
		id: 1,
		event_type: 'document.sent',
		created_at: '2026-07-01T10:00:00Z',
		method: 'POST',
		route: '/documents/{uid}/send',
		status: 200,
		path_params: { uid: 'doc_1' },
		metadata: { subject: { contact_ids: [7] } },
		actor_id: 'u-1',
		actor_sub: 'wos_ada',
		actor_email: 'ada@firm.test',
		actor_name: 'Ada Adviser',
		tenant_id: 1,
		tenant_uid: 'ten_a',
		tenant_name: 'Firm A',
		target_tenant_id: null,
		target_tenant_uid: null,
		target_tenant_name: null
	}
];

const libraryActions = (): DocumentsLibraryActions => ({
	archive: vi.fn(async () => {}),
	deleteFiles: vi.fn(async () => {}),
	retryRender: vi.fn(async () => {}),
	upload: vi.fn()
});

describe('DocumentsLibrary (SSR)', () => {
	it('renders the book with INJECTED document + header links', () => {
		const body = render(DocumentsLibrary, {
			props: { documents: [row()], activity: events, links: libraryLinks, openFolders: true }
		}).body;
		// The row's name link and the feed's document link both come from the builders.
		expect(body).toContain('href="/x/documents/doc_1"');
		expect(body).toContain('Suitability report');
		expect(body).toContain('Ada Adviser');
		// Requests lives in the listing kebab (a closed menu does not SSR its items).
		expect(body).toContain('Listing actions');
		const source = readFileSync(join(import.meta.dirname, 'documents-library.svelte'), 'utf8');
		expect(source).toContain('href={links.requests}');
	});

	it('puts the toolbar inside the listing card beside the rail, with Select all in its header only', () => {
		const body = render(DocumentsLibrary, {
			props: { documents: [row()], links: libraryLinks, canReadActivity: true }
		}).body;
		const list = body.indexOf('aria-label="Documents and files — drop files here to upload"');
		const search = body.indexOf('placeholder="Search by name or client…"');
		expect(search).toBeGreaterThan(body.indexOf('data-slot="card-content"', list));
		expect(body.indexOf('aria-label="Documents and files"')).toBeGreaterThan(search);
		const head = body.slice(body.indexOf('<thead'), body.indexOf('</thead>'));
		expect(head.slice(0, head.indexOf('</th>'))).toContain('aria-label="Select all"');
		expect(body.match(/aria-label="Select all"/g)).toHaveLength(1);
	});

	it('offers the three filters — type, client, author — over the API’s facets', () => {
		// SERVER mode: `listing` + `onquery` supplied, so the options come from `facets` (which
		// describe the whole match set) rather than from the rows on screen. Options derived from the
		// rows could only ever offer values already visible, which makes a filter unable to reach
		// anything the page has not already loaded.
		const body = render(DocumentsLibrary, {
			props: {
				documents: [row()],
				links: libraryLinks,
				listing: {
					q: '',
					filters: {},
					offset: 0,
					limit: 25,
					sort: null,
					dir: 'asc',
					keys: ['type', 'author', 'client']
				},
				facets: { type: ['statement'], author: ['Ada Adviser'], client: ['cnt_1'] },
				total: 1,
				onquery: () => {}
			}
		}).body;
		// The filters live in the BAR, which is shut until something is applied — so an unfiltered
		// arrival offers the funnel, not three dropdowns. Applying one is what puts it on screen.
		expect(body).toContain('aria-label="Show filters"');
		// The counter counts up to the API's `total` — the size of the match set across the whole
		// book, not of the page. That is the entire reason the filters moved server-side.
		expect(body).toContain('1 of 1');
	});

	it('shows the applied filter, named, over the API’s facets', () => {
		// Options come from `facets` (the whole match set), never from the rows on screen — options
		// derived from the rows could only ever offer what is already visible, which makes a filter
		// unable to reach anything the page has not loaded.
		const body = render(DocumentsLibrary, {
			props: {
				documents: [row()],
				links: libraryLinks,
				listing: {
					q: '',
					filters: { type: ['statement'] },
					offset: 0,
					limit: 25,
					sort: null,
					dir: 'asc',
					keys: ['type', 'author', 'client']
				},
				facets: { type: ['statement'], author: ['Ada Adviser'], client: ['cnt_1'] },
				total: 1,
				onquery: () => {}
			}
		}).body;
		expect(body).toContain('aria-label="Hide filters (1 applied)"');
		expect(body).not.toContain('aria-label="Filter by type"');
	});

	it('reports the API’s total, not the number of rows it was handed', () => {
		const body = render(DocumentsLibrary, {
			props: {
				documents: [row()],
				links: libraryLinks,
				listing: {
					q: '',
					filters: {},
					offset: 0,
					limit: 25,
					sort: null,
					dir: 'asc',
					keys: ['type', 'author', 'client']
				},
				facets: {},
				total: 87,
				onquery: () => {}
			}
		}).body;
		expect(body).toContain('1 of 87');
	});

	it('offers View · Download · Delete, in that order, and no Change type', () => {
		// The row kebab is a CONTRACT shared with the client record's Documents tab, which offers the
		// same three in the same order (pinned there too) — a reader who has learned this menu on one
		// surface should not meet a different arrangement of the same document on the other.
		//
		// Read off the source: a closed `DropdownMenu.Content` renders nothing at SSR, the same reason
		// a Select or Popover panel is invisible to these tests. Asserting the rendered body would
		// simply pass for the wrong reason.
		const menu = rowMenuSource();
		const order = ['View', 'Download', 'Delete'].map((l) => menu.indexOf(l));
		expect(order.every((i) => i >= 0)).toBe(true);
		expect(order).toEqual([...order].sort((a, b) => a - b));
		// A document's type is set at upload and is not changed afterwards.
		expect(menu).not.toContain('Change type');
	});

	it('keeps Retry OUT of the kebab — it lives inline on the failed row', () => {
		// The kebab carried a duplicate Retry. It is the four standard actions, not a home for a
		// recovery step that applies to one rare status; the inline retry beside the name is more
		// visible and is now the only one.
		expect(rowMenuSource()).not.toContain('Retry rendering');
		const failed = row({ uid: 'doc_9', status: 'failed', error: 'Template variable missing' });
		const body = render(DocumentsLibrary, {
			props: {
				documents: [failed],
				links: libraryLinks,
				actions: libraryActions(),
				openFolders: true
			}
		}).body;
		expect(body).toContain('Retry rendering Suitability report.pdf');
	});

	it('does not print a row’s id in the listing, and never trails one off', () => {
		// The id used to sit beside the name, `shrink-0`, so two documents generated from the same
		// template (same name) stayed distinguishable. It is out of the LISTING for now: a `shrink-0`
		// id takes its width from the name column, and while dev still holds pre-nanoid ids (32 hex
		// characters against today's 12) that is most of the cell — the name, which is what the row is
		// actually scanned by, loses. The id is still on the RECORD, whole and copyable.
		//
		// Asserted on the rendered TEXT, not on the body: the uid is legitimately still in the row's
		// href, so `toContain` would pass either way — which is precisely how this test kept passing
		// after the id was removed.
		const long = row({ uid: 'doc_69063cf7ca1b44688b8b5987' });
		const body = render(DocumentsLibrary, {
			props: { documents: [long], links: libraryLinks, openFolders: true }
		}).body;
		expect(body).toContain('href="/x/documents/doc_69063cf7ca1b44688b8b5987"');
		expect(body).not.toContain('>doc_69063cf7ca1b44688b8b5987<');
		// Whatever is shown, an id is never followed by an ellipsis — half an id cannot be read out on
		// a call or pasted into a query. Scoped to an id rather than banning `…` outright, because the
		// page legitimately carries "Loading…"-style copy.
		expect(body).not.toMatch(/doc_[A-Za-z0-9_-]*…/);
	});

	it('downloads from the kebab instead of opening the file in a tab', () => {
		// The API serves a PDF `Content-Disposition: inline`, so an onclick that did
		// `location.assign(href)` RENDERED it in the tab rather than saving it. Both Download items
		// have to be real anchors carrying `download`, which the same-origin proxy makes the browser
		// honour. Read off the source for the same reason the menu ORDER is: a closed
		// `DropdownMenu.Content` renders nothing at SSR, so asserting the body would pass for the
		// wrong reason.
		const menu = rowMenuSource();
		// Two Download items — a supporting file and a document — and neither may navigate.
		expect(menu.match(/download=\{downloadName\(doc\)\}/g)).toHaveLength(2);
		expect(menu).not.toMatch(/onclick=\{\(\) => navigate\(links\.(documentContent|fileDownload)/);
		// And never the bare `doc.name`, which has no extension on a generated document.
		expect(menu).not.toMatch(/download=\{doc\.name\}/);
		// The document item is behind the has-bytes guard: a queued or failed render has nothing to
		// save, and `<a download>` would save the proxy's error page as a file named after it.
		expect(menu).toMatch(/\{#if isDownloadable\(doc\)\}/);
	});

	it('read-only by default: no actions ⇒ no Upload/Generate in the kebab', () => {
		const body = render(DocumentsLibrary, {
			props: { documents: [row()], links: libraryLinks }
		}).body;
		// Requests still lives in the kebab — it is navigation, not a mutation.
		expect(body).toContain('Listing actions');
		expect(body).not.toContain('Upload');
		expect(body).not.toContain('Generate');
		expect(body).not.toContain('href="/x/templates"');
	});

	it('shows the write affordances when the app injects actions', () => {
		// Items sit in a closed DropdownMenu, which SSR does not paint. Pin the trigger
		// and the source so Generate/Upload cannot silently leave the kebab.
		const body = render(DocumentsLibrary, {
			props: { documents: [row()], links: libraryLinks, actions: libraryActions() }
		}).body;
		expect(body).toContain('Listing actions');
		const source = readFileSync(join(import.meta.dirname, 'documents-library.svelte'), 'utf8');
		expect(source).toContain('Upload');
		expect(source).toContain('Generate');
		expect(source).toContain('links.templates');
	});

	it('offers NOTIFY and never a send, however permissive the actions are', () => {
		// The whole point of the swap: a client filed against a document can already open it, so the
		// only client-facing act left is telling them. "Send" reappearing anywhere on this surface
		// would re-assert that visibility is ours to grant.
		const body = render(DocumentsLibrary, {
			props: { documents: [row()], links: libraryLinks, actions: libraryActions() }
		}).body;
		expect(body).not.toContain('Send');
		expect(body).not.toContain('Send to a client');
	});

	it('offers NO notify affordance at all — neither toolbar button nor row item', () => {
		// There were two tests here, one asserting the toolbar Notify button appeared with
		// `documents:send` and one asserting it vanished without it. The affordance is gone entirely, so
		// the permission no longer earns anything on this surface.
		//
		// WHY IT WENT. Notifying names ONE document and ONE client. The library holds neither: the
		// button opened a dialog that asked you to pick both again, from a list of every document in the
		// firm — a worse version of the same act available where the context already exists (a
		// document's own record, and a client's Documents tab, where the client is fixed by the route).
		//
		// Asserted for BOTH shapes of `actions`, because "no affordance" has to hold however the app
		// wires it — a full kit and a bare object must look the same here.
		for (const actions of [libraryActions(), {}]) {
			const body = render(DocumentsLibrary, {
				props: { documents: [row()], links: libraryLinks, actions }
			}).body;
			expect(body).not.toMatch(/> Notify</); // the toolbar button's own text node
			expect(body).not.toContain('Tell a client about this'); // the row-menu item
		}
		// …and the drafting kit is untouched by its removal.
		const drafting = render(DocumentsLibrary, {
			props: { documents: [row()], links: libraryLinks, actions: libraryActions() }
		}).body;
		expect(drafting).toContain('Listing actions');
	});

	it('has no Live/Archived switch, and offers no restore', () => {
		// Both slices used to arrive and a Segmented flipped between them. The switch is gone with the
		// vocabulary behind it: browsing a firm's deleted documents is an ops view, and a listing that
		// offers Restore in bulk makes undeleting an ordinary act.
		const body = render(DocumentsLibrary, {
			props: { documents: [row()], links: libraryLinks, actions: libraryActions() }
		}).body;
		expect(body).not.toContain('Archived');
		expect(body).not.toContain('Restore');
		// Structural, not just textual: Segmented is the only tablist this surface ever rendered.
		expect(body).not.toContain('role="tablist"');
	});

	it('says DELETE, not archive, on the destructive affordance', () => {
		const body = render(DocumentsLibrary, {
			props: { documents: [row()], links: libraryLinks, actions: libraryActions() }
		}).body;
		// The kebab item and the bulk bar are both client-side, but the confirm dialog's copy is not:
		// what SSR can prove is that the archive WORD is nowhere on the surface.
		expect(body).not.toContain('Archive');
	});

	it('offers a retry only to a writable surface, on a failed render', () => {
		const failed = row({ uid: 'doc_9', status: 'failed', error: 'Template variable missing' });
		const props = { documents: [failed], links: libraryLinks };
		expect(render(DocumentsLibrary, { props }).body).not.toContain('Retry rendering');
		const writable = render(DocumentsLibrary, {
			props: { ...props, actions: libraryActions(), openFolders: true }
		}).body;
		expect(writable).toContain('Retry rendering Suitability report.pdf');
		expect(writable).toContain('Template variable missing');
	});

	it('distinguishes the empty library from an empty filter result', () => {
		const body = render(DocumentsLibrary, {
			props: { documents: [], links: libraryLinks }
		}).body;
		expect(body).toContain('No documents yet');
	});

	it('always groups Client → Type with no Group by control or pager, shut on first paint', () => {
		const shut = render(DocumentsLibrary, {
			props: { documents: [row()], links: libraryLinks }
		}).body;
		expect(shut).not.toContain('aria-label="Group by"');
		// Shut by default, so nothing jumps on hydration.
		expect(shut).toMatch(/aria-expanded="false"[^>]*aria-label="Bo Client, 1 row"/);
		expect(shut).not.toContain('aria-label="Pagination"');
	});
});

describe('DocumentsLibrarySkeleton (SSR)', () => {
	// The live library's grid tracks, or the page jumps on load.
	const RAIL_GRID = 'lg:grid-cols-[minmax(0,1fr)_22rem]';

	it('is the library — py-3 rows and the Recent activity rail — not a generic h-11 table', () => {
		const live = render(DocumentsLibrary, {
			props: { documents: [], links: libraryLinks, canReadActivity: true }
		}).body;
		expect(live).toContain(RAIL_GRID);
		const body = render(DocumentsLibrarySkeleton, { props: { actions: true } }).body;
		expect(body).toContain('aria-label="Loading"');
		expect(body).toContain('Recent activity');
		expect(body).toContain(RAIL_GRID);
		expect(body).toContain('py-3');
		expect(body).toContain('md:h-[calc(100svh-var(--topbar-h,4rem))]');
	});

	it('drops the rail when the reader cannot see activity', () => {
		const body = render(DocumentsLibrarySkeleton, { props: { activity: false } }).body;
		expect(body).not.toContain('Recent activity');
		expect(body).not.toContain(RAIL_GRID);
	});
});

const detailActions = (): DocumentDetailActions => ({
	rename: vi.fn(async () => true),
	archive: vi.fn(async () => {}),
	restore: vi.fn(async () => {})
});

describe('DocumentDetail (SSR)', () => {
	const props = {
		doc: row(),
		events,
		previewUrl: '/x/documents/doc_1/content',
		previewContentType: 'application/pdf',
		links: detailLinks
	};

	it('renders the record off the injected data, with the INJECTED template link', () => {
		const body = render(DocumentDetail, { props }).body;
		expect(body).toContain('Suitability report.pdf');
		expect(body).toContain('Bo Client');
		// The Details tab's provenance link comes from the builder, not a hardcoded /templates.
		expect(body).toContain('href="/x/templates/tpl_9"');
		expect(body).toContain('Suitability · v2');
	});

	it('has TWO tabs — Details and Events — and no Requests tab', () => {
		// The Requests tab (who has been asked to act on this document, plus a "Raise a request" CTA)
		// is gone: a request asks a client for something that does not exist yet, so raising one from
		// the record of a document that already exists was the wrong door. Requests are reached from
		// the hub, or from a client's own record.
		const body = render(DocumentDetail, { props }).body;
		expect(body).toContain('Details');
		expect(body).toContain('Events');
		expect(body).not.toContain('Requests');
		expect(body).not.toContain('Raise a request');
	});

	it('is READ-ONLY when nothing is injected: download alone', () => {
		const body = render(DocumentDetail, { props }).body;
		expect(body).toContain('Download');
		expect(body).not.toContain('Delete document');
		expect(body).not.toContain('aria-label="Rename"');
	});

	it('shows the writes when the app injects actions', () => {
		const body = render(DocumentDetail, {
			props: { ...props, actions: detailActions() }
		}).body;
		expect(body).toContain('Delete document');
		expect(body).toContain('aria-label="Rename"');
	});

	it('withholds Delete and STATES WHY for a provider-synced document', () => {
		// Withholding alone is what the library does — a menu item that is simply absent. A RECORD is
		// where a reader comes to find out what they may do with a document, so a missing button is
		// not an acceptable answer here: the API answers 409, and the page has to say so.
		const body = render(DocumentDetail, {
			props: { ...props, doc: { ...props.doc, deletable: false }, actions: detailActions() }
		}).body;
		expect(body).not.toContain('Delete document');
		expect(body).toContain('synced from a provider');
		// Renaming is untouched — the flag is about deletion, not about editing.
		expect(body).toContain('aria-label="Rename"');
	});

	it('never offers a send, and never says the document was shared', () => {
		// The record used to carry "Send to a client" plus a "Shared with / Not shared yet" row, both
		// from when a send was what let a client see the document. Filing does that now, so the row
		// states DELIVERY (what a `role='recipient'` link actually records) and no button claims one.
		const body = render(DocumentDetail, {
			props: { ...props, actions: detailActions() }
		}).body;
		expect(body).not.toContain('Send to a client');
		expect(body).not.toContain('Shared with');
		expect(body).not.toContain('Not shared yet');
		// …and no delivery row either. It stated what a `role='recipient'` link records, which only ever
		// came from a SEND — so on a record that can no longer send it read "Nobody yet" for ever: not a
		// fact about the document, a description of a feature that is gone. Filing a document under a
		// client is what lets them see it, and the client it is filed for is in the header.
		expect(body).not.toContain('Delivered to');
		expect(body).not.toContain('Nobody yet');
	});

	it('states the file, its origin and its client — grouped, not one flat list', () => {
		// The tab was seven undifferentiated rows. A document has three separable things to say and a
		// reader after one of them should not have to read the other two, so the headings are the
		// information design rather than ornament.
		const body = render(DocumentDetail, { props }).body;
		for (const band of ['File', 'Origin']) {
			expect(body).toContain(band);
		}
		// …and the facts inside them.
		expect(body).toContain('Category');
		expect(body).toContain('Format');
		expect(body).toContain('Size');
		expect(body).toContain('Source');
		expect(body).toContain('Created');
	});

	it('states the reference as an ordinary row, and makes the id itself the copy target', () => {
		// It was a stacked band because the old 36-character `doc_…` uid truncated in a two-column
		// row. A public id is twelve characters now, so the reason expired and a heading over a
		// single value was noise — it reads as a row like Created and Uploaded by.
		const body = render(DocumentDetail, { props }).body;
		expect(body).toContain('Reference');
		expect(body).toContain('doc_1');
		// The whole value sits INSIDE the button, so a click anywhere on the id copies it. The icon
		// alone was a very small target for the one field on the page people reach for.
		const open = body.lastIndexOf('<button', body.indexOf('Copy document id'));
		const close = body.indexOf('</button>', open);
		expect(open).toBeGreaterThan(-1);
		expect(body.slice(open, close)).toContain('doc_1');
		// Never truncated: clipping is exactly the defect the stacked layout existed to avoid, so a
		// longer legacy id has to wrap instead.
		expect(body).not.toMatch(/truncate[^"]*"[^>]*>doc_1/);
	});

	it('states the REAL format, and never guesses one', () => {
		// `fileFormat` defaults to 'pdf' for any unknown MIME — right for picking an icon, wrong for
		// stating a fact, since it would label a spreadsheet "PDF". The row reads the content type the
		// app actually fetched, so a Word document says Word…
		const word = render(DocumentDetail, {
			props: {
				...props,
				previewContentType:
					'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
			}
		}).body;
		expect(word).toContain('Word (DOCX)');
		expect(word).not.toContain('>PDF<');

		// …an unrecognised one shows the raw MIME rather than a guess (still useful to whoever asks)…
		const odd = render(DocumentDetail, {
			props: { ...props, previewContentType: 'application/x-cbor' }
		}).body;
		expect(odd).toContain('application/x-cbor');

		// …and a document with no bytes yet says so instead of naming a format it does not have.
		const queued = render(DocumentDetail, {
			props: { ...props, doc: row({ status: 'queued' }), previewContentType: null }
		}).body;
		expect(queued).toContain('Pending');
	});

	it('shows the render time only when it differs from the queue time', () => {
		// `createdAt` is when a generate was QUEUED and `generatedAt` when it finished — genuinely two
		// facts on a document that waited. Printing the same timestamp twice is just noise, so an
		// instant render shows one row.
		const waited = render(DocumentDetail, {
			props: {
				...props,
				doc: row({ createdAt: '2026-08-19T09:15:00Z', generatedAt: '2026-08-19T09:42:00Z' })
			}
		}).body;
		expect(waited).toContain('Rendered');

		const instant = render(DocumentDetail, {
			props: {
				...props,
				doc: row({ createdAt: '2026-08-19T09:15:00Z', generatedAt: '2026-08-19T09:15:00Z' })
			}
		}).body;
		expect(instant).not.toContain('Rendered');
	});

	it('links the client when the app injects a builder, and states them plainly when it does not', () => {
		// One click from "which document is this" to "whose is it". A link only with the contact ID —
		// matching a person by display name is how a reader reaches the wrong Margaret.
		const linked = render(DocumentDetail, {
			props: {
				...props,
				doc: row({ client: 'Ada Client', clientContactUid: 'con_ada' }),
				links: { ...detailLinks, client: (uid: string) => `/x/clients/${uid}` }
			}
		}).body;
		expect(linked).toContain('href="/x/clients/con_ada"');

		expect(linked).toContain('Filed for');

		// No builder (the admin portal's read-only mount) ⇒ the name, unlinked. Never a dead anchor.
		const plain = render(DocumentDetail, {
			props: { ...props, doc: row({ client: 'Ada Client', clientContactUid: 'con_ada' }) }
		}).body;
		expect(plain).toContain('Ada Client');
		expect(plain).not.toContain('/x/clients/8');

		// …and a document filed for NOBODY drops the whole band rather than printing a dash under a
		// heading — the same rule that retired the permanently-empty "Delivered to" row.
		const nobody = render(DocumentDetail, {
			props: { ...props, doc: row({ client: '', clientContactId: null }) }
		}).body;
		expect(nobody).not.toContain('Filed for');
	});

	it('shows the document id in full, with a copy affordance', () => {
		// The id is what every OTHER system calls this document — the object-storage key holds it, every
		// event records it, a request line item targets it — so it is the value read out on a support call.
		// In FULL, because a truncated identifier is not an identifier. Neither a slice nor a CSS
		// truncation belongs here: the first cut of this row showed `doc_69063cf7ca1b44688b8b59…`, a
		// reference nobody can act on. (`docRef`, which trailed every uid off at ten characters for the
		// table, is gone for the same reason — the listing shows the id whole too.)
		const body = render(DocumentDetail, { props }).body;
		expect(body).toContain('Reference');
		expect(body).toContain('doc_1');
		expect(body).toContain('aria-label="Copy document id"');
		// Selectable in its own right, so a reader without a clipboard (plain http, refused permission)
		// can still take it.
		expect(body).toContain('select-all');
		// Wraps rather than truncates, so an id of ANY length stays readable. Deliberately says nothing
		// about how long one is: a book can hold more than one generation of id, and this row's job is
		// to show whatever it is handed rather than to know the current shape.
		expect(body).toContain('break-all');
	});

	it('shows the reference to a READ-ONLY reader too', () => {
		// It is a fact about the record, not a write — the admin portal's read-only mount needs it as much
		// as the platform's, since a support call is exactly when someone reads an id out.
		const body = render(DocumentDetail, { props: { ...props, actions: null } }).body;
		expect(body).toContain('aria-label="Copy document id"');
	});

	it('offers no notify affordance and no request CTA, however writable the reader is', () => {
		// This was two tests about a Notify button appearing with `documents:send` and vanishing
		// without it. The button is gone entirely — telling a client about a document was replaced by a
		// document REQUEST, which asks them to act and records whether they did — and the request CTA
		// has since gone the same way, along with the `canRequest` prop that gated it. Neither returns
		// for a writable reader, which is the only way either could come back by accident.
		const writable = render(DocumentDetail, {
			props: { ...props, actions: detailActions() }
		}).body;
		expect(writable).toContain('Delete document');
		expect(writable).not.toContain('Tell a client about this');
		expect(writable).not.toContain('Raise a request');
	});

	it('reads a deleted document as DELETED, and offers Restore only when writable', () => {
		// `archived` is still the API's state; this record is the only place a firm user meets one now
		// (the library has no deleted view), so its badge and its Restore have to say the same word as
		// the control that got it here.
		const archived = { ...props, doc: row({ status: 'archived' }) };
		const readOnly = render(DocumentDetail, { props: archived }).body;
		expect(readOnly).toContain('Deleted');
		expect(readOnly).not.toContain('Archived');
		expect(readOnly).not.toContain('Restore to the library');
		const writable = render(DocumentDetail, {
			props: { ...archived, actions: detailActions() }
		}).body;
		expect(writable).toContain('Restore to the library');
		// A deleted record swaps Delete for Restore — never both.
		expect(writable).not.toContain('Delete document');
	});

	it('renders the not-found state with the injected list link', () => {
		const body = render(DocumentDetail, { props: { doc: null, links: detailLinks } }).body;
		expect(body).toContain('Document not found');
		expect(body).toContain('href="/x/documents"');
	});
});

/** The library row kebab's markup, read off the component — see the tests above for why SSR cannot
 *  see inside a closed DropdownMenu.Content. */
function rowMenuSource(): string {
	const src = readFileSync(join(import.meta.dirname, 'documents-library.svelte'), 'utf8');
	const from = src.indexOf('{#snippet rowMenu(');
	expect(from).toBeGreaterThan(-1);
	// Walk to the MATCHING close, counting depth. It used to slice to the first `{/snippet}`, which
	// silently truncated the menu the moment an item needed a nested snippet of its own — the
	// `{#snippet child({ props })}` the Download anchors use. A source scan that reads half of what
	// it thinks it read is worse than no scan: it reports absence for something plainly present.
	let depth = 0;
	for (let i = from; i < src.length;) {
		if (src.startsWith('{#snippet', i)) {
			depth += 1;
			i += '{#snippet'.length;
		} else if (src.startsWith('{/snippet}', i)) {
			depth -= 1;
			if (depth === 0) return src.slice(from, i);
			i += '{/snippet}'.length;
		} else i += 1;
	}
	throw new Error('rowMenu snippet is unterminated');
}
