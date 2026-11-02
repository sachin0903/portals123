import { describe, expect, it, vi } from 'vitest';
import {
	AGE_TONE,
	nextProfile,
	parseWorkFilter,
	photoHref,
	photoListHref,
	photosViewedHref,
	readPhotoSet,
	recordPhotosViewed,
	shownListing,
	type PhotoSet,
	PHOTOS_UNAVAILABLE,
	profileCache,
	profileDataHref,
	readJson,
	unlistedPhotos,
	withPhotos,
	type AmlClientPage,
	type Read,
	ageText,
	atPlace,
	imagesMissingText,
	amlPill,
	canDecideMatches,
	canRequestPhotos,
	canRerun,
	monitoringCovers,
	checkDecisions,
	clientHref,
	clientPhotoText,
	confirmedReferral,
	GENERIC_PHOTO_REQUEST_TEXT,
	heldOnConfirmedMatch,
	identityPill,
	isClientListQuery,
	isPdf,
	listHref,
	MATCH_ACTIONS,
	matchStatus,
	neighbour,
	parseListQuery,
	parseWorkQuery,
	photoReasonsFor,
	PHOTO_REQUEST_REASONS,
	photosRequested,
	riskOf,
	rowAwaitingResult,
	sentAgo,
	SHOW_OPTIONS,
	showCount,
	statusOf,
	stepToShow,
	undecidedMatches,
	uploadedByLine,
	waitingLong,
	workBreakdown,
	workHref,
	type AmlCheckDetail,
	type AmlClientRow,
	type AmlMatch,
	type AmlWorkSummary,
	type PhotoChoice,
	identitySteps
} from './aml';

function match(decision: AmlMatch['decision']): AmlMatch {
	return {
		uid: `m-${Math.random()}`,
		name: 'A Person',
		match_types: ['pep'],
		categories: ['PEP'],
		score: 90,
		dob: null,
		gender: null,
		countries: [],
		addresses: [],
		sources: [],
		references: [],
		images: [],
		names: [],
		aliases: [],
		roles: [],
		birth_places: [],
		country_details: [],
		dates: [],
		entity_type: null,
		notes: null,
		associations: [],
		source_links: [],
		types: [],
		active: null,
		deceased: null,
		last_reviewed: null,
		decision,
		decided_by: null,
		decided_at: null,
		comment: null
	};
}

function detail(
	kind: 'aml' | 'identity',
	status: AmlCheckDetail['check']['status'],
	matches: AmlMatch[] = []
): AmlCheckDetail {
	return {
		check: {
			uid: 'c1',
			kind,
			origin: 'portal',
			status,
			outcome: null,
			attempt: 1,
			monitoring: false,
			stale_at: null,
			decided_by: null,
			decided_at: null,
			decision_comment: null,
			provider_check_id: null,
			created_at: '2026-10-01T00:00:00Z',
			updated_at: '2026-10-01T00:00:00Z'
		},
		client: {
			name: 'A Client',
			dob: null,
			nationality: null,
			country: null,
			contact_uid: 'k1',
			tenant_uid: 't1',
			tenant_name: 'Firm'
		},
		matches,
		identity: null,
		history: []
	};
}

describe('checkDecisions', () => {
	it('offers Reject and Clear on an identity refer', () => {
		expect(checkDecisions(detail('identity', 'refer'))).toEqual(['rejected', 'clear']);
	});

	it('offers only Reject on an identity check ComplyCube could not run', () => {
		expect(checkDecisions(detail('identity', 'failed'))).toEqual(['rejected']);
	});

	it('never offers a check-level decision on an AML check, even with every match decided', () => {
		expect(checkDecisions(detail('aml', 'refer', [match('dismissed')]))).toEqual([]);
		expect(checkDecisions(detail('aml', 'failed'))).toEqual([]);
	});

	it('offers nothing on an identity check that is pending, settled or already decided', () => {
		for (const status of ['pending', 'clear', 'rejected'] as const) {
			expect(checkDecisions(detail('identity', status))).toEqual([]);
		}
		const decided = detail('identity', 'failed');
		decided.check.decided_at = '2026-10-01T10:00:00Z';
		expect(checkDecisions(decided)).toEqual([]);
	});
});

describe('canRerun', () => {
	it('offers a rerun for a failed AML screen nobody has run again', () => {
		expect(canRerun(detail('aml', 'failed'))).toBe(true);
	});

	it('does not offer one once it was run again, or for any other check', () => {
		const rerun = detail('aml', 'failed');
		rerun.check.decided_at = '2026-10-01T10:00:00Z';
		expect(canRerun(rerun)).toBe(false);
		expect(canRerun(detail('aml', 'refer'))).toBe(false);
		expect(canRerun(detail('identity', 'failed'))).toBe(false);
	});
});

describe('monitoringCovers', () => {
	it('covers a finished AML screen that was started with monitoring', () => {
		const finished = detail('aml', 'clear');
		finished.check.monitoring = true;
		expect(monitoringCovers(finished.check)).toBe(true);
		const referred = detail('aml', 'refer');
		referred.check.monitoring = true;
		expect(monitoringCovers(referred.check)).toBe(true);
	});

	it('does not cover a screen ComplyCube could not run, or one started without monitoring', () => {
		const failed = detail('aml', 'failed');
		failed.check.monitoring = true;
		expect(monitoringCovers(failed.check)).toBe(false);
		expect(monitoringCovers(detail('aml', 'clear').check)).toBe(false);
	});
});

describe('a screen settled on a confirmed match', () => {
	const settled = () => detail('aml', 'refer', [match('confirmed'), match('dismissed')]);
	const partner = () => {
		const d = settled();
		d.check.origin = 'partner';
		return d;
	};

	it('holds a portal journey until Graphene allows it to continue', () => {
		expect(heldOnConfirmedMatch(settled())).toBe(true);
		expect(confirmedReferral(settled())).toBe(false);
		const allowed = settled();
		allowed.check.decided_at = '2026-10-02T12:00:00Z';
		expect(heldOnConfirmedMatch(allowed)).toBe(false);
	});

	it('refers a partner client', () => {
		expect(confirmedReferral(partner())).toBe(true);
		expect(heldOnConfirmedMatch(partner())).toBe(false);
	});

	it('is neither while a match is open, or when every match was rejected', () => {
		const open = detail('aml', 'refer', [match('confirmed'), match(null)]);
		expect(heldOnConfirmedMatch(open)).toBe(false);
		expect(confirmedReferral(open)).toBe(false);
		const rejected = detail('aml', 'refer', [match('dismissed')]);
		expect(heldOnConfirmedMatch(rejected)).toBe(false);
		expect(confirmedReferral(rejected)).toBe(false);
	});

	it('freezes the matches on a portal screen once the journey is allowed to continue', () => {
		expect(canDecideMatches(settled())).toBe(true);
		const allowed = settled();
		allowed.check.decided_at = '2026-10-02T12:00:00Z';
		expect(canDecideMatches(allowed)).toBe(false);
		const referred = partner();
		referred.check.decided_at = '2026-10-02T12:00:00Z';
		expect(canDecideMatches(referred)).toBe(true);
	});
});

describe('undecidedMatches', () => {
	it('counts the matches without a decision', () => {
		expect(undecidedMatches(detail('aml', 'refer', [match(null), match('confirmed')]))).toBe(1);
	});
});

describe('isPdf', () => {
	it('is true only for a PDF', () => {
		expect(isPdf({ content_type: 'application/pdf' })).toBe(true);
		expect(isPdf({ content_type: 'image/jpeg' })).toBe(false);
		expect(isPdf({ content_type: 'image/png' })).toBe(false);
		expect(isPdf({ content_type: 'application/octet-stream' })).toBe(false);
	});
});

describe('canRequestPhotos', () => {
	it('is offered on an undecided portal identity refer with a try left', () => {
		expect(canRequestPhotos(detail('identity', 'refer'))).toBe(true);
		const secondTry = detail('identity', 'refer');
		secondTry.check.attempt = 2;
		expect(canRequestPhotos(secondTry)).toBe(true);
	});

	it('is offered on any try: there is no try limit', () => {
		const laterTry = detail('identity', 'refer');
		laterTry.check.attempt = 5;
		expect(canRequestPhotos(laterTry)).toBe(true);
	});

	it('is not offered on a partner check', () => {
		const partner = detail('identity', 'refer');
		partner.check.origin = 'partner';
		expect(canRequestPhotos(partner)).toBe(false);
	});

	it('is not offered once the check is decided', () => {
		const decided = detail('identity', 'refer');
		decided.check.decided_at = '2026-10-01T10:00:00Z';
		expect(canRequestPhotos(decided)).toBe(false);
	});

	it('is not offered on an AML check', () => {
		expect(canRequestPhotos(detail('aml', 'refer'))).toBe(false);
	});

	it('is not offered off a refer: failed, pending or clear', () => {
		for (const status of ['failed', 'pending', 'clear'] as const) {
			expect(canRequestPhotos(detail('identity', status))).toBe(false);
		}
	});
});

describe('photoReasonsFor', () => {
	function values(photos: PhotoChoice[]): string[] {
		return photoReasonsFor(photos).map((r) => r.value);
	}

	it('offers only the reasons that always fit when no photo is chosen', () => {
		expect(values([])).toEqual(['blurry', 'none']);
	});

	it('adds the selfie-only reason only once the selfie is asked for', () => {
		expect(values(['document'])).toEqual(['blurry', 'cropped', 'glare', 'expired', 'none']);
		expect(values(['selfie'])).toEqual(['blurry', 'face', 'none']);
	});

	it('offers every reason once both photos are asked for', () => {
		expect(values(['document', 'selfie'])).toEqual([
			'blurry',
			'cropped',
			'glare',
			'face',
			'expired',
			'none'
		]);
	});
});

describe('photosRequested', () => {
	it('is true for a decided identity refer — new photos asked for, not cleared or rejected', () => {
		const asked = detail('identity', 'refer');
		asked.check.decided_at = '2026-10-01T10:00:00Z';
		expect(photosRequested(asked)).toBe(true);
	});

	it('is false while the refer is still undecided, or for any other status', () => {
		expect(photosRequested(detail('identity', 'refer'))).toBe(false);
		const settled = detail('identity', 'clear');
		settled.check.decided_at = '2026-10-01T10:00:00Z';
		expect(photosRequested(settled)).toBe(false);
	});
});

describe('client-safe photo-request reasons', () => {
	it('reads each reason exactly as the client sees it', () => {
		expect(clientPhotoText('blurry')).toBe(
			'The photo was not clear enough. Please retake it in good light.'
		);
		expect(clientPhotoText('cropped')).toBe(
			'Part of your document was cut off. Make sure all four corners are in the photo.'
		);
		expect(clientPhotoText('glare')).toBe(
			'There was glare on your document. Please avoid reflections.'
		);
		expect(clientPhotoText('face')).toBe(
			'Your face was not clearly visible in the selfie. Please retake it in good light.'
		);
		expect(clientPhotoText('expired')).toBe(
			'Your document has expired. Please use an in-date document.'
		);
		expect(clientPhotoText('none')).toBe(GENERIC_PHOTO_REQUEST_TEXT);
	});

	it('never names a fraud signal or a face-match problem', () => {
		for (const reason of PHOTO_REQUEST_REASONS) {
			const text = reason.label.toLowerCase();
			expect(text).not.toContain('fraud');
			expect(text).not.toContain('match');
			expect(text).not.toContain('mismatch');
		}
	});
});

describe('labels', () => {
	it('reads a vendor value as words', () => {});

	it('falls back to words for a status it does not know', () => {
		expect(statusOf('refer')).toEqual({ label: 'Refer', tone: 'warning' });
		expect(statusOf('on_hold')).toEqual({ label: 'On hold', tone: 'neutral' });
	});
});

describe('the client list', () => {
	it('names the identity pill by what it waits on', () => {
		expect(identityPill(null)).toEqual({ label: 'Not started', tone: 'neutral' });
		const refer = { uid: 'i1', status: 'refer' as const, attempt: 1, updated_at: 'x' };
		expect(identityPill({ ...refer, decided: false })).toEqual({ label: 'Refer', tone: 'warning' });
		// New photos asked for: the refer now waits on the client, not on Graphene.
		expect(identityPill({ ...refer, decided: true })).toEqual({
			label: 'New photos requested',
			tone: 'info'
		});
		expect(identityPill({ ...refer, status: 'clear', decided: true }).label).toBe('Clear');
	});

	it('names the AML pill: allowed to continue for a portal client, referred for a partner one', () => {
		expect(amlPill(null).label).toBe('Not started');
		const aml = { uid: null, monitoring: false, updated_at: null };
		expect(amlPill({ ...aml, status: 'awaiting_screen', decided: false })).toEqual({
			label: 'Awaiting screen',
			tone: 'info'
		});
		expect(amlPill({ ...aml, status: 'refer', decided: false }).label).toBe('Refer');
		expect(amlPill({ ...aml, status: 'awaiting_screen', decided: false, queued: true })).toEqual({
			label: 'Screen starting',
			tone: 'info'
		});
		expect(amlPill({ ...aml, status: 'refer', decided: true })).toEqual({
			label: 'Allowed to continue',
			tone: 'success'
		});
		expect(amlPill({ ...aml, status: 'refer', decided: true }, 'partner')).toEqual({
			label: 'Referred',
			tone: 'danger'
		});
	});

	it('reads ComplyCube risk levels, and no profile as not set', () => {
		expect(riskOf('low')).toEqual({ label: 'Low', tone: 'success' });
		expect(riskOf('medium')).toEqual({ label: 'Medium', tone: 'warning' });
		expect(riskOf('high')).toEqual({ label: 'High', tone: 'danger' });
		expect(riskOf('very_high')).toEqual({ label: 'Very high', tone: 'danger' });
		expect(riskOf('not_set')).toEqual({ label: 'Not set', tone: 'neutral' });
		expect(riskOf(null)).toEqual({ label: 'Not set', tone: 'neutral' });
		expect(riskOf('extreme')).toEqual({ label: 'Extreme', tone: 'neutral' });
	});

	it('keeps every narrowing in the URL and the plain list at /aml/clients', () => {
		const query = parseListQuery(new URLSearchParams('tenant=t1&q=ada&sort=risk&dir=desc&page=3'));
		expect(query).toEqual({ tenant: 't1', q: 'ada', sort: 'risk', dir: 'desc', page: 3 });
		expect(listHref(query)).toBe('/aml/clients?tenant=t1&q=ada&sort=risk&dir=desc&page=3');
		expect(listHref({ ...query, tenant: '', q: '', sort: '', page: 1 })).toBe('/aml/clients');
	});

	it('tells an old client-list link from a workbench one', () => {
		for (const search of ['q=ada', 'sort=risk&dir=desc', 'page=2', 'tenant=t1&dir=asc'])
			expect(isClientListQuery(new URLSearchParams(search))).toBe(true);
		for (const search of ['', 'tenant=t1', 'show=hits&tenant=t1&at=ct_1'])
			expect(isClientListQuery(new URLSearchParams(search))).toBe(false);
	});

	it('reads a bad URL as the default list', () => {
		expect(parseListQuery(new URLSearchParams('page=-2&dir=sideways'))).toEqual({
			tenant: '',
			q: '',
			sort: '',
			dir: 'asc',
			page: 1
		});
		expect(parseListQuery(new URLSearchParams('page=two')).page).toBe(1);
		// `dir` only travels with a sort.
		expect(listHref({ tenant: '', q: '', sort: '', dir: 'desc', page: 1 })).toBe('/aml/clients');
	});

	it('addresses a client page by its key, optionally on one check', () => {
		expect(clientHref('abc123')).toBe('/aml/clients/abc123');
		expect(clientHref('contact-xyz', 'chk 1')).toBe('/aml/clients/contact-xyz?check=chk%201');
		expect(clientHref('abc123', null)).toBe('/aml/clients/abc123');
	});
});

describe('a match card', () => {
	it('offers True match and False positive, and badges an undecided match', () => {
		expect(MATCH_ACTIONS).toEqual([
			{ value: 'confirmed', label: 'True match' },
			{ value: 'dismissed', label: 'False positive' }
		]);
		expect(matchStatus({ decision: null })).toEqual({ label: 'Undecided', tone: 'warning' });
		expect(matchStatus({ decision: 'confirmed' }).label).toBe('Confirmed');
		expect(matchStatus({ decision: 'dismissed' }).label).toBe('Rejected');
	});

	it('takes decisions only while its screen is a refer', () => {
		expect(canDecideMatches(detail('aml', 'refer', [match(null)]))).toBe(true);
		for (const status of ['pending', 'clear', 'rejected', 'failed'] as const) {
			expect(canDecideMatches(detail('aml', status))).toBe(false);
		}
		expect(canDecideMatches(detail('identity', 'refer'))).toBe(false);
	});
});

describe('a check waiting on ComplyCube', () => {
	const sent = '2026-10-02T14:00:00Z';
	const at = (minutes: number) => new Date(sent).getTime() + minutes * 60_000;

	it('says how long ago it was sent', () => {
		expect(sentAgo(sent, at(0))).toBe('just now');
		expect(sentAgo(sent, at(4))).toBe('4 min ago');
		expect(sentAgo(sent, at(130))).toBe('2 h ago');
		expect(sentAgo(sent, at(60 * 24))).toBe('1 day ago');
		expect(sentAgo(sent, at(60 * 72))).toBe('3 days ago');
		expect(sentAgo('not a date', at(1))).toBe('not a date');
	});

	it('is slow after 30 minutes', () => {
		expect(waitingLong(sent, at(30))).toBe(false);
		expect(waitingLong(sent, at(31))).toBe(true);
		expect(waitingLong('not a date', at(60))).toBe(false);
	});
});

describe('uploadedByLine', () => {
	it('names an operator plainly and an adviser as the firm’s', () => {
		expect(uploadedByLine({ uploaded_by: 'Olu Ops', uploaded_via: 'operations' })).toBe(
			'Photos uploaded by Olu Ops'
		);
		expect(uploadedByLine({ uploaded_by: 'Ada Adviser', uploaded_via: 'firm' })).toBe(
			"Photos uploaded by Ada Adviser, the firm's adviser"
		);
		expect(uploadedByLine({ uploaded_by: 'Olu Ops' })).toBe('Photos uploaded by Olu Ops');
	});

	it('says nothing when the client sent the photos', () => {
		expect(uploadedByLine({ uploaded_by: null, uploaded_via: null })).toBeNull();
		expect(uploadedByLine({})).toBeNull();
	});
});

describe('imagesMissingText', () => {
	it('says why a check shows no photos', () => {
		expect(imagesMissingText('none')).toBe('No photos were sent for this check.');
		expect(imagesMissingText('not_held')).toContain('another key, live or test');
		expect(imagesMissingText('unavailable')).toContain('Reload');
		expect(imagesMissingText(undefined)).toBe('No photos can be shown for this check.');
	});
});

describe('identitySteps', () => {
	const earlier = [
		{ uid: 'aml1', kind: 'aml' as const, created_at: '2026-10-03T09:00:00Z' },
		{ uid: 'id1', kind: 'identity' as const, created_at: '2026-10-01T09:00:00Z' },
		{ uid: 'id2', kind: 'identity' as const, created_at: '2026-10-02T09:00:00Z' }
	];

	it('steps from the current check to the older identity tries, never to an AML screen', () => {
		expect(identitySteps('ct_1', 'id3', earlier, 'id3')).toEqual({
			older: '/aml/clients/ct_1?check=id2',
			newer: null,
			position: 3,
			count: 3
		});
		expect(identitySteps('ct_1', 'id3', earlier, 'id2')).toEqual({
			older: '/aml/clients/ct_1?check=id1',
			newer: '/aml/clients/ct_1',
			position: 2,
			count: 3
		});
		expect(identitySteps('ct_1', 'id3', earlier, 'id1')).toEqual({
			older: null,
			newer: '/aml/clients/ct_1?check=id2',
			position: 1,
			count: 3
		});
	});

	it('offers nothing with only one identity try', () => {
		expect(identitySteps('ct_1', 'id3', [earlier[0]], 'id3')).toBeNull();
	});
});

describe('rowAwaitingResult', () => {
	const identity = (
		status: NonNullable<AmlClientRow['identity']>['status']
	): AmlClientRow['identity'] => ({
		uid: 'id',
		status,
		attempt: 1,
		decided: false,
		updated_at: '2026-10-13T12:00:00Z'
	});
	const aml = (
		status: NonNullable<AmlClientRow['aml']>['status'],
		queued = false
	): AmlClientRow['aml'] => ({
		uid: status === 'awaiting_screen' ? null : 'aml',
		status,
		decided: false,
		monitoring: false,
		updated_at: '2026-10-13T12:00:00Z',
		queued
	});

	it('asks again while a check is still with ComplyCube, or a screen is about to start', () => {
		expect(rowAwaitingResult({ identity: identity('pending'), aml: null })).toBe(true);
		expect(rowAwaitingResult({ identity: null, aml: aml('pending') })).toBe(true);
		expect(rowAwaitingResult({ identity: null, aml: aml('awaiting_screen', true) })).toBe(true);
	});

	it('stops for a person, a finished check, or a screen nobody has queued', () => {
		expect(rowAwaitingResult({ identity: identity('refer'), aml: aml('refer') })).toBe(false);
		expect(rowAwaitingResult({ identity: identity('clear'), aml: aml('clear') })).toBe(false);
		expect(rowAwaitingResult({ identity: null, aml: aml('awaiting_screen', false) })).toBe(false);
		expect(rowAwaitingResult({ identity: null, aml: null })).toBe(false);
	});
});

describe('the workbench', () => {
	it('reads its place from the URL, and anything unknown as all profiles', () => {
		expect(parseWorkQuery(new URLSearchParams('show=hits&tenant=t1&at=ct_1'))).toEqual({
			show: 'hits',
			tenant: 't1',
			at: 'ct_1'
		});
		expect(parseWorkQuery(new URLSearchParams(''))).toEqual({ show: 'all', tenant: '', at: '' });
		expect(parseWorkQuery(new URLSearchParams('show=everything')).show).toBe('all');
		// A choice the workbench no longer offers falls back to the default.
		for (const show of ['open', 'mine', 'none', 'others'])
			expect(parseWorkQuery(new URLSearchParams(`show=${show}`)).show).toBe('all');
		// A key the object inherits is not a choice.
		expect(parseWorkQuery(new URLSearchParams('show=toString')).show).toBe('all');
	});

	it('keeps the plain workbench at /aml and carries only what is set', () => {
		expect(workHref()).toBe('/aml');
		expect(workHref({ show: 'all', tenant: '', at: '' })).toBe('/aml');
		expect(workHref({ show: 'hits', tenant: 't 1', at: 'ct_1' })).toBe(
			'/aml?show=hits&tenant=t+1&at=ct_1'
		);
		const query = parseWorkQuery(new URLSearchParams('show=held&tenant=t1&at=ct_9'));
		expect(workHref(query)).toBe('/aml?show=held&tenant=t1&at=ct_9');
	});

	it('shows all profiles by default, or one reason', () => {
		expect(Object.keys(SHOW_OPTIONS)).toEqual([
			'all',
			'hits',
			'identity',
			'screens',
			'monitoring',
			'held'
		]);
		expect(SHOW_OPTIONS.all).toEqual({ label: 'All profiles', reason: null });
		for (const show of ['hits', 'identity', 'screens', 'monitoring', 'held'] as const)
			expect(SHOW_OPTIONS[show].reason).toBe(show);
	});

	it('counts each choice from the summary', () => {
		const summary: AmlWorkSummary = {
			total: 9,
			by_reason: { hits: 4, identity: 2, screens: 1, monitoring: 1, held: 1 },
			overdue: 3
		};
		expect(showCount('all', summary)).toBe(9);
		expect(showCount('hits', summary)).toBe(4);
		expect(showCount('held', summary)).toBe(1);
	});

	it('splits the work into identity checks and AML hits, each a way into its choice', () => {
		const counts = (identity: number, hits: number) => ({
			by_reason: { hits, identity, screens: 0, monitoring: 0, held: 0 }
		});
		expect(workBreakdown(counts(12, 23))).toEqual([
			{ show: 'identity', count: 12, label: '12 identity checks' },
			{ show: 'hits', count: 23, label: '23 with AML hits' }
		]);
		expect(workBreakdown(counts(1, 1)).map((part) => part.label)).toEqual([
			'1 identity check',
			'1 with AML hits'
		]);
		// A zero still shows, so the split always reads.
		expect(workBreakdown(counts(0, 0)).map((part) => part.label)).toEqual([
			'0 identity checks',
			'0 with AML hits'
		]);
	});

	it('opens a profile on the step the choice is about, else its first open one', () => {
		const steps = [
			{ key: 'identity', state: 'open' },
			{ key: 'screened', state: 'done' },
			{ key: 'monitoring', state: 'advisory' },
			{ key: 'hits', state: 'open' },
			{ key: 'risk', state: 'waiting' }
		] as const;
		expect(stepToShow([...steps], 'all')).toBe('identity');
		expect(stepToShow([...steps], 'hits')).toBe('hits');
		expect(stepToShow([...steps], 'held')).toBe('hits');
		expect(stepToShow([...steps], 'screens')).toBe('identity');
		expect(stepToShow([{ key: 'identity', state: 'done' }], 'all')).toBeNull();
	});

	it('moves to the next and the previous profile, passing over the skipped', () => {
		const keys = ['a', 'b', 'c', 'd'];
		expect(neighbour(keys, 'b', 1)).toBe('c');
		expect(neighbour(keys, 'b', -1)).toBe('a');
		expect(neighbour(keys, 'b', 1, ['c'])).toBe('d');
		expect(neighbour(keys, 'd', -1, new Set(['c', 'b']))).toBe('a');
		// From a profile the queue no longer holds: forward is the first, back the last.
		expect(neighbour(keys, 'gone', 1, ['a'])).toBe('b');
		expect(neighbour(keys, 'gone', -1)).toBe('d');
		expect(neighbour(keys, 'gone', -1, ['d'])).toBe('c');
		expect(neighbour(keys, null, 1)).toBe('a');
		expect(neighbour([], 'a', 1)).toBeNull();
	});

	it('loops round the queue at either end', () => {
		const keys = ['a', 'b', 'c', 'd'];
		// Forward from the last is the first; back from the first is the last.
		expect(neighbour(keys, 'd', 1)).toBe('a');
		expect(neighbour(keys, 'a', -1)).toBe('d');
		// Past the skipped, round the end.
		expect(neighbour(keys, 'b', 1, ['c', 'd'])).toBe('a');
		expect(neighbour(keys, 'c', 1, ['d', 'a'])).toBe('b');
		expect(neighbour(keys, 'b', -1, ['a', 'd'])).toBe('c');
		// Every other profile skipped: nothing to move to, never the profile itself.
		expect(neighbour(keys, 'b', 1, ['a', 'c', 'd'])).toBeNull();
		expect(neighbour(keys, 'b', -1, ['a', 'c', 'd'])).toBeNull();
		// A queue of one never moves to itself.
		expect(neighbour(['a'], 'a', 1)).toBeNull();
		expect(neighbour(['a'], 'a', -1)).toBeNull();
		// From outside the queue with everything skipped, nothing.
		expect(neighbour(keys, 'gone', 1, keys)).toBeNull();
	});

	it('gives a place that was left to the profile that took it', () => {
		// 'b' was at place 1 and left: 'c' moved up into it.
		expect(atPlace(['a', 'c', 'd'], 1)).toBe('c');
		expect(atPlace(['a', 'c', 'd'], 1, ['c'])).toBe('d');
		// Past the skipped, round the end to the first.
		expect(atPlace(['a', 'c', 'd'], 1, ['c', 'd'])).toBe('a');
		// The last profile left: the queue loops to the first.
		expect(atPlace(['a', 'b'], 2)).toBe('a');
		expect(atPlace(['a', 'b'], 2, ['a'])).toBe('b');
		expect(atPlace(['a'], 0, ['a'])).toBeNull();
		expect(atPlace([], 0)).toBeNull();
	});

	it('says a wait in working days, toned by how long it has run', () => {
		expect(ageText({ working_days: 0 })).toBe('Today');
		expect(ageText({ working_days: 1 })).toBe('1 working day');
		expect(ageText({ working_days: 3 })).toBe('3 working days');
		expect(AGE_TONE).toEqual({ fresh: 'neutral', ageing: 'warning', overdue: 'danger' });
	});
});

describe('the workbench between profiles', () => {
	it('reads what the workbench shows and for which firm, without the profile on screen', () => {
		expect(parseWorkFilter(new URLSearchParams('show=hits&tenant=t1&at=ct_1'))).toEqual({
			show: 'hits',
			tenant: 't1'
		});
		expect(parseWorkFilter(new URLSearchParams('show=others'))).toEqual({
			show: 'all',
			tenant: ''
		});
	});

	it('goes next to the following profile, or to the one that took the place of one that left', () => {
		const keys = ['a', 'b', 'c'];
		expect(nextProfile(keys, 'a', 0)).toBe('b');
		expect(nextProfile(keys, 'c', 2)).toBe('a');
		expect(nextProfile(keys, 'a', 0, ['b'])).toBe('c');
		// 'x' was at place 1 and left: 'b' took its place.
		expect(nextProfile(keys, 'x', 1)).toBe('b');
		expect(nextProfile(keys, null, 0)).toBe('a');
		expect(nextProfile([], 'a', 0)).toBeNull();
	});

	it('reads a profile ahead without its photos, and the one on screen with them', () => {
		expect(profileDataHref('ct 1', false)).toBe('/aml/clients/ct%201/data?images=false');
		expect(profileDataHref('ct_1', true)).toBe('/aml/clients/ct_1/data');
		expect(photoListHref('chk/1')).toBe('/aml/checks/chk%2F1/images');
		// Photos read ahead are listed without recording a view, and recorded once shown.
		expect(photoListHref('chk_1', false)).toBe('/aml/checks/chk_1/images?record=false');
		expect(photoHref('chk_1', 'selfie')).toBe('/aml/checks/chk_1/images/selfie');
		expect(photosViewedHref('chk_1')).toBe('/aml/checks/chk_1/images/viewed');
	});
});

describe('readJson', () => {
	const answer = (res: Response | Error) =>
		vi.fn(async () => {
			if (res instanceof Error) throw res;
			return res;
		}) as unknown as typeof globalThis.fetch;

	it('gives the body of a read that worked', async () => {
		const fetch = answer(new Response(JSON.stringify({ key: 'ct_1' })));
		expect(await readJson(fetch, '/x')).toEqual({ ok: true, body: { key: 'ct_1' } });
		expect(fetch).toHaveBeenCalledWith('/x', { headers: { accept: 'application/json' } });
	});

	it('keeps the reason for a refusal, the API one or this app one', async () => {
		const api = answer(
			new Response(JSON.stringify({ detail: 'Client not found.' }), { status: 404 })
		);
		expect(await readJson(api, '/x')).toEqual({
			ok: false,
			status: 404,
			error: 'Client not found.'
		});
		const app = answer(new Response(JSON.stringify({ message: 'Not signed in' }), { status: 401 }));
		expect(await readJson(app, '/x')).toEqual({ ok: false, status: 401, error: 'Not signed in' });
		const bare = answer(new Response('<html>', { status: 502 }));
		expect(await readJson(bare, '/x')).toEqual({
			ok: false,
			status: 502,
			error: 'Something went wrong.'
		});
	});

	it('says the service is unavailable when nothing answers', async () => {
		expect(await readJson(answer(new Error('offline')), '/x')).toEqual({
			ok: false,
			status: 503,
			error: 'The service is unavailable.'
		});
	});
});

describe('the photos of the profile on screen', () => {
	function page(images: [] | null): AmlClientPage {
		const identity = detail('identity', 'refer');
		return {
			key: 'ct_1',
			identity: {
				...identity,
				check: { ...identity.check, uid: 'id1' },
				identity: { document_type: 'passport', breakdown: [], images }
			},
			aml: detail('aml', 'clear')
		} as unknown as AmlClientPage;
	}

	it('finds the identity checks whose photos are not listed yet', () => {
		expect(unlistedPhotos(page(null))).toEqual(['id1']);
		expect(unlistedPhotos(page([]))).toEqual([]);
		expect(unlistedPhotos({ identity: null, aml: null } as unknown as AmlClientPage)).toEqual([]);
	});

	it('fills in one check listing, and leaves the rest as it was', () => {
		const before = page(null);
		const listing = {
			images: [
				{ part: 'front' as const, label: 'Front', content_type: 'image/jpeg' },
				{ part: 'selfie' as const, label: 'Selfie', content_type: 'image/png' }
			],
			images_missing: null
		};
		const after = withPhotos(before, 'id1', listing);
		expect(after.identity?.identity?.images).toEqual(listing.images);
		expect(after.identity?.identity?.images_missing).toBeNull();
		expect(after.aml).toBe(before.aml);
		expect(before.identity?.identity?.images).toBeNull();
		expect(unlistedPhotos(after)).toEqual([]);

		// Another check's listing changes nothing.
		expect(withPhotos(before, 'other', listing).identity).toBe(before.identity);
	});

	it('says why when the photos could not be listed', () => {
		const after = withPhotos(page(null), 'id1', PHOTOS_UNAVAILABLE);
		expect(after.identity?.identity?.images).toEqual([]);
		expect(after.identity?.identity?.images_missing).toBe('unavailable');
	});
});

describe('the photos read ahead', () => {
	const LISTING = {
		images: [
			{ part: 'front' as const, label: 'Front', content_type: 'image/jpeg' },
			{ part: 'selfie' as const, label: 'Selfie', content_type: 'image/png' }
		],
		images_missing: null
	};

	/** A fetch that answers by URL, recording each call. */
	function answers(by: Record<string, Response | Error>) {
		return vi.fn(async (href: string) => {
			const answer = by[href];
			if (!answer) return new Response(null, { status: 404 });
			if (answer instanceof Error) throw answer;
			return answer.clone();
		}) as unknown as typeof globalThis.fetch & ReturnType<typeof vi.fn>;
	}

	it('lists them without recording a view, then reads each photo, a failed one as null', async () => {
		const fetch = answers({
			'/aml/checks/id1/images?record=false': new Response(JSON.stringify(LISTING)),
			'/aml/checks/id1/images/front': new Response('jpeg', {
				headers: { 'content-type': 'image/jpeg' }
			}),
			'/aml/checks/id1/images/selfie': new Error('offline')
		});
		const set = await readPhotoSet(fetch, 'id1');
		expect(set?.listing).toEqual(LISTING);
		expect(await set?.blobs[0]?.text()).toBe('jpeg');
		expect(set?.blobs[0]?.type).toBe('image/jpeg');
		expect(set?.blobs[1]).toBeNull();
		expect(fetch.mock.calls.map(([href]) => href)).toEqual([
			'/aml/checks/id1/images?record=false',
			'/aml/checks/id1/images/front',
			'/aml/checks/id1/images/selfie'
		]);
	});

	it('reads nothing more when the listing fails', async () => {
		const fetch = answers({});
		expect(await readPhotoSet(fetch, 'id1')).toBeNull();
		expect(fetch).toHaveBeenCalledTimes(1);
	});

	it('shows each photo whose bytes arrived from its own URL, and the rest from the relay', () => {
		const set: PhotoSet = { listing: LISTING, blobs: [new Blob(['a']), null] };
		const shown = shownListing(set, () => 'blob:front');
		expect(shown.images[0]).toEqual({ ...LISTING.images[0], src: 'blob:front' });
		expect(shown.images[1]).toEqual(LISTING.images[1]);
		expect(shown.images_missing).toBeNull();
	});

	it('records the view of the photos shown, at most three, and nothing for none', async () => {
		const fetch = vi.fn(async () => new Response(null, { status: 204 }));
		await recordPhotosViewed(fetch as never, 'id1', 2);
		expect(fetch).toHaveBeenCalledWith('/aml/checks/id1/images/viewed', {
			method: 'POST',
			headers: { 'content-type': 'application/json' },
			body: JSON.stringify({ image_count: 2 })
		});
		await recordPhotosViewed(fetch as never, 'id1', 5);
		expect(
			JSON.parse((fetch.mock.calls[1] as unknown as [string, RequestInit])[1].body as string)
		).toEqual({
			image_count: 3
		});
		await recordPhotosViewed(fetch as never, 'id1', 0);
		expect(fetch).toHaveBeenCalledTimes(2);
		// Nothing answering never breaks the page.
		const offline = vi.fn(async () => {
			throw new Error('offline');
		});
		await expect(recordPhotosViewed(offline as never, 'id1', 1)).resolves.toBeUndefined();
	});
});

describe('profileCache', () => {
	/** A profile with one identity check whose photos are not listed yet, unless `photos` is false. */
	const body = (key: string, photos = true) =>
		({
			key,
			identity: photos
				? {
						...detail('identity', 'refer'),
						check: { ...detail('identity', 'refer').check, uid: `id-${key}` },
						identity: { document_type: 'passport', breakdown: [], images: null }
					}
				: null,
			aml: null
		}) as unknown as AmlClientPage;

	const SET: PhotoSet = {
		listing: {
			images: [{ part: 'front', label: 'Front', content_type: 'image/jpeg' }],
			images_missing: null
		},
		blobs: [new Blob(['jpeg'])]
	};

	/** Reads that wait until the test settles them, and object URLs it can count. */
	function setUp() {
		const waiting = new Map<string, (answer: Read<AmlClientPage>) => void>();
		const read = vi.fn(
			(key: string) =>
				new Promise<Read<AmlClientPage>>((resolve) => {
					waiting.set(key, resolve);
				})
		);
		const settle = (key: string, answer: Read<AmlClientPage> = { ok: true, body: body(key) }) =>
			waiting.get(key)?.(answer);
		const readPhotos = vi.fn(async (): Promise<PhotoSet | null> => SET);
		let made = 0;
		const live = new Set<string>();
		const urls = {
			createObjectURL: vi.fn(() => {
				const url = `blob:${++made}`;
				live.add(url);
				return url;
			}),
			revokeObjectURL: vi.fn((url: string) => live.delete(url))
		};
		const cache = profileCache(read, readPhotos, urls);
		return { cache, read, settle, readPhotos, urls, live };
	}

	/** Let the reads that have settled run on. */
	const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

	it('reads each profile once, and holds it for a move', async () => {
		const { cache, read, settle } = setUp();
		cache.preload(['b', null, 'c', 'b']);
		expect(read.mock.calls.map(([key]) => key)).toEqual(['b', 'c']);
		expect(cache.peek('b')).toBeUndefined();

		// A move to a profile still being read waits on that read.
		const moved = cache.load('b');
		expect(read).toHaveBeenCalledTimes(2);
		settle('b');
		const answer = await moved;
		expect(answer.ok && answer.body.body).toEqual(body('b'));
		expect(cache.peek('b')?.key).toBe('b');

		// Held: neither a move nor a read ahead asks again.
		expect((await cache.load('b')).ok).toBe(true);
		cache.preload(['b']);
		expect(read).toHaveBeenCalledTimes(2);
	});

	it('reads the photos ahead with the profile, each kept as an object URL', async () => {
		const { cache, settle, readPhotos, live } = setUp();
		cache.preload(['a']);
		settle('a');
		await flush();
		const held = cache.peek('a')!;
		expect(readPhotos).toHaveBeenCalledWith('id-a');
		const listing = await held.photos.get('id-a');
		expect(listing?.images[0]).toMatchObject({ part: 'front', src: 'blob:1' });
		expect(held.ready.get('id-a')).toBe(listing);
		expect([...live]).toEqual(['blob:1']);

		// A profile with no identity photos reads none.
		cache.preload(['b']);
		settle('b', { ok: true, body: body('b', false) });
		await flush();
		expect(readPhotos).toHaveBeenCalledTimes(1);
	});

	it('keeps only the profiles it is told to, revoking the object URLs of the rest', async () => {
		const { cache, settle, live, urls } = setUp();
		cache.preload(['a', 'b', 'c']);
		for (const key of ['a', 'b', 'c']) settle(key);
		await flush();
		expect(live.size).toBe(3);

		cache.retain(['b', null, 'c']);
		expect(cache.peek('a')).toBeUndefined();
		expect(cache.peek('b')).toBeDefined();
		expect(urls.revokeObjectURL).toHaveBeenCalledWith('blob:1');
		expect([...live]).toEqual(['blob:2', 'blob:3']);
	});

	it('drops a read for a profile no longer wanted: not held, no photos read', async () => {
		const { cache, settle, readPhotos } = setUp();
		const late = cache.load('a');
		cache.retain(['b']);
		settle('a');
		const answer = await late;
		// The caller still gets the profile; the cache does not keep it, nor read its photos.
		expect(answer.ok && answer.body.body).toEqual(body('a'));
		expect(cache.peek('a')).toBeUndefined();
		expect(readPhotos).not.toHaveBeenCalled();
	});

	it('makes no object URL for photos that arrive after their profile went', async () => {
		const { cache, settle, readPhotos, urls } = setUp();
		let arrive: (set: PhotoSet) => void = () => {};
		readPhotos.mockImplementationOnce(() => new Promise((resolve) => (arrive = resolve)));
		cache.preload(['a']);
		settle('a');
		await flush();
		const photos = cache.peek('a')!.photos.get('id-a')!;
		cache.retain([]);
		arrive(SET);
		expect(await photos).toBeNull();
		expect(urls.createObjectURL).not.toHaveBeenCalled();
	});

	it('does not hold a failed read, so the next move reads again', async () => {
		const { cache, read, settle } = setUp();
		const first = cache.load('a');
		settle('a', { ok: false, status: 503, error: 'The service is unavailable.' });
		expect(await first).toMatchObject({ ok: false, status: 503 });
		expect(cache.peek('a')).toBeUndefined();
		void cache.load('a');
		expect(read).toHaveBeenCalledTimes(2);
	});

	it('stops serving everything on a new queue, revoking its URLs only at the next retain', async () => {
		const { cache, read, settle, live } = setUp();
		const held = cache.load('a');
		settle('a');
		await held;
		await flush();
		const late = cache.load('b');
		cache.clear();
		expect(cache.peek('a')).toBeUndefined();
		// The screen may still show them until it moves on.
		expect([...live]).toEqual(['blob:1']);

		// The read from before the clear still answers its caller, but is not held.
		settle('b');
		expect((await late).ok).toBe(true);
		expect(cache.peek('b')).toBeUndefined();

		// A move after the clear reads afresh, and the next retain revokes what was cleared.
		void cache.load('b');
		expect(read).toHaveBeenCalledTimes(3);
		cache.retain(['b']);
		expect(live.size).toBe(0);
	});

	it('revokes every object URL when the page leaves', async () => {
		const { cache, settle, live } = setUp();
		cache.preload(['a', 'b']);
		settle('a');
		settle('b');
		await flush();
		cache.clear();
		cache.preload(['c']);
		settle('c');
		await flush();
		expect(live.size).toBe(3);
		cache.dispose();
		expect(live.size).toBe(0);
		expect(cache.peek('c')).toBeUndefined();
	});
});
