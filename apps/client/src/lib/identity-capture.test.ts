import { describe, expect, it } from 'vitest';
import {
	attemptLabel,
	captureItemsKey,
	GENERIC_RETAKE_REASON,
	IDENTITY_CAPTURE_KEYS,
	identityRefresh,
	identityShots,
	keptDocumentType,
	photosSent,
	phoneWaitingList,
	photosToSend,
	retakeNeeds,
	retakeReason,
	shotsToTake,
	SUBMIT_FAILED,
	submitError,
	type IdentityCaptureItem
} from './identity-capture';

describe('identityShots', () => {
	it('asks for no back of a passport', () => {
		expect(identityShots('passport').map((s) => s.key)).toEqual([
			'idv_document_front',
			'idv_selfie'
		]);
	});

	it('asks for both sides of a card, then the face', () => {
		for (const type of ['driving_licence', 'national_id'] as const) {
			const shots = identityShots(type);
			expect(shots.map((s) => s.key)).toEqual([
				'idv_document_front',
				'idv_document_back',
				'idv_selfie'
			]);
			expect(shots.map((s) => s.mode)).toEqual(['document', 'document', 'face']);
		}
	});

	it('names each photo by what it shows', () => {
		expect(identityShots('driving_licence').map((s) => s.fileName)).toEqual([
			'id-front',
			'id-back',
			'selfie'
		]);
	});

	it('takes every photo key, in order', () => {
		expect(identityShots('national_id').map((s) => s.key)).toEqual([...IDENTITY_CAPTURE_KEYS]);
	});
});

describe('retake copy', () => {
	it('shows a quality reason as given and anything else as the generic line', () => {
		expect(retakeReason('The photo is blurred.')).toBe('The photo is blurred.');
		expect(retakeReason(null)).toBe(GENERIC_RETAKE_REASON);
		expect(retakeReason('  ')).toBe(GENERIC_RETAKE_REASON);
		expect(GENERIC_RETAKE_REASON).toBe('We could not verify your identity. Please try again.');
	});

	it('counts the try', () => {
		expect(attemptLabel(2)).toBe('Try 2');
	});
});

describe('photosToSend', () => {
	const item = (key: IdentityCaptureItem['key'], status: 'open' | 'fulfilled') => ({
		key,
		item_uid: `uid-${key}`,
		status
	});

	it('sends each open item and skips one the check already holds', () => {
		const { send, missing } = photosToSend(identityShots('national_id'), [
			item('idv_document_front', 'fulfilled'),
			item('idv_document_back', 'open'),
			item('idv_selfie', 'open')
		]);
		expect(send.map((s) => s.key)).toEqual(['idv_document_back', 'idv_selfie']);
		expect(missing).toEqual([]);
	});

	it('leaves the back item alone for a passport', () => {
		const { send } = photosToSend(identityShots('passport'), [
			item('idv_document_front', 'open'),
			item('idv_document_back', 'open'),
			item('idv_selfie', 'open')
		]);
		expect(send.map((s) => s.key)).toEqual(['idv_document_front', 'idv_selfie']);
	});

	it('reports a shot with no item to fill', () => {
		const { missing } = photosToSend(identityShots('passport'), [item('idv_selfie', 'open')]);
		expect(missing.map((s) => s.key)).toEqual(['idv_document_front']);
	});
});

describe('captureItemsKey', () => {
	const front = { key: 'idv_document_front', item_uid: 'item-1', status: 'open' } as const;
	const selfie = { key: 'idv_selfie', item_uid: 'item-2', status: 'open' } as const;

	it('moves when any item is new, and not when a photo lands', () => {
		const key = captureItemsKey({ items: [front, selfie] });
		expect(captureItemsKey({ items: [front, { ...selfie, status: 'fulfilled' }] })).toBe(key);
		// A refused selfie is asked for again on a new item; the front is kept.
		expect(captureItemsKey({ items: [front, { ...selfie, item_uid: 'item-3' }] })).not.toBe(key);
	});

	it('is empty with no capture at all', () => {
		expect(captureItemsKey(null)).toBe('');
		expect(captureItemsKey(undefined)).toBe('');
	});
});

describe('a retake that kept some photos', () => {
	const item = (key: IdentityCaptureItem['key'], status: 'open' | 'fulfilled') => ({
		key,
		item_uid: `uid-${key}`,
		status
	});
	const selfieOnly = [
		item('idv_document_front', 'fulfilled'),
		item('idv_document_back', 'fulfilled'),
		item('idv_selfie', 'open')
	];
	const documentOnly = [
		item('idv_document_front', 'open'),
		item('idv_document_back', 'open'),
		item('idv_selfie', 'fulfilled')
	];

	it('keeps the document when only the face is open', () => {
		const capture = {
			status: 'retake' as const,
			items: selfieOnly,
			document_type: 'driving_licence' as const
		};
		expect(keptDocumentType(capture)).toBe('driving_licence');
		expect(keptDocumentType({ ...capture, items: documentOnly })).toBeNull();
		expect(keptDocumentType({ ...capture, document_type: null })).toBeNull();
		expect(keptDocumentType({ ...capture, status: 'capturing' as const })).toBeNull();
	});

	it('asks only for the shots still open', () => {
		expect(shotsToTake(identityShots('driving_licence'), selfieOnly).map((s) => s.key)).toEqual([
			'idv_selfie'
		]);
		expect(shotsToTake(identityShots('driving_licence'), documentOnly).map((s) => s.key)).toEqual([
			'idv_document_front',
			'idv_document_back'
		]);
	});

	it('names the photo it needs', () => {
		expect(retakeNeeds(selfieOnly)).toBe('We need a new photo of your face.');
		expect(retakeNeeds(documentOnly)).toBe('We need a new photo of your document.');
		expect(retakeNeeds(selfieOnly.map((i) => ({ ...i, status: 'open' as const })))).toBeNull();
	});
});

describe('submitError', () => {
	it('is null once the photos are sent', () => {
		expect(submitError({ type: 'success', data: { ok: true } })).toBeNull();
	});

	it('passes the action’s own error through', () => {
		expect(submitError({ type: 'failure', data: { error: 'Send every photo first.' } })).toBe(
			'Send every photo first.'
		);
	});

	it('falls back to the generic line', () => {
		expect(submitError({ type: 'failure', data: {} })).toBe(SUBMIT_FAILED);
		expect(submitError({ type: 'failure', data: { error: '' } })).toBe(SUBMIT_FAILED);
		expect(submitError({ type: 'error' })).toBe(SUBMIT_FAILED);
		expect(submitError({ type: 'redirect' })).toBe(SUBMIT_FAILED);
	});
});

describe('identityRefresh', () => {
	const none = { captures: {}, dialogOpen: false, dialogMachineWait: false, phoneLink: false };

	it('keeps asking after the dialog is closed while the photos are with the check', () => {
		expect(identityRefresh({ ...none, captures: { idv: { status: 'submitted' } } })).toBe('check');
	});

	it('asks faster while the desktop is waiting on the other device', () => {
		expect(identityRefresh({ ...none, dialogOpen: true, phoneLink: true })).toBe('phone');
	});

	it('asks while the dialog is open and the send has not moved on yet', () => {
		expect(
			identityRefresh({
				...none,
				dialogOpen: true,
				dialogMachineWait: true,
				captures: { idv: { status: 'capturing' } }
			})
		).toBe('check');
	});

	it('stops once a person has the photos, or there is nothing in flight', () => {
		expect(
			identityRefresh({ ...none, captures: { idv: { status: 'review' } }, dialogOpen: true })
		).toBe(null);
		expect(
			identityRefresh({
				...none,
				captures: { idv: { status: 'review' } },
				dialogOpen: true,
				phoneLink: true
			})
		).toBe(null);
		expect(
			identityRefresh({
				...none,
				captures: { idv: { status: 'passed' } },
				dialogOpen: true,
				phoneLink: true
			})
		).toBe(null);
		expect(identityRefresh({ ...none, captures: { idv: { status: 'passed' } } })).toBe(null);
		expect(identityRefresh({ ...none, captures: { idv: { status: 'retake' } } })).toBe(null);
		expect(identityRefresh(none)).toBe(null);
	});

	it('keeps asking on the phone while a retake is taken on the other device', () => {
		expect(
			identityRefresh({
				...none,
				captures: { idv: { status: 'retake' } },
				dialogOpen: true,
				phoneLink: true
			})
		).toBe('phone');
		expect(
			identityRefresh({
				...none,
				captures: { earlier: { status: 'review' }, idv: { status: 'capturing' } },
				dialogOpen: true,
				phoneLink: true
			})
		).toBe('phone');
	});

	it('asks on the check cadence once the other device has sent the photos', () => {
		expect(
			identityRefresh({
				...none,
				captures: { idv: { status: 'submitted' } },
				dialogOpen: true,
				phoneLink: true
			})
		).toBe('check');
	});
});

describe('photosSent', () => {
	it('is true while the photos are with the check, and false while the client has something to do', () => {
		expect(photosSent({ status: 'submitted' })).toBe(true);
		expect(photosSent({ status: 'review' })).toBe(true);
		expect(photosSent({ status: 'capturing' })).toBe(false);
		expect(photosSent({ status: 'retake' })).toBe(false);
		expect(photosSent({ status: 'passed' })).toBe(false);
		expect(photosSent(null)).toBe(false);
		expect(photosSent(undefined)).toBe(false);
	});
});

describe('phoneWaitingList', () => {
	const items = [
		{ key: 'idv_document_front', item_uid: 'f', status: 'fulfilled' },
		{ key: 'idv_document_back', item_uid: 'b', status: 'open' },
		{ key: 'idv_selfie', item_uid: 's', status: 'open' }
	] as const;
	const capture = (progress?: {
		document_type: 'passport' | null;
		taken: ('idv_selfie' | 'idv_document_back')[];
	}) => ({
		items: items.map((i) => ({ ...i })),
		status: 'capturing' as const,
		attempt: 1,
		last_reason: null,
		manual_offered: false,
		phone_progress: progress
	});

	it('reads each photo as received, taken on the phone, or waiting', () => {
		expect(phoneWaitingList(capture({ document_type: null, taken: ['idv_selfie'] }))).toEqual([
			{ key: 'idv_document_front', itemUid: 'f', state: 'received' },
			{ key: 'idv_document_back', itemUid: 'b', state: 'waiting' },
			{ key: 'idv_selfie', itemUid: 's', state: 'taken' }
		]);
	});

	it('leaves out the back once the phone says it is a passport', () => {
		expect(
			phoneWaitingList(capture({ document_type: 'passport', taken: [] })).map((i) => i.key)
		).toEqual(['idv_document_front', 'idv_selfie']);
		expect(phoneWaitingList(capture()).map((i) => i.key)).toHaveLength(3);
	});
});
