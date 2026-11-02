/**
 * New identity photos for a client, uploaded on their behalf: by Graphene operations in the admin
 * AML console, and by the firm's advisers on the client's Compliance tab. Either upload starts a new
 * identity check. Graphene decides its result; the upload only supplies the photos.
 */
import { humanise } from '../../../sentence-case.js';

/** The identity documents an identity check reads. */
export type IdentityDocumentType = 'passport' | 'driving_licence' | 'national_id';

export const IDENTITY_DOCUMENT_TYPES: { value: IdentityDocumentType; label: string }[] = [
	{ value: 'passport', label: 'Passport' },
	{ value: 'driving_licence', label: 'Driving licence' },
	{ value: 'national_id', label: 'National ID card' }
];

export function documentTypeLabel(type: string): string {
	return IDENTITY_DOCUMENT_TYPES.find((d) => d.value === type)?.label ?? humanise(type);
}

/**
 * Whether new identity photos can be uploaded for the client now, and what an upload of one part
 * keeps. The API sends it as `identity_upload` on the admin client page and as `upload` on the
 * firm's `GET /crm/contacts/{uid}/identity`.
 */
export type IdentityUpload = {
	allowed: boolean;
	/** Why not, when not allowed. */
	blocked: string | null;
	/** The document on file for the client: an upload of the selfie alone keeps it. */
	document: { document_type: IdentityDocumentType } | null;
	/** A selfie is on file for the client: an upload of the document alone keeps it. */
	selfie: boolean;
	/** The document's issuing country unless the uploader picks another: the client's first
	 *  nationality, else their address country. */
	issuing_country: string | null;
	/** A journey step waits on the client's identity. The upload answers it, and the client is
	 *  asked for nothing. */
	journey: boolean;
};

/** One photo of an upload. Each is also the form field that carries its file. */
export type IdentityPhotoField = 'idv_document_front' | 'idv_document_back' | 'idv_selfie';

export const IDENTITY_PHOTO_FIELDS: readonly IdentityPhotoField[] = [
	'idv_document_front',
	'idv_document_back',
	'idv_selfie'
];

export type IdentityPhotoSlot = {
	field: IdentityPhotoField;
	/** `document`: a photo or a one-page PDF scan. `face`: a photo only. */
	mode: 'document' | 'face';
	label: string;
	/** The file's name, without the extension. */
	fileName: string;
};

const DOCUMENT_SIDES: Record<IdentityDocumentType, { front: string; back: string | null }> = {
	passport: { front: 'Passport photo page', back: null },
	driving_licence: {
		front: 'Front of the driving licence',
		back: 'Back of the driving licence'
	},
	national_id: { front: 'Front of the ID card', back: 'Back of the ID card' }
};

/** The photos an upload carries, in order: every side of the new document (a passport has no
 *  back), then the selfie. `document` is null when the document is kept. */
export function uploadSlots(
	document: IdentityDocumentType | null,
	selfie: boolean
): IdentityPhotoSlot[] {
	const slots: IdentityPhotoSlot[] = [];
	if (document) {
		const { front, back } = DOCUMENT_SIDES[document];
		slots.push({
			field: 'idv_document_front',
			mode: 'document',
			label: front,
			fileName: 'id-front'
		});
		if (back)
			slots.push({
				field: 'idv_document_back',
				mode: 'document',
				label: back,
				fileName: 'id-back'
			});
	}
	if (selfie)
		slots.push({ field: 'idv_selfie', mode: 'face', label: 'Selfie', fileName: 'selfie' });
	return slots;
}
