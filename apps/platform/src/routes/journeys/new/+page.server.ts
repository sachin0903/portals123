import { error, fail, redirect } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, jsonHeaders, detail, fetchJson } from '$lib/api';
import type { FlowOut, CatalogPlatform, CatalogProduct } from '@graphene/ui/book/journeys';
import type { Actions, PageServerLoad } from './$types';
import { liveJourneyUid } from './kickoff';

/** One document a manual identity check asks for (`GET /ref/idv`). */
export type ManualIdentityDocument = {
	key: string;
	title: string;
	guidance: string;
	format_hint: string;
};

export type Client = {
	contact_id: number;
	uid: string;
	name: string | null;
	first_name: string | null;
	middle_name: string | null;
	surname: string | null;
	email: string | null;
	type: 'individual' | 'organisation';
	identity_verified: boolean;
	dob: string | null;
	retirement_age: number | null;
	marital_status: string | null;
	/** The client's own adviser, when they have one. The blank adviser picker inherits it. */
	adviser_id?: string | null;
};

export type KickoffWizard = {
	flows: FlowOut[];
	selectedFlowCode: string | null;
	/** The client this journey is being started FOR, when the start came from their record
	 *  (`?client=<uid>`). The first applicant card is filled with them once the clients load. */
	prefillClientUid: string | null;
	platforms: CatalogPlatform[] | null;
	products: CatalogProduct[];
};

export type OnboardingIllustration = {
	uid: string;
	contact_id: number;
	contact_uid: string;
	client_name: string;
	name: string | null;
	created_at: string;
	quotation_type: string | null;
	existing_fund_value: number | null;
	output_reference: string | null;
	model_name: string | null;
	document_uid: string | null;
};

export const load: PageServerLoad = async ({ locals, fetch, url }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'cases:write')) {
		error(403, 'You do not have permission to start applications.');
	}
	const canCreateIllustrationClient =
		can(locals.permissions, locals.role, 'contacts:create') &&
		can(locals.permissions, locals.role, 'illustrations:write');
	// The 403 is resolved here; the shelf is STREAMED so the wizard's shape is on screen on the
	// click. Clients, illustrations, models, IDV and advisers still load when the step that
	// draws them mounts.
	return {
		canCreateIllustrationClient,
		wizard: loadWizard(
			locals.accessToken,
			fetch,
			url.searchParams.get('flow'),
			url.searchParams.get('client')
		)
	};
};

async function loadWizard(
	accessToken: string | null,
	fetch: typeof globalThis.fetch,
	requestedFlow: string | null,
	requestedClient: string | null
): Promise<KickoffWizard> {
	const h = authHeaders(accessToken);
	// Step 1 of the wizard paints flows + the product/custodian shelf. Clients, illustrations,
	// models, IDV and advisers load when the step that draws them mounts — first paint of this
	// page must not wait on catalogues the applicant card has not opened yet.
	const [flows, platforms, products] = await Promise.all([
		fetchJson<FlowOut[]>(fetch, `${apiUrl()}/flows?status=published`, h),
		fetchJson<CatalogPlatform[]>(fetch, `${apiUrl()}/catalog/custodians`, h),
		fetchJson<CatalogProduct[]>(fetch, `${apiUrl()}/catalog/products`, h)
	]);
	const publishedFlows = flows.ok ? flows.data : [];
	const selectedFlowCode =
		publishedFlows.find((flow) => flow.code === requestedFlow)?.code ??
		publishedFlows[0]?.code ??
		null;
	return {
		flows: publishedFlows,
		selectedFlowCode,
		prefillClientUid: requestedClient?.trim() || null,
		// The firm's ENABLED custodians. `null` is reserved for a read that FAILED, because "this
		// firm has no custodian enabled" and "the list could not be loaded" are different answers
		// and an empty dropdown states neither: the page says which it is, and only a failed read
		// falls back to deriving the options from the product shelf (what the picker did before
		// /catalog/custodians existed, and all an older API can offer).
		platforms: platforms.ok ? platforms.data : null,
		products: products.ok ? products.data : []
	};
}

export const actions: Actions = {
	createIllustrationClient: async ({ request, locals, fetch }) => {
		if (
			!can(locals.permissions, locals.role, 'contacts:create') ||
			!can(locals.permissions, locals.role, 'illustrations:write')
		) {
			return fail(403, {
				action: 'createIllustrationClient',
				error: 'You cannot create this client.'
			});
		}
		const form = await request.formData();
		let party: Record<string, unknown>;
		try {
			party = JSON.parse(String(form.get('party') ?? '{}'));
		} catch {
			return fail(400, { action: 'createIllustrationClient', error: 'Malformed client details.' });
		}
		const firstName = String(party.first_name ?? '').trim();
		const middleName = String(party.middle_name ?? '').trim();
		const surname = String(party.surname ?? '').trim();
		const email = String(party.email ?? '').trim();
		const dob = String(party.dob ?? '').trim();
		if (!firstName || !surname || !email || !dob) {
			return fail(400, {
				action: 'createIllustrationClient',
				error: 'Complete the client details first.'
			});
		}
		// Retirement age and marital status are not asked here. The illustration form asks for
		// whichever the new record does not hold, and generating writes them back.
		const res = await fetch(`${apiUrl()}/firm/contacts`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({
				type: 'individual',
				first_name: firstName,
				middle_name: middleName || null,
				surname,
				name: [firstName, middleName, surname].filter(Boolean).join(' '),
				email,
				dob
			})
		});
		if (!res.ok) {
			return fail(res.status, {
				action: 'createIllustrationClient',
				error: await detail(res)
			});
		}
		const created: { id?: number; uid?: string } = await res.json();
		return {
			action: 'createIllustrationClient',
			contact_id: created.id,
			uid: created.uid
		};
	},
	// Named, not `default`: SvelteKit refuses a page that has both, and `createIllustrationClient`
	// above is named. Kickoff posted to the default action and every attempt answered 500.
	start: async ({ request, locals, fetch }) => {
		if (!can(locals.permissions, locals.role, 'cases:write')) {
			return fail(403, { error: 'You do not have permission to start applications.' });
		}
		const form = await request.formData();
		let payload: unknown;
		try {
			payload = JSON.parse(String(form.get('payload') ?? '{}'));
		} catch {
			return fail(400, { error: 'Malformed request.' });
		}
		const res = await fetch(`${apiUrl()}/applications`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify(payload)
		});
		if (res.status === 409) {
			// The one-live-journey-per-client guard names the journey in the way; the wizard offers
			// to open or stop it rather than telling the adviser to go and find it.
			const refusal = await res
				.clone()
				.json()
				.catch(() => null);
			return fail(409, {
				error: await detail(res),
				liveJourneyUid: liveJourneyUid(refusal)
			});
		}
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		const body = await res.json();
		redirect(303, `/journeys/${body.execution_uid}`);
	},

	/** Stop the live journey a refused kickoff pointed at, so the adviser can start again here. */
	stopLiveJourney: async ({ request, locals, fetch }) => {
		if (!can(locals.permissions, locals.role, 'cases:write')) {
			return fail(403, { error: 'You do not have permission to stop journeys.' });
		}
		const form = await request.formData();
		const uid = String(form.get('execution_uid') ?? '').trim();
		if (!uid) return fail(400, { error: 'No journey specified.' });
		const res = await fetch(`${apiUrl()}/applications/${uid}/cancel`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({ reason: 'Stopped from a new journey kickoff' })
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { stopped: uid };
	}
};
