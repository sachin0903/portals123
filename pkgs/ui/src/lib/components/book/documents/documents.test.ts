import { describe, expect, it } from 'vitest';
import {
	byRequestUrgency,
	documentCategoryLabel,
	documentInFlight,
	downloadName,
	FORMAT_LABELS,
	isDownloadable,
	isRequestOverdue,
	isTransportStatus,
	requestDueLabel,
	requestProgress,
	toRow,
	uploadTransportError,
	type ApiDocument,
	type ApiRequest,
	type ApiRequestItem
} from './documents.js';

/* ── Document categories ──────────────────────────────────────────────────── */

describe('documentCategoryLabel', () => {
	it('labels every category the pickers offer', () => {
		expect(documentCategoryLabel('suitability')).toBe('Suitability report');
		expect(documentCategoryLabel('factfind')).toBe('Fact-find');
		expect(documentCategoryLabel('tax')).toBe('Tax');
	});

	it('labels the system-only terms type (existing rows must still read properly)', () => {
		expect(documentCategoryLabel('terms')).toBe('Terms');
	});

	it('humanises a free-text value — sentence case, one hyphenation, the ID acronym', () => {
		expect(documentCategoryLabel('proof_of_address')).toBe('Proof of address');
		expect(documentCategoryLabel('tax_certificate')).toBe('Tax certificate');
		expect(documentCategoryLabel('fact_find')).toBe('Fact-find');
		expect(documentCategoryLabel('id_verification')).toBe('ID verification');
	});

	it('never renders an empty category as a blank', () => {
		expect(documentCategoryLabel('')).toBe('Uncategorised');
	});
});

/* ── Row mapping ──────────────────────────────────────────────────────────── */

function apiDoc(overrides: Partial<ApiDocument> = {}): ApiDocument {
	return {
		uid: 'doc_abc',
		name: 'Suitability Report',
		document_type: 'suitability',
		source: 'generated',
		status: 'generated',
		content_type: 'application/pdf',
		size_bytes: 1024,
		created_at: '2026-07-24T12:00:00Z',
		generated_at: '2026-07-24T12:00:05Z',
		template_version: 2,
		template_name: 'Suitability',
		template_uid: 'tpl_1',
		created_by: 'James Okafor',
		client: 'Ada Client',
		client_contact_id: null,
		client_contact_uid: null,
		recipients: null,
		client_viewed: false,
		client_viewed_at: null,
		sent_to_client: false,
		sent_at: null,
		...overrides
	};
}

describe('toRow', () => {
	it('carries a failed render’s reason onto the row', () => {
		const row = toRow(apiDoc({ status: 'failed', error: 'Template variable {total} is missing.' }));
		expect(row.status).toBe('failed');
		expect(row.error).toBe('Template variable {total} is missing.');
	});

	it('maps recipients (delivery) separately from client (the subject it is made for)', () => {
		const row = toRow(apiDoc({ client: 'Ada Client', recipients: 'Bob Payne, Carol Vane' }));
		expect(row.client).toBe('Ada Client');
		expect(row.recipients).toBe('Bob Payne, Carol Vane');
	});

	it('maps the per-client sent state (delivered vs merely filed under them)', () => {
		const filed = toRow(apiDoc({ sent_to_client: false, sent_at: null }));
		expect(filed.sentToClient).toBe(false);
		expect(filed.sentAt).toBeUndefined();

		const sent = toRow(apiDoc({ sent_to_client: true, sent_at: '2026-07-30T09:00:00Z' }));
		expect(sent.sentToClient).toBe(true);
		expect(sent.sentAt).toBe('2026-07-30T09:00:00Z');
	});

	it('defaults the reason to null when the API sends none', () => {
		expect(toRow(apiDoc()).error).toBeNull();
	});

	it('maps the canonical generated status onto the UI’s live state', () => {
		expect(toRow(apiDoc()).status).toBe('active');
	});

	it('keeps a custodian document as provider rather than calling it generated', () => {
		expect(toRow(apiDoc({ source: 'provider' })).source).toBe('provider');
	});

	it('tags a terms declaration (no stored file) as HTML, not the file-less PDF default', () => {
		const row = toRow(apiDoc({ document_type: 'terms', content_type: null }));
		expect(row.documentType).toBe('html');
		expect(FORMAT_LABELS[row.documentType]).toBe('HTML');
		expect(downloadName(row)).toBe(`${row.name}.html`);
		expect(toRow(apiDoc({ document_type: 'review', content_type: null })).documentType).toBe('pdf');
	});
});

/* ── Requests ─────────────────────────────────────────────────────────────── */

function item(overrides: Partial<ApiRequestItem> = {}): ApiRequestItem {
	return {
		uid: 'rqi_1',
		action: 'upload',
		title: 'Your latest statement',
		category: null,
		format_hint: null,
		form_uid: null,
		target_document_uid: null,
		fulfilled_document_uid: null,
		status: 'pending',
		...overrides
	};
}

function req(overrides: Partial<ApiRequest> = {}): ApiRequest {
	return {
		uid: 'req_1',
		contact_id: 1,
		contact_uid: null,
		contact_name: 'Ada Client',
		priority: 'normal',
		message: null,
		due_date: null,
		status: 'pending',
		created_at: '2026-07-24T12:00:00Z',
		items: [item()],
		...overrides
	};
}

describe('requestProgress', () => {
	it('counts fulfilled items against the ones that still apply', () => {
		const r = req({
			items: [
				item({ uid: 'a', status: 'fulfilled' }),
				item({ uid: 'b', status: 'pending' }),
				// A cancelled line is neither outstanding nor an achievement.
				item({ uid: 'c', status: 'cancelled' })
			]
		});
		expect(requestProgress(r)).toEqual({ done: 1, total: 2 });
	});
});

describe('isRequestOverdue', () => {
	const now = new Date('2026-07-28T09:00:00Z');

	it('is false for a request due today (the client has the whole day)', () => {
		expect(isRequestOverdue(req({ due_date: '2026-07-28' }), now)).toBe(false);
	});

	it('is true once the due date has passed', () => {
		expect(isRequestOverdue(req({ due_date: '2026-07-27' }), now)).toBe(true);
	});

	it('is false with no deadline, and for a request that is already settled', () => {
		expect(isRequestOverdue(req({ due_date: null }), now)).toBe(false);
		expect(isRequestOverdue(req({ due_date: '2026-07-01', status: 'fulfilled' }), now)).toBe(false);
		expect(isRequestOverdue(req({ due_date: '2026-07-01', status: 'cancelled' }), now)).toBe(false);
	});
});

describe('requestDueLabel', () => {
	const now = new Date('2026-07-28T09:00:00Z');

	it('phrases the deadline relative to today', () => {
		expect(requestDueLabel(req({ due_date: '2026-07-28' }), now)).toBe('Due today');
		expect(requestDueLabel(req({ due_date: '2026-07-29' }), now)).toBe('Due tomorrow');
		expect(requestDueLabel(req({ due_date: '2026-07-31' }), now)).toBe('Due in 3 days');
		expect(requestDueLabel(req({ due_date: '2026-07-27' }), now)).toBe('1 day overdue');
		expect(requestDueLabel(req({ due_date: '2026-07-24' }), now)).toBe('4 days overdue');
	});

	it('says nothing when there is no deadline', () => {
		expect(requestDueLabel(req(), now)).toBe('');
	});
});

describe('byRequestUrgency', () => {
	it('chases overdue first, then the nearest deadline, then priority, then the newest', () => {
		// `isRequestOverdue` inside the comparator reads the real clock, so anchor the fixtures either
		// side of a date that is unambiguously past/future for any run of this suite.
		const overdue = req({ uid: 'overdue', due_date: '2020-01-01' });
		const soon = req({ uid: 'soon', due_date: '2099-01-01' });
		const later = req({ uid: 'later', due_date: '2099-06-01' });
		const undated = req({ uid: 'undated' });
		const order = [undated, later, soon, overdue].toSorted(byRequestUrgency).map((r) => r.uid);
		expect(order).toEqual(['overdue', 'soon', 'later', 'undated']);
	});

	it('ranks priority when neither request has a deadline', () => {
		const urgent = req({ uid: 'urgent', priority: 'urgent' });
		const low = req({ uid: 'low', priority: 'low' });
		expect([low, urgent].toSorted(byRequestUrgency).map((r) => r.uid)).toEqual(['urgent', 'low']);
	});

	it('falls back to newest-first for otherwise equal requests', () => {
		const older = req({ uid: 'older', created_at: '2026-07-01T00:00:00Z' });
		const newer = req({ uid: 'newer', created_at: '2026-07-20T00:00:00Z' });
		expect([older, newer].toSorted(byRequestUrgency).map((r) => r.uid)).toEqual(['newer', 'older']);
	});
});

describe('uploadTransportError', () => {
	it('explains a 413 as the portal refusing the whole drop', () => {
		// adapter-node's BODY_SIZE_LIMIT rejects the request before the action runs, and a multi-file
		// drop is ONE request — so the advice has to be about the drop, not about one file.
		const message = uploadTransportError(413);
		expect(message).toMatch(/too large/i);
		expect(message).toMatch(/fewer or smaller/i);
	});

	it('names a gateway failure as unreachable rather than as a rejected file', () => {
		for (const status of [502, 503, 504]) {
			expect(uploadTransportError(status)).toMatch(/could not be reached/i);
		}
	});

	it('falls back to the status, never to a parse error', () => {
		expect(uploadTransportError(500)).toBe('Upload failed (HTTP 500).');
	});
});

describe('isTransportStatus', () => {
	// The adapter's body-size 413 is raised while SvelteKit reads the body, so it can come back as a
	// serialised `{type:'error'}` rather than as an HTML page a `deserialize` throws on. A caller that
	// only watched for the throw showed SvelteKit's bare "Payload Too Large".
	it('covers the statuses that never reach a form action', () => {
		for (const status of [413, 502, 503, 504]) expect(isTransportStatus(status)).toBe(true);
	});

	it('leaves what an action itself answered alone', () => {
		// 422 is the dialog's too-many-files refusal, 403 its permission one; both carry a message
		// worth more than "Upload failed (HTTP n)". 400/500 likewise come from code that ran.
		for (const status of [200, 400, 403, 422, 500]) expect(isTransportStatus(status)).toBe(false);
	});
});

describe('downloadName', () => {
	// A generated document's name is a TITLE with no extension, and `<a download>` takes the
	// browser's filename from that attribute — so downloading one saved a file the OS could not type
	// or open ("Suitability Report — Q1 2026", shown as "File").
	it('appends an extension to a title that has none', () => {
		expect(downloadName({ name: 'Suitability Report — Q1 2026', documentType: 'pdf' })).toBe(
			'Suitability Report — Q1 2026.pdf'
		);
	});

	it('prefers the RAW content type over the row’s format tag', () => {
		// This is the whole reason the second argument exists. `documentType` comes from `fileFormat`,
		// which defaults to 'pdf' for anything it does not recognise — so trusting it would name a
		// spreadsheet `.pdf`. A caller holding the real MIME gets the truth.
		expect(
			downloadName(
				{ name: 'Fee schedule', documentType: 'pdf' },
				'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
			)
		).toBe('Fee schedule.xlsx');
	});

	it('reads a MIME that carries parameters', () => {
		expect(downloadName({ name: 'Notes' }, 'text/plain; charset=utf-8')).toBe('Notes.txt');
	});

	it('leaves a name that already carries a known extension alone', () => {
		// A supporting file keeps its own extension; the listing strips it for DISPLAY only, so
		// appending here would produce `scan.pdf.pdf`.
		expect(downloadName({ name: 'scan.pdf', documentType: 'pdf' })).toBe('scan.pdf');
		expect(downloadName({ name: 'photo.JPEG', documentType: 'jpg' })).toBe('photo.JPEG');
	});

	it('guesses NOTHING when it knows nothing', () => {
		// A missing extension is a nuisance; a wrong one is a lie about the file's contents.
		expect(downloadName({ name: 'Mystery' })).toBe('Mystery');
		expect(downloadName({ name: 'Mystery' }, 'application/x-unknown-thing')).toBe('Mystery');
	});
});

describe('documentInFlight', () => {
	it('is a render the worker has not finished', () => {
		expect(documentInFlight('queued')).toBe(true);
		expect(documentInFlight('generating')).toBe(true);
		expect(documentInFlight('generated')).toBe(false);
		expect(documentInFlight('failed')).toBe(false);
		expect(documentInFlight(null)).toBe(false);
	});
});

describe('isDownloadable', () => {
	// An `<a download>` saves whatever comes back. On a row with no bytes the proxy answers an error,
	// so the browser cheerfully saved the ERROR PAGE as a ~1KB file named after the document — worse
	// than no affordance, because it looks like it worked.
	it('refuses a render that has produced no bytes', () => {
		expect(isDownloadable({ status: 'queued' })).toBe(false);
		expect(isDownloadable({ status: 'generating' })).toBe(false);
		expect(isDownloadable({ status: 'failed' })).toBe(false);
	});

	it('allows every status that HAS bytes, including the historical ones', () => {
		// `superseded` and `archived` still have content — a reader looking at an old version is
		// entitled to save it.
		for (const status of ['active', 'pending_action', 'superseded', 'archived'] as const) {
			expect(isDownloadable({ status })).toBe(true);
		}
	});
});
