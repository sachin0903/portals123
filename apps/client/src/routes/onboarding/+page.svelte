<script lang="ts">
	import {
		Button,
		Card,
		REFRESH_WHILE,
		RefreshWhile,
		Checkbox,
		Dialog,
		DocumentViewer,
		EmptyState,
		FileDropzone,
		formatDate,
		formatGBP,
		formatPhoneNumber,
		LoadErrorBanner,
		OnboardingNextAction,
		Skeleton,
		StatusBadge,
		toast
	} from '@graphene/ui';
	import { summariseAppliedFee, type AppliedFeeSummary } from '@graphene/ui/book/journeys';
	import { paymentInWording } from '$lib/onboarding/funding';
	import CheckIcon from '@lucide/svelte/icons/check';
	import CircleCheckIcon from '@lucide/svelte/icons/circle-check';
	import ClipboardListIcon from '@lucide/svelte/icons/clipboard-list';
	import FileTextIcon from '@lucide/svelte/icons/file-text';
	import FileSignatureIcon from '@lucide/svelte/icons/file-signature';
	import ScanFaceIcon from '@lucide/svelte/icons/scan-face';
	import UploadIcon from '@lucide/svelte/icons/upload';
	import UserCogIcon from '@lucide/svelte/icons/user-cog';
	import FormFillDialog from '$lib/components/form-fill-dialog.svelte';
	import IdentityVerification from '$lib/components/onboarding/identity-verification.svelte';
	import { formDataSources } from '$lib/forms';
	import {
		captureItemsKey,
		IDENTITY_CAPTURE_KEYS,
		identityRefresh,
		photosSent,
		PHOTOS_SENT_DETAIL,
		PHOTOS_SENT_TITLE,
		SUBMIT_FAILED,
		submitError,
		type IdentityCaptureKey,
		type IdentityDocumentType
	} from '$lib/identity-capture';
	import { partyRows, roleLabel, termsAgreeIndex } from '$lib/onboarding';
	import { deserialize } from '$app/forms';
	import { invalidate } from '$app/navigation';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	const cases = $derived(data.cases);
	const stages = $derived(data.stages);
	const review = $derived(data.review);
	const onboardingDataSources = $derived(
		data.countries?.length
			? { ...formDataSources, nationalities: data.countries, countries: data.countries }
			: formDataSources
	);

	/**
	 * Re-read this page AND the shell after finishing a step: the journey's own state changes, and so
	 * does the layout's Onboarding tab, which disappears once nothing is outstanding. Two keys rather
	 * than `invalidateAll` so a completed task does not also re-fetch the notification feed and the
	 * client switcher; `invalidate` takes ONE dependency, hence the pair.
	 */
	const refreshJourney = () => Promise.all([invalidate('app:onboarding'), invalidate('app:shell')]);

	// Element types derived from the loader's return (defined in +page.server.ts).
	type OnboardingCase = PageData['cases'][number];
	type OnboardingStep = OnboardingCase['steps'][number];
	type ReviewAccount = NonNullable<PageData['review']>['accounts'][number];

	/** The API returns null when there is no active journey, so presence alone is enough — sparse
	 *  fields must not hide the card or block terms. */
	const showReview = $derived(review != null);

	function titleCase(value: string): string {
		return value.replace(/[_-]+/g, ' ').replace(/\b\w/g, (m) => m.toUpperCase());
	}

	function formatList(values: string[] | null | undefined): string | null {
		if (!values?.length) return null;
		return values.join(', ');
	}

	function formatAddress(
		addr: {
			line1?: string;
			line2?: string;
			city?: string;
			region?: string;
			postcode?: string;
			country?: string;
		} | null
	): string | null {
		if (!addr) return null;
		const line = [addr.line1, addr.line2, addr.city, addr.region, addr.postcode, addr.country]
			.map((p) => (p ?? '').trim())
			.filter(Boolean)
			.join(', ');
		return line || null;
	}

	/** A stored number is canonical `+447715215412`; this reads it back in the grouping it was
	 *  typed under. The SAME `formatPhoneNumber` the form field uses (@graphene/ui masks), so the
	 *  review cannot disagree with the input a client filled in a moment earlier.
	 *
	 *  Defensive about the shape as well as the spacing: `crm.contacts.mobile` is a text column,
	 *  and a book merged from Seccl before the conformed model started extracting `->> 'number'`
	 *  holds the provider's whole `{"number", "isMobile", "locale"}` object in it. The API unwraps
	 *  that now (`app.contacts.normalise_phone`), which is where the fix belongs — a portal cannot
	 *  fix a book. This is the belt: the JSON has no digits outside the number, so the mask would
	 *  otherwise render its punctuation as a phone number. */
	function formatPhone(value: string | null): string | null {
		if (!value) return null;
		const raw = value.trim();
		if (!raw) return null;
		const wrapped = /"number"\s*:\s*"([^"]*)"/.exec(raw);
		const number = wrapped ? wrapped[1].trim() : raw;
		if (!number) return null;
		// A number with no digits at all is not one — return it as it stands rather than letting
		// the mask emit a bare "+" for it.
		return /\d/.test(number) ? formatPhoneNumber(number) || number : number;
	}

	function formatBank(
		bank: { name?: string; sort_code?: string; account_number?: string } | null
	): string | null {
		if (!bank) return null;
		const parts = [bank.name, bank.sort_code, bank.account_number]
			.map((p) => (p ?? '').trim())
			.filter(Boolean);
		return parts.length ? parts.join(' · ') : null;
	}

	function transferLine(account: ReviewAccount): string | null {
		if (!account.has_transfer && !account.transfer) return null;
		const xfer = account.transfer;
		const parts: string[] = [];
		if (xfer?.extent === 'full' || xfer?.extent === 'partial') parts.push(xfer.extent);
		if (xfer?.type) parts.push(String(xfer.type).replace(/_/g, ' '));
		if (xfer?.amount != null && Number.isFinite(Number(xfer.amount))) {
			parts.push(formatGBP(Number(xfer.amount)));
		}
		if (xfer?.provider_name) parts.push(xfer.provider_name);
		return parts.length ? `Transfer in: ${parts.join(' · ')}` : 'Transfer in';
	}

	function fundingLine(account: ReviewAccount): string | null {
		const rc = account.regular_contribution;
		if (!rc?.intent) return null;
		const labels: Record<string, string> = {
			single: 'Single',
			monthly: 'Monthly',
			quarterly: 'Quarterly',
			half_yearly: 'Half-yearly',
			annually: 'Annually'
		};
		const freqKey = (rc.frequency ?? 'single').trim().toLowerCase().replace(/-/g, '_');
		const freq = labels[freqKey] ?? paymentInWording(account.product_type).fallback;
		if (rc.amount != null && Number.isFinite(Number(rc.amount))) {
			return `${freq} ${formatGBP(Number(rc.amount))}`;
		}
		return freq;
	}

	function jisaChildLine(account: ReviewAccount): string | null {
		const child = account.jisa_child;
		if (!child) return null;
		const name = (child.name ?? '').trim();
		const dob = (child.dob ?? '').trim();
		if (!name && !dob) return null;
		if (name && dob) {
			try {
				return `Child: ${name} (${formatDate(dob)})`;
			} catch {
				return `Child: ${name} (${dob})`;
			}
		}
		return name ? `Child: ${name}` : `Child: ${dob}`;
	}

	function capacityLine(account: ReviewAccount): string | null {
		switch ((account.investment_capacity ?? '').trim().toLowerCase()) {
			case 'advisory':
				return 'Advisory';
			case 'discretionary':
				return 'Discretionary';
			case 'execution_only':
				return 'Execution only';
			default:
				return null;
		}
	}

	function dividendLine(account: ReviewAccount): string | null {
		switch ((account.dividend_treatment ?? '').trim().toLowerCase()) {
			case 'reinvest':
				return 'Dividends: Reinvest';
			case 'cash':
				return 'Dividends: Cash';
			case 'pay_away':
				return 'Dividends: Pay away';
			default:
				return null;
		}
	}

	/** Every charge on the account, stated in full rather than by its pricing label. */
	function feeSummaries(account: ReviewAccount): AppliedFeeSummary[] {
		const fees = account.fees;
		if (fees?.length) return fees.map((fee) => summariseAppliedFee(fee));
		// Older API build: names only, which is what this card showed before.
		return (account.fee_packages ?? [])
			.filter((name) => name.trim())
			.map((name) => ({ name: name.trim(), charge: '', frequency: '', bands: [], notes: [] }));
	}

	/** One account's facts as labelled rows, for a definition list.
	 *
	 *  This replaces a stack of `·`-joined muted micro-lines. A client on this page is answering
	 *  "what exactly am I getting?" — which means finding ONE fact (the fee, the model, who owns
	 *  it) — and a run-on sentence has to be read whole to be searched. Labels can be scanned.
	 *  Empty values are dropped rather than shown blank: a sparse dossier is normal early on, and
	 *  a column of "—" reads as missing information rather than information not yet decided. */
	function accountRows(account: ReviewAccount): { label: string; value: string }[] {
		const rows: { label: string; value: string }[] = [];
		const add = (label: string, value: string | null | undefined): void => {
			const trimmed = (value ?? '').trim();
			if (trimmed) rows.push({ label, value: trimmed });
		};
		const transfer = transferLine(account);
		for (const row of partyRows(account)) rows.push(row);
		add('Currency', account.currency);
		add('How it’s managed', capacityLine(account));
		add('Held with', account.custody ? titleCase(account.custody) : null);
		add('Investment model', account.model);
		add('Dividends', dividendLine(account)?.replace(/^Dividends: /, ''));
		// transferLine() degrades to the bare label when the adviser has flagged a transfer without
		// its detail yet — under a "Transfer in" label that would read as "Transfer in: Transfer in".
		add(
			'Transfer in',
			transfer === 'Transfer in' ? 'Yes' : transfer?.replace(/^Transfer in: /, '')
		);
		add(
			'Safeguarded rights',
			account.transfer && 'safeguarded_benefits' in account.transfer
				? account.transfer.safeguarded_benefits
					? 'Yes'
					: 'No'
				: null
		);
		add('FCA reference', account.transfer?.fca_reference);
		add(paymentInWording(account.product_type).row, fundingLine(account));
		add('Child', jisaChildLine(account)?.replace(/^Child: /, ''));
		return rows;
	}

	const STEP_ICON = {
		fill_form: FileTextIcon,
		accept_terms: FileSignatureIcon,
		upload_document: UploadIcon,
		verify_identity: ScanFaceIcon,
		setup_account: UserCogIcon
	} as const;
	function stepIcon(type: string): typeof FileTextIcon {
		return (STEP_ICON as Record<string, typeof FileTextIcon>)[type] ?? ClipboardListIcon;
	}

	const STEP_KIND_LABEL: Record<string, string> = {
		fill_form: 'Form',
		accept_terms: 'Agreement',
		upload_document: 'Document upload',
		verify_identity: 'Identity check',
		setup_account: 'Account setup'
	};

	/** A client acts on this step only when it's their pending step of a live case AND the server
	 *  says it is actually theirs to do now (`available`: prerequisites met, artifact minted).
	 *
	 *  Without that last clause every terms declaration was offered the moment the journey started —
	 *  the first of them is held on a STAFF step, so the client was being asked for work their
	 *  adviser had not reached. The button then failed with "please try again shortly", which
	 *  described a deliberate ordering as a temporary glitch. */
	function isActionable(c: OnboardingCase, s: OnboardingStep): boolean {
		return (
			c.status !== 'cancelled' &&
			c.status !== 'completed' &&
			s.assignee === 'client' &&
			// An identity check is WAITING by design — the engine parks it the moment it opens, and it
			// is released by the verdict reaching the API, not by the client pressing anything. It is
			// still theirs to do, so `pending` alone would have shown it as "Not yet" for ever.
			(s.status === 'pending' || (s.status === 'waiting' && s.step_type === 'verify_identity')) &&
			// ...until their photos are sent: then it waits on the check, not on them.
			!identitySent(s) &&
			s.available !== false
		);
	}

	/** Post one step's fulfilment (accept_terms confirm-only, or upload_document with a file), then
	 * reload so the case status advances. Returns null on success, an error message otherwise —
	 * callers surface it in their own dialog (errors are scoped, not shared page state). */
	async function postFulfil(step: OnboardingStep, file?: File): Promise<string | null> {
		if (step.catalogue_uid) {
			const body = new FormData();
			body.append('uid', step.catalogue_uid);
			const res = await fetch('/onboarding?/acceptCatalogue', { method: 'POST', body });
			const result = deserialize(await res.text());
			if (result.type === 'success') {
				await refreshJourney();
				return null;
			}
			return result.type === 'failure'
				? String(result.data?.error ?? 'That didn’t work.')
				: 'That didn’t work.';
		}
		if (!step.request_uid || !step.request_item_uid) {
			// Not a transient failure: the step is held behind earlier work (often the adviser's), so
			// "try again shortly" was telling the client to keep pressing a button that cannot work.
			return 'This will open after the earlier steps are finished.';
		}
		return postItem(step.request_uid, step.request_item_uid, file);
	}

	/** Fulfil ONE request item. Keyed by the item rather than by a step, because the documents a
	 *  manual identity check asks for are items on the client's request that no step owns — a file
	 *  landing must not complete the check itself. */
	async function postItem(
		requestUid: string,
		itemUid: string,
		file?: File
	): Promise<string | null> {
		const body = new FormData();
		body.append('req_uid', requestUid);
		body.append('item_uid', itemUid);
		if (file) body.append('file', file, file.name);
		const res = await fetch('/onboarding?/fulfilItem', { method: 'POST', body });
		const result = deserialize(await res.text());
		if (result.type === 'success') {
			await refreshJourney();
			return null;
		}
		return result.type === 'failure'
			? String(result.data?.error ?? 'That didn’t work.')
			: 'That didn’t work. Please try again.';
	}

	// Upload flow (upload_document). Errors scoped to the upload dialog.
	let uploadOpen = $state(false);
	let uploadStep = $state<OnboardingStep | null>(null);
	let uploadError = $state('');
	let files = $state<File[]>([]);
	let uploading = $state(false);
	$effect(() => {
		if (files.length) uploadError = '';
	});

	function openUpload(step: OnboardingStep): void {
		uploadStep = step;
		files = [];
		uploadError = '';
		uploadOpen = true;
	}

	async function submitUpload(): Promise<void> {
		if (uploading || !uploadStep) return;
		if (!files.length) {
			uploadError = 'Choose a file.';
			return;
		}
		uploading = true;
		try {
			const err = await postFulfil(uploadStep, files[0]);
			if (err) uploadError = err;
			else uploadOpen = false;
		} finally {
			uploading = false;
		}
	}

	// Accept-terms flow: the client must SEE the document before agreeing (no blind confirmations).
	// Errors scoped to the agree dialog.
	let agreeOpen = $state(false);
	let agreeStep = $state<OnboardingStep | null>(null);
	let agreeError = $state('');
	let agreeChecked = $state(false);
	let agreeing = $state(false);
	let agreeLoading = $state(false);
	let agreeDoc = $state<{
		url: string | null;
		name: string | null;
		content_type: string | null;
		content: string | null;
	} | null>(null);

	async function loadAgreeDoc(step: OnboardingStep): Promise<void> {
		if (step.catalogue_uid) {
			// Written copy is inline; a PDF term reads through the same-origin proxy, like a
			// declaration's own target does.
			agreeDoc = {
				url: step.catalogue_html ? null : (step.catalogue_file?.url ?? null),
				name: step.title,
				content_type: step.catalogue_html ? null : (step.catalogue_file?.content_type ?? null),
				content: step.catalogue_html ?? null
			};
			return;
		}
		if (!step.request_uid || !step.request_item_uid) return;
		agreeLoading = true;
		try {
			const body = new FormData();
			body.append('req_uid', step.request_uid);
			body.append('item_uid', step.request_item_uid);
			const res = await fetch('/onboarding?/targetDoc', { method: 'POST', body });
			const result = deserialize(await res.text());
			// A terms declaration returns inline HTML `content`; a stored document returns a `url`.
			if (result.type === 'success') {
				const url = (result.data?.url as string) || null;
				const content = (result.data?.content as string) || null;
				agreeDoc = {
					url,
					name: (result.data?.name as string) ?? null,
					content_type: (result.data?.content_type as string) ?? null,
					content
				};
			} else {
				agreeError = 'Couldn’t load the document to review. Please try again.';
			}
		} finally {
			agreeLoading = false;
		}
	}

	async function openAgree(step: OnboardingStep): Promise<void> {
		agreeStep = step;
		agreeChecked = false;
		agreeDoc = null;
		agreeError = '';
		agreeOpen = true;
		await loadAgreeDoc(step);
	}

	const isPdf = $derived(agreeDoc?.content_type === 'application/pdf');

	/** Every declaration on this application, settled or not, in list order — what lets the dialog
	 *  say "Document 2 of N" while chaining. Skipped ones are excluded: a declaration this journey
	 *  ruled out is not one of the client's set. */
	const termsAll = $derived(
		cases.flatMap((c) =>
			c.steps.filter(
				(s) => s.step_type === 'accept_terms' && s.status !== 'skipped' && s.status !== 'cancelled'
			)
		)
	);
	const agreeIndex = $derived(termsAgreeIndex(termsAll, agreeStep?.key));
	/** More declarations after the open one — drives "Agree and continue" over a bare "Agree". */
	const moreTerms = $derived(agreeIndex > 0 && agreeIndex < termsAll.length);

	async function submitAgree(): Promise<void> {
		if (!agreeStep || !agreeChecked || agreeing) return;
		agreeing = true;
		try {
			const err = await postFulfil(agreeStep);
			if (err) {
				agreeError = err;
				return;
			}
			// Agreeing one declaration releases the next: the seeded journey wires the five of them in
			// sequence, so closing here made a client open the dialog, read, agree and close five
			// separate times — with a list refresh in between each. `postFulfil` has already
			// refreshed, so `readyNow` is the post-agreement list and the next document is simply the
			// next one in it. Chaining in place keeps one reading position and one checkbox habit.
			const next = readyNow.find((p) => p.s.step_type === 'accept_terms');
			if (!next) {
				agreeOpen = false;
				return;
			}
			agreeStep = next.s;
			agreeChecked = false;
			agreeDoc = null;
			agreeError = '';
			await loadAgreeDoc(next.s);
		} finally {
			agreeing = false;
		}
	}

	// Fill-form flow (complete_form via the shared dialog).
	let formFillOpen = $state(false);
	let formFillStep = $state<OnboardingStep | null>(null);

	function openFormFill(step: OnboardingStep): void {
		formFillStep = step;
		formFillOpen = true;
	}

	// Persist the answers via the per-item form-response endpoint, then refresh so the step advances.
	// Throwing on failure keeps the dialog open (its submit() swallows the close on reject).
	async function saveFormFill(values: Record<string, unknown>): Promise<void> {
		const step = formFillStep;
		if (!step || !step.request_uid || !step.request_item_uid) return;
		const body = new FormData();
		body.append('req_uid', step.request_uid);
		body.append('item_uid', step.request_item_uid);
		body.append('answers', JSON.stringify(values));
		const res = await fetch('/onboarding?/saveForm', { method: 'POST', body });
		const result = deserialize(await res.text());
		if (result.type === 'success') {
			step.formAnswers = values;
			toast.success('Progress saved');
			return;
		}
		throw new Error(
			result.type === 'failure'
				? String(result.data?.error ?? 'That didn’t work.')
				: 'That didn’t work. Please try again.'
		);
	}

	async function submitFormFill(values: Record<string, unknown>): Promise<void> {
		const step = formFillStep;
		if (!step || !step.request_uid || !step.request_item_uid) return;
		const body = new FormData();
		body.append('req_uid', step.request_uid);
		body.append('item_uid', step.request_item_uid);
		body.append('answers', JSON.stringify(values));
		const res = await fetch('/onboarding?/submitForm', { method: 'POST', body });
		const result = deserialize(await res.text());
		if (result.type === 'success') {
			await refreshJourney();
			return;
		}
		throw new Error(
			result.type === 'failure'
				? String(result.data?.error ?? 'That didn’t work.')
				: 'That didn’t work. Please try again.'
		);
	}

	// Identity verification (verify_identity): the client photographs their document and takes a
	// selfie in-app. The verdict is the API's — we never decide an outcome from the browser.
	let idvOpen = $state(false);
	let idvStep = $state<OnboardingStep | null>(null);
	let idvError = $state('');
	/** Whether asking for a person to check the documents is on offer. Doing the check digitally
	 *  needs no choice recorded; asking for a person does, and it only moves one way. */
	let idvManualOffered = $state(false);
	/** The client has asked for a person, and the step is now their adviser's. */
	let idvHandedOver = $state(false);
	let idvHandingOver = $state(false);
	/** The one-time phone link, while the desktop is waiting for the photos to be taken there. */
	let idvPhone = $state<{ url: string; qrSvg: string } | null>(null);
	let idvPhoneBusy = $state(false);
	let idvPhoneError = $state('');
	/** What a manual check ASKS FOR: a government-issued photo ID and a proof of address. The
	 *  digital path captures both inside the in-app capture itself; the manual one had no
	 *  equivalent, so choosing it left the two documents to be collected by email — off the record,
	 *  and with nothing on the journey to say whether they had arrived. The spec (title, what counts)
	 *  comes from the API so re-wording it reaches a client who was asked last week. */
	type IdentityDoc = {
		key: string;
		title: string;
		guidance: string;
		format_hint: string;
		request_uid: string;
		item_uid: string;
		status: string;
	};
	let idvDocs = $state<IdentityDoc[]>([]);
	let idvDocFiles = $state<Record<string, File[]>>({});
	let idvDocErrors = $state<Record<string, string>>({});
	let idvDocBusy = $state<string | null>(null);
	const idvDocsOutstanding = $derived(idvDocs.filter((d) => d.status === 'pending').length);

	/** The in-portal photo capture for the open identity step. Where the API offers one, the client
	 *  takes the photos here; where it does not (no entry in `data.identityCapture`), a person
	 *  checks the documents instead. */
	const idvCapture = $derived(idvStep ? (data.identityCapture?.[idvStep.key] ?? null) : null);
	/** The capture as it stood when the photos were sent — attempt, status, and its items, so new
	 *  items at the SAME attempt (a technical failure, or a refused photo asked for again) are not
	 *  mistaken for the try already sent still pending. */
	let idvSentFrom = $state<{ attempt: number; status: string; itemsKey: string } | null>(null);
	/** A result is on its way: poll for it. */
	const idvAwaiting = $derived(
		idvCapture
			? idvCapture.status === 'submitted' ||
					idvCapture.status === 'review' ||
					(idvSentFrom?.attempt === idvCapture.attempt &&
						idvSentFrom.status === idvCapture.status &&
						idvSentFrom.itemsKey === captureItemsKey(idvCapture))
			: false
	);

	/** Name the document and send the photos the try needs, in one request: the API holds them
	 *  until the identity check is finished and sends them on itself, so they never go through a
	 *  request item. Resolves once the capture is re-read, so the dialog moves straight on to the
	 *  check running. */
	async function finishIdentityCapture(
		documentType: IdentityDocumentType,
		photos: Partial<Record<IdentityCaptureKey, File>>
	): Promise<string | null> {
		if (!idvStep) return 'This check cannot be sent yet. Close this and try again.';
		const from = idvCapture
			? {
					attempt: idvCapture.attempt,
					status: idvCapture.status,
					itemsKey: captureItemsKey(idvCapture)
				}
			: null;
		const body = new FormData();
		body.append('step_key', idvStep.step_id);
		if (idvStep.case_uid) body.append('case_uid', idvStep.case_uid);
		body.append('document_type', documentType);
		for (const key of IDENTITY_CAPTURE_KEYS) {
			const file = photos[key];
			if (file) body.append(key, file, file.name);
		}
		let failure: string | null;
		try {
			const res = await fetch('/onboarding?/identityCaptureSubmit', { method: 'POST', body });
			failure = submitError(deserialize(await res.text()));
		} catch {
			// The request never landed: the client still holds the photos and sends them again.
			failure = SUBMIT_FAILED;
		}
		if (failure) return failure;
		idvSentFrom = from;
		await refreshJourney();
		return null;
	}

	async function phoneAction(
		name: 'identityPhone' | 'identityPhoneCancel'
	): Promise<{ type: string; data?: Record<string, unknown> }> {
		if (!idvStep) return { type: 'failure', data: { error: 'This check cannot be sent yet.' } };
		const body = new FormData();
		body.append('step_key', idvStep.step_id);
		if (idvStep.case_uid) body.append('case_uid', idvStep.case_uid);
		const res = await fetch(`/onboarding?/${name}`, { method: 'POST', body });
		return deserialize(await res.text()) as { type: string; data?: Record<string, unknown> };
	}

	/** Ask for a one-time link and show it. A link already on screen is replaced: the API
	 *  withdraws the previous one. */
	async function startPhone(): Promise<void> {
		if (!idvStep || idvPhoneBusy) return;
		idvPhoneBusy = true;
		idvPhoneError = '';
		try {
			const result = await phoneAction('identityPhone');
			if (result.type === 'success' && result.data?.url && result.data?.qr_svg) {
				idvPhone = { url: String(result.data.url), qrSvg: String(result.data.qr_svg) };
				return;
			}
			idvPhoneError =
				result.type === 'failure'
					? String(result.data?.error ?? 'That didn’t work.')
					: 'That didn’t work.';
		} finally {
			idvPhoneBusy = false;
		}
	}

	/** The photos will be taken here. The link stops working. */
	async function cancelPhone(): Promise<void> {
		if (idvPhoneBusy) return;
		idvPhoneBusy = true;
		idvPhoneError = '';
		try {
			const result = await phoneAction('identityPhoneCancel');
			if (result.type === 'success') {
				idvPhone = null;
				return;
			}
			idvPhoneError =
				result.type === 'failure'
					? String(result.data?.error ?? 'That didn’t work.')
					: 'That didn’t work.';
		} finally {
			idvPhoneBusy = false;
		}
	}

	/** Hand the check to a person. Recorded server-side, the step moves to the adviser, and the two
	 *  documents a manual check is made of come back as the things to send. */
	async function chooseManualIdentity(): Promise<void> {
		if (!idvStep) return;
		idvHandingOver = true;
		idvError = '';
		try {
			const body = new FormData();
			body.append('step_key', idvStep.step_id);
			// The case as well as the step: a client with two journeys holds `idv` in both.
			if (idvStep.case_uid) body.append('case_uid', idvStep.case_uid);
			const res = await fetch('/onboarding?/identityManual', { method: 'POST', body });
			const result = deserialize(await res.text());
			if (result.type === 'success') {
				idvHandedOver = true;
				idvDocs = (result.data?.documents ?? []) as IdentityDoc[];
				try {
					await refreshJourney();
				} finally {
					// The choice hands the step to the adviser. Close only after the refreshed
					// journey has removed it from the client's work, so the dialog cannot be
					// re-opened by the same render that handles this click.
					idvOpen = false;
				}
				return;
			}
			idvError =
				result.type === 'failure'
					? String(result.data?.error ?? 'That didn’t work.')
					: 'That didn’t work.';
		} finally {
			idvHandingOver = false;
		}
	}

	/** What was already asked for, so closing the dialog and coming back keeps the two slots. */
	async function loadIdentityDocuments(step: OnboardingStep): Promise<boolean> {
		const body = new FormData();
		body.append('step_key', step.step_id);
		if (step.case_uid) body.append('case_uid', step.case_uid);
		const res = await fetch('/onboarding?/identityDocuments', { method: 'POST', body });
		const result = deserialize(await res.text());
		if (result.type !== 'success') return false;
		const documents = (result.data?.documents ?? []) as IdentityDoc[];
		// FileDropzone has a bindable [] fallback, so Svelte refuses an undefined value at mount.
		// Seed every keyed array BEFORE exposing the rows that render those bindings.
		idvDocFiles = {
			...idvDocFiles,
			...Object.fromEntries(
				documents.map((document) => [document.item_uid, idvDocFiles[document.item_uid] ?? []])
			)
		};
		idvDocs = documents;
		return result.data?.chosen === true;
	}

	/** Send one of the two. The status comes back from a re-read, not from assuming it worked. */
	async function uploadIdentityDoc(doc: IdentityDoc): Promise<void> {
		const file = idvDocFiles[doc.item_uid]?.[0];
		if (!file || idvDocBusy) return;
		idvDocBusy = doc.item_uid;
		idvDocErrors = { ...idvDocErrors, [doc.item_uid]: '' };
		try {
			const err = await postItem(doc.request_uid, doc.item_uid, file);
			if (err) {
				idvDocErrors = { ...idvDocErrors, [doc.item_uid]: err };
				return;
			}
			idvDocFiles = { ...idvDocFiles, [doc.item_uid]: [] };
			if (idvStep) await loadIdentityDocuments(idvStep);
		} finally {
			idvDocBusy = null;
		}
	}

	async function openIdentity(step: OnboardingStep): Promise<void> {
		idvStep = step;
		idvError = '';
		idvManualOffered = false;
		idvHandedOver = false;
		idvDocs = [];
		idvDocFiles = {};
		idvDocErrors = {};
		idvSentFrom = null;
		idvPhone = null;
		idvPhoneError = '';
		idvOpen = true;
		// Already chosen the manual path? Then the two documents are the whole of this dialog, and
		// offering the capture again would re-open a choice the client has already made.
		if (await loadIdentityDocuments(step)) {
			idvHandedOver = true;
			return;
		}
		// The capture comes with its own manual offer; a step with no capture at all has no digital
		// path, so a person is the only way through it.
		const capture = data.identityCapture?.[step.key];
		idvManualOffered = capture ? capture.manual_offered === true : true;
	}

	// The verdict arrives at the API, not here. Ask again while the photos are with the check,
	// including after the dialog is closed, and faster while a phone link is up. A review is a
	// person, so that wait does not keep asking.
	const identityWait = $derived(
		identityRefresh({
			captures: data.identityCapture,
			dialogOpen: idvOpen,
			dialogMachineWait: idvAwaiting && idvCapture?.status !== 'review',
			phoneLink: idvPhone != null
		})
	);

	// Close on success. The loader's fresh data is the only thing that can tell us the check was
	// settled, so the dialog dismisses itself rather than asking the client to.
	$effect(() => {
		if (!idvOpen || !idvStep) return;
		const current = cases
			.flatMap((c) => c.steps)
			.find((s) => s.step_id === idvStep?.step_id && s.step_type === 'verify_identity');
		if (current && current.status === 'fulfilled') idvOpen = false;
	});

	// Retry for a failed load — pending state on the banner's button.
	let retrying = $state(false);
	async function retry(): Promise<void> {
		if (retrying) return;
		retrying = true;
		try {
			await refreshJourney();
		} finally {
			retrying = false;
		}
	}

	function startStep(c: OnboardingCase, s: OnboardingStep): void {
		if (s.step_type === 'fill_form') openFormFill(s);
		else if (s.step_type === 'accept_terms') void openAgree(s);
		else if (s.step_type === 'upload_document') openUpload(s);
		else if (s.step_type === 'verify_identity') void openIdentity(s);
	}

	// Per-case progress across the steps that actually apply (skipped/cancelled excluded).
	function caseProgress(c: OnboardingCase): { done: number; total: number } {
		const live = c.steps.filter((s) => s.status !== 'skipped' && s.status !== 'cancelled');
		return { done: live.filter((s) => s.status === 'fulfilled').length, total: live.length };
	}

	/** A step paired with the case that owns it — starting one needs both. */
	type Pair = { c: OnboardingCase; s: OnboardingStep };

	/** Every step the client can act on RIGHT NOW, in list order. The hero, its button and the
	 *  "Ready for you" group all read this one list: derived separately, the headline action and
	 *  the list beneath it could name different steps. */
	const readyNow = $derived<Pair[]>(
		cases.flatMap((c) => c.steps.filter((s) => isActionable(c, s)).map((s) => ({ c, s })))
	);

	/** Live steps that are NOT the client's to do yet — held behind earlier work. Kept apart from
	 *  the ready ones because mixing them was the page's central confusion: a client scanning one
	 *  undifferentiated list could not tell what they were being asked to do. */
	const heldSteps = $derived<Pair[]>(
		cases.flatMap((c) =>
			c.steps
				.filter(
					(s) =>
						s.status !== 'fulfilled' &&
						s.status !== 'cancelled' &&
						s.status !== 'skipped' &&
						!isActionable(c, s)
				)
				.map((s) => ({ c, s }))
		)
	);

	const doneSteps = $derived<Pair[]>(
		cases.flatMap((c) => c.steps.filter((s) => s.status === 'fulfilled').map((s) => ({ c, s })))
	);

	const readyForms = $derived(readyNow.filter((p) => p.s.step_type === 'fill_form'));
	const firstReady = $derived<Pair | null>(readyNow[0] ?? null);
	/** Several forms ready at once share one hero ("complete the remaining forms"). The button
	 *  still opens the first of them — a press that did nothing left the client on this page. */
	const nextUp = $derived<Pair | null>(readyForms.length > 1 ? null : firstReady);
	const heroNext = $derived(
		readyForms.length > 1
			? {
					step_type: 'fill_form',
					title: 'Complete the remaining forms',
					index: 1,
					total: readyNow.length
				}
			: nextUp
				? {
						step_type: nextUp.s.step_type,
						title: nextUp.s.title ?? 'Your next step',
						index: 1,
						total: readyNow.length
					}
				: null
	);
	/** The milestone the whole journey is at — what the hero shows a client with nothing to do.
	 *  The title, not the raw status: a journey started before its milestones were named still
	 *  carries the slug ("Recorded"), and the stage list already replaces that. */
	const currentStage = $derived.by(() => {
		const stage = stages.find((s) => s.status === 'current');
		return stage ? stageTitle(stage) : null;
	});
	/** The journey's own verdict, not the client's step count: their list grows as the journey
	 *  advances, so "all set" has to be a statement about the application. No rollup (an older API)
	 *  ⇒ null, and the hero falls back to the counts. */
	const journeyComplete = $derived(stages.length ? stages.every((s) => s.status === 'done') : null);

	/** Why a held step is not actionable. `waiting` is a step already in flight (a result is coming
	 *  back to us); anything else is simply held behind earlier work. Neither is a fault, and the
	 *  copy says so — the bare "Not yet" badge it replaces read as a refusal. */
	function holdReason(s: OnboardingStep): string {
		if (identitySent(s)) return 'Photos sent. We’re checking them.';
		if (s.status === 'waiting') return 'In progress. Waiting for the result.';
		return 'Opens once the earlier steps are finished.';
	}

	/** An identity step whose photos are with the check: it waits on the result, not on the client. */
	function identitySent(s: OnboardingStep): boolean {
		return s.step_type === 'verify_identity' && photosSent(data.identityCapture?.[s.key]);
	}

	/** Nothing is the client's to do, and what they are waiting on is their identity check. */
	const checkingIdentity = $derived(
		readyNow.length === 0 && heldSteps.some((pair) => identitySent(pair.s))
	);

	const STAGE_COPY: Record<string, string> = {
		details: 'Your adviser gathers and checks the information needed for your application.',
		sign: 'Check what you’re applying for, then read and agree to the required documents.',
		verification: 'Confirm your identity so your application can move forward safely.',
		setup: 'Your adviser completes the final checks and opens your account.',
		complete: 'Your application is complete and your new account is ready.',
		fact_find: 'Tell us about yourself, your finances and how you feel about risk.',
		recorded: 'Your adviser adds their notes. Nothing is needed from you for this.',
		declared: 'Read the declaration and confirm it.',
		recommendation: 'Your adviser prepares the recommendation.',
		report: 'Your suitability report is prepared for you to read.',
		acknowledged: 'Read the report and confirm you have seen it.'
	};
	/** Labels for the seeded advice milestones. A journey that has authored its own label wins;
	 *  these replace the bare status slug ("Recorded", "Declared") a running journey still carries. */
	const STAGE_LABEL: Record<string, string> = {
		fact_find: 'Your details',
		recorded: 'With your adviser',
		declared: 'Your declaration',
		recommendation: 'Recommendation',
		report: 'Your report',
		acknowledged: 'Your acknowledgement'
	};

	function stageKey(key: string): string {
		return key.includes(':') ? (key.split(':').at(-1) ?? key) : key;
	}
	function humanised(slug: string): string {
		const text = slug.replaceAll('_', ' ').trim();
		return text ? text.charAt(0).toUpperCase() + text.slice(1) : slug;
	}
	function stageTitle(stage: (typeof stages)[number]): string {
		const key = stageKey(stage.key);
		const named = STAGE_LABEL[key];
		return named && stage.label === humanised(key) ? named : stage.label;
	}
	function stageBlurb(stage: (typeof stages)[number]): string {
		return (
			stage.summary ||
			STAGE_COPY[stageKey(stage.key)] ||
			(onboardingJourney
				? 'Your application moves forward through this stage.'
				: 'This is part of the journey.')
		);
	}

	function stageState(status: 'done' | 'current' | 'upcoming'): string {
		if (status === 'done') return 'Complete';
		if (status === 'current') return 'In progress';
		return 'Coming up';
	}

	/** Any active journey surface — stages, cases, or the review dossier. */
	const hasJourney = $derived(cases.length > 0 || stages.length > 0 || showReview);
	const onboardingJourney = $derived(
		data.flowCode === 'onboarding' ||
			stages.some((stage) => stage.key === 'details' || stage.key.endsWith(':details'))
	);
	const pageTitle = $derived(
		onboardingJourney ? 'Onboarding' : data.flowName?.trim() || 'Your journey'
	);
	const showAccounts = $derived(onboardingJourney || Boolean(review && review.accounts.length > 0));

	const overallProgress = $derived(
		cases.reduce(
			(acc, c) => {
				const p = caseProgress(c);
				return { done: acc.done + p.done, total: acc.total + p.total };
			},
			{ done: 0, total: 0 }
		)
	);

	const youRows = $derived.by(() => {
		if (!review) return [] as { label: string; value: string }[];
		const you = review.you;
		return [
			{ label: 'Name', value: you.name },
			{ label: 'Email', value: you.email },
			{ label: 'Date of birth', value: you.dob },
			{ label: 'Mobile', value: formatPhone(you.mobile) },
			{ label: 'Phone', value: formatPhone(you.phone) },
			{ label: 'Nationality', value: formatList(you.nationality) },
			{ label: 'Tax residency', value: formatList(you.tax_residency) },
			{
				label: you.national_identifier_type
					? titleCase(you.national_identifier_type)
					: 'National identifier',
				value: you.national_identifier
			},
			{ label: 'Address', value: formatAddress(you.address) },
			{ label: 'Bank account', value: formatBank(you.bank_account) },
			{
				label: 'Flexi-access / crystallised benefits',
				value: you.flexi_access_taken ? 'Yes' : null
			}
		].filter((r): r is { label: string; value: string } => Boolean(r.value));
	});
</script>

<svelte:head><title>{pageTitle}</title></svelte:head>

<RefreshWhile
	active={identityWait != null}
	interval={identityWait === 'phone' ? REFRESH_WHILE.phoneCapture : REFRESH_WHILE.identityCapture}
	onpoll={() => invalidate('app:onboarding')}
/>

<!-- One row, three postures. Titles WRAP rather than truncate: "Terms of business — discretionary
     management" clipped to one line on a phone told a client nothing about what they were agreeing
     to, and these are the only rows on the page they are asked to act on. -->
{#snippet stepRow(pair: Pair, mode: 'ready' | 'held' | 'done')}
	{@const s = pair.s}
	{@const Icon = stepIcon(s.step_type)}
	<li class="flex items-center gap-4 px-5 py-3.5">
		<div
			class="flex size-9 shrink-0 items-center justify-center rounded-lg {mode === 'done'
				? 'bg-success/10 text-success'
				: mode === 'ready'
					? 'bg-brand/10 text-brand'
					: 'bg-muted text-muted-foreground'}"
		>
			{#if mode === 'done'}
				<CheckIcon class="size-4" />
			{:else}
				<Icon class="size-4" />
			{/if}
		</div>
		<div class="min-w-0 flex-1">
			<p class="text-sm font-medium break-words">{s.title ?? 'Step'}</p>
			<p class="text-muted-foreground text-xs">
				{#if mode === 'held'}
					{holdReason(s)}
				{:else}
					{STEP_KIND_LABEL[s.step_type] ?? 'Step'}{#if s.required && mode === 'ready'}
						· Required{/if}
				{/if}
			</p>
		</div>
		{#if mode === 'done'}
			<StatusBadge tone="success" class="shrink-0">Done</StatusBadge>
		{:else if mode === 'ready'}
			<Button size="sm" class="shrink-0" onclick={() => startStep(pair.c, s)}>
				{#if s.step_type === 'accept_terms'}
					Review &amp; agree
				{:else if s.step_type === 'upload_document'}
					Upload
				{:else if s.step_type === 'verify_identity'}
					Verify identity
				{:else}
					Complete
				{/if}
			</Button>
		{/if}
	</li>
{/snippet}

<div class="space-y-6">
	<!-- Page header. Deliberately plain: the hero below states the action and the progress, and
	     saying either of them twice in the first screenful buries the one that matters. -->
	<div>
		<h1 class="text-3xl font-semibold tracking-tight">{pageTitle}</h1>
		<p class="text-muted-foreground mt-1">
			{#if onboardingJourney}
				{review?.firm_name ? `Your application with ${review.firm_name}` : 'Your application'}
			{:else if review?.firm_name}
				With {review.firm_name}
			{/if}
		</p>
	</div>

	{#if data.loadError}
		<LoadErrorBanner
			message="We couldn’t load this journey. Please try again."
			retryLabel={retrying ? 'Retrying…' : 'Retry'}
			onRetry={retry}
		/>
	{:else if !hasJourney}
		<!-- No journey at all — keep a persistent, reassuring state so the page never reads as broken. -->
		<EmptyState
			icon={CircleCheckIcon}
			title="Nothing to onboard"
			description="You have no onboarding tasks. Your adviser will let you know if anything is needed."
		/>
	{:else}
		<!-- What to do next, or who the application is waiting on — before any of the review
		     sections, because a client arrives here to act, not to browse. Its button starts the
		     same step the "Ready for you" group lists first. -->
		<OnboardingNextAction
			next={heroNext}
			done={overallProgress.done}
			total={overallProgress.total}
			stageLabel={currentStage}
			{journeyComplete}
			waitingTitle={checkingIdentity ? PHOTOS_SENT_TITLE : null}
			waitingDetail={checkingIdentity ? PHOTOS_SENT_DETAIL : null}
			onstart={() => nextUp && startStep(nextUp.c, nextUp.s)}
		/>

		<!-- ONE CONTINUOUS PAGE. This replaced a four-tab strip (`?section=`) over the same content,
		     which put "what am I actually applying for" one click away from a client who did not know
		     the click was there — and the accounts tab is the whole point of a review: it is what they
		     are agreeing to when they sign the declarations. The reading order is what you are getting,
		     then what happens next, then the facts behind it, with what you must DO pinned alongside.
		     Co-applicants stay name/type/role only, and no tab ever gated fulfilment. -->
		<div class="grid items-start gap-4 lg:grid-cols-[minmax(0,1.35fr)_minmax(18rem,0.65fr)]">
			<div class="space-y-4">
				{#if showAccounts}
					<Card class="gap-0 py-0">
						<div
							class="flex flex-wrap items-baseline justify-between gap-3 border-b px-5 py-4 sm:px-6"
						>
							<div>
								<h2 class="text-xl font-semibold tracking-tight">What you’re applying for</h2>
								<p class="text-muted-foreground mt-1 text-sm">
									Please check this is right before you agree to any documents.
								</p>
							</div>
							{#if review && review.accounts.length}
								<span class="text-muted-foreground text-xs tabular-nums">
									{review.accounts.length === 1
										? '1 account'
										: `${review.accounts.length} accounts`}
								</span>
							{/if}
						</div>
						<div class="space-y-4 px-5 py-5 sm:px-6">
							{#if review && review.accounts.length}
								{#each review.accounts as account (account.uid)}
									{@const rows = accountRows(account)}
									{@const fees = feeSummaries(account)}
									<section class="rounded-lg border">
										<header
											class="bg-muted/40 flex flex-wrap items-center justify-between gap-2 border-b px-4 py-3"
										>
											<h3 class="min-w-0 truncate font-semibold">{account.name}</h3>
											<div class="flex shrink-0 items-center gap-2">
												{#if account.has_transfer}
													<StatusBadge tone="info">Transfer in</StatusBadge>
												{/if}
												<StatusBadge tone="neutral">{account.product}</StatusBadge>
											</div>
										</header>
										<div class="px-4 py-3">
											{#if rows.length}
												<dl class="grid gap-3 text-sm sm:grid-cols-2">
													{#each rows as row (row.label)}
														<div>
															<dt class="text-muted-foreground text-xs">{row.label}</dt>
															<dd class="mt-0.5 break-words">{row.value}</dd>
														</div>
													{/each}
												</dl>
											{:else}
												<p class="text-muted-foreground text-sm">
													Your adviser hasn’t added the details for this account yet.
												</p>
											{/if}
											<!-- Charges are their own block, not a "Fee packages" row: a client agreeing to
											     this application needs the figure and how often it is charged, which is two
											     or three lines per charge and does not fit a one-line definition row. -->
											{#if fees.length}
												<div class="mt-3 border-t pt-3">
													<p class="text-muted-foreground text-xs">What you’ll pay</p>
													<ul class="mt-1 space-y-2 text-sm">
														<!-- Keyed on the position as well as the name: two packages can legitimately
														     share a name, and Svelte throws on a duplicate key and blanks the
														     subtree (the trap `$lib/onboarding.ts::stepKey` exists for). -->
														{#each fees as fee, i (`${fee.name}:${i}`)}
															<li>
																<div class="flex flex-wrap items-baseline justify-between gap-x-3">
																	<span class="font-medium">{fee.name}</span>
																	{#if fee.charge || fee.frequency}
																		<span class="tabular-nums">
																			{fee.charge}{#if fee.charge && fee.frequency}
																				<span class="text-muted-foreground">
																					· {fee.frequency}</span
																				>
																			{:else if fee.frequency}
																				<span class="text-muted-foreground">{fee.frequency}</span>
																			{/if}
																		</span>
																	{/if}
																</div>
																{#if fee.bands.length}
																	<ul class="text-muted-foreground mt-0.5 space-y-0.5 text-xs">
																		{#each fee.bands as band (band)}
																			<li class="tabular-nums">{band}</li>
																		{/each}
																	</ul>
																{/if}
																{#if fee.notes.length}
																	<p class="text-muted-foreground mt-0.5 text-xs">
																		{fee.notes.join(' · ')}
																	</p>
																{/if}
															</li>
														{/each}
													</ul>
												</div>
											{/if}
											{#if account.beneficiaries?.length}
												<div class="mt-3 border-t pt-3">
													<p class="text-muted-foreground text-xs">Beneficiaries</p>
													<ul class="mt-1 space-y-0.5 text-sm">
														{#each account.beneficiaries as b, i (i)}
															<li>
																{[b.first_name, b.surname].filter(Boolean).join(' ')}
																{#if b.portion != null}
																	<span class="text-muted-foreground tabular-nums">
																		· {b.portion}%
																	</span>
																{/if}
															</li>
														{/each}
													</ul>
												</div>
											{/if}
										</div>
									</section>
								{/each}
							{:else}
								<p class="text-muted-foreground text-sm">
									No accounts have been added to this application yet.
								</p>
							{/if}
						</div>
					</Card>
				{/if}

				<!-- Where the application has got to. Kept below the accounts: progress matters, but not
				     before the thing being progressed. -->
				<Card class="gap-0 py-0">
					<div class="border-b px-5 py-4 sm:px-6">
						<h2 class="text-xl font-semibold tracking-tight">Your journey</h2>
						<p class="text-muted-foreground mt-1 text-sm">
							{onboardingJourney
								? 'Follow your application from information gathering through to account opening.'
								: 'These are the stages of this journey.'}
						</p>
					</div>
					<ol
						class="px-5 py-2 sm:px-6"
						aria-label={onboardingJourney ? 'Onboarding journey' : 'Your journey'}
					>
						<!-- Index as well as the key: two journeys of one flow used to repeat `onboarding:details`,
						     and a repeated key throws here and blanks the page after login. -->
						{#each stages as stage, index (`${stage.key}:${index}`)}
							<li
								class="relative grid grid-cols-[2.5rem_minmax(0,1fr)] gap-3 py-4 {stage.status ===
								'current'
									? 'text-foreground'
									: 'text-muted-foreground'}"
								aria-current={stage.status === 'current' ? 'step' : undefined}
							>
								{#if index < stages.length - 1}
									<span
										class="absolute top-12 bottom-0 left-[1.18rem] w-px {stage.status === 'done'
											? 'bg-success/50'
											: 'bg-border'}"
										aria-hidden="true"
									></span>
								{/if}
								<span
									class="relative z-10 flex size-9 items-center justify-center rounded-full border text-sm font-semibold {stage.status ===
									'done'
										? 'border-success bg-success text-success-foreground'
										: stage.status === 'current'
											? 'border-brand bg-brand text-brand-foreground ring-brand/15 ring-4'
											: 'border-border bg-background text-muted-foreground'}"
								>
									{#if stage.status === 'done'}
										<CheckIcon class="size-4" aria-hidden="true" />
									{:else}
										{index + 1}
									{/if}
								</span>
								<div class="min-w-0 pt-0.5">
									<div class="flex flex-wrap items-center justify-between gap-2">
										<h3 class="text-foreground font-semibold">{stageTitle(stage)}</h3>
										<StatusBadge
											tone={stage.status === 'done'
												? 'success'
												: stage.status === 'current'
													? 'info'
													: 'neutral'}>{stageState(stage.status)}</StatusBadge
										>
									</div>
									<p class="mt-1 max-w-xl text-sm leading-relaxed">
										{stageBlurb(stage)}
									</p>
									{#if stage.status === 'current' && (readyNow.length || !/nothing is needed/i.test(stageBlurb(stage)))}
										<p class="text-foreground mt-2 text-sm font-medium">
											{#if readyNow.length}
												{readyNow.length === 1
													? 'You have one action ready now.'
													: `You have ${readyNow.length} actions ready now.`}
											{:else}
												Nothing is needed from you. We’ll let you know when it is.
											{/if}
										</p>
									{/if}
								</div>
							</li>
						{/each}
					</ol>
				</Card>

				<!-- Read-only parity with the legacy applicant step. Sparse detail does not block terms. -->
				{#if onboardingJourney}
					<Card class="gap-0 py-0">
						<div class="border-b px-5 py-4 sm:px-6">
							<h2 class="text-lg leading-snug font-semibold">Your details</h2>
							<p class="text-muted-foreground mt-1 text-sm">
								Please check the personal details your adviser has entered.
							</p>
						</div>
						<div class="px-5 py-5 sm:px-6">
							{#if showReview && youRows.length}
								<dl class="grid gap-4 text-sm sm:grid-cols-2 lg:grid-cols-3">
									{#each youRows as row (row.label)}
										<div class={row.label === 'Address' ? 'sm:col-span-2 lg:col-span-3' : ''}>
											<dt class="text-muted-foreground text-xs">{row.label}</dt>
											<dd class="mt-0.5 break-words">{row.value}</dd>
										</div>
									{/each}
								</dl>
							{:else}
								<p class="text-muted-foreground text-sm">
									Your adviser has not recorded personal details yet.
								</p>
							{/if}
						</div>
					</Card>
				{/if}

				<!-- Name / type / role only — never AML or identity status (legacy overview defect). -->
				{#if showReview && review && review.applicants.length > 1}
					<Card class="gap-0 py-0">
						<div class="border-b px-5 py-4 sm:px-6">
							<h2 class="text-lg leading-snug font-semibold">
								{onboardingJourney ? 'People on this application' : 'People on this journey'}
							</h2>
							<p class="text-muted-foreground mt-1 text-sm">
								{onboardingJourney
									? 'Who is named on the application. Compliance checks for other people are not shown here.'
									: 'Who this journey is for.'}
							</p>
						</div>
						<div class="px-5 py-5 sm:px-6">
							<ul class="divide-border divide-y rounded-lg border text-sm">
								{#each review.applicants as person (person.uid)}
									<li class="flex flex-wrap items-center justify-between gap-2 px-4 py-3">
										<div class="min-w-0">
											<p class="truncate font-medium">
												{person.name}{#if person.is_you}
													<span class="text-muted-foreground font-normal"> (you)</span>
												{/if}
											</p>
											<p class="text-muted-foreground text-xs">
												{titleCase(person.type)} · {roleLabel(person.role)}
											</p>
										</div>
									</li>
								{/each}
							</ul>
						</div>
					</Card>
				{/if}
			</div>

			<div class="space-y-4 lg:sticky lg:top-24">
				<Card class="gap-0 py-0">
					<div class="border-b px-5 py-4">
						<h2 class="text-lg font-semibold">What you need to do</h2>
						<p class="text-muted-foreground mt-1 text-sm">
							Required actions appear here as soon as they are ready.
						</p>
					</div>
					{#if readyNow.length}
						<ol class="divide-y">
							{#each readyNow as pair (pair.s.key)}
								{@render stepRow(pair, 'ready')}
							{/each}
						</ol>
					{:else}
						<div class="px-5 py-5">
							<div class="bg-success/8 text-success flex items-start gap-3 rounded-lg p-3">
								<CircleCheckIcon class="mt-0.5 size-5 shrink-0" aria-hidden="true" />
								<div>
									<p class="text-foreground text-sm font-medium">You’re up to date</p>
									<p class="text-muted-foreground mt-0.5 text-sm">
										{onboardingJourney
											? 'Your adviser is moving the application forward. We’ll let you know when there is more to do.'
											: 'Your adviser is working on this. We’ll let you know when there is more to do.'}
									</p>
								</div>
							</div>
						</div>
					{/if}
				</Card>

				{#if doneSteps.length || heldSteps.length}
					<Card class="gap-0 py-0">
						<div class="border-b px-5 py-4">
							<h2 class="text-lg font-semibold">Your steps</h2>
							<p class="text-muted-foreground mt-1 text-sm">
								{doneSteps.length} complete · {heldSteps.length + readyNow.length} remaining
							</p>
						</div>
						<ol class="divide-y">
							{#each doneSteps as pair (pair.s.key)}
								{@render stepRow(pair, 'done')}
							{/each}
							{#each heldSteps as pair (pair.s.key)}
								{@render stepRow(pair, 'held')}
							{/each}
						</ol>
					</Card>
				{/if}
			</div>
		</div>
	{/if}
</div>

<!-- Upload dialog (upload_document) -->
<Dialog.Root
	bind:open={uploadOpen}
	onOpenChange={(o) => {
		if (!o) {
			files = [];
			uploadStep = null;
			uploadError = '';
		}
	}}
>
	<Dialog.Content class="sm:max-w-lg">
		<Dialog.Header>
			<Dialog.Title>{uploadStep?.title ?? 'Upload a document'}</Dialog.Title>
			<!-- No list of file types: a request can take fewer than the default (a signed W-9 or W-8
			     is a PDF or an image), and the refusal names what this one takes. -->
			<Dialog.Description>Upload the requested document, up to 25 MB.</Dialog.Description>
		</Dialog.Header>
		<div class="space-y-2 py-2">
			<FileDropzone
				bind:files
				rich
				title="Upload document"
				onreject={(reason) => (uploadError = reason)}
			/>
			{#if uploadError}
				<p class="text-destructive text-xs" role="alert">{uploadError}</p>
			{/if}
		</div>
		<Dialog.Footer>
			<Button variant="ghost" onclick={() => (uploadOpen = false)}>Cancel</Button>
			<Button disabled={files.length === 0 || uploading} onclick={submitUpload}>
				{uploading ? 'Uploading…' : 'Upload'}
			</Button>
		</Dialog.Footer>
	</Dialog.Content>
</Dialog.Root>

<!-- Accept-terms dialog: the client reads the actual document, THEN agrees. -->
<Dialog.Root
	bind:open={agreeOpen}
	onOpenChange={(o) => {
		if (!o) {
			agreeStep = null;
			agreeError = '';
		}
	}}
>
	<Dialog.Content class="flex max-h-[90svh] flex-col overflow-hidden sm:max-w-2xl">
		<Dialog.Header>
			{#if termsAll.length > 1 && agreeIndex > 0}
				<!-- Position in the run, so a client agreeing five declarations in one sitting knows how
				     much is left rather than discovering a sixth each time they finish one. -->
				<p class="text-muted-foreground text-xs font-medium tracking-wide uppercase">
					Document {agreeIndex} of {termsAll.length}
				</p>
			{/if}
			<Dialog.Title>{agreeStep?.title ?? 'Review & agree'}</Dialog.Title>
			<!-- No file name: the stored document's name is the firm's own filing (often the uploaded
			     file), and a client agreeing to their client agreement has no use for it. The title
			     above already says what they are being asked to agree to. -->
			<Dialog.Description>Please read this in full before agreeing.</Dialog.Description>
		</Dialog.Header>

		<!-- The DOCUMENT is what gives way when the dialog meets its `max-h`, never the footer. The
		     body is the shrinkable column (`min-h-0`, as request-dialog.svelte does), so a zoomed
		     laptop or a short phone still shows the Agree button under the page it has to read. -->
		<div class="flex min-h-0 flex-col py-2">
			{#if agreeLoading}
				<!-- A skeleton of the document rather than the words "Loading document…": the shape of
				     what is coming reads as progress, and the wait recurs once per declaration. -->
				<div class="space-y-2.5 rounded border p-4" role="status" aria-label="Loading document">
					<Skeleton class="h-5 w-2/5" />
					{#each [0, 1, 2, 3, 4, 5, 6, 7] as line (line)}
						<Skeleton class="h-3 {line % 4 === 3 ? 'w-3/5' : 'w-full'}" />
					{/each}
				</div>
			{:else if agreeDoc?.content}
				<!-- A terms declaration: inline HTML rendered for the client to read in-app. The HTML is
				     firm-authored terms copy (not user input); the API returns only `document_type='terms'`
				     documents here. -->
				<!-- Taller than it was (a 288px window on a multi-page declaration is a scroll marathon):
				     viewport-relative so a laptop shows a readable page and a phone still fits. -->
				<div
					class="prose prose-sm max-h-[50vh] min-h-0 max-w-none overflow-y-auto rounded border p-4 sm:max-h-[60vh]"
				>
					<!-- eslint-disable-next-line svelte/no-at-html-tags -->
					{@html agreeDoc.content}
				</div>
			{:else if agreeDoc?.url && isPdf}
				<!-- The shared viewer (pdf.js), not an <iframe> to the browser's plugin: same chrome as
				     the documents surface, and it reads the bytes off our own origin — see the API's
				     `/target` for why a presigned URL previewed as an empty frame. -->
				<DocumentViewer
					src={agreeDoc.url}
					contentType={agreeDoc.content_type ?? undefined}
					name={agreeStep?.title ?? 'Document'}
					class="h-[50vh] min-h-32 shrink overflow-hidden rounded border sm:h-[60vh]"
					searchable={false}
					zoomable={false}
				/>
			{:else if agreeDoc?.url}
				<a
					href={agreeDoc.url}
					target="_blank"
					rel="noopener noreferrer"
					class="text-brand flex h-24 items-center justify-center gap-2 rounded border underline"
				>
					<FileTextIcon class="size-4" /> Open the document
				</a>
			{:else if agreeDoc}
				<div class="flex h-24 flex-col items-center justify-center gap-2 rounded border p-4">
					<p class="text-sm">This is an acknowledgement. Tick below to confirm.</p>
				</div>
			{:else}
				<div class="flex h-24 flex-col items-center justify-center gap-2 rounded border">
					<p class="text-muted-foreground text-sm">Couldn’t load the document.</p>
					<Button
						variant="outline"
						size="sm"
						onclick={() => agreeStep && loadAgreeDoc(agreeStep)}
						disabled={agreeLoading}
					>
						{agreeLoading ? 'Loading…' : 'Try again'}
					</Button>
				</div>
			{/if}
		</div>

		<label class="flex shrink-0 items-start gap-2.5 py-1 text-sm">
			<Checkbox
				checked={agreeChecked}
				disabled={!agreeDoc}
				onCheckedChange={(v) => (agreeChecked = v === true)}
			/>
			<span>I have read and agree to this document.</span>
		</label>
		{#if agreeError}
			<p class="text-destructive shrink-0 text-xs" role="alert">{agreeError}</p>
		{/if}
		<Dialog.Footer class="shrink-0">
			<Button variant="ghost" onclick={() => (agreeOpen = false)}>Cancel</Button>
			<Button
				disabled={!agreeDoc || !agreeChecked || agreeing || agreeLoading}
				onclick={submitAgree}
			>
				{#if agreeing}
					Confirming…
				{:else if moreTerms}
					Agree and continue
				{:else}
					Agree
				{/if}
			</Button>
		</Dialog.Footer>
	</Dialog.Content>
</Dialog.Root>

<!-- Identity verification (verify_identity): the client photographs their document and takes a
     selfie in-app, and the verdict is the API's — we show the capture and wait for it to move on. -->
<Dialog.Root
	bind:open={idvOpen}
	onOpenChange={(o) => {
		if (!o) {
			idvStep = null;
			idvError = '';
			idvManualOffered = false;
			idvHandedOver = false;
			idvDocs = [];
			idvDocFiles = {};
			idvDocErrors = {};
			idvSentFrom = null;
		}
	}}
>
	<Dialog.Content class="flex max-h-[90svh] flex-col overflow-hidden sm:max-w-3xl">
		<Dialog.Header>
			<Dialog.Title>{idvStep?.title ?? 'Verify your identity'}</Dialog.Title>
			<Dialog.Description>
				We check your identity with a photo ID and a quick selfie. It takes about two minutes.
			</Dialog.Description>
		</Dialog.Header>

		<!-- The body scrolls and the footer stays. On a phone the camera and the photo review are
		     taller than the screen, and the dialog is pinned to its middle with the page scroll locked,
		     so this is the only way to reach the likeness box and Submit. `-mx-4 px-4` keeps focus
		     rings and the scrollbar off the clipped edge. -->
		<div class="-mx-4 min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 py-2">
			{#if idvHandedOver}
				<!-- The manual path IS these two documents: a government-issued photo ID and a proof of
				     address. It used to say "your adviser will be in touch", which left both to be
				     collected by email — off the record, and with nothing on the journey to say whether
				     they had arrived. Each is its own item, because "we have one of the two" is the
				     state a chase is actually about. -->
				<div class="space-y-3">
					<div class="flex items-start gap-3 rounded border px-4 py-3">
						<div
							class="bg-info/10 text-info flex size-9 shrink-0 items-center justify-center rounded-full"
						>
							<ScanFaceIcon class="size-5" />
						</div>
						<div class="min-w-0">
							<p class="font-medium">A person will check your identity</p>
							<p class="text-muted-foreground text-sm">
								{#if idvDocs.length === 0}
									Your adviser will be in touch about what to send.
								{:else if idvDocsOutstanding === 0}
									We have everything we need. Your adviser will review it and let you know.
								{:else}
									Send us the {idvDocsOutstanding === 1
										? 'document'
										: `${idvDocsOutstanding} documents`}
									below and your adviser will review them.
								{/if}
							</p>
						</div>
					</div>

					{#each idvDocs as doc (doc.item_uid)}
						{@const sent = doc.status !== 'pending'}
						<div class="space-y-2 rounded border px-4 py-3">
							<div class="flex items-start justify-between gap-3">
								<div class="min-w-0">
									<p class="text-sm font-medium">{doc.title}</p>
									<p class="text-muted-foreground text-xs">{doc.guidance}</p>
								</div>
								{#if sent}
									<span
										class="bg-success/10 text-success flex shrink-0 items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium"
									>
										<CheckIcon class="size-3.5" /> Sent
									</span>
								{/if}
							</div>
							{#if !sent}
								<!-- The dropzone default mirrors the API's byte-sniffed document allowlist,
								     including HEIC from a phone camera and common Office formats. -->
								<FileDropzone
									bind:files={idvDocFiles[doc.item_uid]}
									title={doc.title}
									onreject={(reason) =>
										(idvDocErrors = { ...idvDocErrors, [doc.item_uid]: reason })}
								/>
								<div class="flex items-center justify-between gap-3">
									<p class="text-muted-foreground text-xs">{doc.format_hint}, up to 25 MB</p>
									<Button
										size="sm"
										disabled={!idvDocFiles[doc.item_uid]?.length || idvDocBusy !== null}
										onclick={() => uploadIdentityDoc(doc)}
									>
										{idvDocBusy === doc.item_uid ? 'Sending…' : 'Send'}
									</Button>
								</div>
								{#if idvDocErrors[doc.item_uid]}
									<p class="text-destructive text-xs" role="alert">
										{idvDocErrors[doc.item_uid]}
									</p>
								{/if}
							{/if}
						</div>
					{/each}
				</div>
			{:else if idvCapture}
				<IdentityVerification
					capture={idvCapture}
					send={finishIdentityCapture}
					phone={photosSent(idvCapture) || idvCapture.status === 'passed'
						? null
						: {
								link: idvPhone,
								busy: idvPhoneBusy,
								error: idvPhoneError,
								start: startPhone,
								cancel: cancelPhone
							}}
				/>
			{:else}
				<!-- No digital capture on offer for this step: a person checks the documents instead. -->
				<div class="flex h-48 flex-col items-center justify-center gap-3 rounded border px-6">
					<p class="text-muted-foreground max-w-sm text-center text-sm">
						Your adviser will verify your identity with you.
					</p>
				</div>
			{/if}
		</div>

		{#if idvError}
			<p class="text-destructive text-xs" role="alert">{idvError}</p>
		{/if}
		<Dialog.Footer>
			<!-- The other way of doing this, offered while the digital one is still open. Photographing
			     a document is not something everybody can do, and the person who knows that is the
			     client — so it is their choice, not a firm setting and not the adviser's guess. Shown
			     only where the API says a person would actually pick it up, and not while a result is
			     already on its way. -->
			{#if idvManualOffered && !idvHandedOver && !idvAwaiting}
				<Button
					variant="outline"
					class="mr-auto h-auto min-h-8 py-1.5 whitespace-normal"
					disabled={idvHandingOver}
					onclick={chooseManualIdentity}
				>
					{idvHandingOver ? 'Letting them know…' : 'I’d rather a person checked my documents'}
				</Button>
			{/if}
			<Button variant="ghost" onclick={() => (idvOpen = false)}>Close</Button>
		</Dialog.Footer>
	</Dialog.Content>
</Dialog.Root>

<!-- Fill-form flow (fill_form) — the shared in-app builder-form dialog. -->
<FormFillDialog
	bind:open={formFillOpen}
	formName={formFillStep?.formName ?? undefined}
	fields={formFillStep?.formFields ?? []}
	answers={formFillStep?.formAnswers ?? {}}
	dataSources={onboardingDataSources}
	requestTitle={formFillStep?.title ?? undefined}
	onsubmit={submitFormFill}
	onsave={saveFormFill}
/>
