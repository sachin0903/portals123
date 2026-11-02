import { error, fail, redirect } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, jsonHeaders, detail, fetchJson } from '$lib/api';
import {
	firmWorkOwner,
	parseStepAnswers,
	stepPurpose,
	stepSubjectLine
} from '@graphene/ui/book/journeys';
import type { JourneyExecution, JourneyStep } from '@graphene/ui/book/journeys';
import type { ApiFormField } from '$lib/forms';
import type { Actions, PageServerLoad } from './$types';
import { productCurrencies } from './currency';

/** `GET /applications/{uid}/cases/{case}/steps/{key}/form` — the form behind a staff-assigned
 *  `fill_form` step, plus whatever has already been submitted. */
export interface StepForm {
	step_key: string;
	title: string | null;
	status: string;
	form_uid: string;
	name: string;
	description: string | null;
	fields: ApiFormField[];
	answers: Record<string, unknown> | null;
	context?: Record<string, unknown>;
	editable: boolean;
}

type OptionRow = { id: number; name: string; [key: string]: unknown };
type CurrencyRow = {
	code: string;
	currency: string | null;
	symbol: string | null;
	is_base: boolean;
};

interface ApiCountry {
	code: string;
	name: string;
	adjective: string | null;
	in_eea: boolean;
	identifiers: unknown;
}

function stepUrl(params: Record<string, string>, participant: string | null): string {
	const base = `${apiUrl()}/applications/${params.uid}/cases/${params.case_uid}/steps/${params.step_key}/form`;
	return participant ? `${base}?participant_contact_id=${participant}` : base;
}

interface FormLink {
	href: string;
	title: string;
	status: string;
}

type FormStep = { caseUid: string; step: JourneyStep };

function formHref(journeyUid: string, caseUid: string, step: JourneyStep): string {
	const base = `/journeys/${journeyUid}/steps/${caseUid}/${step.step_key}`;
	return step.participant_subject_id ? `${base}?participant=${step.participant_subject_id}` : base;
}

function isListedForm(step: JourneyStep): boolean {
	if (step.step_type !== 'fill_form' || step.request_item_id === null) return false;
	if (['skipped', 'cancelled'].includes(step.status)) return false;
	// Staff forms stay listed after they are fulfilled, so they can be amended.
	if (step.assignee === 'staff') return true;
	// A pending client gather form is the client's, and the adviser may complete it.
	return step.assignee === 'client' && step.mode === 'gather' && step.status !== 'fulfilled';
}

function orderedForms(execution: JourneyExecution): FormStep[] {
	return execution.cases
		.flatMap((journeyCase) => journeyCase.steps.map((step) => ({ caseUid: journeyCase.uid, step })))
		.filter(({ step }) => isListedForm(step))
		.sort(
			(a, b) =>
				(a.step.order ?? 0) - (b.step.order ?? 0) ||
				a.step.step_key.localeCompare(b.step.step_key) ||
				(a.step.participant_subject_id ?? 0) - (b.step.participant_subject_id ?? 0)
		);
}

export const load: PageServerLoad = async ({ params, url, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'cases:read')) {
		error(403, 'You do not have permission to view journeys.');
	}
	const participant = url.searchParams.get('participant');
	const canManage = can(locals.permissions, locals.role, 'cases:write');
	// Working a case and editing the FORM behind it are different permissions. The empty state
	// sends the reader to /forms/{uid}/edit, whose own loader returns `form: null` without
	// `forms:read` — so a `cases:write` holder with no forms permission was offered a button
	// that lands on "Form not found".
	const canEditForm = can(locals.permissions, locals.role, 'forms:write');
	// The 403 is resolved here; the step is STREAMED so the form's shape is on screen on the click.
	// A missing step still 404s — from inside the promise, which Streamed reports as retry.
	return {
		canManage,
		canEditForm,
		record: loadStep(params, participant, locals.accessToken, fetch)
	};
};

async function loadStep(
	params: Record<string, string>,
	participant: string | null,
	accessToken: string | null,
	fetch: typeof globalThis.fetch
) {
	const [res, executionRes, countriesRes, modelsRes, feesRes, providersRes] = await Promise.all([
		fetchJson<StepForm>(fetch, stepUrl(params, participant), authHeaders(accessToken)),
		fetchJson<JourneyExecution>(
			fetch,
			`${apiUrl()}/applications/${params.uid}`,
			authHeaders(accessToken)
		),
		fetchJson<ApiCountry[]>(fetch, `${apiUrl()}/ref/countries`, authHeaders(accessToken)),
		fetchJson<OptionRow[]>(fetch, `${apiUrl()}/catalog/models`, authHeaders(accessToken)),
		fetchJson<OptionRow[]>(fetch, `${apiUrl()}/catalog/fee-packages`, authHeaders(accessToken)),
		fetchJson<OptionRow[]>(
			fetch,
			`${apiUrl()}/catalog/transfer-providers`,
			authHeaders(accessToken)
		)
	]);
	if (!res.ok) {
		if (res.status === 404) error(404, 'That step could not be found.');
		if (res.status === 422) error(422, 'That step has no form to complete.');
		error(res.status, 'Could not load the step.');
	}
	const account =
		res.data.context?.account && typeof res.data.context.account === 'object'
			? (res.data.context.account as Record<string, unknown>)
			: {};
	const productId = typeof account.product_id === 'number' ? account.product_id : null;
	const currenciesUrl = new URL(`${apiUrl()}/ref/currencies`);
	if (productId !== null) currenciesUrl.searchParams.set('product_id', String(productId));
	const currencies = await fetchJson<CurrencyRow[]>(
		fetch,
		currenciesUrl.toString(),
		authHeaders(accessToken)
	);
	// THE CUSTODIAN, BY CODE. It read `platform_id` on both sides — a `platform.platforms` serial
	// that 0138 dropped — so both were `undefined`, `undefined == null` was true, and the whole
	// custodian narrowing passed every model and every fee package. The step offered a firm the
	// models and charges of custodians the account is not held at; the API validates the final
	// pair, so it surfaced as a refusal on submit rather than as a picker that never offered it.
	const custodian = typeof account.custodian === 'string' ? account.custodian : null;
	const productType = String(account.product_type ?? '').toUpperCase();
	const compatibleModels = (modelsRes.ok ? modelsRes.data : []).filter((model) => {
		const products = Array.isArray(model.products) ? model.products.map(String) : [];
		return (
			// `?? null` and `===`, not `==`: an absent field must not read as "matches anything",
			// which is exactly how the serial version came to narrow nothing.
			((model.provider ?? null) === null || model.provider === custodian) &&
			(products.length === 0 || products.map((value) => value.toUpperCase()).includes(productType))
		);
	});
	// Keep model-scoped packages visible while the model is being selected in this same form.
	// The API validates the final pair atomically; product/custodian incompatibilities never appear.
	const compatibleFees = (feesRes.ok ? feesRes.data : []).filter(
		(fee) =>
			((fee.provider ?? null) === null || fee.provider === custodian) &&
			(fee.product_id == null || fee.product_id === productId)
	);
	const forms = executionRes.ok ? orderedForms(executionRes.data) : [];
	const current = forms.findIndex(
		({ caseUid, step }) =>
			caseUid === params.case_uid &&
			step.step_key === params.step_key &&
			String(step.participant_subject_id ?? '') === (participant ?? '')
	);
	const linkAt = (index: number): FormLink | null => {
		const item = forms[index];
		if (!item) return null;
		const journeyCase = executionRes.ok
			? executionRes.data.cases.find((candidate) => candidate.uid === item.caseUid)
			: undefined;
		const title = journeyCase
			? `${item.step.title ?? item.step.step_key.replaceAll('_', ' ')} · ${stepSubjectLine(item.step, journeyCase)}`
			: (item.step.title ?? item.step.step_key.replaceAll('_', ' '));
		return {
			href: formHref(params.uid, item.caseUid, item.step),
			title: item.step.assignee === 'client' ? `${title} · Client's form` : title,
			status: item.step.status
		};
	};
	const currentItem = current >= 0 ? forms[current] : undefined;
	const currentCase =
		currentItem && executionRes.ok
			? executionRes.data.cases.find((candidate) => candidate.uid === currentItem.caseUid)
			: undefined;
	return {
		form: res.data,
		countries: countriesRes.ok
			? countriesRes.data.map((country) => ({
					code: country.code,
					name: country.name,
					adjective: country.adjective,
					inEea: country.in_eea,
					identifiers: country.identifiers
				}))
			: null,
		models: compatibleModels,
		feePackages: compatibleFees,
		transferProviders: providersRes.ok ? providersRes.data : [],
		currencies: productCurrencies(
			(currencies.ok ? currencies.data : []).map((currency) => ({
				...currency,
				label: currency.currency ? `${currency.currency} (${currency.code})` : currency.code
			})),
			account.allowed_currencies
		),
		journeyUid: params.uid,
		caseUid: params.case_uid,
		participant,
		formContext:
			currentItem && currentCase
				? {
						subject: stepSubjectLine(currentItem.step, currentCase),
						purpose:
							stepPurpose(currentItem.step) ??
							res.data.description ??
							'Information required for this journey',
						owner: executionRes.ok ? firmWorkOwner(executionRes.data) : null
					}
				: null,
		previousForm: current > 0 ? linkAt(current - 1) : null,
		nextForm: current >= 0 ? linkAt(current + 1) : null,
		clientGather: currentItem?.step.assignee === 'client' && currentItem.step.mode === 'gather'
	};
}

export const actions: Actions = {
	/** Complete a staff-assigned form on the client's behalf. The API routes it through the SAME
	 *  fulfilment hook the client path uses, so the CRM write-back mapping runs identically. */
	submit: async ({ params, request, locals, fetch }) => {
		if (!can(locals.permissions, locals.role, 'cases:write')) {
			return fail(403, { error: 'You do not have permission to complete journey steps.' });
		}
		const form = await request.formData();
		const continueTo = String(form.get('continue_to') ?? '').trim();
		const participant = String(form.get('participant') ?? '').trim();
		const parsed = parseStepAnswers(form.get('answers'));
		if (!parsed.ok) {
			// `absent` means the browser posted natively (no JavaScript): the questions live in a
			// component that has no native inputs, so there is nothing to submit. Say that, instead of
			// falling back to `{}` and completing the step with nothing recorded.
			return fail(422, {
				error:
					parsed.reason === 'absent'
						? 'Your answers were not submitted. Enable JavaScript and try again.'
						: 'Malformed answers.'
			});
		}
		const answers = parsed.answers;
		const res = await fetch(
			`${apiUrl()}/applications/${params.uid}/cases/${params.case_uid}/steps/${params.step_key}/form`,
			{
				method: 'POST',
				headers: jsonHeaders(locals.accessToken),
				body: JSON.stringify({
					answers,
					participant_contact_id: participant ? Number(participant) : null
				})
			}
		);
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		// The fulfilment can succeed while the step's write-back is REFUSED (a fee package the
		// account cannot take, a value the book rejects): the API answers 200 with `blocked`. Moving
		// on would tell the adviser it saved and leave the journey stuck behind them — with the
		// answers recorded, so the form then read as submitted. Stay here with the reason.
		const body = (await res.json().catch(() => null)) as {
			blocked?: boolean;
			reason?: string;
		} | null;
		if (body?.blocked) {
			return fail(422, {
				error: body.reason ?? 'That could not be saved against the account. Check your answers.'
			});
		}
		const ownFormPath = new RegExp(`^/journeys/${params.uid}/steps/[^/]+/[^/?]+(?:\\?.*)?$`);
		let destination = ownFormPath.test(continueTo) ? continueTo : '';
		if (!destination) {
			// Initial details release the remaining applicable forms, which had no request items
			// when this page loaded. Derive Next from the execution after the submission.
			const execution = await fetchJson<JourneyExecution>(
				fetch,
				`${apiUrl()}/applications/${params.uid}`,
				authHeaders(locals.accessToken)
			);
			if (execution.ok) {
				const forms = orderedForms(execution.data);
				const current = forms.findIndex(
					({ caseUid, step }) =>
						caseUid === params.case_uid &&
						step.step_key === params.step_key &&
						String(step.participant_subject_id ?? '') === participant
				);
				const next = current >= 0 ? forms[current + 1] : undefined;
				if (next) destination = formHref(params.uid, next.caseUid, next.step);
			}
		}
		redirect(303, destination || `/journeys/${params.uid}`);
	},

	/** Write the answers and leave the step pending. Required fields are not checked. */
	save: async ({ params, request, locals, fetch }) => {
		if (!can(locals.permissions, locals.role, 'cases:write')) {
			return fail(403, { error: 'You do not have permission to complete journey steps.' });
		}
		const form = await request.formData();
		const participant = String(form.get('participant') ?? '').trim();
		const parsed = parseStepAnswers(form.get('answers'));
		if (!parsed.ok) {
			return fail(422, {
				error:
					parsed.reason === 'absent'
						? 'Your answers were not submitted. Enable JavaScript and try again.'
						: 'Malformed answers.'
			});
		}
		const res = await fetch(
			`${apiUrl()}/applications/${params.uid}/cases/${params.case_uid}/steps/${params.step_key}/form/save`,
			{
				method: 'POST',
				headers: jsonHeaders(locals.accessToken),
				body: JSON.stringify({
					answers: parsed.answers,
					participant_contact_id: participant ? Number(participant) : null
				})
			}
		);
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { saved: true };
	}
};
