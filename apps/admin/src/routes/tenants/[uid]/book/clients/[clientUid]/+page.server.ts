import { error, fail, redirect } from '@sveltejs/kit';
import { authHeaders, jsonHeaders, detail as apiDetail } from '$lib/api';
import { bookUrl, getJson, type BookContactDetail } from '$lib/book/api';
import { can } from '$lib/permissions';
import type { TaskDisplayRow } from '@graphene/ui/book/tasks';

/** The slice of GET /tasks rows the book record displays (the shared display rules read only
 *  `due_date`/`status`; `uid`/`title` are this page's own rendering). */
type BookTaskRow = TaskDisplayRow & { uid: string; title: string };
// The lake classification AND the money classification live with the shared book components so
// both portals' loaders and gates read ONE definition of "cash" / the allocation buckets / the
// fundable legs (pkgs/ui book/clients/{lake,funding,bank}.ts).
import {
	INSTRUCTION_LEG,
	accountFunding,
	applyHoldingsSummary,
	basisByAccount,
	basisFromSummaryLegs,
	committedByAccount,
	withdrawalDebit,
	readBankDetails,
	validateBank,
	CLEARED_BANK,
	takesFatcaDocument,
	formatGBP as money,
	type HoldingsSummary,
	type InstructionKind,
	type InstructionRow,
	type RawHolding
} from '@graphene/ui';
import { PAGE_SIZE } from '@graphene/ui/book/listing';
import { justificationKeys, type ReviewPolicy } from '@graphene/ui/book/reviews';
import type { Actions, PageServerLoad } from './$types';

/**
 * One client of the target firm — the platform's `/clients/[id]` reads with `?tenant=` on every
 * call. Two postures share this loader:
 *  · `book:read` only — read-only proxy view. Instructions/funding are not fetched at all:
 *    they exist to feed the money dialogs and the page mounts `actions: null`.
 *  · `book:write` — the approved middle-office actions are live, so the record also loads the
 *    instruction history and per-account fundable legs (all via `?tenant=`).
 *    A 403 from those reads degrades gracefully (an older API without book-enabled writes must not
 *    blank the record): instructions render empty.
 * Screening comes free: the contact detail's `kyc` summary is what the record renders (hit detail
 * and adjudication live in the /aml console, not here).
 */
export const load: PageServerLoad = async ({ locals, params, fetch, parent }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant } = await parent();
	const headers = authHeaders(locals.accessToken);
	const canWrite = can(locals.permissions, locals.role, 'book:write');

	const res = await fetch(bookUrl(`/crm/contacts/${params.clientUid}`, tenant.uid), {
		headers
	});
	// 404 covers "no such client" AND "archived" (every read filters deleted_at) — same as the
	// platform. 403 would mean the book grant was revoked between the layout gate and this fetch.
	if (res.status === 404) error(404, 'Client not found in this firm’s book.');
	if (!res.ok) error(res.status, 'Could not load the client.');
	const detail: BookContactDetail = await res.json();
	const contactId = detail.contact.id;

	const [summaryRes, tasksRes, insRes, policiesRes] = await Promise.all([
		fetch(bookUrl('/crm/holdings/summary', tenant.uid, { contact_id: String(contactId) }), {
			headers
		}),
		// The book overlay grants tasks:read for the one request, so this needs no gate on the
		// operator's own (operations-portal) permissions — see db.py::_BOOK_READ_VISIBILITY.
		fetch(
			bookUrl('/tasks', tenant.uid, { assignee: `contact:${contactId}`, limit: String(PAGE_SIZE) }),
			{ headers }
		),
		canWrite
			? fetch(bookUrl(`/firm/contacts/${params.clientUid}/instructions`, tenant.uid), { headers })
			: null,
		canWrite ? fetch(bookUrl('/reviews/policies', tenant.uid), { headers }) : null
	]);
	const summary: HoldingsSummary | null = summaryRes.ok ? await summaryRes.json() : null;

	// Allocation ring + cash figure, exactly the platform's derivation: the lake's anonymised
	// absolute values don't reconcile with the OLTP account values in the header, so scale the
	// classes to the client's AUM — one factor over every bucket. The unpaged holdings list is
	// not a fallback.
	const clientAum = detail.accounts.reduce((s, a) => s + (a.value ?? 0), 0);
	const { allocations, cash, bookCost, unrealisedGain, accountLegs } = applyHoldingsSummary(
		summary,
		clientAum
	);

	const tasks: BookTaskRow[] = tasksRes.ok ? await tasksRes.json() : [];
	const instructions: InstructionRow[] = insRes?.ok ? await insRes.json() : [];

	return {
		detail,
		allocations,
		cash,
		bookCost,
		unrealisedGain,
		holdingsError: !summaryRes.ok,
		tasks,
		bankAccount: readBankDetails(detail.contact),
		canWrite,
		instructions,
		// What each account can actually fund, on the SAME basis the withdrawal gate uses (raw
		// provider legs, net of pending claims) — deliberately NOT the scaled `cash` figure above.
		// Only derived when the dialogs that state it can open. The summary's per-account legs
		// feed this when the API sends them.
		funding: canWrite
			? accountFunding(basisFromSummaryLegs(accountLegs), committedByAccount(instructions))
			: [],
		justificationRequired:
			policiesRes?.ok === true
				? justificationKeys((await policiesRes.json()) as ReviewPolicy[])
				: []
	};
};

/** Every mutation below requires `book:write` — `book:read` is the proxy VIEW, never the pen.
 *  Mirrors the platform's guard shape (redirect, not fail: there is no form outcome to decorate
 *  when the caller shouldn't have seen a form at all). */
function guard(locals: App.Locals, uid: string, clientUid: string): void {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'book:write'))
		redirect(303, `/tenants/${uid}/book/clients/${clientUid}`);
}

/** The three affordances the shared record renders that are NOT on the product owner's approved
 *  middle-office list. The `ClientRecordActions` contract requires their names, so they exist —
 *  as refusals that never touch the API. */
const notSupport = (action: string, what: string) =>
	fail(403, {
		action,
		error: `${what} is not available here. The firm must complete it in its own portal.`
	});

/** Fields the details form is allowed to blank out. `null` here means "clear it". */
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

/**
 * The approved middle-office actions, ported from the platform's client record
 * (apps/platform/src/routes/clients/[id]/+page.server.ts) with every refusal intact: the SAME
 * staff-side endpoints, targeted at the firm via `?tenant=<uid>` on every write (the API's
 * book-enabled write path requires operations scope + `book:write`; audit keeps naming the
 * operator). Close/deactivate/notes are deliberately NOT ported — see `notSupport`.
 */
export const actions: Actions = {
	updateClient: async ({ request, locals, params, fetch }) => {
		// Actions run outside the load tree, so there is no parent(): the gate and the addresses
		// below both come from `params`.
		guard(locals, params.uid, params.clientUid);
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
		// Presence, not an always-sent body: the shared record's five section cards post through
		// this one action (the platform's `updateClient` rule). Dumping every contact field made a
		// Compliance save send `first_name: null` — and never `source_of_wealth` at all.
		const body: Record<string, unknown> = {};
		const put = (key: string, value: unknown) => {
			if (data.has(key)) Object.assign(body, { [key]: value });
		};
		/** The signed W-9 or W-8 from the Compliance card, uploaded once the PATCH has saved. */
		let fatcaDocument: File | null = null;
		put('service_type', str('service_type') || null);
		if (isOrg) put('sub_type', str('sub_type') || null);
		put('title', str('title') || null);
		put('first_name', isOrg ? null : first || null);
		put('surname', isOrg ? null : surname || null);
		// The database owns a person's name, from the parts (0239); only an organisation's is sent.
		if (isOrg) put('name', str('name') || null);
		put('email', email || null);
		put('mobile', str('mobile') || null);
		put('phone', str('phone') || null);
		if (data.has('addr_line1')) body.address = hasAddress ? addr : null;
		if (!isOrg) {
			put('gender', str('gender') || null);
			put('dob', str('dob') || null);
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
				body.flexi_access_date = taken ? str('flexi_access_date') || null : null;
			}
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
			const earnings = Number(str('relevant_uk_earnings').replace(/[^0-9.-]/g, ''));
			put('relevant_uk_earnings', Number.isFinite(earnings) && earnings > 0 ? earnings : null);
			put('source_of_wealth', str('source_of_wealth') || null);
			put('source_of_wealth_detail', str('source_of_wealth_detail') || null);
		}
		const res = await fetch(bookUrl(`/firm/contacts/${params.clientUid}`, params.uid), {
			method: 'PATCH',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify(body)
		});
		if (!res.ok) return fail(res.status, { action: 'updateClient', error: await apiDetail(res) });
		// The API builds its SET clause from non-null fields only, so a requested clear can be
		// silently dropped. Compare what came back and tell the truth rather than toasting
		// "Client updated" over a no-op.
		const saved: Record<string, unknown> = await res.json();
		if (fatcaDocument) {
			const upload = new FormData();
			upload.append('file', fatcaDocument, fatcaDocument.name);
			const uploaded = await fetch(
				bookUrl(`/crm/contacts/${params.clientUid}/fatca-document`, params.uid),
				{
					method: 'POST',
					// No Content-Type — let fetch set the multipart boundary.
					headers: authHeaders(locals.accessToken),
					body: upload
				}
			);
			// The PATCH has already saved, so the message says so. The form stays open with the file.
			if (!uploaded.ok)
				return fail(uploaded.status, {
					action: 'updateClient',
					field: 'fatca_document',
					error: `The answer is saved, but the form did not upload: ${await apiDetail(uploaded)}`
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

	/** Set or clear the client's bank details — same shape and same validation as the platform
	 *  (the shared `validateBank`), so the adviser-grade field messages hold for ops too. Nothing
	 *  in this action logs, and no message it returns carries a digit. */
	updateBankAccount: async ({ request, locals, params, fetch }) => {
		// Actions run outside the load tree, so there is no parent(): the gate and the addresses
		// below both come from `params`.
		guard(locals, params.uid, params.clientUid);
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
		const res = await fetch(bookUrl(`/crm/contacts/${params.clientUid}/bank-account`, params.uid), {
			method: 'PUT',
			headers: jsonHeaders(locals.accessToken),
			// An empty form clears the details: send every field explicitly null rather than omitting
			// them, so "cleared" can't be read as "unchanged".
			body: JSON.stringify({
				...(checked.value ?? CLEARED_BANK),
				...(str('justification').trim() ? { justification: str('justification').trim() } : {})
			})
		});
		if (!res.ok)
			return fail(res.status, { action: 'updateBankAccount', error: await apiDetail(res) });
		return { action: 'updateBankAccount', ok: true, cleared: checked.value === null };
	},

	/** Associate a model with one of the client's accounts, change it, or clear it. The API
	 *  refuses (409) while the account has pending model instructions — that message is passed
	 *  through rather than swallowed: a live claim is a reason not to re-point a mandate. */
	setAccountModel: async ({ request, locals, params, fetch }) => {
		// Actions run outside the load tree, so there is no parent(): the gate and the addresses
		// below both come from `params`.
		guard(locals, params.uid, params.clientUid);
		const data = await request.formData();
		// The account's uid, not its serial — see the platform's twin.
		const accountUid = String(data.get('account_uid') ?? '').trim();
		if (!accountUid)
			return fail(400, { action: 'setAccountModel', error: 'No account specified.' });
		const raw = String(data.get('model_id') ?? '').trim();
		const modelId = raw ? Number(raw) : null;
		if (modelId !== null && !Number.isInteger(modelId))
			return fail(400, { action: 'setAccountModel', error: 'Choose a model.' });
		const justification = String(data.get('justification') ?? '').trim();
		const res = await fetch(bookUrl(`/crm/accounts/${accountUid}/model`, params.uid), {
			method: 'PUT',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({
				model_id: modelId,
				...(justification ? { justification } : {})
			})
		});
		if (!res.ok)
			return fail(res.status, { action: 'setAccountModel', error: await apiDetail(res) });
		return { action: 'setAccountModel', ok: true, cleared: modelId === null };
	},

	/** Record a money-movement instruction (expected-in / withdrawal / model top-up / withdraw).
	 *
	 *  The platform's withdrawal control, verbatim in behaviour: withdrawals and model top-ups are
	 *  checked against the provider's cash leg for that account, model withdrawals against what is
	 *  actually invested, and the leg is netted against the account's already-PENDING instructions.
	 *  If either basis can't be established the request is refused: for a cash-out, "we can't tell"
	 *  must not mean "go ahead". (The API enforces all of this too — apps/api/app/instructions.py —
	 *  because this action is one HTTP call away from being bypassed. This is the defence in depth,
	 *  and the message the operator reads.) */
	createInstruction: async ({ request, locals, params, fetch }) => {
		// Actions run outside the load tree, so there is no parent(): the gate and the addresses
		// below both come from `params`.
		guard(locals, params.uid, params.clientUid);
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
		const cRes = await getJson<BookContactDetail>(
			fetch,
			bookUrl(`/crm/contacts/${params.clientUid}`, params.uid),
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
		// The `?contact_id=` filter below is named for what it takes, so it keeps the serial off the
		// record just re-read; every PATH takes the route's own uid.
		const contactId = cRes.data.contact.id;

		const leg = INSTRUCTION_LEG[kind];
		if (leg) {
			const [hRes, iRes] = await Promise.all([
				getJson<RawHolding[]>(
					fetch,
					bookUrl('/crm/holdings', params.uid, { contact_id: String(contactId) }),
					headers
				),
				getJson<InstructionRow[]>(
					fetch,
					bookUrl(`/firm/contacts/${params.clientUid}/instructions`, params.uid),
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
		const justification = String(data.get('justification') ?? '').trim();
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
		const res = await fetch(
			bookUrl(`/firm/contacts/${params.clientUid}/instructions`, params.uid),
			{
				method: 'POST',
				headers: jsonHeaders(locals.accessToken),
				body: JSON.stringify(body)
			}
		);
		if (!res.ok)
			return fail(res.status, { action: 'createInstruction', error: await apiDetail(res) });
		return { action: 'createInstruction', ok: true };
	},

	cancelInstruction: async ({ request, locals, params, fetch }) => {
		// Actions run outside the load tree, so there is no parent(): the gate and the addresses
		// below both come from `params`.
		guard(locals, params.uid, params.clientUid);
		const form = await request.formData();
		const uid = String(form.get('uid') ?? '');
		if (!uid) return fail(400, { action: 'cancelInstruction', error: 'No instruction specified.' });
		const justification = String(form.get('justification') ?? '').trim();
		const res = await fetch(bookUrl(`/firm/instructions/${uid}/cancel`, params.uid), {
			method: 'POST',
			headers: justification ? jsonHeaders(locals.accessToken) : authHeaders(locals.accessToken),
			...(justification ? { body: JSON.stringify({ justification }) } : {})
		});
		if (!res.ok)
			return fail(res.status, { action: 'cancelInstruction', error: await apiDetail(res) });
		return { action: 'cancelInstruction', ok: true };
	},

	// ── Not on the approved list ─────────────────────────────────────────────
	// Note-taking is the firm's own decision about its client, not middle-office support;
	// the form still renders (the shared record shows it whenever `addNote` is passed) but the
	// server answers with why, and no API call is ever made.
	addNote: async ({ locals, params }) => {
		// Actions run outside the load tree, so there is no parent(): the gate and the addresses
		// below both come from `params`.
		guard(locals, params.uid, params.clientUid);
		return notSupport('addNote', 'Adding a note to a firm’s client');
	}
};
