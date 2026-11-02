/**
 * The in-portal identity check: the client photographs an identity document (front, and back for a
 * card) and their face, and sends the photos in ONE submit that names the document type. The API
 * holds them only until the identity check is finished and sends them on itself. The verdict is
 * the API's — the browser only ever reports what was sent.
 */

/** The three photos a capture asks for. Each is also the submit's form field for its file. */
export type IdentityCaptureKey = 'idv_document_front' | 'idv_document_back' | 'idv_selfie';

/** Every photo key, in the order the photos are taken. */
export const IDENTITY_CAPTURE_KEYS: readonly IdentityCaptureKey[] = [
	'idv_document_front',
	'idv_document_back',
	'idv_selfie'
];

/** Where the capture stands (`GET /applications/me/identity-capture`).
 *  - `capturing`: the photos are the client's to take.
 *  - `submitted`: every photo is in and the check is running.
 *  - `retake`: the check could not verify the photos and the client can try again.
 *  - `review`: a person is checking the photos.
 *  - `passed`: the client is verified. */
export type IdentityCaptureStatus = 'capturing' | 'submitted' | 'retake' | 'review' | 'passed';

/** One photo of the try. `fulfilled`: the check already holds it for this try (kept from an
 *  earlier send), so it is not sent again. `open`: the client sends it. A try that asks for a
 *  photo again gives it a new `item_uid`. */
export type IdentityCaptureItem = {
	key: IdentityCaptureKey;
	item_uid: string;
	status: 'open' | 'fulfilled';
};

export type IdentityCapture = {
	items: IdentityCaptureItem[];
	status: IdentityCaptureStatus;
	/** The try the client is on now, from 1. A request for new photos moves it to the next try. */
	attempt: number;
	/** Why new photos were asked for: a photo-quality or document reason chosen for the client.
	 *  Null when none was given. */
	last_reason: string | null;
	/** Whether a person would pick up a manual check, so the client may choose one instead. */
	manual_offered: boolean;
	/** The document the photos are of, once sent. A retake that kept the document's photos keeps
	 *  it, and then only the photos still open are asked for. */
	document_type?: IdentityDocumentType | null;
	/** What the client has taken on a phone and not sent yet, so the computer waiting on it shows
	 *  each photo arrive. The document is named once something is taken. */
	phone_progress?: { document_type: IdentityDocumentType | null; taken: IdentityCaptureKey[] };
};

/** The photos the computer waits on while the client takes them on a phone: the ones still open,
 *  without the back once the phone says the document is a passport. Each is `received` once it is
 *  in, `taken` once the phone has it, or `waiting`. */
export function phoneWaitingList(
	capture: IdentityCapture
): { key: IdentityCaptureKey; itemUid: string; state: 'received' | 'taken' | 'waiting' }[] {
	const progress = capture.phone_progress;
	const passport = progress?.document_type === 'passport';
	return capture.items
		.filter((item) => !(passport && item.key === 'idv_document_back'))
		.map((item) => ({
			key: item.key,
			itemUid: item.item_uid,
			state:
				item.status === 'fulfilled'
					? 'received'
					: progress?.taken.includes(item.key)
						? 'taken'
						: 'waiting'
		}));
}

/** The kinds of identity document the client can photograph. */
export type IdentityDocumentType = 'passport' | 'driving_licence' | 'national_id';

/** The photo IDs the client can use, each with the sides it needs. */
export const IDENTITY_DOCUMENT_OPTIONS: readonly {
	value: IdentityDocumentType;
	label: string;
	sides: string;
}[] = [
	{ value: 'passport', label: 'Passport', sides: 'The photo page' },
	{ value: 'driving_licence', label: 'Driving licence', sides: 'Front and back' },
	{ value: 'national_id', label: 'National ID card', sides: 'Front and back' }
];

/** One photo the client takes. */
export type IdentityShot = {
	key: IdentityCaptureKey;
	mode: 'document' | 'face';
	label: string;
	hint: string;
	/** The uploaded file's name, without the extension. */
	fileName: string;
};

const DOCUMENT_HINT = 'Lay it flat in good light. Fill the frame and avoid glare.';

/** The photos for a document type, in the order they are taken. A passport has no back. */
export function identityShots(type: IdentityDocumentType): IdentityShot[] {
	const names: Record<IdentityDocumentType, { front: string; back: string | null }> = {
		passport: { front: 'The photo page of your passport', back: null },
		driving_licence: {
			front: 'The front of your driving licence',
			back: 'The back of your driving licence'
		},
		national_id: { front: 'The front of your ID card', back: 'The back of your ID card' }
	};
	const { front, back } = names[type];
	const shots: IdentityShot[] = [
		{
			key: 'idv_document_front',
			mode: 'document',
			label: front,
			hint: DOCUMENT_HINT,
			fileName: 'id-front'
		}
	];
	if (back) {
		shots.push({
			key: 'idv_document_back',
			mode: 'document',
			label: back,
			hint: DOCUMENT_HINT,
			fileName: 'id-back'
		});
	}
	shots.push({
		key: 'idv_selfie',
		mode: 'face',
		label: 'A photo of your face',
		hint: 'Look straight at the camera in good light. Take off glasses and hats.',
		fileName: 'selfie'
	});
	return shots;
}

/** The copy for a try that was sent back. A reason given for the client is shown as it stands;
 *  without one the screen never says more than "try again". */
export const GENERIC_RETAKE_REASON = 'We could not verify your identity. Please try again.';

export function retakeReason(lastReason: string | null | undefined): string {
	return lastReason?.trim() || GENERIC_RETAKE_REASON;
}

export function attemptLabel(attempt: number): string {
	return `Try ${attempt}`;
}

/** Every item id the capture carries, as one key. A new set of items at the same attempt — a
 *  fresh set after the photos could not be sent, or a new item for just the photo that was
 *  refused — moves it, which is how a caller tells "still waiting on the try just sent" apart from
 *  "a new try has started". */
export function captureItemsKey(
	capture: Pick<IdentityCapture, 'items'> | null | undefined
): string {
	return (capture?.items ?? [])
		.map((item) => `${item.key}:${item.item_uid}`)
		.sort()
		.join(',');
}

/** The document a retake keeps: the photos of it are in and only the face is open again. Null when
 *  the client is to choose the document. */
export function keptDocumentType(
	capture: Pick<IdentityCapture, 'status' | 'items' | 'document_type'>
): IdentityDocumentType | null {
	if (capture.status !== 'retake' || !capture.document_type) return null;
	const documentOpen = capture.items.some(
		(item) => item.key !== 'idv_selfie' && item.status === 'open'
	);
	return documentOpen ? null : capture.document_type;
}

/** The shots still to take: each whose item is not already in. A retake that kept some photos asks
 *  only for the rest. */
export function shotsToTake(shots: IdentityShot[], items: IdentityCaptureItem[]): IdentityShot[] {
	const fulfilled = new Set(
		items.filter((item) => item.status === 'fulfilled').map((item) => item.key)
	);
	return shots.filter((shot) => !fulfilled.has(shot.key));
}

/** Which photo a retake that kept the others needs, or null when it needs them all. */
export function retakeNeeds(items: IdentityCaptureItem[]): string | null {
	const open = items.filter((item) => item.status === 'open').map((item) => item.key);
	if (!open.length || !items.some((item) => item.status === 'fulfilled')) return null;
	if (open.every((key) => key === 'idv_selfie')) return 'We need a new photo of your face.';
	if (!open.includes('idv_selfie')) return 'We need a new photo of your document.';
	return null;
}

/** What a submit sends: each shot whose item is still open. The check already holds the photo of a
 *  fulfilled item, so it is not sent twice. `missing` is a shot with no item, which means the
 *  capture is not ready for it. */
export function photosToSend(
	shots: IdentityShot[],
	items: IdentityCaptureItem[]
): { send: IdentityShot[]; missing: IdentityShot[] } {
	const byKey = new Map(items.map((item) => [item.key, item]));
	const send: IdentityShot[] = [];
	const missing: IdentityShot[] = [];
	for (const shot of shots) {
		const item = byKey.get(shot.key);
		if (!item) missing.push(shot);
		else if (item.status === 'open') send.push(shot);
	}
	return { send, missing };
}

/** Whether the client's photos are with the check: sent, or being looked at. Nothing is theirs to
 *  do until the result, or until new photos are asked for (a retake). */
export function photosSent(capture: Pick<IdentityCapture, 'status'> | null | undefined): boolean {
	return capture?.status === 'submitted' || capture?.status === 'review';
}

/** How the onboarding page should ask again.
 *  `phone` — the desktop is waiting on the other device.
 *  `check` — the photos are with the check (the dialog may already be closed).
 *  Null once a person has the photos, or nothing is in flight. A review is a person, so it
 *  does not keep asking. */
export function identityRefresh(input: {
	captures: Record<string, { status: string } | null | undefined> | null | undefined;
	dialogOpen: boolean;
	/** The open dialog is waiting on the check, not on a person. */
	dialogMachineWait: boolean;
	phoneLink: boolean;
}): 'phone' | 'check' | null {
	const captures = Object.values(input.captures ?? {}).filter(
		(capture): capture is { status: string } => capture != null
	);
	// Photos already with the check outrank the phone link: the other device has finished, and
	// the dialog often stays open with that link still on screen.
	if (captures.some((capture) => capture.status === 'submitted')) return 'check';
	// The phone link belongs to the open dialog. A retake still waits on the other device, and a
	// different step already in review must not silence that wait. Review and a pass do not ask.
	const phoneStillWaiting =
		captures.length === 0 ||
		captures.some((capture) => capture.status !== 'review' && capture.status !== 'passed');
	if (input.dialogOpen && input.phoneLink && phoneStillWaiting) return 'phone';
	if (input.dialogOpen && input.dialogMachineWait) return 'check';
	return null;
}

/** What the desktop calls each photo while it waits for the other device. */
export function captureItemLabel(key: IdentityCaptureKey): string {
	switch (key) {
		case 'idv_document_front':
			return 'Photo of the document';
		case 'idv_document_back':
			return 'Back of the document';
		case 'idv_selfie':
			return 'Photo of your face';
	}
}

/** What the client reads while their photos are being checked. The same for the check and for a
 *  person looking at it: a client is never told which, nor anything of what was found. */
export const PHOTOS_SENT_TITLE = 'We’re checking your identity';
export const PHOTOS_SENT_DETAIL =
	'Thank you for sending your photos. We’ll let you know if we need anything else.';

/** The line a failed submit shows when the action carries no message of its own. Nothing is lost:
 *  the client still holds their photos and can send them again. */
export const SUBMIT_FAILED = 'Your photos could not be sent. Please try again.';

/** What the client is told after a submit: null when the photos were sent (a photo the check
 *  refused comes back as a retake on the capture, not here), else the action's own `error` (the
 *  API's sentence, such as the nationality their adviser has to add) or the generic line. */
export function submitError(result: { type: string; data?: unknown }): string | null {
	if (result.type === 'success') return null;
	if (result.type !== 'failure') return SUBMIT_FAILED;
	const error = (result.data as { error?: unknown } | null | undefined)?.error;
	return typeof error === 'string' && error ? error : SUBMIT_FAILED;
}
