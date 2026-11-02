import type { StatusTone } from '../../ui/status-badge/index.js';
import type { DocSource, DocStatus, DocumentRow } from './display.js';
import { sentenceCase } from '../../../sentence-case.js';

/** The API's DocumentOut shape (storage slice). */
export type ApiDocument = {
	uid: string;
	name: string | null;
	document_type: string;
	source: string;
	status: string;
	content_type: string | null;
	size_bytes: number | null;
	created_at: string;
	generated_at: string | null;
	/** Template (name + version + uid) that produced a generated document; null for uploads. */
	template_version: number | null;
	template_name: string | null;
	template_uid: string | null;
	/** Staff member who created it, and the ONE client it's *made for* (intended_contact); null
	 * until set. Distinct from `recipients` — what it's about, not who it went to. */
	created_by: string | null;
	client: string | null;
	/** …and their contact id. The name is for display; the id is what opens another surface ON that
	 *  client (see `LibraryRow.clientContactId`). */
	client_contact_id: number | null;
	client_contact_uid: string | null;
	/** Who it has actually been delivered to (document_contacts recipients, comma-joined); null
	 * until sent. Distinct from `client` (the subject): delivery, not what it's about. */
	recipients: string | null;
	/** Whether the client passed as `contact_id` to the list endpoint has opened this document
	 * (any of their logins), and when — only meaningful when that param was given (the
	 * client-detail Documents tab); always false/null on the tenant-wide overview. */
	client_viewed: boolean;
	client_viewed_at: string | null;
	/** Whether this document has been SENT (delivered) to the client passed as `contact_id`, and
	 * when — the client-detail Documents tab's "Sent / Not sent" state. A document made-for a client
	 * but not yet sent reads `sent_to_client=false`. False/null on the tenant-wide overview. */
	sent_to_client: boolean;
	sent_at: string | null;
	/** Why a `failed` render failed, already mapped to a human sentence by the API (null otherwise).
	 * Surfaced on the row + the detail page so a dead generation isn't a silent dead end. */
	error?: string | null;
	/** Whether the firm may DELETE this document (migration 0122). False for a record it does not
	 * own — one synced from a custodian — where the API answers 409. Optional so a response from an
	 * older API reads as deletable rather than locking a whole library nobody can archive. */
	deletable?: boolean;
};

/** Action a client is asked to take on a request item. ('agree' is confirm-only for now — view the
 * document and tick "I agree"; real signing comes later.) */
export type RequestAction = 'upload' | 'agree' | 'acknowledge' | 'complete_form';

/** The API's RequestItemOut shape (one line of a request). */
export type ApiRequestItem = {
	uid: string;
	action: RequestAction;
	title: string;
	category: string | null;
	format_hint: string | null;
	form_uid: string | null;
	target_document_uid: string | null;
	fulfilled_document_uid: string | null;
	status: 'pending' | 'fulfilled' | 'cancelled';
};

/** The API's RequestOut shape (an adviser request with one or more items). */
export type ApiRequest = {
	uid: string;
	contact_id: number;
	/** The client's own uid — what a link to their record is built from. `contact_id` is the API's
	 *  join key and never an address. Nullable: the request outlives a deleted contact. */
	contact_uid: string | null;
	contact_name: string | null;
	priority: 'low' | 'normal' | 'high' | 'urgent';
	message: string | null;
	due_date: string | null;
	status: 'pending' | 'fulfilled' | 'cancelled';
	created_at: string;
	items: ApiRequestItem[];
};

/** Human labels for the request actions (shared by both portals' request UIs). */
export const REQUEST_ACTION_LABELS: Record<RequestAction, string> = {
	upload: 'Upload',
	agree: 'Agree',
	acknowledge: 'Acknowledge',
	complete_form: 'Complete form'
};

/** What we ask the client to do, phrased for the staff-side request builder. */
export const REQUEST_ACTION_HINTS: Record<RequestAction, string> = {
	upload: 'The client uploads a file, which lands in the library as a document.',
	agree: 'The client reads a document you attach and ticks “I agree”.',
	acknowledge: 'The client confirms they have seen a document or a set of terms.',
	complete_form: 'The client fills one of your forms in the portal.'
};

/** Actions that act ON an existing document, so the item needs a target (the API rejects them
 * without one — see `create_request`). */
export const TARGETED_ACTIONS: RequestAction[] = ['agree', 'acknowledge'];

export const REQUEST_PRIORITIES = ['low', 'normal', 'high', 'urgent'] as const;
export type RequestPriority = (typeof REQUEST_PRIORITIES)[number];

/** Request/item lifecycle → badge tone + label. Same three states on the header and its items. */
export const REQUEST_STATUS_META: Record<
	ApiRequest['status'],
	{ label: string; tone: StatusTone }
> = {
	pending: { label: 'Outstanding', tone: 'warning' },
	fulfilled: { label: 'Complete', tone: 'success' },
	cancelled: { label: 'Cancelled', tone: 'neutral' }
};

/** Items that still count: cancelled ones are neither outstanding nor achievements. */
export function requestProgress(r: ApiRequest): { done: number; total: number } {
	const live = r.items.filter((i) => i.status !== 'cancelled');
	return { done: live.filter((i) => i.status === 'fulfilled').length, total: live.length };
}

/** A pending request whose due date has passed. `due_date` is a plain date, so compare against the
 * end of that day in the reader's timezone — a request due today is not yet overdue. */
export function isRequestOverdue(r: ApiRequest, now: Date = new Date()): boolean {
	if (!r.due_date || r.status !== 'pending') return false;
	return new Date(`${r.due_date}T23:59:59`) < now;
}

/** "Due today" / "Due in 3 days" / "4 days overdue" / '' — mirrors the tasks inbox's phrasing. */
export function requestDueLabel(r: ApiRequest, now: Date = new Date()): string {
	if (!r.due_date) return '';
	const days = Math.round(
		(new Date(`${r.due_date}T00:00:00`).getTime() - new Date(now).setHours(0, 0, 0, 0)) / 86400000
	);
	if (days === 0) return 'Due today';
	if (days === 1) return 'Due tomorrow';
	if (days > 1) return `Due in ${days} days`;
	const late = Math.abs(days);
	return `${late} ${late === 1 ? 'day' : 'days'} overdue`;
}

const PRIORITY_RANK: Record<string, number> = { urgent: 0, high: 1, normal: 2, low: 3 };

/** Chase order for the outstanding list: overdue first, then nearest due date, then priority,
 * then newest. */
export function byRequestUrgency(a: ApiRequest, b: ApiRequest): number {
	const ao = isRequestOverdue(a) ? 0 : 1;
	const bo = isRequestOverdue(b) ? 0 : 1;
	if (ao !== bo) return ao - bo;
	if (a.due_date && b.due_date && a.due_date !== b.due_date)
		return a.due_date < b.due_date ? -1 : 1;
	if (!!a.due_date !== !!b.due_date) return a.due_date ? -1 : 1;
	const ap = PRIORITY_RANK[a.priority] ?? 2;
	const bp = PRIORITY_RANK[b.priority] ?? 2;
	if (ap !== bp) return ap - bp;
	return b.created_at.localeCompare(a.created_at);
}

/** A client an adviser can request/send a document to — the crm.contact (API's ClientOut). */
export type ClientOption = {
	contact_id: number;
	name: string | null;
	email: string | null;
};

/** A tenant document-type tag (the open-ended documents.document_types registry) — the picker
 *  suggestions for upload + reclassify. */
export type DocumentTypeOption = {
	/** 'document' (a classification) or 'file' (a supporting-file type). Lets one list drive both the
	 *  Document and the Supporting-file pickers in the upload dialog. */
	kind: 'document' | 'file';
	slug: string;
	label: string;
};

/** Human file-format label (the "PDF"/"PNG" a file browser shows next to the type). */
export const FORMAT_LABELS: Record<DocumentRow['documentType'], string> = {
	pdf: 'PDF',
	docx: 'DOCX',
	jpg: 'JPG',
	png: 'PNG',
	html: 'HTML'
};

/** MIME type -> a readable format label, or null when we genuinely do not know.
 *
 *  Deliberately NOT `fileFormat`, which answers a different question. That function maps a MIME onto
 *  the four ICONS the UI has and so `default`s to `'pdf'` — right for choosing a glyph, wrong for
 *  stating a fact: it would label a spreadsheet "PDF", and a queued render (no content type yet)
 *  "PDF" as well. A document's format is either known or it is not, and an unknown one shows the raw
 *  MIME rather than a guess, because the raw string is still useful to whoever is asking.
 */
export function contentTypeLabel(contentType: string | null | undefined): string | null {
	if (!contentType) return null;
	const known: Record<string, string> = {
		'application/pdf': 'PDF',
		'image/png': 'PNG',
		'image/jpeg': 'JPEG',
		'image/webp': 'WebP',
		'text/plain': 'Text',
		'text/csv': 'CSV',
		'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'Word (DOCX)',
		'application/msword': 'Word (DOC)',
		'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'Excel (XLSX)',
		'application/vnd.ms-excel': 'Excel (XLS)',
		'application/vnd.openxmlformats-officedocument.presentationml.presentation':
			'PowerPoint (PPTX)',
		'application/vnd.ms-powerpoint': 'PowerPoint (PPT)'
	};
	// The MIME may carry parameters (`text/plain; charset=utf-8`); the type itself is the key.
	const bare = (contentType.split(';')[0] ?? contentType).trim().toLowerCase();
	return known[bare] ?? contentType;
}

/** Known file extensions, so an extension is never appended to a name that already carries one
 *  (a supporting file keeps its own — the listing strips it for DISPLAY only). */
const KNOWN_EXTENSIONS = ['.pdf', '.docx', '.doc', '.jpg', '.jpeg', '.png', '.html'];

/** MIME type -> file extension. Only what we genuinely know, the discipline `contentTypeLabel`
 *  follows: an unrecognised type yields nothing rather than a guess. */
const EXTENSION_FOR_MIME: Record<string, string> = {
	'application/pdf': 'pdf',
	'image/png': 'png',
	'image/jpeg': 'jpg',
	'image/webp': 'webp',
	'text/plain': 'txt',
	'text/csv': 'csv',
	'text/html': 'html',
	'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'docx',
	'application/msword': 'doc',
	'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'xlsx',
	'application/vnd.ms-excel': 'xls',
	'application/vnd.openxmlformats-officedocument.presentationml.presentation': 'pptx',
	'application/vnd.ms-powerpoint': 'ppt'
};

/** The filename a Download should SAVE AS.
 *
 *  A generated document's `name` is a TITLE ("Suitability Report — Q1 2026") with no extension, and
 *  an `<a download>` takes the browser's filename from that attribute — so downloading one produced
 *  a file Windows typed as "File" and could not open.
 *
 *  The extension is taken from the RAW content type when the caller has it, and only then from
 *  `documentType`. That order matters: `documentType` comes from `fileFormat`, which `default`s to
 *  `'pdf'` for a MIME it does not recognise (see `contentTypeLabel` — right for choosing a glyph,
 *  wrong for stating a fact), so preferring it would name a spreadsheet `.pdf`. A caller holding the
 *  real content type gets a truthful extension; one holding only the row's format tag gets the same
 *  answer the UI already prints in its Format column, so the file at least agrees with the screen it
 *  came from. With neither, the name is returned UNCHANGED rather than guessed — a missing extension
 *  is a nuisance, a wrong one is a lie about the file's contents.
 *
 *  A name that already ends in a known extension keeps it: a supporting file carries its own, and
 *  the listing strips it for DISPLAY only.
 */
export function downloadName(
	doc: { name: string; documentType?: DocumentRow['documentType'] },
	contentType?: string | null
): string {
	const lower = doc.name.toLowerCase();
	if (KNOWN_EXTENSIONS.some((ext) => lower.endsWith(ext))) return doc.name;
	const bare = contentType ? (contentType.split(';')[0] ?? '').trim().toLowerCase() : '';
	const ext = EXTENSION_FOR_MIME[bare] ?? doc.documentType;
	return ext ? `${doc.name}.${ext}` : doc.name;
}

/** Whether there are BYTES to download. A queued or failed render has none, and an `<a download>`
 *  saves whatever comes back — so offering it on those rows saved the proxy's ERROR PAGE as a ~1KB
 *  file named after the document, which is worse than no affordance: it looks like it worked. The
 *  record's own Download button already guards this (`disabled={!preview.src}`); the row kebab did
 *  not. */
export function isDownloadable(doc: { status: DocumentRow['status'] }): boolean {
	return doc.status !== 'queued' && doc.status !== 'generating' && doc.status !== 'failed';
}

/** A render the worker has not finished. The page that is showing it asks again until it leaves
 *  these two. */
export function documentInFlight(status: string | null | undefined): boolean {
	return status === 'queued' || status === 'generating';
}

/** MIME type -> the file-format tag the UI renders. */
export function fileFormat(contentType: string | null): DocumentRow['documentType'] {
	switch (contentType) {
		case 'image/png':
			return 'png';
		case 'image/jpeg':
			return 'jpg';
		case 'application/vnd.openxmlformats-officedocument.wordprocessingml.document':
			return 'docx';
		default:
			return 'pdf';
	}
}

/* ── Document categories (one vocabulary) ──────────────────────────────────────
   `documents.document_type` is free text, and the product used to offer THREE disjoint lists: the
   template taxonomy (`DOC_TYPE_META`: illustration/suitability/…) in the retype menu, and
   report/statement/agreement/identity/tax/other at upload and on the detail editor. Retyping
   therefore wrote values the rest of the product couldn't represent. This is the single list every
   platform surface offers, and the single label map every surface renders through. */
const CATEGORY_LABELS: Record<string, string> = {
	suitability: 'Suitability report',
	illustration: 'Illustration',
	valuation: 'Valuation',
	review: 'Review',
	factfind: 'Fact-find',
	// `sentenceCase` would print `Id verification`; `fact_find` must read the same as `factfind`.
	id_verification: 'ID verification',
	fact_find: 'Fact-find',
	report: 'Report',
	statement: 'Statement',
	agreement: 'Agreement',
	identity: 'Identity',
	tax: 'Tax',
	// System-authored: a terms declaration carries its HTML inline (see the API's /terms endpoint),
	// so staff never classify a document as 'terms' — but existing rows must still read properly.
	terms: 'Terms',
	other: 'Other'
};

/** Statuses that mean the upload never got as far as our own form action, so nothing it could have
 * said applies. The node adapter's request-body ceiling raises the 413 from INSIDE SvelteKit while
 * the body is being read, which is reported either as an HTML error page or as a serialised
 * `{type:'error'}` — hence a caller needs this alongside a failed `deserialize`, not instead of it.
 *
 * `fail(413)` from an action is a `failure`, never an `error`, so relaying the API's own message
 * (which names the real limit) stays the caller's job. */
export function isTransportStatus(status: number): boolean {
	return status === 413 || status === 502 || status === 503 || status === 504;
}

/** Message for an upload POST whose response is not a SvelteKit `ActionResult` — the request never
 * reached the action, so there is no `error` to report and `deserialize` throws on the HTML body.
 *
 * 413 is the important one: a multi-file drop travels as ONE request, and the node adapter enforces
 * its own request-body ceiling (`BODY_SIZE_LIMIT`) before SvelteKit routes anything — so the size
 * that matters is the whole drop, and the honest advice is to split it. Its default is 512 KB, which
 * rejects every real upload; deployments set it above the API's own ceiling (`docker-compose.yaml`'s
 * `x-portal` block), and `vite dev` has no ceiling at all, which is why this only ever bites a
 * deployment. Without this the user saw a JSON parse error ("Unexpected token '<'"). */
export function uploadTransportError(status: number): string {
	if (status === 413) {
		return 'That upload is too large for the portal to accept in one request — try fewer or smaller files.';
	}
	if (status === 502 || status === 503 || status === 504) {
		return 'The portal could not be reached. Please try again.';
	}
	return `Upload failed (HTTP ${status}).`;
}

/** Human label for a `document_type`. Anything outside the vocabulary (older free-text rows) is
 * humanised rather than shown raw — never a bare `proof_of_address` in the UI. */
export function documentCategoryLabel(value: string): string {
	if (!value) return 'Uncategorised';
	// `sentenceCase`, not a fourth local copy of the same three lines — the estate had four and
	// they disagreed about which separators split a key.
	return CATEGORY_LABELS[value] ?? sentenceCase(value);
}

/** Human label for a library row's `category`, whichever kind of row it is.
 *
 * Documents and supporting files arrive in ONE list from two tables with two vocabularies, and both
 * now carry a raw slug so the Type filter can match either. They cannot share a single map: `other`
 * means "Other" as a document type and "Other file" as an attachment type, so the kind has to be
 * passed rather than inferred. Everything else falls through to `documentCategoryLabel`, which
 * humanises an unknown tag rather than showing a bare `proof_of_address`.
 */
export function libraryTypeLabel(category: string, isFile = false): string {
	if (isFile) return ATTACHMENT_TYPE_LABELS[category] ?? documentCategoryLabel(category);
	return documentCategoryLabel(category);
}

/** A library row. `DocumentRow` (the shared list shape) predates the render pipeline, so the
 * failure reason travels alongside it rather than inside it. */
export type LibraryRow = DocumentRow & { error: string | null };

/** Map an API document onto the DocumentRow shape the UI expects. Fields the storage slice doesn't
 * model yet (owner, client, tags — they arrive with platform_crm) get safe defaults, and the
 * canonical 'generated' status maps onto the UI's live 'active'. */
export function toRow(d: ApiDocument): LibraryRow {
	return {
		error: d.error ?? null,
		uid: d.uid,
		name: d.name ?? 'Untitled document',
		category: d.document_type,
		// A terms declaration has no stored file: the API serves its HTML.
		documentType:
			d.content_type == null && d.document_type === 'terms' ? 'html' : fileFormat(d.content_type),
		source: (d.source === 'uploaded' || d.source === 'provider'
			? d.source
			: 'generated') as DocSource,
		status: (d.status === 'generated' ? 'active' : d.status) as DocStatus,
		scope: 'team',
		sizeBytes: d.size_bytes ?? 0,
		createdAt: d.created_at,
		createdBy: d.created_by ?? '',
		generatedAt: d.generated_at ?? undefined,
		client: d.client ?? '',
		clientContactId: d.client_contact_id ?? null,
		clientContactUid: d.client_contact_uid ?? null,
		recipients: d.recipients ?? '',
		// The template (name + version) behind a generated document (uploads have none — lists show —).
		version: d.template_version != null ? `v${d.template_version}` : undefined,
		templateName: d.template_name ?? undefined,
		templateUid: d.template_uid ?? undefined,
		clientViewed: d.client_viewed,
		clientViewedAt: d.client_viewed_at ?? undefined,
		sentAt: d.sent_at ?? undefined,
		sentToClient: d.sent_to_client,
		deletable: d.deletable ?? true
	};
}

/** A supporting client file (documents.attachments) — a passport isn't really a document, so these
 * are a separate entity, but they list alongside documents with file semantics. */
export type ApiFile = {
	uid: string;
	name: string | null;
	attachment_type: string;
	content_type: string;
	size_bytes: number;
	contact_id: number | null;
	contact_name: string | null;
	/** …and their uid, which is what the library's client filter matches on (two clients can share a
	 *  name). Optional so an older API reads as unfiled rather than breaking the list. */
	contact_uid?: string | null;
	created_at: string;
	created_by: string | null;
};

export const ATTACHMENT_TYPE_LABELS: Record<string, string> = {
	passport: 'Passport',
	national_id: 'National ID',
	proof_of_address: 'Proof of address',
	other: 'Other file'
};

/** Map a supporting file onto the DocumentRow shape so files and documents share one list. The
 * `isFile` marker switches row semantics (download/delete instead of view/share/archive), and the
 * attachment type becomes the row's category (its Type column, filter and band). */
export function fileToRow(f: ApiFile): LibraryRow {
	return {
		error: null,
		uid: f.uid,
		name: f.name ?? 'Untitled file',
		// The SLUG, not the label. `category` is what the library's Type filter matches on, and the
		// API facets both halves of the list by slug — a pre-humanised label here would make every
		// supporting file unmatchable. `libraryTypeLabel` renders it.
		category: f.attachment_type,
		documentType: fileFormat(f.content_type),
		source: 'uploaded' as DocSource,
		status: 'active' as DocStatus,
		scope: 'team',
		sizeBytes: f.size_bytes,
		createdAt: f.created_at,
		createdBy: f.created_by ?? '',
		client: f.contact_name ?? '',
		clientContactUid: f.contact_uid ?? null,
		isFile: true,
		// A supporting file is always the firm's own upload — there is no provider-synced
		// attachment — so it keeps its delete unconditionally.
		deletable: true
	};
}
