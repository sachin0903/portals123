import { describe, expect, it } from 'vitest';
import {
	documentSignals,
	documentWarnings,
	MIN_CHARACTERS,
	type DocumentSignals
} from './document-check';

/** The photo page of a UK specimen passport, as a text reader returns it. */
const UK_PASSPORT = `UNITED KINGDOM OF GREAT BRITAIN AND NORTHERN IRELAND
PASSPORT
Type/Type Code/Code Passport No./Passeport No.
P GBR 925076473
Surname/Nom (1)
UK SPECIMEN
Given names/Prénoms (2)
ANGELA ZOE
P<GBRUK<SPECIMEN<<ANGELA<ZOE<<<<<<<<<<<<<<<<
9250764733GBR8809117F2007162<<<<<<<<<<<<<<08`;

/** The front of a UK photocard driving licence. */
const UK_LICENCE = `DRIVING LICENCE UK
1. SPECIMEN
2. MR SARAH MEREDYTH
3. 03.07.1981 UNITED KINGDOM
4a. 01.04.2013 4c. DVLA
4b. 31.03.2023
5. MORGA753116SM9IJ 35`;

/** The back of a German identity card: three 30-character machine-readable lines. */
const DE_CARD_BACK = `IDD<<T220001293<<<<<<<<<<<<<<<
6408125<2010315D<<<<<<<<<<<<<4
MUSTERMANN<<ERIKA<<<<<<<<<<<<<`;

const NOTHING: DocumentSignals = {
	passport: false,
	drivingLicence: false,
	nationalId: false,
	characters: 80
};

describe('documentSignals', () => {
	it('reads a passport from its name and its machine-readable lines', () => {
		expect(documentSignals(UK_PASSPORT)).toMatchObject({
			passport: true,
			drivingLicence: false,
			nationalId: false
		});
		// The lines alone are enough, with « read for <.
		const lines =
			'P«GBRSMITH««JOHN«««««««««««««««««««««««««««««\n1234567890GBR8001019M2501017<<<<<<<<<<<<<<04';
		expect(documentSignals(lines).passport).toBe(true);
	});

	it('reads a driving licence and an identity card by the words printed on them', () => {
		expect(documentSignals(UK_LICENCE)).toMatchObject({
			passport: false,
			drivingLicence: true,
			nationalId: false
		});
		expect(documentSignals('Driving   licence\nDVLA').drivingLicence).toBe(true);
		expect(documentSignals('BUNDESREPUBLIK DEUTSCHLAND PERSONALAUSWEIS').nationalId).toBe(true);
		expect(documentSignals("RÉPUBLIQUE FRANÇAISE CARTE NATIONALE D'IDENTITÉ").nationalId).toBe(
			true
		);
		expect(documentSignals('Führerschein').drivingLicence).toBe(true);
	});

	it('reads an identity card back from its 30-character lines', () => {
		expect(documentSignals(DE_CARD_BACK)).toMatchObject({ nationalId: true, passport: false });
	});

	it('does not take a card whose holder is called P… for a passport', () => {
		const card = 'IDGBR1234567890<<<<<<<<<<<<<<<\nPETERS<<JOHN<<<<<<<<<<<<<<<<<<';
		expect(documentSignals(card)).toMatchObject({ nationalId: true, passport: false });
	});

	it('does not take a 44-character line for a card, whatever it starts with', () => {
		const line = 'AXGBR<<'.padEnd(44, '<');
		expect(documentSignals(`${line}\nPASSPORT`)).toMatchObject({
			passport: true,
			nationalId: false
		});
		// The same start on a 30-character line is a card.
		expect(documentSignals('AXGBR<<'.padEnd(30, '<')).nationalId).toBe(true);
	});

	it('counts the letters and digits read', () => {
		expect(documentSignals('ab 12 ~~ ,.').characters).toBe(4);
		expect(documentSignals(UK_LICENCE).characters).toBeGreaterThan(MIN_CHARACTERS);
	});
});

describe('documentWarnings', () => {
	const passport = documentSignals(UK_PASSPORT);
	const licence = documentSignals(UK_LICENCE);

	it('says nothing for the right side of the right document', () => {
		expect(
			documentWarnings({ kind: 'passport', side: 'front', faces: 1, signals: passport })
		).toEqual([]);
		expect(
			documentWarnings({ kind: 'driving_licence', side: 'front', faces: 2, signals: licence })
		).toEqual([]);
		expect(
			documentWarnings({ kind: 'driving_licence', side: 'back', faces: 0, signals: NOTHING })
		).toEqual([]);
	});

	it('warns when the front shows no face', () => {
		expect(
			documentWarnings({ kind: 'passport', side: 'front', faces: 0, signals: passport })
		).toEqual([
			'We could not find a photo of a face. Check this is the photo page of your passport, and that all of it is in the photo.'
		]);
	});

	it('warns when the back shows a face', () => {
		expect(
			documentWarnings({ kind: 'national_id', side: 'back', faces: 1, signals: null })
		).toEqual([
			'This side has a photo of a face, so it may be the front. Turn your identity card over and take the back.'
		]);
	});

	it('warns when the print names another document', () => {
		expect(
			documentWarnings({ kind: 'passport', side: 'front', faces: 1, signals: licence })
		).toEqual([
			'This looks like a driving licence, not a passport. Check you chose the right photo ID.'
		]);
	});

	it('warns when a front shows no sign of its document, but never asks that of a back', () => {
		expect(
			documentWarnings({ kind: 'driving_licence', side: 'front', faces: 1, signals: NOTHING })
		).toEqual(['We could not confirm this is the front of your driving licence.']);
		expect(
			documentWarnings({ kind: 'national_id', side: 'back', faces: 0, signals: NOTHING })
		).toEqual([]);
	});

	it('asks for a clearer photo when almost nothing could be read, and guesses nothing else', () => {
		const blurred = { ...licence, characters: MIN_CHARACTERS - 1 };
		expect(
			documentWarnings({ kind: 'passport', side: 'front', faces: 0, signals: blurred })
		).toEqual([
			'We could not find a photo of a face. Check this is the photo page of your passport, and that all of it is in the photo.',
			'We could not read the writing in this photo. Take it again in good light, with no glare, and hold the camera still.'
		]);
	});

	it('warns about nothing it could not find out', () => {
		expect(
			documentWarnings({ kind: 'passport', side: 'front', faces: null, signals: null })
		).toEqual([]);
	});
});
