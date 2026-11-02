/**
 * A firm's view of a client's identity checks (`GET /crm/contacts/{uid}/identity`): each check's
 * status in the firm's words, its date, the document read and who sent the photos, and the photos
 * and analysis of the shown check. Never Graphene's decision comments: the API does not send them.
 */
import {
	humanise,
	type IdentityAnalysis,
	type IdentityDocumentType,
	type StatusTone
} from '@graphene/ui';

export type FirmIdentityStatus = 'in_progress' | 'passed' | 'failed' | 'superseded';

export type FirmIdentityCheck = {
	uid: string;
	status: FirmIdentityStatus;
	document_type: IdentityDocumentType | null;
	/** An adviser's name, "Graphene" for an operations upload, null when the client sent them. */
	uploaded_by: string | null;
	created_at: string;
	updated_at: string;
};

/** One photo of the current check. The relay route streams its bytes. */
export type FirmIdentityImage = {
	part: 'front' | 'back' | 'selfie';
	label: string;
	content_type: string;
};

export type FirmIdentity = {
	/** Newest first: the first is the current check. */
	checks: FirmIdentityCheck[];
	/** The check whose photos `images` are: the current one unless another was asked for. */
	shown?: string | null;
	/** The current check's photos. Empty when there are none or they cannot be fetched now. */
	images: FirmIdentityImage[];
	/** Why there are no photos, when there are none. */
	images_missing?: 'none' | 'not_configured' | 'not_held' | 'unavailable' | null;
	/** The shown check's analysis: its scores, facts and outcomes. Null before it has a result. */
	analysis?: IdentityAnalysis | null;
};

const STATUS: Record<FirmIdentityStatus, { label: string; tone: StatusTone }> = {
	in_progress: { label: 'In progress', tone: 'info' },
	passed: { label: 'Passed', tone: 'success' },
	failed: { label: 'Failed', tone: 'danger' },
	superseded: { label: 'Superseded', tone: 'neutral' }
};

export function identityStatus(status: string): { label: string; tone: StatusTone } {
	return STATUS[status as FirmIdentityStatus] ?? { label: humanise(status), tone: 'neutral' };
}

/** What the card says when the current check shows no photos. Never names the provider. */
export function photosMissingText(reason: FirmIdentity['images_missing']): string {
	if (reason === 'none') return 'No photos were sent for this check.';
	if (reason === 'unavailable') return 'The photos cannot be shown right now. Try again shortly.';
	if (reason === 'not_held' || reason === 'not_configured')
		return 'The photos for this check are no longer on file.';
	return 'No photos can be shown for this check.';
}

/** Who sent the check's photos. */
export function sentBy(check: Pick<FirmIdentityCheck, 'uploaded_by'>): string {
	return check.uploaded_by ? `Uploaded by ${check.uploaded_by}` : 'Sent by the client';
}

/** Which check of the list to show: the one asked for, else the current (newest) one. */
export function shownIndex(identity: Pick<FirmIdentity, 'checks' | 'shown'>): number {
	const found = identity.checks.findIndex((check) => check.uid === identity.shown);
	return found < 0 ? 0 : found;
}

/** Where one photo of a check streams from: this portal's same-origin relay. */
export function identityImageHref(contactUid: string, checkUid: string, part: string): string {
	return `/clients/${encodeURIComponent(contactUid)}/compliance/identity/${encodeURIComponent(checkUid)}/${part}`;
}
