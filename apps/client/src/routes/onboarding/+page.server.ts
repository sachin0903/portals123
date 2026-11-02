import { loginPath } from '@graphene/auth';
import { fail, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, jsonHeaders, detail, fetchJson, publicId } from '$lib/api';
import { toFormField, type ApiFormField, type ApiRequestForm } from '$lib/forms';
import { IDENTITY_CAPTURE_KEYS, SUBMIT_FAILED, type IdentityCapture } from '$lib/identity-capture';
import { catalogueTerms, stepKey, type OutstandingTerm } from '$lib/onboarding';
import type { FormField } from '@graphene/ui';
import type { Actions, PageServerLoad } from './$types';

/** One step of a running onboarding case (API's `CaseStepOut`). Client-actionable steps
 * (`fill_form`/`accept_terms`/`upload_document`) carry the backing document-request uids so the
 * client completes them through the existing request-fulfilment endpoints; `setup_account` is a
 * staff step the client only observes. */
export type ApiCaseStep = {
	step_id: string;
	/** Journey definition order. Display sort puts pending first; the terms counter uses this. */
	order?: number;
	/** Which case this step belongs to. A step key is unique only within a case, and a client with
	 *  two journeys in flight holds the same `idv` key in both — so an action that names only the
	 *  key can land on the wrong journey. */
	case_uid?: string | null;
	step_type: 'fill_form' | 'accept_terms' | 'upload_document' | 'setup_account' | string;
	title: string | null;
	/** 'client' (the signed-in client acts) or 'staff' (their adviser acts). */
	assignee: string;
	required: boolean;
	/** 'pending' | 'fulfilled' | 'skipped' | 'cancelled'. */
	status: string;
	request_uid: string | null;
	request_item_uid: string | null;
	form_uid: string | null;
	account_id: number | null;
	/** Form definition SNAPSHOT (fill_form steps), frozen at instantiate — the client renders this
	 * frozen copy, not the live form, so later edits/archival can't change or break the step. */
	form?: { uid?: string; name?: string | null; fields?: ApiFormField[] } | null;
};

/** A running case (API's `CaseOut`). `status` is the engine-derived state
 * (draft/in_progress/<milestone>/completed/cancelled). */
export type ApiCase = {
	uid: string;
	flow_uid: string;
	flow_name: string;
	subject_contact_id: number | null;
	subject_name: string | null;
	status: string;
	initiation: string | null;
	steps: ApiCaseStep[];
};

/** A step enriched for the page: the raw step plus (for a pending `fill_form`) the resolved builder
 * form the client fills in-app. */
export type OnboardingStep = ApiCaseStep & {
	/** See `MyOnboarding.steps[].available` — mirrored onto the page's step shape. */
	available?: boolean;
	/** Unique identity for the keyed `{#each}` — see `$lib/onboarding`. `step_id` is only unique
	 *  within a case, and this list is flattened across all of the client's cases. */
	key: string;
	formName?: string | null;
	formFields?: FormField[];
	formAnswers?: Record<string, unknown>;
	/** A catalogue term no step of the journey pins — no request item; POST `/terms/{uid}/accept`. */
	catalogue_uid?: string;
	catalogue_html?: string | null;
	/** The term's published PDF, through the same-origin proxy, when it is a file not copy. */
	catalogue_file?: { url: string; content_type: string | null } | null;
};

export type OnboardingCase = Omit<ApiCase, 'steps'> & { steps: OnboardingStep[] };

/** Read-only dossier of adviser-entered detail — what the client checks before agreeing terms.
 * Co-applicants carry name/type/role only (no AML/identity status; email only on `is_you`). */
export type OnboardingReview = {
	application_uid: string;
	custody: string | null;
	firm_name?: string | null;
	applicants: {
		uid: string;
		name: string;
		type: string;
		role: string;
		email?: string | null;
		is_you: boolean;
	}[];
	accounts: {
		uid: string;
		name: string;
		product: string | null;
		product_type?: string | null;
		currency: string;
		custody: string | null;
		has_transfer: boolean;
		/** OWNERS only. A JISA's owner is the child; the adult on it is a `registered_contact`
		 *  and appears in `parties` under that role, never here. */
		owners: string[];
		/** Everyone named on the account with the role they hold. */
		parties?: { name: string; role: string }[];
		model?: string | null;
		/** Each charge in full — amount, what it is taken of, and how often. */
		fees?: {
			name: string;
			fee_type?: string | null;
			frequency?: string | null;
			charge_basis?: string | null;
			payer?: string | null;
			currency?: string | null;
			description?: string | null;
			vatable?: boolean | null;
			minimum_amount?: number | null;
			maximum_amount?: number | null;
			rule?: unknown;
		}[];
		fee_packages?: string[];
		transfer?: {
			type?: string | null;
			extent?: string | null;
			amount?: number | null;
			provider_name?: string | null;
			safeguarded_benefits?: boolean | null;
			fca_reference?: string | null;
		} | null;
		jisa_child?: { name?: string | null; dob?: string | null } | null;
		beneficiaries?: {
			first_name: string;
			surname: string;
			portion?: number | null;
		}[];
		regular_contribution?: {
			intent?: boolean;
			amount?: number | null;
			frequency?: string | null;
			expected_by?: string | null;
		} | null;
		investment_capacity?: string | null;
		dividend_treatment?: string | null;
	}[];
	you: {
		name: string;
		email: string | null;
		dob: string | null;
		nationality: string[] | null;
		tax_residency: string[] | null;
		national_identifier: string | null;
		national_identifier_type: string | null;
		mobile: string | null;
		phone: string | null;
		address: {
			line1?: string;
			line2?: string;
			city?: string;
			region?: string;
			postcode?: string;
			country?: string;
		} | null;
		bank_account: {
			name?: string;
			sort_code?: string;
			account_number?: string;
		} | null;
		flexi_access_taken?: boolean;
	};
};

/** The v2 `/applications/me` response — the client's own steps across every running journey (their
 * profile/KYC case + any account cases they're a party to), already scoped to them. `stages` is
 * the whole-journey milestone rollup (their own steps plus the staff-only tiers they wait on).
 * `review` is the dossier (null when no active journey). */
type MyOnboarding = {
	steps: {
		step_key: string;
		step_type: string;
		title: string | null;
		status: string;
		/** Which case the step belongs to. A step key is unique only within a case, and a client
		 *  with two journeys holds the same `idv` key in both — so an action naming only the key
		 *  can land on the wrong journey. Optional so an older API build still parses. */
		case_uid?: string | null;
		item_uid: string | null;
		request_uid: string | null;
		/** The catalogue term an `accept_terms` step pins, when it pins one. */
		term_uid?: string | null;
		/** Server's verdict on whether this step is the client's to do NOW — prerequisites met and
		 *  the artifact minted. `/applications/me` always sends it; the type stays optional so a
		 *  build that does not is treated as saying "not yet" rather than as unlocking every step. */
		available?: boolean;
		/** Journey definition order — used for "Document N of X", not the display sort. */
		order?: number;
	}[];
	done: number;
	total: number;
	available?: number;
	stages: {
		key: string;
		label: string;
		status: 'done' | 'current' | 'upcoming';
		summary?: string | null;
	}[];
	review?: OnboardingReview | null;
	flow_name?: string | null;
	flow_code?: string | null;
};

/** The in-portal photo capture for one identity step, or null where the API has none for it.
 *
 * A 404 is the API saying the capture is not on offer for this step, and the page then falls back
 * to the manual path — a person checks the client's documents instead. Any other failure is
 * treated the same way: manual is the path that still works, and the photos are the API's to ask
 * for. */
async function loadIdentityCapture(
	fetch: typeof globalThis.fetch,
	headers: Record<string, string>,
	step: OnboardingStep
): Promise<IdentityCapture | null> {
	const params = new URLSearchParams({ step_key: step.step_id });
	if (step.case_uid) params.set('case_uid', step.case_uid);
	try {
		const res = await fetch(`${apiUrl()}/applications/me/identity-capture?${params}`, { headers });
		if (!res.ok) return null;
		const body = (await res.json()) as IdentityCapture;
		return Array.isArray(body?.items) && typeof body.status === 'string' ? body : null;
	} catch {
		return null;
	}
}

export const load: PageServerLoad = async ({ locals, fetch, depends, parent, url }) => {
	if (!locals.user) redirect(303, loginPath(url));
	depends('app:onboarding');
	const headers = authHeaders(locals.accessToken);
	// The ACTIVE contact, not the whole login. A login linked to two clients holds two journeys,
	// and unscoped this page merged them into one checklist — the same `idv` key twice, two sets
	// of terms, and "Your details" showing whichever of the two the party ordering reached first.
	// The layout resolves and validates the choice; the API ignores an id that is not the
	// caller's, so a null here is simply the login-wide answer it always gave.
	const { activeContactId } = await parent();
	const contact = activeContactId !== null ? `?contact_id=${activeContactId}` : '';
	const [mineR, countriesR, outstandingR] = await Promise.all([
		fetchJson<MyOnboarding>(fetch, `${apiUrl()}/applications/me${contact}`, headers),
		fetchJson<Record<string, unknown>[]>(fetch, `${apiUrl()}/ref/countries`, headers),
		fetchJson<{ items: OutstandingTerm[] }>(
			fetch,
			`${apiUrl()}/terms/outstanding?required_at=onboarding`,
			headers
		)
	]);
	// A failed fetch surfaces as a retry banner rather than an empty page — a transient 401 at the
	// token-refresh boundary must not read as "no onboarding".
	const loadError = !mineR.ok;
	const mine: MyOnboarding = mineR.ok
		? mineR.data
		: { steps: [], done: 0, total: 0, stages: [], review: null };

	// The tiered v2 engine returns a flat list of the client's steps across their profile + account
	// cases; the portal presents them as one onboarding checklist. Map onto the page's step shape.
	// A SKIPPED step is one this journey decided it does not need — a condition ruled it out. It is
	// not the client's, it is not anybody's, and showing it as a task on their checklist describes
	// the flow rather than their application.
	const steps: OnboardingStep[] = mine.steps
		.filter((s) => s.status !== 'skipped')
		.map((s, i) => ({
			key: stepKey(s, i),
			step_id: s.step_key,
			case_uid: s.case_uid ?? null,
			step_type: s.step_type,
			title: s.title,
			assignee: 'client',
			required: true,
			status: s.status,
			// Fail closed: a step the API did not vouch for is not the client's to act on. Defaulting
			// to true here re-enabled every step whenever the field was missing, which is the one
			// direction the server-side gate exists to prevent.
			available: s.available ?? false,
			order: s.order,
			request_uid: s.request_uid,
			request_item_uid: s.item_uid,
			form_uid: null,
			account_id: null,
			form: null
		}));
	const catalogueSteps: OnboardingStep[] = catalogueTerms(
		outstandingR.ok ? outstandingR.data.items : [],
		mine.steps
	).map((item) => ({
		key: `catalogue:${item.uid}`,
		step_id: item.uid,
		case_uid: null,
		step_type: 'accept_terms',
		title: item.title,
		assignee: 'client',
		required: true,
		status: 'pending',
		available: true,
		request_uid: null,
		request_item_uid: null,
		form_uid: null,
		account_id: null,
		form: null,
		catalogue_uid: item.uid,
		catalogue_html: item.body_html ?? null,
		catalogue_file: item.has_file
			? { url: `/api/terms/${item.uid}/file`, content_type: item.file_content_type ?? null }
			: null
	}));
	const allSteps = [...catalogueSteps, ...steps];
	const cases: OnboardingCase[] = allSteps.length
		? [
				{
					uid: 'onboarding',
					flow_uid: '',
					flow_name: 'Onboarding',
					subject_contact_id: null,
					subject_name: null,
					status: mine.done === mine.total && mine.total > 0 ? 'completed' : 'in_progress',
					initiation: null,
					steps: allSteps
				}
			]
		: [];

	// The photo capture for each identity step still open, keyed by the step's `key`. A step with
	// no entry falls back to the manual path — a person checks the client's documents instead.
	const identityCapture: Record<string, IdentityCapture> = {};
	const identityLoads = steps
		.filter(
			(s) => s.step_type === 'verify_identity' && (s.status === 'pending' || s.status === 'waiting')
		)
		.map(async (s) => {
			const capture = await loadIdentityCapture(fetch, headers, s);
			if (capture) identityCapture[s.key] = capture;
		});

	// Pending fill_form steps render a real builder form in-app. Prefer the SNAPSHOT frozen at
	// instantiate (so a later form edit can't change/break an in-flight step); fall back to the live
	// owner-checked render endpoint for any pre-snapshot case. Small N per client.
	await Promise.all([
		...identityLoads,
		...cases.flatMap((c) =>
			c.steps
				.filter(
					(s) =>
						s.step_type === 'fill_form' &&
						s.status === 'pending' &&
						s.assignee === 'client' &&
						s.request_uid &&
						s.request_item_uid
				)
				.map(async (s) => {
					const r = await fetch(
						`${apiUrl()}/documents/requests/${s.request_uid}/items/${s.request_item_uid}/form`,
						{ headers }
					);
					if (r.ok) {
						const def: ApiRequestForm = await r.json();
						s.formAnswers = def.answers ?? {};
						if (s.form?.fields?.length) {
							s.formName = s.form.name ?? def.name;
							s.formFields = s.form.fields.map(toFormField);
						} else {
							s.formName = def.name;
							s.formFields = def.fields.map(toFormField);
						}
						return;
					}
					if (s.form?.fields?.length) {
						s.formName = s.form.name ?? undefined;
						s.formFields = s.form.fields.map(toFormField);
					}
				})
		)
	]);

	// Sparse review is fine — the page hides empty fields and never blocks terms on missing detail.
	const review = mine.review ?? null;
	return {
		cases,
		loadError,
		stages: mine.stages ?? [],
		flowName: mine.flow_name ?? null,
		flowCode: mine.flow_code ?? null,
		review,
		countries: countriesR.ok ? countriesR.data : [],
		identityCapture
	};
};

export const actions: Actions = {
	/** Fulfil an `accept_terms` (confirm-only, no file) or `upload_document` (multipart file) step by
	 * posting to the elevated per-item fulfilment endpoint; the flow engine advances the case
	 * server-side once the backing request item is done. */
	fulfilItem: async ({ request, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		const form = await request.formData();
		const reqUid = publicId(form.get('req_uid'));
		const itemUid = publicId(form.get('item_uid'));
		if (!reqUid || !itemUid) return fail(400, { error: 'No step specified.' });
		const file = form.get('file');
		const body = new FormData();
		if (file instanceof File && file.size > 0) body.append('file', file, file.name);
		const res = await fetch(`${apiUrl()}/documents/requests/${reqUid}/items/${itemUid}/fulfill`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken),
			body
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},

	/** Accept a catalogue-only Graphene term that is not backed by a request item. */
	acceptCatalogue: async ({ request, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		const form = await request.formData();
		const uid = publicId(form.get('uid'));
		if (!uid) return fail(400, { error: 'No terms specified.' });
		const res = await fetch(`${apiUrl()}/terms/${uid}/accept`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({})
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},

	/** Submit a completed `fill_form` step: POST the collected answers (JSON) to the per-item
	 * form-response endpoint, which stores them, marks the item done and advances the case. */
	submitForm: async ({ request, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		const form = await request.formData();
		const reqUid = publicId(form.get('req_uid'));
		const itemUid = publicId(form.get('item_uid'));
		if (!reqUid || !itemUid) return fail(400, { error: 'No step specified.' });
		let answers: unknown;
		try {
			answers = JSON.parse(String(form.get('answers') ?? '{}'));
		} catch {
			return fail(400, {
				error: 'We couldn’t read your answers. Please check them and try again.'
			});
		}
		const res = await fetch(
			`${apiUrl()}/documents/requests/${reqUid}/items/${itemUid}/form-response`,
			{
				method: 'POST',
				headers: jsonHeaders(locals.accessToken),
				body: JSON.stringify({ answers })
			}
		);
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},

	/** Store answers and leave the step pending. The dialog stays open. */
	saveForm: async ({ request, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		const form = await request.formData();
		const reqUid = publicId(form.get('req_uid'));
		const itemUid = publicId(form.get('item_uid'));
		if (!reqUid || !itemUid) return fail(400, { error: 'No step specified.' });
		let answers: unknown;
		try {
			answers = JSON.parse(String(form.get('answers') ?? '{}'));
		} catch {
			return fail(400, {
				error: 'We couldn’t read your answers. Please check them and try again.'
			});
		}
		const res = await fetch(
			`${apiUrl()}/documents/requests/${reqUid}/items/${itemUid}/form-response/save`,
			{
				method: 'POST',
				headers: jsonHeaders(locals.accessToken),
				body: JSON.stringify({ answers })
			}
		);
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { saved: true };
	},

	/** Resolve what an `accept_terms` step asks the client to review, so they can read it before
	 * agreeing. A declaration authored as inline HTML carries no stored file → try `/terms` first
	 * and return `{ content }`; anything with a blob (a firm's own PDF client agreement) returns
	 * its name, content type and the SAME-ORIGIN URL its bytes are proxied on.
	 *
	 * The `content` test is the fall-through, not `termsRes.ok`: a `document_type='terms'` row may
	 * hold a PDF and no HTML, and taking the terms branch on its 200 reported an empty body — the
	 * dialog then told the client there was nothing to review. */
	targetDoc: async ({ request, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		const form = await request.formData();
		const reqUid = publicId(form.get('req_uid'));
		const itemUid = publicId(form.get('item_uid'));
		if (!reqUid || !itemUid) return fail(400, { error: 'No step specified.' });
		const base = `${apiUrl()}/documents/requests/${reqUid}/items/${itemUid}`;
		const termsRes = await fetch(`${base}/terms`, { headers: authHeaders(locals.accessToken) });
		const terms = termsRes.ok
			? ((await termsRes.json()) as { name: string | null; content: string | null })
			: null;
		if (terms?.content) {
			return { ok: true, name: terms.name, content: terms.content, url: null, content_type: null };
		}
		const res = await fetch(`${base}/target`, { headers: authHeaders(locals.accessToken) });
		if (!res.ok) {
			// A terms row with neither HTML nor a stored file is a bare acknowledgement — the dialog
			// says there is no body to review and offers the tick. Only the case where we found no
			// reviewable document AT ALL is a failure.
			if (terms) {
				return { ok: true, name: terms.name, content: null, url: null, content_type: null };
			}
			return fail(res.status, { error: await detail(res) });
		}
		const doc = (await res.json()) as { name: string | null; content_type: string | null };
		return {
			ok: true,
			...doc,
			url: `/api/request-items/${reqUid}/${itemUid}/content`,
			content: null
		};
	},

	/** Name the document and send the identity photos the try needs, in one request. The API
	 *  holds them until the identity check is finished and sends them on itself. Only the three
	 *  photo fields go on, each under its item key; anything else in the form is dropped. A 503
	 *  lost nothing, and the client sends the same photos again. */
	identityCaptureSubmit: async ({ request, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		const form = await request.formData();
		const key = String(form.get('step_key') ?? '').trim();
		const caseUid = String(form.get('case_uid') ?? '').trim();
		const documentType = String(form.get('document_type') ?? '').trim();
		if (!['passport', 'driving_licence', 'national_id'].includes(documentType))
			return fail(400, { error: 'Choose the document you photographed.' });
		const body = new FormData();
		if (key) body.append('step_key', key);
		if (caseUid) body.append('case_uid', caseUid);
		body.append('document_type', documentType);
		for (const photo of IDENTITY_CAPTURE_KEYS) {
			const file = form.get(photo);
			if (file instanceof File && file.size > 0) body.append(photo, file, file.name);
		}
		// No content-type: fetch sets the multipart boundary.
		const res = await fetch(`${apiUrl()}/applications/me/identity-capture/submit`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken),
			body
		});
		if (res.status === 503) return fail(503, { error: SUBMIT_FAILED });
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},

	/** A one-time link for taking the photos on a phone. The code is in the URL, once. */
	identityPhone: async ({ request, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		const form = await request.formData();
		const key = String(form.get('step_key') ?? '').trim();
		const caseUid = String(form.get('case_uid') ?? '').trim();
		const res = await fetch(`${apiUrl()}/applications/me/identity-capture/phone`, {
			method: 'POST',
			headers: { ...authHeaders(locals.accessToken), 'content-type': 'application/json' },
			body: JSON.stringify({
				...(key ? { step_key: key } : {}),
				...(caseUid ? { case_uid: caseUid } : {})
			})
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		const body = (await res.json()) as { url?: string; qr_svg?: string };
		if (!body.url || !body.qr_svg) return fail(502, { error: 'The phone link could not be made.' });
		return { url: body.url, qr_svg: body.qr_svg };
	},

	/** Withdraw the phone link. The photos will be taken on this device. */
	identityPhoneCancel: async ({ request, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		const form = await request.formData();
		const key = String(form.get('step_key') ?? '').trim();
		const caseUid = String(form.get('case_uid') ?? '').trim();
		const res = await fetch(`${apiUrl()}/applications/me/identity-capture/phone/cancel`, {
			method: 'POST',
			headers: { ...authHeaders(locals.accessToken), 'content-type': 'application/json' },
			body: JSON.stringify({
				...(key ? { step_key: key } : {}),
				...(caseUid ? { case_uid: caseUid } : {})
			})
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},

	/** The client chooses to have a PERSON check their documents instead of doing it digitally.
	 *
	 * Whether an identity check is automatic or manual is theirs to decide — they hold the document
	 * and know whether they can photograph it. The API records the choice and moves the step to
	 * staff; it decides no outcome, and there is no way back from here (a client who changes their
	 * mind asks their adviser, who can retry the step). */
	identityManual: async ({ request, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		const form = await request.formData();
		const key = String(form.get('step_key') ?? '').trim();
		const caseUid = String(form.get('case_uid') ?? '').trim();
		const res = await fetch(`${apiUrl()}/applications/me/identity-manual`, {
			method: 'POST',
			headers: { ...authHeaders(locals.accessToken), 'content-type': 'application/json' },
			// Both, so the choice cannot land on another journey's identity step: the key alone is
			// `idv` in every journey the seeded flow starts.
			body: JSON.stringify({
				...(key ? { step_key: key } : {}),
				...(caseUid ? { case_uid: caseUid } : {})
			})
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		const body = (await res.json()) as { documents?: unknown };
		return { ok: true, documents: body.documents ?? [] };
	},

	/** What a manual identity check has asked this client for, and whether each has landed.
	 *
	 * A separate read from the choice that raised them, because the client closes the dialog and
	 * comes back — the two documents have to survive that, and the only other record of what was
	 * asked for was a response nobody kept. */
	identityDocuments: async ({ request, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		const form = await request.formData();
		const params = new URLSearchParams();
		const key = String(form.get('step_key') ?? '').trim();
		const caseUid = String(form.get('case_uid') ?? '').trim();
		if (key) params.set('step_key', key);
		if (caseUid) params.set('case_uid', caseUid);
		const query = params.size ? `?${params}` : '';
		const res = await fetch(`${apiUrl()}/applications/me/identity-documents${query}`, {
			headers: authHeaders(locals.accessToken)
		});
		// 404 is a real answer here — this client has no identity step — and the page treats it as
		// "nothing has been asked for", not as a failure.
		if (res.status === 404) return { ok: true, chosen: false, documents: [] };
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		const body = (await res.json()) as { chosen?: boolean; documents?: unknown };
		return { ok: true, chosen: body.chosen === true, documents: body.documents ?? [] };
	}
};
