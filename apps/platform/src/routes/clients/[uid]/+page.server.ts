import { error, fail, redirect } from '@sveltejs/kit';
import {
	apiUrl,
	authHeaders,
	jsonHeaders,
	detail,
	fetchJson,
	type ContactDetail,
	type Instruction,
	type InstructionKind
} from '$lib/api';
import { justificationKeys, type ReviewPolicy, type ReviewRow } from '@graphene/ui/book/reviews';
// The money classification behind the withdrawal gate lives with the shared book components
// (@graphene/ui book/clients) so the admin portal's loaders derive the SAME headroom figures.
import {
	INSTRUCTION_LEG,
	accountFunding,
	applyHoldingsSummary,
	basisByAccount,
	basisFromSummaryLegs,
	committedByAccount,
	durationValue,
	withdrawalDebit,
	readBankDetails,
	validateBank,
	CLEARED_BANK,
	readClientTeamRole,
	takesFatcaDocument,
	type ClientTeamRole,
	formatGBP as money,
	type HoldingsSummary,
	type RawHolding,
	type Transaction
} from '@graphene/ui';
import { clientTabTarget } from '@graphene/ui/nav';
import type { ClientJourneySummary } from '@graphene/ui';
import type { FlowOut } from '@graphene/ui/book/journeys';
import { startJourneyLinks } from '$lib/start-journey';
import { loadClientBookPin } from '$lib/classification';
import { loadClientGroups } from '$lib/client-groups';
import { can } from '@graphene/api-client/permissions';
import type { Actions, PageServerLoad } from './$types';

/** Outstanding rows for the Action required strip — not a page of settled history. */
const ACTION_STRIP_LIMIT = 20;

/**
 * The record's landing view: the client's own dashboard.
 *
 * The layout supplies only the shell. This load fetches the fat record (bank, FATCA, notes) plus
 * what the landing view states: a holdings SUMMARY for the donut/cash cards, the outstanding
 * action-strip rows and instructions. Members, models and countries are NOT read
 * here — the pickers fetch them when a drawer or popout opens (`/pickers/…`).
 */
export const load: PageServerLoad = async ({ locals, params, url, fetch, parent }) => {
	if (!locals.user) redirect(303, '/login');

	// The old `?tab=` deep links keep working: bookmarks and anything still linking `?tab=documents`
	// (the dashboard's rail did) land on the section, not on a page that ignores them. A tab that
	// became a route redirects to it; a tab that folded into THIS page is left alone here and brought
	// into view client-side — see the `afterNavigate` hook in +page.svelte.
	const tab = url.searchParams.get('tab');
	if (tab !== null) {
		const target = clientTabTarget(params.uid, tab);
		if ('redirect' in target) redirect(307, target.redirect);
	}

	const { detail: shell, canManage, canReadInstructions, canReadJourneys } = await parent();
	const headers = authHeaders(locals.accessToken);
	const contactId = shell.contact.id;
	const canStartJourneys = can(locals.permissions, locals.role, 'cases:write');
	const canListBooks = canManage || can(locals.permissions, locals.role, 'contacts:read');

	// The shell (and the tab redirect) are resolved here; the fat record is STREAMED so the
	// overview's shape is on screen the moment the layout has named the client.
	return {
		record: loadRecord({
			uid: params.uid,
			contactId,
			canReadInstructions,
			canReadJourneys,
			canStartJourneys,
			canListBooks,
			headers,
			fetch
		})
	};
};

async function loadRecord({
	uid,
	contactId,
	canReadInstructions,
	canReadJourneys,
	canStartJourneys = false,
	canListBooks,
	headers,
	fetch
}: {
	uid: string;
	contactId: number;
	canReadInstructions: boolean;
	canReadJourneys: boolean;
	canStartJourneys?: boolean;
	canListBooks: boolean;
	headers: HeadersInit;
	fetch: typeof globalThis.fetch;
}) {
	// Four independent reads, in one round trip with the fat record:
	//  · the fat contact — bank, FATCA, notes, the person fields the header and cards state.
	//  · holdings SUMMARY (not the unpaged list) — the allocation ring and the cash figure.
	//  · this client's outstanding transactions for the Action required strip
	//    (`needs_action=true`, a small limit — not a page of settled rows).
	//  · the money-movement instructions — instructions:read.
	// A read the caller isn't entitled to is not made at all.
	// Members / models / countries wait for the picker that needs them.
	// The client-book pin rides in the same round trip — it was the layout's second awaited
	// stretch, and the header's book badge + pin menu are this section's to draw.
	const [
		fatRes,
		summaryRes,
		txRes,
		insRes,
		pendingRes,
		policiesRes,
		journeysRes,
		flowsRes,
		pin,
		membership
	] = await Promise.all([
		fetch(`${apiUrl()}/crm/contacts/${uid}`, { headers }),
		fetch(`${apiUrl()}/crm/holdings/summary?contact_id=${contactId}`, { headers }),
		fetch(
			`${apiUrl()}/crm/transactions?contact_id=${contactId}&needs_action=true&limit=${ACTION_STRIP_LIMIT}`,
			{ headers }
		),
		canReadInstructions
			? fetch(`${apiUrl()}/firm/contacts/${uid}/instructions`, { headers })
			: null,
		fetch(`${apiUrl()}/reviews/pending?contact_uid=${uid}`, { headers }),
		fetch(`${apiUrl()}/reviews/policies`, { headers }),
		canReadJourneys ? fetch(`${apiUrl()}/crm/contacts/${uid}/journeys`, { headers }) : null,
		// The flows the header's Actions menu may start for this client — only for a reader
		// who can start one.
		canStartJourneys ? fetch(`${apiUrl()}/flows?status=published`, { headers }) : null,
		loadClientBookPin(uid, headers, fetch, { canListBooks }),
		loadClientGroups(uid, headers, fetch, { canList: canListBooks })
	]);
	if (fatRes.status === 404) error(404, 'Client not found');
	if (!fatRes.ok) error(fatRes.status, 'Could not load client');
	const detailData: ContactDetail = await fatRes.json();

	const summary: HoldingsSummary | null = summaryRes.ok ? await summaryRes.json() : null;
	const clientAum = detailData.accounts.reduce((s, a) => s + (a.value ?? 0), 0);
	const { allocations, cash, bookCost, unrealisedGain, accountLegs } = applyHoldingsSummary(
		summary,
		clientAum
	);

	const instructions: Instruction[] = insRes?.ok ? await insRes.json() : [];

	// The Action required strip's rows. `needsAction` (in the shared component) decides which of
	// them are outstanding, so the filtering rule stays in one place; this is only the read.
	const pendingTransactions: Transaction[] = txRes.ok ? await txRes.json() : [];

	// What each account can actually fund, on the SAME basis the gate uses (raw provider legs, net
	// of pending claims) — deliberately NOT the scaled `cash` figure above, which is anchored to
	// AUM for the donut. The money dialogs state this per account, so a refusal is something the
	// adviser saw coming rather than a number appearing from nowhere. The summary's per-account
	// legs feed this when the API sends them; without them the dialogs have no first-paint basis
	// (the create-instruction action still re-reads the full holdings list).
	const funding = accountFunding(
		basisFromSummaryLegs(accountLegs),
		committedByAccount(instructions)
	);

	return {
		detail: detailData,
		allocations,
		cash,
		bookCost,
		unrealisedGain,
		holdingsError: !summaryRes.ok,
		instructions,
		funding,
		bankAccount: readBankDetails(detailData.contact),
		pendingReviews: pendingRes.ok ? ((await pendingRes.json()) as ReviewRow[]) : [],
		justificationRequired: policiesRes.ok
			? justificationKeys((await policiesRes.json()) as ReviewPolicy[])
			: [],
		// Deliberately NOT accompanied by a `transactionsError`: the strip is rendered inside the
		// SHARED record component, which has no slot for a second banner, and a failed read here
		// hides an action prompt rather than misstating a figure. The account record — where a
		// blank Transactions band would read as "no movements" — does surface its own.
		pendingTransactions,
		journeys: journeysRes?.ok
			? (((await journeysRes.json()) as { journeys?: ClientJourneySummary[] }).journeys ?? [])
			: [],
		newJourneys: startJourneyLinks(flowsRes?.ok ? ((await flowsRes.json()) as FlowOut[]) : [], uid),
		...pin,
		...membership
	};
}

/** The record hosts three permission families (docs/api.md "six splits" plus the Morgan Lloyd
 *  sync): editing the client themselves is `contacts:write`; moving their money — instructions,
 *  bank details, mandates — is `instructions:write`; pushing them to Morgan Lloyd is
 *  `contacts:sync_morgan_lloyd`. Each action guards on exactly the slug the API will demand. */
function guard(
	locals: App.Locals,
	uid: string,
	slug: 'contacts:write' | 'instructions:write' | 'contacts:sync_morgan_lloyd'
): void {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, slug)) redirect(303, `/clients/${uid}`);
}

/** Fields the details form is allowed to blank out. `null` here means "clear it". */
/** A save the firm holds for a second person: nothing is written yet, so the toast must not say
 *  it was. The API answers 202 naming the case, and says when this edit JOINED a case already
 *  waiting (`amended`) rather than opening another. */
function held(
	action: string,
	body: { review_uid?: string; case_no?: number | null; amended?: boolean } | null
) {
	return {
		action,
		pending_review: true,
		case_no: body?.case_no ?? null,
		amended: body?.amended === true
	};
}

const CLEARABLE = [
	'title',
	'first_name',
	'surname',
	'email',
	'mobile',
	'phone',
	'address'
] as const;

/** Whether an address-book email is well-formed enough to send to. Deliberately permissive — it
 *  rejects the typos a person makes, not the exotic-but-legal RFC cases. */
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;

/** Did the API's response actually clear this field? */
const isCleared = (value: unknown): boolean => value === null || value === undefined;

export const actions: Actions = {
	updateClient: async ({ request, locals, params, fetch }) => {
		guard(locals, params.uid, 'contacts:write');
		const data = await request.formData();
		const str = (k: string) => String(data.get(k) ?? '').trim();
		// Build the address object from its parts; send null when every part is blank so an
		// empty form clears it rather than storing {}.
		const addr = {
			line1: str('addr_line1') || null,
			line2: str('addr_line2') || null,
			city: str('addr_city') || null,
			postcode: str('addr_postcode') || null,
			country: str('addr_country') || null
		};
		const hasAddress = Object.values(addr).some((v) => v);
		const email = str('email');
		if (email && !EMAIL_RE.test(email))
			return fail(400, {
				action: 'updateClient',
				field: 'email',
				error: 'That doesn’t look like an email address.'
			});
		const isOrg = str('type') === 'organisation';
		const first = str('first_name');
		const surname = str('surname');

		// EVERY FIELD GOES THROUGH `put`, and that is what lets ONE action serve the four
		// independently-editable cards (contact, personal, pension, employer) plus source of
		// wealth on Compliance. They share this action because the API's PATCH already draws the
		// distinction that makes it safe — a key present as null CLEARS, a key absent is LEFT
		// ALONE — so a form that carries only its own fields updates only its own fields.
		const body: Record<string, unknown> = {};
		const put = (key: string, value: unknown) => {
			if (data.has(key)) Object.assign(body, { [key]: value });
		};
		// ── Employer bank account (0271) ─────────────────────────────────────────────────────
		// The same shape and the same browser-side check as the client's own bank details; the
		// API normalises it again and asks the sort-code directory. Every field blank clears it.
		if (data.has('employer_bank_sort_code') || data.has('employer_bank_account_number')) {
			const checked = validateBank({
				scheme: 'bacs',
				name: str('employer_bank_name'),
				sort_code: str('employer_bank_sort_code'),
				account_number: str('employer_bank_account_number'),
				iban: '',
				bic: '',
				method: 'bacs'
			});
			if (!checked.ok)
				return fail(400, { action: 'updateClient', field: checked.field, error: checked.error });
			body.employer_bank_account = checked.value
				? {
						name: checked.value.name,
						sort_code: checked.value.sort_code,
						account_number: checked.value.account_number
					}
				: null;
		}
		/** The signed W-9 or W-8 from the Compliance card, uploaded once the PATCH has saved. */
		let fatcaDocument: File | null = null;
		put('service_type', str('service_type') || null);
		// An organisation's kind (`trust`/`company`/`charity`). Only the organisation branch of
		// the form renders the control, so an individual posts nothing and keeps its NULL —
		// which is what `crm.contacts`' own CHECK requires of it.
		if (isOrg) put('sub_type', str('sub_type') || null);

		// ── Contact details ────────────────────────────────────────────────────────────────────
		put('title', str('title') || null);
		put('first_name', isOrg ? null : first || null);
		put('surname', isOrg ? null : surname || null);
		// An organisation's name is its own field. A person's is never sent: the database owns it,
		// first, middle and surname whoever writes the parts (0239), and would replace a sent one
		// anyway. Sending it would also rewrite an older spelling on a save of something unrelated.
		if (isOrg) put('name', str('name') || null);
		put('email', email || null);
		put('mobile', str('mobile') || null);
		put('phone', str('phone') || null);
		// The address parts arrive as five fields and land as one jsonb column, so the marker is the
		// line-1 field rather than the object: `data.has('address')` would never be true.
		if (data.has('addr_line1')) body.address = hasAddress ? addr : null;

		if (!isOrg) {
			// ── Personal details ───────────────────────────────────────────────────────────────
			put('gender', str('gender') || null);
			put('dob', str('dob') || null);
			// ISO CODES from `ref.countries` (0050). Both values are repeated form entries and
			// array columns; a single `get` would silently keep only the first selection.
			if (data.has('nationality')) {
				body.nationality = data
					.getAll('nationality')
					.map(String)
					.map((code) => code.trim())
					.filter(Boolean);
			}
			if (data.has('tax_residency')) {
				body.tax_residency = data
					.getAll('tax_residency')
					.map(String)
					.map((code) => code.trim())
					.filter(Boolean);
			}
			put('national_identifier', str('national_identifier') || null);
			put('national_identifier_type', str('national_identifier_type') || null);
			put('occupation', str('occupation') || null);
			put('occupation_detail', str('occupation_detail') || null);
			// An unanswered select posts an empty value and must not clear a declaration.
			// The hidden value is what the record already holds, so a blank answer is still
			// checked against it when the W-9 or W-8 changes.
			const postedUs = data.has('us_person') ? str('us_person') : '';
			if (postedUs === 'Yes' || postedUs === 'No') body.us_person = postedUs === 'Yes';
			const wasUs = str('us_person_was');
			const usPerson =
				postedUs === 'Yes' || postedUs === 'No'
					? postedUs === 'Yes'
					: wasUs === 'Yes'
						? true
						: wasUs === 'No'
							? false
							: null;
			// The tax form is its own answer. Blank clears it. The date moves only when the answer
			// changes, so a W-8 saved again with other details keeps the date it was given.
			if (data.has('fatca_status')) {
				const form = str('fatca_status');
				if (form === 'w8' && usPerson === true)
					return fail(400, {
						action: 'updateClient',
						field: 'fatca_status',
						error: 'A US person has a W-9, not a W-8.'
					});
				if (form === 'w9' && usPerson === false)
					return fail(400, {
						action: 'updateClient',
						field: 'fatca_status',
						error: 'A W-9 is only for a US person.'
					});
				if (!data.has('fatca_status_was') || form !== str('fatca_status_was')) {
					body.fatca_status = form || null;
					body.fatca_certified_at = form ? new Date().toISOString().slice(0, 10) : null;
				}
				// The file belongs to the answer this save leaves on the record, so a blank or another
				// code sends none. A browser posts an empty file when nothing was chosen.
				const file = data.get('fatca_document');
				if (takesFatcaDocument(form) && file instanceof File && file.size > 0) fatcaDocument = file;
			}

			// ── Pension / SIPP details ─────────────────────────────────────────────────────────
			put('marital_status', str('marital_status') || null);
			put('employment_status', str('employment_status') || null);
			put('tax_relief', str('tax_relief') || null);
			if (data.has('retirement_age')) {
				const digits = str('retirement_age').replace(/\D/g, '');
				body.retirement_age = digits ? Number(digits) : null;
			}
			if (data.has('employer_contributions_present'))
				body.employer_contributions = data.get('employer_contributions') !== null;
			if (data.has('flexi_access_taken_present')) {
				const taken = data.get('flexi_access_taken') !== null;
				body.flexi_access_taken = taken;
				// The date only means anything while the flag is true, so turning the flag off clears
				// it. Leaving a date behind would let a later re-tick inherit a date nobody restated.
				body.flexi_access_date = taken ? str('flexi_access_date') || null : null;
			}

			// ── Employer details ───────────────────────────────────────────────────────────────
			put('employer_name', str('employer_name') || null);
			put('employer_registration_number', str('employer_registration_number') || null);
			if (data.has('employer_addr_line1')) {
				const employerAddr = {
					line1: str('employer_addr_line1'),
					line2: str('employer_addr_line2'),
					city: str('employer_addr_city'),
					postcode: str('employer_addr_postcode'),
					country: str('employer_addr_country')
				};
				body.employer_address = Object.values(employerAddr).some((v) => v) ? employerAddr : null;
			}

			put('country_of_birth', str('country_of_birth') || null);
			put('town_of_birth', str('town_of_birth') || null);
			put('source_of_funds', str('source_of_funds') || null);
			put('source_of_funds_detail', str('source_of_funds_detail') || null);
			// EMPLOYMENT (0126 + 0051), on the Pension section — so for a GIA-only client the fields
			// are absent from every form and the keys are never sent, which is the difference between
			// "not asked" and "cleared".
			put('employer_name', str('employer_name') || null);
			const earnings = Number(str('relevant_uk_earnings').replace(/[^0-9.-]/g, ''));
			put('relevant_uk_earnings', Number.isFinite(earnings) && earnings > 0 ? earnings : null);
			// Source of wealth is Morgan Lloyd's own twelve-member list, so the stored value
			// matches `_SOURCE_OF_WEALTH`'s keys exactly and the vendor's structured field gets
			// filled — a typed "Investments" filled our column and left theirs empty. `detail` is
			// the words behind Other (`cam_sourceofwealthstring`). Individuals only: the
			// Compliance card's person-facts are gated the same way.
			put('source_of_wealth', str('source_of_wealth') || null);
			put('source_of_wealth_detail', str('source_of_wealth_detail') || null);
		}
		const res = await fetch(`${apiUrl()}/firm/contacts/${params.uid}`, {
			method: 'PATCH',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify(body)
		});
		if (!res.ok) return fail(res.status, { action: 'updateClient', error: await detail(res) });
		// Held for a second person: nothing is saved yet, so the toast must not say it was. The
		// body names the case, and whether this edit joined a case already waiting.
		if (res.status === 202) return held('updateClient', await res.json());
		// The API builds its SET clause from non-null fields only, so a requested clear can be
		// silently dropped. Compare what came back and tell the truth rather than toasting
		// "Client updated" over a no-op. (When app/contacts.py learns to NULL an explicit null,
		// this list is always empty and the warning never fires.)
		const saved: Record<string, unknown> = await res.json();
		if (fatcaDocument) {
			const upload = new FormData();
			upload.append('file', fatcaDocument, fatcaDocument.name);
			const uploaded = await fetch(`${apiUrl()}/crm/contacts/${params.uid}/fatca-document`, {
				method: 'POST',
				// No Content-Type — let fetch set the multipart boundary.
				headers: authHeaders(locals.accessToken),
				body: upload
			});
			// The PATCH has already saved, so the message says so. The form stays open with the file.
			if (!uploaded.ok)
				return fail(uploaded.status, {
					action: 'updateClient',
					field: 'fatca_document',
					error: `The answer is saved, but the form did not upload: ${await detail(uploaded)}`
				});
		}
		const notCleared = CLEARABLE.filter((k) => body[k] === null && !isCleared(saved[k]));
		return notCleared.length
			? {
					action: 'updateClient',
					ok: true,
					warning: `Saved, but these couldn’t be cleared: ${notCleared.join(', ')}.`
				}
			: { action: 'updateClient', ok: true };
	},

	/** Set or clear the client's bank details.
	 *
	 *  Its own action rather than a field on `updateClient`: bank data has its own endpoint
	 *  (`PUT /crm/contacts/{id}/bank-account`), its own validation, and its own audit row — and one
	 *  form's failure must not half-save the other's fields. Validated here as well as at the API so
	 *  the adviser gets the field message without a round trip; the API stays the authority.
	 *
	 *  Nothing in this action logs, and no message it returns carries a digit. */
	/** Record or clear a client's vulnerability (FCA FG21/1 drivers).
	 *
	 *  Its own action rather than a field on the details form: it is special-category-adjacent, it
	 *  drives the banner on the profile, and a details-form validation failure must not be able to
	 *  half-save it. The API owns the vocabulary — this only shapes the form post into the JSON
	 *  body, so a driver cannot be spelled one way here and another way there. */
	/** Push this client (and their pension accounts' schemes) into Morgan Lloyd.
	 *
	 *  Idempotent server-side: the API skips anything `platform.links` already records, so a second
	 *  click cannot duplicate a person in a real back-office system. The per-step report comes back
	 *  200 even when one account failed, so the toast has to read the body rather than the status —
	 *  "synced" when nothing failed, a warning naming the count when something did. */
	/** Who looks after this client, and who can sign in as them — the People page's two sections,
	 *  now the two popouts the record's gear and key open. Each is `contacts:write` (the same guard
	 *  the page used) and each posts to its own endpoint.
	 *
	 *  `role` is one of the four team roles (`CLIENT_TEAM_ROLES`); blank means `adviser`, the API's
	 *  own default. Anything else is refused here rather than sent for a 422. The API owns the rest
	 *  (who may hold which role, the old adviser staying on as Support) and its `detail` is what
	 *  the toast shows. */
	assignAdviser: async ({ request, locals, params, fetch }) => {
		guard(locals, params.uid, 'contacts:write');
		const data = await request.formData();
		const adviserId = String(data.get('adviser_id') ?? '');
		if (!adviserId) return fail(400, { action: 'assignAdviser', error: 'Pick a person.' });
		const role: ClientTeamRole | null = readClientTeamRole(data.get('role'));
		if (role === null) return fail(400, { action: 'assignAdviser', error: 'Pick a role.' });
		const res = await fetch(`${apiUrl()}/firm/contacts/${params.uid}/advisers`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({ adviser_id: adviserId, role })
		});
		if (!res.ok) return fail(res.status, { action: 'assignAdviser', error: await detail(res) });
		return { action: 'assignAdviser', ok: true };
	},

	unassignAdviser: async ({ request, locals, params, fetch }) => {
		guard(locals, params.uid, 'contacts:write');
		const data = await request.formData();
		const adviserId = String(data.get('adviser_id') ?? '');
		const res = await fetch(`${apiUrl()}/firm/contacts/${params.uid}/advisers/${adviserId}`, {
			method: 'DELETE',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { action: 'unassignAdviser', error: await detail(res) });
		return { action: 'unassignAdviser', ok: true };
	},

	linkUser: async ({ request, locals, params, fetch }) => {
		guard(locals, params.uid, 'contacts:write');
		const data = await request.formData();
		const userId = String(data.get('user_id') ?? '');
		if (!userId) return fail(400, { action: 'linkUser', error: 'Pick a person.' });
		const res = await fetch(`${apiUrl()}/firm/contacts/${params.uid}/users`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({ user_id: userId, capacity: String(data.get('capacity') ?? 'self') })
		});
		if (!res.ok) return fail(res.status, { action: 'linkUser', error: await detail(res) });
		return { action: 'linkUser', ok: true };
	},

	unlinkUser: async ({ request, locals, params, fetch }) => {
		guard(locals, params.uid, 'contacts:write');
		const data = await request.formData();
		const userId = String(data.get('user_id') ?? '');
		const res = await fetch(`${apiUrl()}/firm/contacts/${params.uid}/users/${userId}`, {
			method: 'DELETE',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { action: 'unlinkUser', error: await detail(res) });
		return { action: 'unlinkUser', ok: true };
	},

	syncToMorganLloyd: async ({ locals, params, fetch }) => {
		guard(locals, params.uid, 'contacts:sync_morgan_lloyd');
		const res = await fetch(`${apiUrl()}/crm/contacts/${params.uid}/sync-to-morgan-lloyd`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { action: 'syncToMorganLloyd', error: await detail(res) });
		const report = (await res.json()) as {
			contact: { status: string };
			accounts: { status: string }[];
			warnings: string[];
		};
		const failed = report.accounts.filter((a) => a.status === 'failed').length;
		const created =
			(report.contact.status === 'created' ? 1 : 0) +
			report.accounts.filter((a) => a.status === 'created').length;
		return {
			action: 'syncToMorganLloyd',
			ok: true,
			created,
			failed,
			// A warning wins the toast: "nothing to do" and "created but unusable" both need saying.
			warning:
				failed > 0
					? `${failed} account${failed === 1 ? '' : 's'} could not be set up in Morgan Lloyd.`
					: (report.warnings[0] ?? undefined)
		};
	},

	setVulnerability: async ({ request, locals, params, fetch }) => {
		guard(locals, params.uid, 'contacts:write');
		const data = await request.formData();
		// An unchecked checkbox posts nothing at all, so absence is the "false" here.
		const isVulnerable = data.get('is_vulnerable') !== null;
		const res = await fetch(`${apiUrl()}/crm/contacts/${params.uid}/vulnerability`, {
			method: 'PUT',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({
				is_vulnerable: isVulnerable,
				reasons: data.getAll('reasons').map(String),
				// The FG21/1 characteristics (0116). `getAll` because MultiSelect emits one hidden
				// input per selected value under the same name, and an empty selection posts nothing
				// — which the API reads as "none recorded", the intended meaning.
				characteristics: data.getAll('characteristics').map(String),
				// Absent/'' means "not established" — a third state, not false. Shared with the
				// control that posts it (`durationChoice`), so the two cannot disagree.
				is_temporary: durationValue(data.get('is_temporary')?.toString()),
				notes: String(data.get('notes') ?? '').trim() || null
			})
		});
		if (!res.ok) return fail(res.status, { action: 'setVulnerability', error: await detail(res) });
		if (res.status === 202) return held('setVulnerability', await res.json());
		return { action: 'setVulnerability', ok: true, cleared: !isVulnerable };
	},

	updateBankAccount: async ({ request, locals, params, fetch }) => {
		guard(locals, params.uid, 'instructions:write');
		const data = await request.formData();
		const str = (k: string) => String(data.get(k) ?? '');
		const scheme = str('bank_scheme') === 'iban' ? 'iban' : 'bacs';
		const checked = validateBank({
			scheme,
			name: str('bank_name'),
			sort_code: str('bank_sort_code'),
			account_number: str('bank_account_number'),
			iban: str('bank_iban'),
			bic: str('bank_bic'),
			method: str('bank_method') === 'faster_payment' ? 'faster_payment' : 'bacs'
		});
		if (!checked.ok)
			return fail(400, {
				action: 'updateBankAccount',
				field: checked.field,
				error: checked.error
			});
		const justification = str('justification').trim() || null;
		const res = await fetch(`${apiUrl()}/crm/contacts/${params.uid}/bank-account`, {
			method: 'PUT',
			headers: jsonHeaders(locals.accessToken),
			// An empty form clears the details: send every field explicitly null rather than omitting
			// them, so "cleared" can't be read as "unchanged". Justification is only on the wire
			// when the firm has four-eye on and the form asked for one.
			body: JSON.stringify({
				...(checked.value ?? CLEARED_BANK),
				...(justification ? { justification } : {})
			})
		});
		if (res.status === 202) return held('updateBankAccount', await res.json());
		if (!res.ok) return fail(res.status, { action: 'updateBankAccount', error: await detail(res) });
		return { action: 'updateBankAccount', ok: true, cleared: checked.value === null };
	},

	/** Associate a model with one of the client's accounts, change it, or clear it.
	 *
	 *  The mandate is what makes an account discretionary, so this is also what makes model top-up
	 *  and model withdrawal available on it. `platform.accounts.model_id` was read in four places and
	 *  written by nothing, which left `GET /catalog/models` with no consumer at all.
	 *
	 *  The API refuses (409) while the account has pending model instructions — that message is
	 *  passed through rather than swallowed: a live claim is a reason not to re-point a mandate. */
	setAccountModel: async ({ request, locals, params, fetch }) => {
		guard(locals, params.uid, 'instructions:write');
		const data = await request.formData();
		// The account's uid, not its serial: `/crm/accounts/{account_uid}/model` resolves the id
		// itself. `model_id` stays numeric — it is a BODY field, and a body is not a URL.
		const accountUid = String(data.get('account_uid') ?? '').trim();
		if (!accountUid)
			return fail(400, { action: 'setAccountModel', error: 'No account specified.' });
		const raw = String(data.get('model_id') ?? '').trim();
		const modelId = raw ? Number(raw) : null;
		if (modelId !== null && !Number.isInteger(modelId))
			return fail(400, { action: 'setAccountModel', error: 'Choose a model.' });
		const justification = String(data.get('justification') ?? '').trim() || null;
		const res = await fetch(`${apiUrl()}/crm/accounts/${accountUid}/model`, {
			method: 'PUT',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({
				model_id: modelId,
				...(justification ? { justification } : {})
			})
		});
		if (res.status === 202) {
			return { action: 'setAccountModel', pending_review: true };
		}
		if (!res.ok) return fail(res.status, { action: 'setAccountModel', error: await detail(res) });
		return { action: 'setAccountModel', ok: true, cleared: modelId === null };
	},

	/** Close a client (soft-delete): they leave the live book and AUM, their history stays, and
	 *  the record itself stays loadable so the header can say Closed. The API refuses (409) while
	 *  any account has a balance or a pending instruction. Reopen is `?/restoreClient`. */
	closeClient: async ({ locals, params, fetch }) => {
		guard(locals, params.uid, 'contacts:write');
		const res = await fetch(`${apiUrl()}/firm/contacts/${params.uid}`, {
			method: 'DELETE',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { action: 'closeClient', error: await detail(res) });
		return { action: 'closeClient', ok: true };
	},

	/** Reopen a closed client. */
	restoreClient: async ({ locals, params, fetch }) => {
		guard(locals, params.uid, 'contacts:write');
		const res = await fetch(`${apiUrl()}/firm/contacts/${params.uid}/restore`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({})
		});
		if (!res.ok) return fail(res.status, { action: 'restoreClient', error: await detail(res) });
		return { action: 'restoreClient', ok: true };
	},

	/** Invite this client to the portal (create-or-reuse login + link + set-password email). */
	invitePortal: async ({ locals, params, fetch }) => {
		guard(locals, params.uid, 'contacts:write');
		if (
			!can(locals.permissions, locals.role, 'members:write') &&
			!can(locals.permissions, locals.role, 'teams:write') &&
			!can(locals.permissions, locals.role, 'profiles:write')
		) {
			return fail(403, {
				action: 'invitePortal',
				error: 'You do not have permission to invite this client.'
			});
		}
		const res = await fetch(`${apiUrl()}/firm/contacts/${params.uid}/portal-invite`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({})
		});
		if (!res.ok) return fail(res.status, { action: 'invitePortal', error: await detail(res) });
		return { action: 'invitePortal', ok: true };
	},

	/** Record a money-movement instruction (expected-in / withdrawal / model top-up / withdraw).
	 *
	 *  Withdrawals and model top-ups are checked against the provider's cash leg for that account,
	 *  and model withdrawals against what is actually invested — NOT against the account's total
	 *  value, which would authorise paying away a whole portfolio as if it were cash. The leg is
	 *  netted against the account's already-PENDING instructions: nothing settles them, so each one
	 *  is still a claim on the same money, and checking one request at a time let the limit be
	 *  exceeded by simply submitting again. If either basis can't be established the request is
	 *  refused: for a cash-out, "we can't tell" must not mean "go ahead". (The API enforces all of
	 *  this too — apps/api/app/instructions.py — because this action is one HTTP call away from
	 *  being bypassed. This is the defence in depth, and the message the adviser reads.) */
	createInstruction: async ({ request, locals, params, fetch }) => {
		guard(locals, params.uid, 'instructions:write');
		const data = await request.formData();
		const amount = Number(String(data.get('amount') ?? '').replace(/[^0-9.-]/g, ''));
		const kind = String(data.get('kind') ?? '') as InstructionKind;
		const accountId = Number(data.get('account_id'));
		if (!Number.isFinite(amount) || amount <= 0) {
			return fail(400, {
				action: 'createInstruction',
				error: 'Enter an amount greater than zero.'
			});
		}
		const dueDate = String(data.get('due_date') ?? '').trim() || null;
		// The API casts this straight to a date, so a malformed value 500s there instead of 400ing
		// here. A payment in may be backdated (the client paid last week; the API holds the floor
		// of a year); money the custodian has yet to move may not carry a past date.
		if (dueDate) {
			if (!/^\d{4}-\d{2}-\d{2}$/.test(dueDate) || Number.isNaN(Date.parse(dueDate)))
				return fail(400, { action: 'createInstruction', error: 'Enter a valid date.' });
			if (kind !== 'expected_in' && dueDate < new Date().toISOString().slice(0, 10))
				return fail(400, {
					action: 'createInstruction',
					error: 'The expected date is in the past.'
				});
		}

		const headers = authHeaders(locals.accessToken);
		// An inactive client is not a client you move money for. Re-read rather than trust the page:
		// the record may have been deactivated since it was rendered.
		const cRes = await fetchJson<ContactDetail>(
			fetch,
			`${apiUrl()}/crm/contacts/${params.uid}`,
			headers
		);
		if (!cRes.ok)
			return fail(cRes.status || 502, {
				action: 'createInstruction',
				error: 'Couldn’t re-check this client, so nothing was recorded. Try again.'
			});
		if (!cRes.data.contact.enabled)
			return fail(409, {
				action: 'createInstruction',
				error: 'This client is inactive. Reactivate them before raising an instruction.'
			});
		// The id-typed endpoints below take it from the record we just re-read, so raising an
		// instruction still costs one resolve and not two.
		const contactId = cRes.data.contact.id;

		const leg = INSTRUCTION_LEG[kind];
		if (leg) {
			const [hRes, iRes] = await Promise.all([
				fetchJson<RawHolding[]>(fetch, `${apiUrl()}/crm/holdings?contact_id=${contactId}`, headers),
				fetchJson<Instruction[]>(
					fetch,
					`${apiUrl()}/firm/contacts/${params.uid}/instructions`,
					headers
				)
			]);
			// Half a basis is no basis: we'd know the leg but not what is already promised off it.
			if (!hRes.ok || !iRes.ok)
				return fail(502, {
					action: 'createInstruction',
					error:
						'Couldn’t confirm what this account holds, so the instruction wasn’t raised. Try again.'
				});
			const f = accountFunding(basisByAccount(hRes.data), committedByAccount(iRes.data)).find(
				(x) => x.accountId === accountId
			);
			const held = leg === 'cash' ? (f?.cash ?? 0) : (f?.invested ?? 0);
			const committed = leg === 'cash' ? (f?.committedCash ?? 0) : (f?.committedInvested ?? 0);
			const available = leg === 'cash' ? (f?.availableCash ?? 0) : (f?.availableInvested ?? 0);
			const paymentMethod = String(data.get('payment_method') ?? '').trim() || null;
			const passRailFee = String(data.get('pass_rail_fee') ?? '') !== 'false';
			const accountRow = cRes.data.accounts.find((a) => a.id === accountId);
			// The fee is only added when this read named a tariff. A missing account leaves it
			// to the API, which is the gate that cannot be skipped.
			const debit =
				kind === 'withdrawal' && accountRow && paymentMethod
					? withdrawalDebit(amount, paymentMethod, accountRow.withdrawal_fees ?? null, passRailFee)
					: amount;
			const label = leg === 'cash' ? 'cash' : 'invested in the model';
			const tail =
				kind === 'withdrawal'
					? 'A larger withdrawal needs a disinvestment first.'
					: kind === 'model_topup'
						? 'A top-up can only invest cash the account already holds.'
						: kind === 'adhoc_fee'
							? 'A larger fee needs more cash on the account.'
							: 'Reduce the amount.';
			if (debit > available)
				return fail(400, {
					action: 'createInstruction',
					error:
						`Only ${money(available)} is ${label} on this account` +
						`${f?.asOf ? ` (provider position as at ${f.asOf})` : ''}` +
						(committed > 0
							? `. ${money(held)} is held, but ${money(committed)} is already committed to pending instructions`
							: '') +
						(debit > amount
							? `. The payment method fee of ${money(debit - amount)} is included`
							: '') +
						`. ${tail}`
				});
		}

		const paymentMethod = String(data.get('payment_method') ?? '').trim() || null;
		const passRailFee = String(data.get('pass_rail_fee') ?? '') !== 'false';
		const justification = String(data.get('justification') ?? '').trim() || null;
		const body = {
			kind,
			account_id: accountId,
			amount,
			model_id: data.get('model_id') ? Number(data.get('model_id')) : null,
			due_date: dueDate,
			note: String(data.get('note') ?? '').trim() || null,
			...(kind === 'withdrawal' && paymentMethod ? { payment_method: paymentMethod } : {}),
			...(kind === 'withdrawal' && data.has('pass_rail_fee') ? { pass_rail_fee: passRailFee } : {}),
			...(justification ? { justification } : {})
		};
		const res = await fetch(`${apiUrl()}/firm/contacts/${params.uid}/instructions`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify(body)
		});
		if (res.status === 202) {
			return { action: 'createInstruction', pending_review: true };
		}
		if (!res.ok) return fail(res.status, { action: 'createInstruction', error: await detail(res) });
		// BOOKED BUT NOT SENT. The API keeps the row when the custodian refuses or cannot be
		// reached and says so in `provider_error`; the record must say the same, or the adviser
		// reads "Instruction recorded" and assumes the money moved.
		const created = (await res.json().catch(() => ({}))) as { provider_error?: string };
		return {
			action: 'createInstruction',
			ok: true,
			...(created.provider_error
				? { warning: `Recorded, but not sent to the custodian: ${created.provider_error}` }
				: {})
		};
	},

	cancelInstruction: async ({ request, locals, params, fetch }) => {
		guard(locals, params.uid, 'instructions:write');
		const form = await request.formData();
		const uid = String(form.get('uid') ?? '');
		if (!uid) return fail(400, { action: 'cancelInstruction', error: 'No instruction specified.' });
		const justification = String(form.get('justification') ?? '').trim() || null;
		const res = await fetch(`${apiUrl()}/firm/instructions/${uid}/cancel`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify(justification ? { justification } : {})
		});
		if (res.status === 202) {
			return { action: 'cancelInstruction', pending_review: true };
		}
		if (!res.ok) return fail(res.status, { action: 'cancelInstruction', error: await detail(res) });
		return { action: 'cancelInstruction', ok: true };
	},

	cancelReview: async ({ request, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const uid = String((await request.formData()).get('review_uid') ?? '');
		if (!uid) return fail(400, { action: 'cancelReview', error: 'No review specified.' });
		const res = await fetch(`${apiUrl()}/reviews/${uid}/cancel`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { action: 'cancelReview', error: await detail(res) });
		return { action: 'cancelReview', ok: true };
	},

	/** Delete a note you wrote. The API restricts it to the CALLER'S OWN notes in the WHERE clause,
	 *  so this passes the id and lets the server decide — the component cannot know who wrote one
	 *  (`ClientNoteRow` carries an author NAME, not an id) and matching on a display name is the
	 *  inference this estate refuses. Somebody else's note answers 404, not 403. */
	deleteNote: async ({ request, locals, params, fetch }) => {
		guard(locals, params.uid, 'contacts:write');
		const noteUid = String((await request.formData()).get('note_uid') ?? '');
		const res = await fetch(`${apiUrl()}/firm/contacts/${params.uid}/notes/${noteUid}`, {
			method: 'DELETE',
			headers: jsonHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { action: 'deleteNote', error: await detail(res) });
		return { action: 'deleteNote', ok: true, note_uid: noteUid };
	},

	pinClassification: async ({ request, locals, params, fetch }) => {
		guard(locals, params.uid, 'contacts:write');
		const uid = String((await request.formData()).get('classification_uid') ?? '').trim();
		const res = await fetch(`${apiUrl()}/crm/contacts/${params.uid}/classification`, {
			method: 'PUT',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({ classification_uid: uid || null })
		});
		if (!res.ok) return fail(res.status, { action: 'pinClassification', error: await detail(res) });
		return { action: 'pinClassification', ok: true };
	},

	setGroups: async ({ request, locals, params, fetch }) => {
		guard(locals, params.uid, 'contacts:write');
		const group_uids = (await request.formData())
			.getAll('group_uid')
			.map((value) => String(value).trim())
			.filter(Boolean);
		const res = await fetch(`${apiUrl()}/crm/contacts/${params.uid}/groups`, {
			method: 'PUT',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({ group_uids })
		});
		if (!res.ok) return fail(res.status, { action: 'setGroups', error: await detail(res) });
		return { action: 'setGroups', ok: true };
	},

	addNote: async ({ request, locals, params, fetch }) => {
		guard(locals, params.uid, 'contacts:write');
		const data = await request.formData();
		const body = String(data.get('body') ?? '').trim();
		if (!body) return fail(400, { action: 'addNote', error: 'Write a note first.' });
		const res = await fetch(`${apiUrl()}/firm/contacts/${params.uid}/notes`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({ body })
		});
		if (!res.ok) return fail(res.status, { action: 'addNote', error: await detail(res) });
		const note = (await res.json()) as Record<string, unknown>;
		return { action: 'addNote', ok: true, note };
	}
};
