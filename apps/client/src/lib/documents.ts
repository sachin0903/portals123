import type { FormField } from '@graphene/ui';
import type { ClientDoc } from '$lib/demo';

/* Documents API shapes (client-facing subset). These mirror the API's generated types
 * (`DocumentOut`/`RequestItemOut`/`RequestOut` in `@graphene/api-client`); they're kept
 * hand-declared here only because the generated barrel also re-exports a client module that
 * currently fails svelte-check, and the package doesn't expose a types-only entry point. Keep
 * them in step with the OpenAPI schema. */

/** Shape returned by `GET /documents` (API's `DocumentOut`). */
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
	/** True until the calling principal has opened the document. */
	unread: boolean;
	/** When the calling principal last opened the document; null if never. */
	last_viewed_at: string | null;
};

/** Action a client is asked to take on a request item. ('agree' is confirm-only for now — view the
 * document and tick "I agree"; real signing comes later.) */
export type RequestAction = 'upload' | 'agree' | 'acknowledge' | 'complete_form';

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

/** An adviser-created request with one or more items (API's `RequestOut`). The client is resolved
 * server-side from the auth token's `contact_scope`, so the row carries the API-resolved
 * `contact_id`/`contact_name` — the client portal never sends a client/contact id. */
export type ApiRequest = {
	uid: string;
	contact_id: number;
	contact_name: string | null;
	priority: 'low' | 'normal' | 'high' | 'urgent';
	message: string | null;
	due_date: string | null;
	status: 'pending' | 'fulfilled' | 'cancelled';
	created_at: string;
	items: ApiRequestItem[];
};

export const REQUEST_ACTION_LABELS: Record<RequestAction, string> = {
	upload: 'Upload',
	agree: 'Agree',
	acknowledge: 'Acknowledge',
	complete_form: 'Complete form'
};

/** One actionable, still-pending request item, flattened for the client's attention list. */
export type RequestActionItem = {
	requestUid: string;
	itemUid: string;
	action: RequestAction;
	title: string;
	/** Free-text document type the adviser asked for (e.g. "identity verification"), if any. */
	category: string | null;
	/** Adviser's accepted-format hint for upload items ('pdf' | 'image' | 'pdf,image'); '' / null = any. */
	formatHint: string | null;
	/** For complete_form items: the builder form to render (its uid). */
	formUid: string | null;
	/** For complete_form items: the resolved form definition, fetched in the loader (real builder
	    form via the ownership-checked render endpoint). Null if it couldn't be resolved. */
	formName?: string | null;
	formFields?: FormField[];
	/** File-backed actions (upload/complete_form) need a file; agree/acknowledge are confirm-only. */
	needsFile: boolean;
	/** Agree items view the target document + tick "I agree" — its preview URL, resolved in the loader. */
	needsAgreement: boolean;
	targetDocumentUid: string | null;
	targetPreviewUrl: string | null;
	dueDate: string | null;
	priority: 'low' | 'normal' | 'high' | 'urgent';
	message: string | null;
};

export function toRequestActionItems(reqs: ApiRequest[]): RequestActionItem[] {
	const out: RequestActionItem[] = [];
	for (const r of reqs) {
		for (const it of r.items) {
			if (it.status !== 'pending') continue;
			out.push({
				requestUid: r.uid,
				itemUid: it.uid,
				action: it.action,
				title: it.title,
				category: it.category,
				formatHint: it.format_hint,
				formUid: it.form_uid,
				needsFile: it.action === 'upload' || it.action === 'complete_form',
				needsAgreement: it.action === 'agree',
				targetDocumentUid: it.target_document_uid,
				targetPreviewUrl: null,
				dueDate: r.due_date,
				priority: r.priority,
				message: r.message
			});
		}
	}
	return out;
}

/** Friendly labels for the API's `document_type` slugs (a free-text column — templates/uploads
 * set it, there's no fixed enum). Anything not listed is humanised on the fly, so the tag always
 * reflects the real database value (e.g. "illustration") rather than collapsing to "Other". */
const DOCUMENT_TYPE_LABELS: Record<string, string> = {
	illustration: 'Illustration',
	fact_find: 'Fact find',
	id_verification: 'ID verification',
	suitability_report: 'Suitability report',
	client_agreement: 'Client agreement',
	annual_review: 'Annual review',
	other: 'Other'
};

export function documentTypeLabel(documentType: string): string {
	return (
		DOCUMENT_TYPE_LABELS[documentType] ??
		documentType.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
	);
}

export function toClientDoc(d: ApiDocument): ClientDoc {
	return {
		uid: d.uid,
		name: d.name ?? 'Document',
		// The real document_type, unmodified — the tag/filter reflects the database directly rather
		// than bucketing into a fixed category set that didn't cover most real values.
		category: d.document_type,
		createdAt: d.created_at,
		sizeBytes: d.size_bytes ?? undefined,
		contentType: d.content_type ?? undefined,
		source: d.source,
		unread: d.unread,
		lastViewedAt: d.last_viewed_at ?? undefined
	};
}
