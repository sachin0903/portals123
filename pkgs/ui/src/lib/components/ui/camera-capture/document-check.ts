/**
 * A rough check that an identity document photo is the document and the side the person said it
 * is, so a mistake is caught before the photos are sent rather than after the check. It only
 * WARNS: the face finder and the text reader both miss things (glare, a worn card, a foreign
 * layout), and a warning the person can read and pass is the most a guess may do. ComplyCube
 * still checks the document properly once it is sent.
 *
 * Two signals, each read in the browser:
 * - the face (`findFacesIn`): the front of every photo ID carries the holder's photo, and the
 *   back of a licence or identity card does not;
 * - the printed text (`readText`): the word for the document ("passport", "driving licence",
 *   "identity card", in the common European languages too) and its machine-readable lines (a
 *   passport's start `P<`, an identity card's `I`, `A` or `C`).
 *
 * The rules are pure functions (`documentSignals`, `documentWarnings`); `checkDocumentPhoto` runs
 * them on a photo.
 *
 * On a phone only the face is looked for (`readsText`). The text reader is the slow part there:
 * about 4 MB more to load, and many seconds to read one photo on a phone's processor. The face
 * finder is loaded for the selfie in any case.
 */
import { prepareTextReader, readText } from './document-text.js';
import { findFacesIn, prepareFaceFinder } from './face-detector.js';

/** The photo IDs the identity check takes. */
export type DocumentKind = 'passport' | 'driving_licence' | 'national_id';
/** Which side of the document a photo is meant to show. A passport has only its photo page. */
export type DocumentSide = 'front' | 'back';

/** What the printed text says the document is. More than one can be true of a misread photo. */
export type DocumentSignals = {
	passport: boolean;
	drivingLicence: boolean;
	nationalId: boolean;
	/** How many letters and digits were read. Few means the photo could not be read. */
	characters: number;
};

/** Fewer letters and digits than this, and the photo is taken as unreadable. Every photo ID
 *  prints far more: a name, dates, a number. */
export const MIN_CHARACTERS = 25;

/** Each document's name, as it is printed on one, in letters only (spaces and accents dropped, so
 *  "DRIVING  LICENCE" read with a doubled space or a lost one still matches). */
const PRINTED_NAMES: Record<keyof Omit<DocumentSignals, 'characters'>, readonly string[]> = {
	passport: [
		'PASSPORT',
		'PASSEPORT',
		'PASAPORTE',
		'PASSAPORTO',
		'PASSAPORTE',
		'REISEPASS',
		'PASPOORT',
		'PASZPORT'
	],
	drivingLicence: [
		'DRIVINGLICENCE',
		'DRIVINGLICENSE',
		'DRIVERLICENSE',
		'DRIVERSLICENSE',
		'PERMISDECONDUIRE',
		'FUHRERSCHEIN',
		'PERMISODECONDUCCION',
		'PATENTEDIGUIDA',
		'RIJBEWIJS',
		'PRAWOJAZDY',
		'CARTADECONDUCAO',
		'KORKORT'
	],
	nationalId: [
		'IDENTITYCARD',
		'NATIONALIDENTITY',
		'IDENTITYDOCUMENT',
		'CARTEDIDENTITE',
		'CARTENATIONALE',
		'PERSONALAUSWEIS',
		'DOCUMENTONACIONAL',
		'CARTADIDENTITA',
		'IDENTITEITSKAART',
		'DOWODOSOBISTY',
		'CARTAODECIDADAO'
	]
};

/** Upper case with the accents dropped: "Führerschein" → "FUHRERSCHEIN". */
function plain(text: string): string {
	return text.normalize('NFD').replace(/\p{M}/gu, '').toUpperCase();
}

/**
 * The machine-readable lines in ``text``: lines of letters, digits and `<` only, as they are once
 * spaces are dropped, with at least one `<<` (no printed word has one). `«` is read for `<`, which
 * a text reader often prints in its place.
 */
function machineLines(text: string): string[] {
	return plain(text)
		.split('\n')
		.map((line) => line.replace(/\s+/g, '').replace(/«/g, '<'))
		.filter((line) => line.length >= 25 && line.includes('<<') && /^[A-Z0-9<]+$/.test(line));
}

/** A passport's machine-readable lines are 44 characters long, and an identity card's 30 (36 on
 *  the rarer two-line card). The length tells them apart where the first letters cannot: a
 *  card's name line starts with the holder's surname, which may well start with a `P`. The
 *  bounds leave room for a few characters the reader drops or doubles. */
const PASSPORT_LINE_MIN = 40;
const CARD_LINE_MAX = 38;

/** What the text read off a photo says the document is. */
export function documentSignals(text: string): DocumentSignals {
	const letters = plain(text).replace(/[^A-Z]/g, '');
	const says = (names: readonly string[]): boolean => names.some((name) => letters.includes(name));
	const machine = machineLines(text);
	return {
		// A passport's first line starts `P`, a type letter or `<`, and its country.
		passport:
			says(PRINTED_NAMES.passport) ||
			machine.some((line) => line.length >= PASSPORT_LINE_MIN && /^P[A-Z<][A-Z<]{3}/.test(line)),
		drivingLicence: says(PRINTED_NAMES.drivingLicence),
		// An identity card's first line starts `I`, `A` or `C`.
		nationalId:
			says(PRINTED_NAMES.nationalId) ||
			machine.some((line) => line.length <= CARD_LINE_MAX && /^[IAC][A-Z<][A-Z<]{3}/.test(line)),
		characters: plain(text).replace(/[^A-Z0-9]/g, '').length
	};
}

const NAME: Record<DocumentKind, string> = {
	passport: 'a passport',
	driving_licence: 'a driving licence',
	national_id: 'an identity card'
};

const SHORT_NAME: Record<DocumentKind, string> = {
	passport: 'passport',
	driving_licence: 'driving licence',
	national_id: 'identity card'
};

const SIGNAL: Record<DocumentKind, keyof Omit<DocumentSignals, 'characters'>> = {
	passport: 'passport',
	driving_licence: 'drivingLicence',
	national_id: 'nationalId'
};

/** Where the expected side is named in a message: a passport's photo page, a card's front. */
function frontOf(kind: DocumentKind): string {
	return kind === 'passport'
		? 'the photo page of your passport'
		: `the front of your ${SHORT_NAME[kind]}`;
}

/**
 * What to warn the person about for a photo meant to show ``side`` of ``kind``. ``faces`` is how
 * many faces were found on it, and ``signals`` what its text says; either is null where it could
 * not be found out, and then it warns about nothing.
 *
 * - The front with no face, or a back with one: the side is wrong, or the photo is not of the
 *   document at all.
 * - Text too sparse to read: the photo is blurred, dark or glared.
 * - Text naming another document and not this one: the wrong document was chosen or photographed.
 * - The front with no sign of its own document: it may not be one. Not asked of a back, which on
 *   most cards prints neither the name nor, on a licence, any machine-readable line.
 */
export function documentWarnings(input: {
	kind: DocumentKind;
	side: DocumentSide;
	faces: number | null;
	signals: DocumentSignals | null;
}): string[] {
	const { kind, side, faces, signals } = input;
	const warnings: string[] = [];
	if (faces !== null) {
		if (side === 'front' && faces === 0) {
			warnings.push(
				`We could not find a photo of a face. Check this is ${frontOf(kind)}, and that all of it is in the photo.`
			);
		} else if (side === 'back' && faces > 0) {
			warnings.push(
				`This side has a photo of a face, so it may be the front. Turn your ${SHORT_NAME[kind]} over and take the back.`
			);
		}
	}
	if (signals === null) return warnings;
	if (signals.characters < MIN_CHARACTERS) {
		warnings.push(
			'We could not read the writing in this photo. Take it again in good light, with no glare, and hold the camera still.'
		);
		return warnings;
	}
	const isIt = signals[SIGNAL[kind]];
	const other = (Object.keys(SIGNAL) as DocumentKind[]).find(
		(candidate) => candidate !== kind && signals[SIGNAL[candidate]]
	);
	if (other && !isIt) {
		warnings.push(
			`This looks like ${NAME[other]}, not ${NAME[kind]}. Check you chose the right photo ID.`
		);
	} else if (!isIt && side === 'front') {
		warnings.push(`We could not confirm this is ${frontOf(kind)}.`);
	}
	return warnings;
}

/** The longest side, in pixels, a document photo is checked at: enough for the small print of a
 *  passport's machine-readable lines. */
export const CHECK_SIDE = 1600;

/** The part of a document's width, from the left, its holder's photo sits in on a passport, a
 *  driving licence and most identity cards. Looked at on its own as well, where the face is
 *  larger to the finder than in the whole photo. */
const PHOTO_SIDE = 0.55;

/** ``photo`` drawn on a canvas no longer than ``CHECK_SIDE``, or null where it cannot be read. */
async function drawPhoto(photo: File): Promise<HTMLCanvasElement | null> {
	if (typeof createImageBitmap !== 'function') return null;
	let bitmap: ImageBitmap;
	try {
		bitmap = await createImageBitmap(photo);
	} catch {
		return null;
	}
	try {
		const scale = Math.min(1, CHECK_SIDE / Math.max(bitmap.width, bitmap.height, 1));
		const canvas = document.createElement('canvas');
		canvas.width = Math.max(1, Math.round(bitmap.width * scale));
		canvas.height = Math.max(1, Math.round(bitmap.height * scale));
		const context = canvas.getContext('2d');
		if (!context) return null;
		context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
		return canvas;
	} finally {
		bitmap.close();
	}
}

/** How many faces are on ``page``: the most found in the whole of it or in its photo side. */
async function countFaces(page: HTMLCanvasElement): Promise<number | null> {
	const whole = await findFacesIn(page);
	if (whole === null) return null;
	const left = document.createElement('canvas');
	left.width = Math.max(1, Math.round(page.width * PHOTO_SIDE));
	left.height = page.height;
	const context = left.getContext('2d');
	if (!context) return whole.length;
	context.drawImage(page, 0, 0, left.width, left.height, 0, 0, left.width, left.height);
	const part = (await findFacesIn(left)) ?? [];
	return Math.max(whole.length, part.length);
}

/** Whether the printed text is read here: not on a phone (the module comment says why). The
 *  browser's own answer where it gives one, else its user agent. */
export function readsText(): boolean {
	if (typeof navigator === 'undefined') return false;
	const mobile = (navigator as Navigator & { userAgentData?: { mobile?: boolean } }).userAgentData
		?.mobile;
	if (typeof mobile === 'boolean') return !mobile;
	return !/Android|iPhone|iPod|Mobile/i.test(navigator.userAgent);
}

/** Start loading the face finder and, where text is read, the text reader, so a document photo is
 *  checked at once when it is taken. Call it when a document step opens. */
export function prepareDocumentCheck(): void {
	prepareFaceFinder();
	if (readsText()) void prepareTextReader();
}

/**
 * The warnings for a photo meant to show ``side`` of ``kind`` (`documentWarnings`). Empty where
 * the photo cannot be read as an image or neither check can run here: no warning is ever made up.
 */
export async function checkDocumentPhoto(
	photo: File,
	expected: { kind: DocumentKind; side: DocumentSide }
): Promise<string[]> {
	const page = await drawPhoto(photo);
	if (!page) return [];
	const [faces, text] = await Promise.all([
		countFaces(page),
		readsText() ? readText(page) : Promise.resolve(null)
	]);
	return documentWarnings({
		...expected,
		faces,
		signals: text === null ? null : documentSignals(text)
	});
}
