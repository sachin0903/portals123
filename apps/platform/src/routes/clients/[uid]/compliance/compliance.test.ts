/**
 * The client's Compliance tab: the load (the identity block and its analysis beside what it loaded
 * before, whether AML monitoring is on, the reports to download, and the page still showing when any
 * of those reads fails) and the photo-missing wording.
 */
import { describe, expect, it, vi } from 'vitest';
import { load } from './+page.server';
import { photosMissingText } from './identity';

const locals = (permissions: string[] = ['screening:read', 'contacts:write']) =>
	({
		user: { id: 'u-adviser' },
		accessToken: 'tok',
		permissions,
		role: 'adviser'
	}) as never;

const PARAMS = { uid: 'ct_1' };

const IDENTITY = {
	checks: [
		{
			uid: 'chk_2',
			status: 'in_progress',
			document_type: 'passport',
			uploaded_by: 'Sam Lee',
			created_at: '2026-10-01T09:00:00Z',
			updated_at: '2026-10-01T09:00:00Z'
		},
		{
			uid: 'chk_1',
			status: 'superseded',
			document_type: 'driving_licence',
			uploaded_by: null,
			created_at: '2026-09-01T09:00:00Z',
			updated_at: '2026-09-02T09:00:00Z'
		}
	],
	images: [{ part: 'front', label: 'ID front', content_type: 'image/jpeg' }],
	analysis: {
		groups: [
			{
				key: 'faceAnalysis',
				label: 'Face analysis',
				items: [{ key: 'faceAnalysis.faceDetection', label: 'Face detection', outcome: 'clear' }]
			}
		],
		scores: [{ key: 'faceAnalysis.livenessScore', label: 'Liveness check score', value: 83 }],
		facts: [{ key: 'faceAnalysis.documentSideUsed', label: 'Document side used', value: 'front' }],
		links: []
	}
};

const SCREENING = {
	checks: [
		{ kind: 'identity', status: 'clear', updated_at: '2026-10-01T09:00:00Z', monitored: null },
		{ kind: 'aml', status: 'clear', updated_at: '2026-10-01T09:00:00Z', monitored: true }
	]
};

const REPORTS = {
	client: { available: true, reason: null },
	identity: { check_uid: 'chk_2', available: true, reason: null },
	aml: { check_uid: 'aml_1', available: false, reason: 'Available once the screen is clear.' },
	applications: [
		{ uid: 'ex_1', title: 'Onboarding', status: 'completed', created_at: '2026-09-01T09:00:00Z' }
	]
};

const json = (body: unknown, status = 200) =>
	new Response(JSON.stringify(body), {
		status,
		headers: { 'content-type': 'application/json' }
	});

// ── load ─────────────────────────────────────────────────────────────────────────────────────

function runLoad(
	identity: (url: string) => Promise<Response>,
	who = locals(),
	query = '',
	screening: () => Promise<Response> = async () => json({}, 404),
	reports: () => Promise<Response> = async () => json({}, 404),
	amlMatches: () => Promise<Response> = async () => json({}, 404)
) {
	const urls: string[] = [];
	const fetch = vi.fn(async (url: string | URL) => {
		urls.push(String(url));
		if (/\/identity(\?|$)/.test(String(url))) return identity(String(url));
		if (String(url).endsWith('/term-acceptances')) return json([]);
		if (String(url).endsWith('/screening')) return screening();
		if (String(url).endsWith('/compliance-reports')) return reports();
		if (String(url).endsWith('/aml-matches')) return amlMatches();
		return json({}, 404);
	}) as unknown as typeof globalThis.fetch;
	const parent = async () => ({ detail: { kyc: [{ kind: 'identity', status: 'pending' }] } });
	const url = new URL(`http://localhost/clients/ct_1/compliance${query}`);
	// eslint-disable-next-line @typescript-eslint/no-explicit-any
	const result = (load as any)({ locals: who, params: PARAMS, fetch, parent, url });
	return { result, urls };
}

describe('compliance load', () => {
	it('loads the identity block beside the status rows and the terms', async () => {
		const { result, urls } = runLoad(async () => json(IDENTITY));
		const data = await result;
		expect(urls).toContain('http://127.0.0.1:8000/crm/contacts/ct_1/identity');
		expect(data.identity).toEqual(IDENTITY);
		expect(data.kyc).toHaveLength(1);
		expect(data.acceptances).toEqual([]);
	});

	it('asks for the photos of the check the tab is showing', async () => {
		const { result, urls } = runLoad(async () => json(IDENTITY), locals(), '?check=chk_old');
		await result;
		expect(urls).toContain('http://127.0.0.1:8000/crm/contacts/ct_1/identity?check=chk_old');
	});

	it('shows the current check when the one asked for is not the client’s', async () => {
		const { result, urls } = runLoad(
			async (url) => (url.includes('?check=') ? json({ detail: 'No such' }, 404) : json(IDENTITY)),
			locals(),
			'?check=nope'
		);
		expect((await result).identity).toEqual(IDENTITY);
		expect(urls.filter((u) => u.includes('/identity'))).toEqual([
			'http://127.0.0.1:8000/crm/contacts/ct_1/identity?check=nope',
			'http://127.0.0.1:8000/crm/contacts/ct_1/identity'
		]);
	});

	it('shows the rest of the page when the identity read fails', async () => {
		const refused = await runLoad(async () => json({ detail: 'ComplyCube is down' }, 503)).result;
		expect(refused.identity).toBeNull();
		expect(refused.kyc).toHaveLength(1);

		const thrown = await runLoad(async () => {
			throw new Error('network down');
		}).result;
		expect(thrown.identity).toBeNull();
		expect(thrown.acceptances).toEqual([]);
	});

	it('passes the shown check’s analysis through', async () => {
		const data = await runLoad(async () => json(IDENTITY)).result;
		expect(data.identity.analysis).toEqual(IDENTITY.analysis);
	});

	it('says whether ongoing AML monitoring is on', async () => {
		const { result, urls } = runLoad(
			async () => json(IDENTITY),
			locals(),
			'',
			async () => json(SCREENING)
		);
		expect((await result).amlMonitored).toBe(true);
		expect(urls).toContain('http://127.0.0.1:8000/crm/contacts/ct_1/screening');

		const before = await runLoad(
			async () => json(IDENTITY),
			locals(),
			'',
			async () =>
				json({ checks: [{ kind: 'aml', status: 'pending', updated_at: null, monitored: null }] })
		).result;
		expect(before.amlMonitored).toBeNull();
	});

	it('shows the rest of the page when the screening read fails', async () => {
		const refused = await runLoad(
			async () => json(IDENTITY),
			locals(),
			'',
			async () => json({ detail: 'Down' }, 503)
		).result;
		expect(refused.amlMonitored).toBeNull();
		expect(refused.identity).toEqual(IDENTITY);

		const thrown = await runLoad(
			async () => json(IDENTITY),
			locals(),
			'',
			async () => {
				throw new Error('network down');
			}
		).result;
		expect(thrown.amlMonitored).toBeNull();
		expect(thrown.kyc).toHaveLength(1);
	});

	it('passes the reports block through', async () => {
		const { result, urls } = runLoad(
			async () => json(IDENTITY),
			locals(),
			'',
			undefined,
			async () => json(REPORTS)
		);
		expect((await result).reports).toEqual(REPORTS);
		expect(urls).toContain('http://127.0.0.1:8000/crm/contacts/ct_1/compliance-reports');
	});

	it('says whether the visitor can open an application summary', async () => {
		expect((await runLoad(async () => json(IDENTITY)).result).canReadApplications).toBe(false);
		const withCases = await runLoad(
			async () => json(IDENTITY),
			locals(['screening:read', 'cases:read'])
		).result;
		expect(withCases.canReadApplications).toBe(true);
	});

	it('shows the rest of the page when the reports read fails', async () => {
		const refused = await runLoad(
			async () => json(IDENTITY),
			locals(),
			'',
			undefined,
			async () => json({ detail: 'Down' }, 503)
		).result;
		expect(refused.reports).toBeNull();
		expect(refused.identity).toEqual(IDENTITY);

		const thrown = await runLoad(
			async () => json(IDENTITY),
			locals(),
			'',
			undefined,
			async () => {
				throw new Error('network down');
			}
		).result;
		expect(thrown.reports).toBeNull();
		expect(thrown.kyc).toHaveLength(1);
	});

	it('refuses a visitor without screening:read', async () => {
		const { result, urls } = runLoad(async () => json(IDENTITY), locals(['contacts:read']));
		await expect(result).rejects.toMatchObject({ status: 403 });
		expect(urls).toHaveLength(0);
	});
});

describe('confirmed AML matches', () => {
	const MATCHES = {
		check_uid: 'aml_1',
		allowed_at: '2026-10-02T09:00:00Z',
		matches: [
			{
				name: 'Aleksandr Matchmann',
				match_types: ['pep', 'adverse_media'],
				score: 92,
				dob: '1970-07-19',
				countries: ['GB'],
				sources: ['UK PEP list']
			}
		]
	};

	it('loads the matches Graphene let the onboarding continue past', async () => {
		const { result, urls } = runLoad(
			async () => json(IDENTITY),
			locals(),
			'',
			undefined,
			undefined,
			async () => json(MATCHES)
		);
		expect((await result).amlMatches).toEqual(MATCHES);
		expect(urls).toContain('http://127.0.0.1:8000/crm/contacts/ct_1/aml-matches');
	});

	it('shows no matches, and the rest of the page, when the read fails', async () => {
		const refused = await runLoad(async () => json(IDENTITY)).result;
		expect(refused.amlMatches).toEqual({ check_uid: null, allowed_at: null, matches: [] });
		const thrown = await runLoad(
			async () => json(IDENTITY),
			locals(),
			'',
			undefined,
			undefined,
			async () => {
				throw new Error('network down');
			}
		).result;
		expect(thrown.amlMatches.matches).toEqual([]);
		expect(thrown.identity).toEqual(IDENTITY);
	});
});

describe('photosMissingText', () => {
	it('says why there are no photos without naming the provider', () => {
		expect(photosMissingText('none')).toBe('No photos were sent for this check.');
		expect(photosMissingText('not_held')).toBe('The photos for this check are no longer on file.');
		expect(photosMissingText('not_configured')).toBe(
			'The photos for this check are no longer on file.'
		);
		expect(photosMissingText('unavailable')).toContain('Try again shortly');
		for (const reason of ['none', 'not_held', 'not_configured', 'unavailable', null] as const)
			expect(photosMissingText(reason)).not.toMatch(/complycube/i);
	});
});
