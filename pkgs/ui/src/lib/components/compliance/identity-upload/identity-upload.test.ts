import { describe, expect, it } from 'vitest';
import { IDENTITY_PHOTO_FIELDS, documentTypeLabel, uploadSlots } from './identity-upload';

describe('uploadSlots', () => {
	const fields = (slots: ReturnType<typeof uploadSlots>) => slots.map((s) => s.field);

	it('asks for both sides of a card and the selfie', () => {
		expect(fields(uploadSlots('driving_licence', true))).toEqual([
			'idv_document_front',
			'idv_document_back',
			'idv_selfie'
		]);
		expect(fields(uploadSlots('national_id', false))).toEqual([
			'idv_document_front',
			'idv_document_back'
		]);
	});

	it('asks for no back for a passport', () => {
		const slots = uploadSlots('passport', false);
		expect(fields(slots)).toEqual(['idv_document_front']);
		expect(slots[0]).toMatchObject({ mode: 'document', label: 'Passport photo page' });
	});

	it('asks for the selfie alone when the document is kept', () => {
		const slots = uploadSlots(null, true);
		expect(slots).toEqual([
			{ field: 'idv_selfie', mode: 'face', label: 'Selfie', fileName: 'selfie' }
		]);
		expect(uploadSlots(null, false)).toEqual([]);
	});

	it('uses only the form fields the API reads', () => {
		const every = [...uploadSlots('driving_licence', true), ...uploadSlots('passport', true)];
		for (const slot of every) expect(IDENTITY_PHOTO_FIELDS).toContain(slot.field);
	});
});

describe('documentTypeLabel', () => {
	it('names each document type in words', () => {
		expect(documentTypeLabel('passport')).toBe('Passport');
		expect(documentTypeLabel('driving_licence')).toBe('Driving licence');
		expect(documentTypeLabel('national_id')).toBe('National ID card');
		expect(documentTypeLabel('residence_permit')).toBe('Residence permit');
	});
});
