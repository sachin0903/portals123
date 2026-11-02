/**
 * Real, fixture-free display layer for the documents surfaces — the view types plus the
 * status/tone/event chrome and the pure formatters that every documents screen renders REAL API
 * data through.
 *
 * These lived in the platform app's `$lib/demo` next to the invented fixtures, which is a trap:
 * importing `formatDate` from there trains everyone to treat that module as legitimate, so real-vs-fabricated is
 * invisible at the import site (docs/audit/06-core.md → "Seed / demo data strategy"). Nothing here
 * reads the fixed demo "now" and nothing here is invented data, so anything imported from this
 * module is safe on a signed-in user's own records.
 *
 * The apps' `$lib/format` modules hold their CRM-specific display helpers; these are the
 * document/status ones. Same rule applies to both: no server-only imports, they run in the client
 * bundle.
 */
import type { StatusTone } from '../../ui/status-badge/index.js';
import { initials as sharedInitials } from '../../../initials.js';
import UploadCloudIcon from '@lucide/svelte/icons/upload-cloud';
import ShareIcon from '@lucide/svelte/icons/share-2';
import EyeIcon from '@lucide/svelte/icons/eye';
import ClipboardIcon from '@lucide/svelte/icons/clipboard-list';
import ClipboardCheckIcon from '@lucide/svelte/icons/clipboard-check';
import FileOutputIcon from '@lucide/svelte/icons/file-output';
import ArchiveIcon from '@lucide/svelte/icons/archive';

/* ── Document view types ──────────────────────────────────────────────────
   The shape the documents screens render, mapped from the API in `./documents.ts`. */

export type DocStatus =
	'queued' | 'generating' | 'active' | 'pending_action' | 'failed' | 'superseded' | 'archived';
export type DocSource = 'uploaded' | 'generated' | 'provider';

/** How a document's origin reads. A custodian sync is neither an upload nor a render. */
export function sourceLabel(source: DocSource): string {
	if (source === 'uploaded') return 'Uploaded';
	if (source === 'provider') return 'Provider';
	return 'Generated';
}

/** 'private' = only the creating adviser can see it. 'team' = all advisers in the tenant can see it. */
export type DocScope = 'private' | 'team';

export type TrackingStatus = 'sent' | 'viewed' | 'accepted';

export interface DocumentRow {
	uid: string;
	name: string;
	category: string;
	documentType: 'pdf' | 'docx' | 'jpg' | 'png' | 'html';
	source: DocSource;
	status: DocStatus;
	scope: DocScope;
	sizeBytes: number;
	createdAt: string;
	/** When this document was delivered to the client the list was scoped to (client-detail
	 *  Documents tab); undefined until sent, and on the tenant-wide overview. */
	sentAt?: string;
	/** Whether it has been SENT to that client at all — false = made-for/filed but not delivered.
	 *  Undefined on the tenant-wide overview (no client scope). */
	sentToClient?: boolean;
	createdBy: string;
	/** When a GENERATED document's render actually finished — distinct from `createdAt`, which is
	 *  when it was queued. The two differ by however long the render sat in the worker's queue, so on
	 *  a document that took a while they are genuinely different facts. Undefined for an upload
	 *  (nothing was rendered) and for a generate that has not finished. */
	generatedAt?: string;
	/** The one client this document is *made for* (its subject). */
	client: string;
	/** …and their contact ID, which is what lets another surface be opened ON that client. The name
	 *  is a display string: "Raise a request" has to reach the request builder with the client
	 *  already chosen, and matching a person by display name is how a request gets raised against the
	 *  wrong Margaret. Null when the document is made for nobody. */
	clientContactId?: number | null;
	clientContactUid?: string | null;
	/** Who it has actually been delivered to (recipients, comma-joined) — distinct from `client`;
	 *  delivery, not what it's about. Empty/undefined until sent. */
	recipients?: string;
	version?: string;
	/** Name/uid of the template that generated this document (group-by-template browsing + the
	 *  detail page's link back to the producing template). */
	templateName?: string;
	templateUid?: string;
	trackingStatus?: TrackingStatus;
	/** True for a supporting file (documents.attachments — passport, ID, proof of address): listed
	 *  alongside documents but with file semantics (download/delete, no share/archive/versions). */
	isFile?: boolean;
	/** Whether the client has opened this document (any of their logins) — only meaningful when
	 *  the list was fetched scoped to that one client (the client-detail Documents tab). */
	clientViewed?: boolean;
	clientViewedAt?: string;
	/** Whether the firm may delete this row. False ⇒ the Delete affordance is withheld and the row
	 * is excluded from the bulk bar's count, because the API refuses it (409). Defaults true, so a
	 * surface that does not set it behaves exactly as it did. */
	deletable?: boolean;
}

export interface TaskRow {
	id: string;
	documentUid: string;
	contact: string;
	taskType: 'review' | 'acknowledge' | 'upload_requested';
	status: 'pending' | 'completed' | 'cancelled';
	message?: string;
	dueDate?: string;
}

export interface VersionRow {
	uid: string;
	name: string;
	createdAt: string;
	isCurrent: boolean;
}

export interface UploadRequestRow {
	id: string;
	contact: string;
	category: string;
	description: string;
	dueDate: string;
	status: 'pending' | 'fulfilled' | 'cancelled';
}

export interface FeedEvent {
	id: string;
	action:
		| 'uploaded'
		| 'shared'
		| 'viewed'
		| 'task_assigned'
		| 'task_completed'
		| 'generated'
		| 'archived';
	documentUid: string;
	documentName: string;
	actor: string;
	client: string;
	at: string;
}

export const STATUS_META: Record<DocStatus, { label: string; tone: StatusTone; pulse?: boolean }> =
	{
		active: { label: 'Active', tone: 'success' },
		pending_action: { label: 'Pending action', tone: 'warning' },
		queued: { label: 'Queued', tone: 'info', pulse: true },
		generating: { label: 'Generating', tone: 'info', pulse: true },
		failed: { label: 'Failed', tone: 'danger' },
		// `archived` is the DB/API word and stays one (the enum is not renamed); the product calls it
		// DELETED, because that is what the act reads as to the person doing it — a document leaves the
		// library and is retained on the record. Labelling the same state two different words across
		// the badge and the control that produced it is how a soft delete gets mistaken for a filing.
		archived: { label: 'Deleted', tone: 'neutral' },
		superseded: { label: 'Superseded', tone: 'neutral' }
	};

/* ── Shared display helpers ───────────────────────────────────────────────
   One source of truth for the accent tones, name initials and document-event
   chrome used across the documents list, per-client view and detail pages —
   so the same event always reads with the same icon/tone. */

export type AccentTone =
	'brand' | 'info' | 'success' | 'warning' | 'primary' | 'danger' | 'neutral';

export const TONE_CLASSES: Record<AccentTone, { text: string; bg: string }> = {
	brand: { text: 'text-brand', bg: 'bg-brand/10' },
	info: { text: 'text-info', bg: 'bg-info/10' },
	success: { text: 'text-success', bg: 'bg-success/10' },
	warning: { text: 'text-warning', bg: 'bg-warning/10' },
	primary: { text: 'text-primary', bg: 'bg-primary/10' },
	danger: { text: 'text-destructive', bg: 'bg-destructive/10' },
	neutral: { text: 'text-muted-foreground', bg: 'bg-muted' }
};

/** Two-letter initials for an avatar, from a person/firm name. See `lib/initials.ts`. */
export function initials(name: string): string {
	return sharedInitials(name, '');
}

export type DocEventAction =
	| 'uploaded'
	| 'shared'
	| 'viewed'
	| 'task_assigned'
	| 'task_completed'
	| 'generated'
	| 'archived'
	| 'signed';

/** Icon + tone + default label for each document event — shared so the activity
    feed and per-client timeline never disagree on how an event looks. */
export const ACTION_META: Record<
	DocEventAction,
	{ label: string; icon: typeof EyeIcon; tone: AccentTone }
> = {
	uploaded: { label: 'Uploaded', icon: UploadCloudIcon, tone: 'info' },
	shared: { label: 'Shared', icon: ShareIcon, tone: 'brand' },
	viewed: { label: 'Viewed', icon: EyeIcon, tone: 'neutral' },
	task_assigned: { label: 'Task assigned', icon: ClipboardIcon, tone: 'warning' },
	task_completed: { label: 'Task completed', icon: ClipboardCheckIcon, tone: 'success' },
	generated: { label: 'Generated', icon: FileOutputIcon, tone: 'primary' },
	archived: { label: 'Archived', icon: ArchiveIcon, tone: 'neutral' },
	signed: { label: 'Signed', icon: ClipboardCheckIcon, tone: 'success' }
};

export const TRACKING_META: Record<TrackingStatus, { label: string; tone: StatusTone }> = {
	accepted: { label: 'Accepted', tone: 'success' },
	viewed: { label: 'Viewed', tone: 'warning' },
	sent: { label: 'Sent', tone: 'info' }
};

/* ── Pure formatters ─────────────────────────────────────────────────────
   Including the relative-time maths, which now measures against the REAL clock. It used to
   live in the platform app's `$lib/demo` and subtract from a hardcoded `DEMO_NOW` (2026-06-22) — the worst kind of
   wrong on real data: an age rendered from a real timestamp would be silently mis-stated
   rather than visibly broken. `now` is injectable for tests only. */

/** Clock time (24h, e.g. "14:32") shown alongside the relative label in activity feeds. */
export function formatTime(iso: string): string {
	return new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
}

/** A date without the year ("22 Jun") — dense table columns. */
export function formatDateShort(iso: string): string {
	return new Date(iso).toLocaleDateString('en-GB', { day: '2-digit', month: 'short' });
}

const DAY_MS = 86_400_000;

/** Relative-time label (e.g. "3h ago") against the real clock. */
export function relativeTime(iso: string, now: Date = new Date()): string {
	const diff = now.getTime() - new Date(iso).getTime();
	const mins = Math.floor(diff / 60000);
	const hours = Math.floor(diff / 3600000);
	const days = Math.floor(diff / DAY_MS);
	if (mins < 1) return 'just now';
	if (mins < 60) return `${mins}m ago`;
	if (hours < 24) return `${hours}h ago`;
	if (days === 1) return 'yesterday';
	return `${days}d ago`;
}
