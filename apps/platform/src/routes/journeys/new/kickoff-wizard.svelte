<script lang="ts">
	import {
		Alert,
		AlertDescription,
		Badge,
		Button,
		Card,
		CardContent,
		Checkbox,
		Combobox,
		ConfirmDialog,
		DocumentPreviewSheet,
		EmptyState,
		humanise,
		EmailInput,
		Input,
		Label,
		PageHeader,
		SectionHeading,
		Segmented,
		SelectMenu,
		Separator,
		Textarea,
		formatDate,
		formatGBP
	} from '@graphene/ui';
	import { deserialize, enhance } from '$app/forms';
	import type { ActionResult } from '@sveltejs/kit';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import UsersIcon from '@lucide/svelte/icons/users';
	import WalletIcon from '@lucide/svelte/icons/wallet';
	import ShieldCheckIcon from '@lucide/svelte/icons/shield-check';
	import TriangleAlertIcon from '@lucide/svelte/icons/triangle-alert';
	import RefreshCwIcon from '@lucide/svelte/icons/refresh-cw';
	import FileTextIcon from '@lucide/svelte/icons/file-text';
	import type { CatalogProduct } from '@graphene/ui/book/journeys';
	import { DEFAULT_INTAKE } from '@graphene/ui/book/journeys';
	import IllustrationDrawer from '$lib/illustrations/illustration-drawer.svelte';
	import {
		adviserChoiceRequired,
		completedAge,
		custodiesFor,
		defaultCustody,
		keepGeneratedIllustration
	} from './kickoff';
	import type { Client, KickoffWizard, OnboardingIllustration } from './+page.server';
	import type { IllustrationModel } from '$lib/illustrations/form';

	let {
		wizard,
		form,
		canCreateIllustrationClient
	}: {
		wizard: KickoffWizard;
		form: { error?: string; liveJourneyUid?: string | null } | null;
		canCreateIllustrationClient: boolean;
	} = $props();
	type IdentityMode = 'default' | 'manual';
	type PartyMode = 'new' | 'existing';
	type PartyType = 'individual' | 'organisation';
	type Party = {
		key: string;
		mode: PartyMode;
		contact_id: number | null;
		contact_uid: string | null;
		first_name: string;
		middle_name: string;
		surname: string;
		org_name: string;
		email: string;
		type: PartyType;
		dob: string;
		retirement_age: number | null;
		marital_status: string;
		registered_contact_key: string | null;
		identity_mode: IdentityMode;
		identity_verified: boolean;
		/** An existing client's own adviser, for the blank-picker rule. A new client has none. */
		adviser_id: string | null;
		/** Whether the adviser has finished with this card — set when focus LEAVES it, so what is
		 *  still being typed is never marked wrong. A blank card is incomplete, not invalid. */
		touched: boolean;
	};
	type Account = {
		key: string;
		product_id: number | null;
		provider: string | null;
		custody: 'onshore' | 'offshore' | null;
		illustration_uid: string | null;
		owner_keys: string[];
		touched: boolean;
	};
	type WizardStep = 'applicants' | 'accounts' | 'review';

	let nextKey = 0;
	const key = (prefix: string) => `${prefix}-${++nextKey}`;
	function blankParty(mode: PartyMode = 'new'): Party {
		return {
			key: key('party'),
			mode,
			contact_id: null,
			contact_uid: null,
			first_name: '',
			middle_name: '',
			surname: '',
			org_name: '',
			email: '',
			type: 'individual',
			dob: '',
			retirement_age: null,
			marital_status: '',
			registered_contact_key: null,
			identity_mode: 'default',
			identity_verified: false,
			adviser_id: null,
			touched: false
		};
	}
	function blankAccount(): Account {
		return {
			key: key('account'),
			product_id: null,
			provider: null,
			custody: null,
			illustration_uid: null,
			owner_keys: [],
			touched: false
		};
	}

	let parties = $state<Party[]>([blankParty()]);
	let accounts = $state<Account[]>([blankAccount()]);
	let wizardStep = $state<WizardStep>('applicants');
	/** Flipped by pressing Continue on an incomplete stage: the one moment the adviser has SAID
	 *  they are done, which is when every outstanding answer should be pointed at. */
	let showApplicantErrors = $state(false);
	let showAccountErrors = $state(false);
	const WIZARD_STEPS = [
		{ value: 'applicants', label: '1 · Applicants' },
		{ value: 'accounts', label: '2 · Accounts' },
		{ value: 'review', label: '3 · Review' }
	];
	let submitting = $state(false);
	let refreshingIllustrations = $state(false);
	let creatingIllustrationClient = $state<string | null>(null);
	let illustrationCreateError = $state<string | null>(null);
	let illustrationDrawerOpen = $state(false);
	/** Sum of the owner's other SIPP accounts. Null hides the line — a client just created has none. */
	let illustrationSippTotal = $state<number | null>(null);
	let illustrationPartyKey = $state<string | null>(null);
	let illustrationAccountKey = $state<string | null>(null);
	let previewingIllustration = $state<OnboardingIllustration | null>(null);
	let adviserId = $state<string | null>(null);
	let fieldValues = $state<Record<string, string>>({});

	let clients = $state<Client[]>([]);
	let advisers = $state<{ user_id: string; name: string | null; email: string | null }[]>([]);
	/** Whether the person signed in is marked an adviser — the blank picker's last fallback. */
	let signedInIsAdviser = $state(false);
	let digitalIdvAvailable = $state(false);
	let illustrations = $state<OnboardingIllustration[]>([]);
	let models = $state<IllustrationModel[]>([]);
	let applicantsLoaded = $state(false);
	let accountsLoaded = $state(false);

	async function loadApplicants(): Promise<void> {
		if (applicantsLoaded) return;
		applicantsLoaded = true;
		await fetchApplicants();
	}

	async function fetchApplicants(): Promise<void> {
		clientsFetchedAt = Date.now();
		const res = await fetch('/journeys/new/applicants');
		if (!res.ok) return;
		const body = (await res.json()) as {
			clients: Client[];
			advisers: { user_id: string; name: string | null; email: string | null }[];
			signedInIsAdviser?: boolean;
			digitalIdvAvailable: boolean;
		};
		clients = body.clients ?? [];
		advisers = body.advisers ?? [];
		signedInIsAdviser = body.signedInIsAdviser === true;
		digitalIdvAvailable = body.digitalIdvAvailable === true;
		prefillClient();
	}

	// The client list is fetched when the step mounts, and a wizard stays open for a while: a
	// client created meanwhile — in another tab, or from the client record — was missing from
	// this search but present on a fresh "New journey". Each open of the picker, and each return
	// to the tab, re-reads the list; the window keeps a quick succession of opens to one fetch.
	const CLIENTS_STALE_MS = 5_000;
	let clientsFetchedAt = 0;
	function refreshClients(): void {
		if (!applicantsLoaded || Date.now() - clientsFetchedAt < CLIENTS_STALE_MS) return;
		void fetchApplicants();
	}

	// Started from a client's record (`?client=<uid>`): the first card is that client, chosen as
	// an existing applicant, so the adviser is not asked to find the person they just came from.
	// Once only, and only into an untouched card — a reload after they have changed it must not
	// put the first choice back.
	let prefilled = false;
	function prefillClient(): void {
		if (prefilled || !wizard.prefillClientUid) return;
		prefilled = true;
		const client = clients.find((candidate) => candidate.uid === wizard.prefillClientUid);
		const first = parties[0];
		if (!client || !first || first.contact_id !== null || first.first_name || first.surname) {
			return;
		}
		useExisting(first, client);
	}

	async function loadAccounts(force = false): Promise<void> {
		if (accountsLoaded && !force) return;
		accountsLoaded = true;
		const res = await fetch('/journeys/new/accounts');
		if (!res.ok) return;
		const body = (await res.json()) as {
			illustrations: OnboardingIllustration[];
			models: IllustrationModel[];
		};
		illustrations = body.illustrations ?? [];
		models = body.models ?? [];
	}

	$effect(() => {
		if (wizardStep === 'applicants') void loadApplicants();
		if (wizardStep === 'accounts' || wizardStep === 'review') void loadAccounts();
	});

	const selectedFlow = $derived(
		wizard.flows.find((flow) => flow.code === wizard.selectedFlowCode) ?? wizard.flows[0]
	);
	const flowCode = $derived(selectedFlow?.code ?? '');
	const intake = $derived(selectedFlow?.intake ?? DEFAULT_INTAKE);
	/** A person-subject journey gathers one or two existing clients and opens no account. */
	const personSubject = $derived(intake.subject === 'person');
	/** The confirmation page. A person journey leaves it off unless the flow asks for it. */
	const needsReview = $derived(intake.review ?? !personSubject);
	const wizardSteps = $derived(
		personSubject
			? needsReview
				? [
						{ value: 'applicants' as const, label: '1 · Clients' },
						{ value: 'review' as const, label: '2 · Review' }
					]
				: []
			: needsReview
				? WIZARD_STEPS
				: [
						{ value: 'applicants' as const, label: '1 · Applicants' },
						{ value: 'accounts' as const, label: '2 · Accounts' }
					]
	);
	const fieldsValid = $derived(
		intake.fields.every((field) => !field.required || (fieldValues[field.key] ?? '').trim() !== '')
	);
	const offeredProducts = $derived(wizard.products.filter((product) => product.enabled));
	const platformNames = $derived(
		new Map((wizard.platforms ?? []).map((platform) => [String(platform.id), platform.name]))
	);

	$effect(() => {
		if (adviserId || advisers.length !== 1) return;
		adviserId = advisers[0].user_id;
	});

	const IDENTITY_OPTIONS = [
		{ value: 'default', label: 'Auto IDV' },
		{ value: 'manual', label: 'Manual IDV' }
	];
	const isIdentityMode = (value: string): value is IdentityMode =>
		value === 'default' || value === 'manual';
	const productOf = (account: Account): CatalogProduct | undefined =>
		offeredProducts.find((product) => product.id === account.product_id);
	const partyOf = (partyKey: string): Party | undefined =>
		parties.find((party) => party.key === partyKey);
	const illustrationOf = (account: Account) =>
		illustrations.find((illustration) => illustration.uid === account.illustration_uid);
	function illustrationsFor(account: Account) {
		// A client created in this step is flipped to `existing` (with a contact id) before the
		// drawer opens, so the run just generated belongs to an owner this filter keeps.
		const ownerIds = new Set(
			account.owner_keys
				.map(partyOf)
				.filter((party): party is Party => party?.mode === 'existing')
				.map((party) => party.contact_id)
				.filter((id): id is number => id !== null)
		);
		return illustrations.filter((illustration) => ownerIds.has(illustration.contact_id));
	}
	function illustrationLabel(illustration: OnboardingIllustration): string {
		const amount =
			illustration.existing_fund_value == null ? null : formatGBP(illustration.existing_fund_value);
		return [
			formatDate(illustration.created_at, ''),
			illustration.quotation_type,
			amount,
			illustration.model_name,
			illustration.output_reference
		]
			.filter(Boolean)
			.join(' · ');
	}
	async function openIllustration(account: Account, party: Party): Promise<void> {
		illustrationAccountKey = account.key;
		illustrationPartyKey = party.key;
		illustrationSippTotal = null;
		illustrationCreateError = null;
		// Before the drawer opens, so Generate cannot file a zero fund while the total is still
		// on its way — and so the figure is on screen with the form, not after it. A failed read
		// stays closed: opening would illustrate the client as holding nothing.
		const loaded = await loadIllustrationSippTotal(party);
		if (illustrationPartyKey !== party.key) return;
		if (!loaded) {
			illustrationCreateError =
				'This client’s other SIPPs could not be read, so the illustration was not opened.';
			return;
		}
		illustrationDrawerOpen = true;
	}
	async function loadIllustrationSippTotal(party: Party): Promise<boolean> {
		if (!party.contact_uid) return true;
		const res = await fetch(
			`/journeys/new/sipp-total?uid=${encodeURIComponent(party.contact_uid)}`
		);
		if (illustrationPartyKey !== party.key) return false;
		if (!res.ok) return false;
		const body = (await res.json()) as { total?: number | null };
		if (illustrationPartyKey !== party.key) return false;
		illustrationSippTotal = body.total ?? null;
		return true;
	}
	/** Posts to the named action directly rather than from a `<form use:enhance>`.
	 *
	 *  The whole wizard is already one `<form>`, and a form inside a form is invalid HTML: the
	 *  submit event bubbles, so the outer `use:enhance` handled it too and posted the entire
	 *  journey to the default action — starting (or refusing) the application from a button whose
	 *  only job is to save one client. */
	async function createIllustrationClient(account: Account, party: Party): Promise<void> {
		creatingIllustrationClient = party.key;
		illustrationCreateError = null;
		const body = new FormData();
		body.set('party', JSON.stringify($state.snapshot(party)));
		try {
			const response = await fetch('?/createIllustrationClient', {
				method: 'POST',
				headers: { 'x-sveltekit-action': 'true' },
				body
			});
			const result = deserialize(await response.text()) as ActionResult<
				{ contact_id?: number; uid?: string },
				{ error?: string }
			>;
			if (result.type !== 'success') {
				illustrationCreateError =
					result.type === 'failure' && typeof result.data?.error === 'string'
						? result.data.error
						: 'The client record could not be created.';
				return;
			}
			const created = result.data ?? {};
			if (!created.contact_id || !created.uid) {
				illustrationCreateError = 'The client was created without a usable reference.';
				return;
			}
			party.mode = 'existing';
			party.contact_id = created.contact_id;
			party.contact_uid = created.uid;
			applicantsLoaded = false;
			await loadApplicants();
			await openIllustration(account, party);
		} finally {
			creatingIllustrationClient = null;
		}
	}

	/** Stop the live journey the refusal named, then press Start again on the adviser's behalf.
	 *  Posted the same way as `createIllustrationClient`: the button sits inside the wizard form. */
	let stopLiveJourneyOpen = $state(false);
	let stopLiveJourneyError = $state<string | null>(null);
	let wizardForm = $state<HTMLFormElement | null>(null);
	async function stopLiveJourney(): Promise<void> {
		const uid = form?.liveJourneyUid;
		if (!uid) return;
		stopLiveJourneyError = null;
		const body = new FormData();
		body.set('execution_uid', uid);
		const response = await fetch('?/stopLiveJourney', {
			method: 'POST',
			headers: { 'x-sveltekit-action': 'true' },
			body
		});
		const result = deserialize(await response.text()) as ActionResult<
			{ stopped?: string },
			{ error?: string }
		>;
		if (result.type !== 'success') {
			stopLiveJourneyError =
				result.type === 'failure' && typeof result.data?.error === 'string'
					? result.data.error
					: 'The journey could not be stopped.';
			return;
		}
		wizardForm?.requestSubmit();
	}

	function clientParts(client: Client): { first: string; middle: string; surname: string } {
		if (client.first_name || client.surname) {
			return {
				first: client.first_name ?? '',
				middle: client.middle_name ?? '',
				surname: client.surname ?? ''
			};
		}
		const parts = (client.name ?? '').trim().split(/\s+/).filter(Boolean);
		return {
			first: parts[0] ?? '',
			middle: parts.slice(1, -1).join(' '),
			surname: parts.length > 1 ? parts.at(-1)! : ''
		};
	}
	function clientLabel(client: Client): string {
		const name = clientParts(client);
		return (
			[name.first, name.surname].filter(Boolean).join(' ') ||
			client.name ||
			client.email ||
			`#${client.contact_id}`
		);
	}
	function partyName(party: Party): string {
		if (party.type === 'organisation') return party.org_name.trim();
		return [party.first_name, party.middle_name, party.surname]
			.map((part) => part.trim())
			.filter(Boolean)
			.join(' ');
	}
	function partyLabel(party: Party): string {
		return (
			partyName(party) ||
			party.email.trim() ||
			(personSubject ? 'Choose a client' : 'New applicant')
		);
	}
	const labelledClients = $derived(
		clients.map((client) => ({
			value: String(client.contact_id),
			label: clientLabel(client),
			eligibleRegisteredContact:
				client.type === 'individual' &&
				!!client.email?.trim() &&
				!!client.dob &&
				(completedAge(client.dob) ?? -1) >= 18
		}))
	);
	function clientOptions(party: Party) {
		return labelledClients
			.filter((client) => !isRegisteredContact(party) || client.eligibleRegisteredContact)
			.map(({ value, label }) => ({ value, label }));
	}
	const clientsByEmail = $derived(
		new Map(
			clients
				.filter((client) => client.email?.trim())
				.map((client) => [client.email!.trim().toLowerCase(), client])
		)
	);
	function emailClash(party: Party): Client | null {
		if (party.mode === 'existing') return null;
		return clientsByEmail.get(party.email.trim().toLowerCase()) ?? null;
	}
	function samePersonClash(party: Party): Client | null {
		const client = emailClash(party);
		if (!client) return null;
		const entered = `${party.first_name.trim()} ${party.surname.trim()}`.trim().toLowerCase();
		return entered && clientLabel(client).toLowerCase() === entered ? client : null;
	}

	function setMode(party: Party, mode: PartyMode): void {
		if (party.mode === mode) return;
		const fresh = blankParty(mode);
		Object.assign(party, { ...fresh, key: party.key });
	}
	function useExisting(party: Party, client: Client): void {
		if (
			isRegisteredContact(party) &&
			(client.type !== 'individual' ||
				!client.email?.trim() ||
				!client.dob ||
				(completedAge(client.dob) ?? -1) < 18)
		) {
			return;
		}
		const name = clientParts(client);
		party.mode = 'existing';
		party.contact_id = client.contact_id;
		party.contact_uid = client.uid;
		party.first_name = name.first;
		party.middle_name = name.middle;
		party.surname = name.surname;
		party.org_name = client.name ?? '';
		party.email = client.email ?? '';
		party.type = client.type;
		party.identity_verified = client.identity_verified;
		party.dob = client.dob ?? '';
		party.retirement_age = client.retirement_age;
		party.marital_status = client.marital_status ?? '';
		party.adviser_id = client.adviser_id ?? null;
		ensureRegisteredContact(party);
	}
	function chooseExisting(party: Party, value: string): void {
		const client = clients.find((item) => item.contact_id === Number(value));
		if (client) useExisting(party, client);
	}
	function ageOf(party: Party): number | null {
		if (party.type !== 'individual' || !party.dob) return null;
		return completedAge(party.dob);
	}
	function isChild(party: Party): boolean {
		const age = ageOf(party);
		return age !== null && age >= 0 && age < 18;
	}
	function isEligibleRegisteredContact(party: Party | undefined): boolean {
		if (!party) return false;
		const age = ageOf(party);
		return party.type === 'individual' && age !== null && age >= 18 && !!party.email.trim();
	}
	function registeredContactOptions(child: Party) {
		return parties
			.filter(
				(party) =>
					party.key !== child.key &&
					(party.key === child.registered_contact_key || isEligibleRegisteredContact(party))
			)
			.map((party) => ({ value: party.key, label: partyLabel(party) }));
	}
	function isRegisteredContact(party: Party): boolean {
		return parties.some((child) => child.registered_contact_key === party.key);
	}
	function isEmptyApplicant(party: Party): boolean {
		return (
			party.contact_id === null &&
			!party.first_name.trim() &&
			!party.middle_name.trim() &&
			!party.surname.trim() &&
			!party.org_name.trim() &&
			!party.email.trim() &&
			!party.dob
		);
	}
	function designateRegisteredContact(child: Party, partyKey: string): void {
		const previousKey = child.registered_contact_key;
		child.registered_contact_key = partyKey || null;
		if (!previousKey || previousKey === partyKey) return;
		const previous = partyOf(previousKey);
		if (!previous || !isEmptyApplicant(previous) || isRegisteredContact(previous)) return;
		const previousIndex = parties.findIndex((party) => party.key === previousKey);
		if (previousIndex >= 0) removeParty(previousIndex);
	}
	function addRegisteredContact(child: Party, mode: PartyMode): void {
		const registered = blankParty(mode);
		const childIndex = parties.findIndex((party) => party.key === child.key);
		parties.splice(childIndex < 0 ? parties.length : childIndex + 1, 0, registered);
		child.registered_contact_key = registered.key;
	}
	function ensureRegisteredContact(party: Party): void {
		if (!isChild(party) || party.registered_contact_key) return;
		const existingAdult = parties.find(
			(candidate) => candidate.key !== party.key && isEligibleRegisteredContact(candidate)
		);
		if (existingAdult) {
			party.registered_contact_key = existingAdult.key;
			return;
		}
		addRegisteredContact(party, 'new');
	}
	function setPartyType(party: Party, type: PartyType): void {
		party.type = type;
		if (type !== 'organisation' || !isRegisteredContact(party)) return;
		for (const child of parties) {
			if (child.registered_contact_key !== party.key) continue;
			child.registered_contact_key = null;
			ensureRegisteredContact(child);
		}
	}
	function setDob(party: Party, dob: string): void {
		party.dob = dob;
		if (isChild(party) && isRegisteredContact(party)) {
			for (const child of parties) {
				if (child.registered_contact_key !== party.key) continue;
				child.registered_contact_key = null;
				ensureRegisteredContact(child);
			}
		}
		ensureRegisteredContact(party);
	}
	function partyError(party: Party): string | null {
		if (personSubject) {
			if (party.mode !== 'existing' || party.contact_id === null)
				return 'Choose an existing client.';
			return null;
		}
		if (party.mode === 'existing' && party.contact_id === null) return 'Choose an existing client.';
		if (party.type === 'organisation') {
			return partyName(party) ? null : 'Enter the organisation name.';
		}
		if (party.mode === 'new' && (!party.first_name.trim() || !party.surname.trim())) {
			return 'Enter the applicant’s first name and surname.';
		}
		const age = ageOf(party);
		if (age === null) {
			return party.mode === 'existing'
				? 'This client has no date of birth recorded. Update their client record first.'
				: 'Enter the applicant’s date of birth.';
		}
		if (age < 0) return 'Date of birth cannot be in the future.';
		if (age >= 18 && !party.email.trim()) {
			return party.mode === 'existing'
				? 'This adult client has no email recorded. Update their client record first.'
				: 'Enter the adult applicant’s email.';
		}
		const registered = partyOf(party.registered_contact_key ?? '');
		const registeredAge = registered ? ageOf(registered) : null;
		if (age < 18 && !registered) {
			// A linked contact that is not yet complete reports that on its own card, as an applicant
			// in its own right — repeating it here said the same thing twice about two different cards.
			return 'Choose an adult registered contact with an email.';
		}
		if (
			age < 18 &&
			registered &&
			(registered.type !== 'individual' || (registeredAge !== null && registeredAge < 18))
		) {
			return 'Designate an adult individual applicant as the registered contact.';
		}
		if (samePersonClash(party)) {
			return 'This person is already a client. Use the existing client record.';
		}
		return null;
	}
	/** Nobody on the application brings an adviser and the person signed in is not one, so a
	 *  blank picker has nobody to fall to. The API refuses that kickoff; asking here is kinder. */
	const adviserRequired = $derived(
		!personSubject && applicantsLoaded && adviserChoiceRequired(parties, signedInIsAdviser)
	);
	const adviserMissing = $derived(adviserRequired && !adviserId);
	const adviserError = $derived(
		!adviserMissing
			? null
			: advisers.length
				? 'Choose an adviser. These applicants have none yet, and you are not marked as an adviser.'
				: 'Nobody at this firm is marked as an adviser. Turn on Adviser on a team page first.'
	);
	const applicantsReady = $derived(
		!!flowCode &&
			fieldsValid &&
			parties.length > 0 &&
			(!personSubject || parties.length <= 2) &&
			!adviserMissing &&
			parties.every((party) => partyError(party) === null)
	);

	$effect(() => {
		if (!personSubject) return;
		if (wizardStep === 'accounts') wizardStep = 'applicants';
		if (parties.length > 2) parties = parties.slice(0, 2);
		for (const party of parties) {
			if (party.mode !== 'existing') setMode(party, 'existing');
		}
	});

	function addParty(mode: PartyMode = 'new'): void {
		parties.push(blankParty(mode));
	}
	/** Mark a card done only when focus lands OUTSIDE it.
	 *
	 *  `focusout` bubbles, so tabbing from First name to Surname fires it on the card too — and
	 *  treating that as "finished" is how a half-typed name gets told it is wrong. `relatedTarget`
	 *  is where focus is going (null when it leaves the document, which is not an answer either). */
	function leftCard(event: FocusEvent, mark: () => void): void {
		const card = event.currentTarget;
		const next = event.relatedTarget;
		if (next === null) return;
		const inside = card instanceof HTMLElement && next instanceof Node && card.contains(next);
		if (!inside) mark();
	}
	function removeParty(index: number): void {
		const [removed] = parties.splice(index, 1);
		if (!removed) return;
		for (const party of parties) {
			if (party.registered_contact_key === removed.key) {
				party.registered_contact_key = null;
				ensureRegisteredContact(party);
			}
		}
		for (const account of accounts) {
			account.owner_keys = account.owner_keys.filter((ownerKey) => ownerKey !== removed.key);
			invalidateProduct(account);
		}
	}
	function normaliseAccount(account: Account): void {
		const product = productOf(account);
		if (!product) {
			account.provider = null;
			account.custody = null;
			account.illustration_uid = null;
			return;
		}
		if (product.product_type !== 'SIPP') account.illustration_uid = null;
		const custodians = product.custodians ?? [];
		if (!account.provider || !custodians.includes(account.provider)) {
			account.provider = custodians.length === 1 ? custodians[0] : null;
		}
		const custodies = custodiesFor(product.allowed_custodies, account.provider, wizard.platforms);
		if (!account.custody || !custodies.includes(account.custody)) {
			account.custody = defaultCustody(custodies);
		}
	}
	function accountContactCount(account: Account): number {
		const registeredContacts = new Set(
			account.owner_keys
				.map(partyOf)
				.filter((party): party is Party => party !== undefined && isChild(party))
				.map((party) => party.registered_contact_key)
				.filter((partyKey): partyKey is string => partyKey !== null)
		);
		return account.owner_keys.length + registeredContacts.size;
	}
	function hasOwnerRegisteredContactConflict(account: Account): boolean {
		const selected = new Set(account.owner_keys);
		return account.owner_keys
			.map(partyOf)
			.filter((party): party is Party => party !== undefined && isChild(party))
			.some(
				(child) =>
					child.registered_contact_key !== null && selected.has(child.registered_contact_key)
			);
	}
	function ownerSelectionConflicts(account: Account, party: Party): boolean {
		if (account.owner_keys.includes(party.key)) return false;
		const selected = new Set(account.owner_keys);
		if (party.registered_contact_key && selected.has(party.registered_contact_key)) {
			return true;
		}
		return account.owner_keys
			.map(partyOf)
			.filter((owner): owner is Party => owner !== undefined && isChild(owner))
			.some((child) => child.registered_contact_key === party.key);
	}
	function ownerAgeError(product: CatalogProduct, party: Party): string | null {
		if (party.type === 'organisation') return null;
		const age = ageOf(party);
		if (age === null) return `${partyLabel(party)} has no valid date of birth.`;
		if (age < 0) return 'Date of birth cannot be in the future.';
		if (product.min_age != null && age < product.min_age) {
			return `${product.name} is only available from age ${product.min_age}.`;
		}
		if (product.max_age != null && age > product.max_age) {
			return `${product.name} is only available up to age ${product.max_age}.`;
		}
		return null;
	}
	function productEligible(account: Account, product: CatalogProduct): boolean {
		if (!account.owner_keys.length) return false;
		const owners = account.owner_keys.map(partyOf).filter((party): party is Party => !!party);
		if (owners.length !== account.owner_keys.length) return false;
		if (hasOwnerRegisteredContactConflict(account)) return false;
		if (owners.some((owner) => !product.allowed_client_types.includes(owner.type))) return false;
		if (accountContactCount(account) > product.max_parties) return false;
		return owners.every((owner) => ownerAgeError(product, owner) === null);
	}
	function productsFor(account: Account): CatalogProduct[] {
		return offeredProducts.filter((product) => productEligible(account, product));
	}
	function invalidateProduct(account: Account): void {
		const product = productOf(account);
		if (product && productEligible(account, product)) {
			if (
				account.illustration_uid &&
				!illustrationsFor(account).some(
					(illustration) => illustration.uid === account.illustration_uid
				)
			) {
				account.illustration_uid = null;
			}
			return;
		}
		if (account.product_id !== null) {
			account.product_id = null;
			account.provider = null;
			account.custody = null;
			account.illustration_uid = null;
		}
		const eligible = productsFor(account);
		if (eligible.length === 1) {
			account.product_id = eligible[0].id;
			normaliseAccount(account);
		}
	}
	function toggleOwner(account: Account, partyKey: string, checked: boolean): void {
		if (checked && !account.owner_keys.includes(partyKey)) account.owner_keys.push(partyKey);
		if (!checked) account.owner_keys = account.owner_keys.filter((key) => key !== partyKey);
		invalidateProduct(account);
	}
	function chooseProduct(account: Account, value: string): void {
		account.product_id = Number(value) || null;
		normaliseAccount(account);
	}
	function addAccount(): void {
		accounts.push(blankAccount());
	}
	function removeAccount(index: number): void {
		accounts.splice(index, 1);
	}
	function accountError(account: Account): string | null {
		if (!account.owner_keys.length) return 'Choose one or more owners before selecting a product.';
		if (hasOwnerRegisteredContactConflict(account)) {
			return 'A child’s registered contact is attached automatically; do not also select them as an owner.';
		}
		if (!productOf(account)) {
			return productsFor(account).length
				? 'Choose an eligible product.'
				: 'No offered product supports these owners, ages and party count.';
		}
		const product = productOf(account)!;
		if (product.product_type === 'SIPP' && !account.illustration_uid) {
			return illustrationsFor(account).length
				? 'Choose the SIPP illustration this account is being opened against.'
				: 'Run a successful SIPP illustration for an existing owner before continuing.';
		}
		if (product.custodians.length && !account.provider) return 'Choose a custodian.';
		if (account.provider && !product.custodians.includes(account.provider)) {
			return 'Choose a custodian offered for this product.';
		}
		const custodies = custodiesFor(product.allowed_custodies, account.provider, wizard.platforms);
		if (!account.custody || !custodies.includes(account.custody)) {
			return 'Choose an allowed custody location.';
		}
		return null;
	}
	const accountsReady = $derived(
		applicantsReady &&
			accounts.length > 0 &&
			accounts.every((account) => accountError(account) === null)
	);

	$effect(() => {
		for (const account of accounts) {
			invalidateProduct(account);
		}
	});

	function ownerPayload(
		party: Party,
		role: 'owner' | 'registered_contact',
		registeredContactFor: string[] = []
	) {
		const identity_mode =
			party.type === 'individual' && !isChild(party) && !party.identity_verified
				? party.identity_mode
				: null;
		if (party.mode === 'existing') {
			return {
				contact_id: party.contact_id,
				role,
				identity_mode,
				party_key: party.key,
				registered_contact_for: registeredContactFor
			};
		}
		if (party.type === 'organisation') {
			return {
				name: party.org_name.trim(),
				email: party.email.trim() || null,
				type: 'organisation',
				role,
				party_key: party.key,
				registered_contact_for: registeredContactFor
			};
		}
		return {
			first_name: party.first_name.trim(),
			middle_name: party.middle_name.trim() || null,
			surname: party.surname.trim(),
			email: party.email.trim() || null,
			dob: party.dob,
			type: 'individual',
			role,
			identity_mode,
			party_key: party.key,
			registered_contact_for: registeredContactFor
		};
	}
	function accountOwners(account: Account) {
		const owners = account.owner_keys
			.map(partyOf)
			.filter((party): party is Party => party !== undefined);
		const registeredFor = new Map<string, string[]>();
		for (const child of owners.filter(isChild)) {
			if (!child.registered_contact_key) continue;
			const children = registeredFor.get(child.registered_contact_key) ?? [];
			children.push(child.key);
			registeredFor.set(child.registered_contact_key, children);
		}
		return [
			...owners.map((party) => ownerPayload(party, 'owner')),
			...[...registeredFor].flatMap(([partyKey, childKeys]) => {
				const registered = partyOf(partyKey);
				return registered ? [ownerPayload(registered, 'registered_contact', childKeys)] : [];
			})
		];
	}
	const payload = $derived(
		personSubject
			? JSON.stringify({
					flow_code: flowCode,
					accounts: [],
					clients: parties
						.map((party) => party.contact_id)
						.filter((id): id is number => id !== null),
					intake: { ...fieldValues }
				})
			: JSON.stringify({
					flow_code: flowCode,
					adviser_id: adviserId,
					accounts: accounts.map((account) => ({
						product_id: account.product_id,
						product_type: productOf(account)?.product_type ?? null,
						provider: account.provider,
						custody: account.custody,
						illustration_uid: account.illustration_uid,
						owners: accountOwners(account)
					})),
					intake: { ...fieldValues }
				})
	);
	/** Who a blank picker falls to, in the review's words. */
	const inheritedAdviser = $derived(
		parties.some((party) => party.mode === 'existing' && !!party.adviser_id)
	);
	const adviserName = $derived(
		advisers.find((adviser) => adviser.user_id === adviserId)?.name ??
			advisers.find((adviser) => adviser.user_id === adviserId)?.email ??
			(inheritedAdviser ? 'The applicants’ own adviser' : signedInIsAdviser ? 'You' : 'Not chosen')
	);
	const illustrationParty = $derived(
		illustrationPartyKey ? (partyOf(illustrationPartyKey) ?? null) : null
	);
	const illustrationAccount = $derived(
		illustrationAccountKey
			? (accounts.find((account) => account.key === illustrationAccountKey) ?? null)
			: null
	);
</script>

<svelte:head><title>New journey · Platform</title></svelte:head>

<div class="w-full">
	<PageHeader
		title={selectedFlow ? `New ${selectedFlow.name}` : 'New journey'}
		description={personSubject
			? 'Choose one or two existing clients. This journey does not open an account.'
			: 'Gather each applicant once, then choose who owns each account.'}
		back={{ href: '/journeys', label: 'Journeys' }}
	/>

	{#if !selectedFlow}
		<EmptyState
			title="No published flow selected."
			description="Choose a published flow from the New journey menu."
		>
			{#snippet action()}<Button href="/journeys">Back to journeys</Button>{/snippet}
		</EmptyState>
	{:else}
		<form
			method="POST"
			action="?/start"
			class="space-y-6"
			bind:this={wizardForm}
			use:enhance={() => {
				submitting = true;
				return async ({ update }) => {
					submitting = false;
					await update();
				};
			}}
		>
			<input type="hidden" name="payload" value={payload} />
			{#if form?.error}
				<Alert variant="destructive">
					<AlertDescription>
						<p>{stopLiveJourneyError ?? form.error}</p>
						{#if form.liveJourneyUid}
							<div class="mt-2 flex flex-wrap gap-2">
								<Button size="sm" variant="outline" href={`/journeys/${form.liveJourneyUid}`}>
									Open the live journey
								</Button>
								<Button
									size="sm"
									variant="destructive"
									type="button"
									onclick={() => (stopLiveJourneyOpen = true)}
								>
									Stop it and start this one
								</Button>
							</div>
						{/if}
					</AlertDescription>
				</Alert>
			{/if}

			{#if wizardSteps.length > 1}
				<div class="space-y-2">
					<Segmented
						class="w-full"
						value={wizardStep}
						options={wizardSteps}
						onValueChange={(value) => {
							if (value === 'applicants') wizardStep = value;
							// Jumping ahead over an incomplete stage says the same thing as pressing Continue,
							// so it marks the outstanding answers rather than ignoring the click in silence.
							if (value === 'accounts' && !personSubject) {
								if (applicantsReady) wizardStep = value;
								else showApplicantErrors = true;
							}
							if (value === 'review') {
								const ready = personSubject ? applicantsReady : accountsReady;
								if (ready) wizardStep = value;
								else {
									showApplicantErrors = !applicantsReady;
									showAccountErrors = applicantsReady && !personSubject;
								}
							}
						}}
					/>
					<p class="text-muted-foreground text-xs">
						{wizardStep === 'applicants'
							? personSubject
								? 'Choose one or two existing clients. No account is opened.'
								: 'Add each person or organisation once. A child needs a second, adult applicant designated as their registered contact.'
							: wizardStep === 'accounts'
								? 'Choose which applicants will own each account before choosing its product.'
								: 'Review exactly what will be created before starting the journey.'}
					</p>
				</div>
			{/if}

			{#if wizardStep === 'applicants'}
				{#if intake.fields.length || ((advisers.length || adviserRequired) && !personSubject)}
					<Card glass>
						<CardContent class="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
							{#if (advisers.length || adviserRequired) && !personSubject}
								<div class="space-y-1.5">
									<Label for="kickoff-adviser">
										Assigned adviser{#if adviserRequired}<span class="text-destructive">
												*</span
											>{/if}
									</Label>
									<SelectMenu
										id="kickoff-adviser"
										items={advisers.map((adviser) => ({
											value: adviser.user_id,
											label: adviser.name || adviser.email || adviser.user_id
										}))}
										value={adviserId ?? ''}
										required={adviserRequired}
										placeholder={adviserRequired ? 'Choose an adviser' : 'The client’s own adviser'}
										onValueChange={(value) => (adviserId = value || null)}
									/>
									{#if adviserError && (showApplicantErrors || !advisers.length)}
										<p class="text-destructive text-xs">{adviserError}</p>
									{:else if adviserRequired}
										<p class="text-muted-foreground text-xs">
											These applicants have no adviser yet, and you are not marked as one, so the
											journey needs an adviser chosen here.
										</p>
									{:else if !inheritedAdviser && signedInIsAdviser}
										<p class="text-muted-foreground text-xs">
											Left blank, the journey is yours: these applicants have no adviser yet.
										</p>
									{:else}
										<p class="text-muted-foreground text-xs">
											Left blank, the journey is assigned to the adviser already looking after the
											applicants; only a client with no adviser falls to you.
										</p>
									{/if}
								</div>
							{/if}
							{#each intake.fields as field (field.key)}
								<div class="space-y-1.5" class:sm:col-span-2={field.type === 'textarea'}>
									<Label>
										{field.label}{#if field.required}<span class="text-destructive"> *</span>{/if}
									</Label>
									{#if field.type === 'select'}
										<SelectMenu
											items={field.options.map((option) => ({ value: option, label: option }))}
											bind:value={fieldValues[field.key]}
										/>
									{:else if field.type === 'textarea'}
										<Textarea rows={2} bind:value={fieldValues[field.key]} />
									{:else if field.type === 'checkbox'}
										<Checkbox
											checked={fieldValues[field.key] === 'true'}
											onCheckedChange={(value) =>
												(fieldValues[field.key] = value === true ? 'true' : '')}
										/>
									{:else if field.type === 'email'}
										<EmailInput
											value={fieldValues[field.key]}
											onchange={(v) => (fieldValues[field.key] = v)}
										/>
									{:else}
										<Input
											type={field.type === 'number'
												? 'number'
												: field.type === 'date'
													? 'date'
													: 'text'}
											bind:value={fieldValues[field.key]}
										/>
									{/if}
								</div>
							{/each}
						</CardContent>
					</Card>
				{/if}

				<section class="space-y-3">
					<SectionHeading title={personSubject ? 'Clients' : 'Applicants'} count={parties.length} />
					<!-- Normal row-major placement keeps the visual and keyboard order identical. Equal
					     implicit rows make every applicant card (and the add slot) the same height,
					     even when a child carries the extra registered-contact explanation. -->
					<div class="grid auto-rows-fr gap-3 lg:grid-cols-2">
						{#each parties as party, partyIndex (party.key)}
							{@const error = partyError(party)}
							{@const clash = emailClash(party)}
							<Card glass class="h-full overflow-visible">
								<CardContent
									class="h-full space-y-4"
									onfocusout={(event) => leftCard(event, () => (party.touched = true))}
								>
									<div class="flex items-center gap-2">
										<UsersIcon class="text-muted-foreground size-4" />
										<p class="min-w-0 flex-1 truncate font-medium">{partyLabel(party)}</p>
										{#if isChild(party)}<Badge variant="secondary">Child</Badge>{/if}
										{#if isRegisteredContact(party)}
											<Badge variant="outline">Registered contact</Badge>
										{/if}
										{#if parties.length > 1}
											<Button
												type="button"
												variant="ghost"
												size="icon"
												aria-label={`Remove ${personSubject ? 'client' : 'applicant'} ${partyIndex + 1}`}
												onclick={() => removeParty(partyIndex)}
											>
												<Trash2Icon class="size-4" />
											</Button>
										{/if}
									</div>
									{#if !personSubject}
										<Segmented
											size="sm"
											shape="square"
											label="Applicant source"
											value={party.mode}
											options={[
												{ value: 'new', label: 'New applicant' },
												{ value: 'existing', label: 'Existing client' }
											]}
											onValueChange={(value) => {
												if (value === 'new' || value === 'existing') setMode(party, value);
											}}
										/>
									{/if}

									{#if party.mode === 'existing'}
										<div class="space-y-1.5">
											<Label>Client</Label>
											<Combobox
												class="w-full"
												placeholder="Search this firm’s clients…"
												emptyText="No matching clients."
												options={clientOptions(party)}
												onOpen={refreshClients}
												bind:value={
													() => (party.contact_id ? String(party.contact_id) : ''),
													(value) => chooseExisting(party, value)
												}
											/>
											{#if party.contact_id}
												<p class="text-muted-foreground text-xs">
													{party.type === 'organisation'
														? 'Organisation'
														: `${party.dob || 'No date of birth recorded'} · ${party.email || 'No email recorded'}`}
												</p>
											{/if}
										</div>
									{:else}
										{#if !isRegisteredContact(party)}
											<Segmented
												size="sm"
												shape="square"
												label="Applicant type"
												value={party.type}
												options={[
													{ value: 'individual', label: 'Individual' },
													{ value: 'organisation', label: 'Organisation' }
												]}
												onValueChange={(value) => {
													if (value === 'individual' || value === 'organisation') {
														setPartyType(party, value);
													}
												}}
											/>
										{/if}
										{#if party.type === 'organisation'}
											<div class="grid gap-3 sm:grid-cols-2">
												<div class="space-y-1.5">
													<Label>Organisation name</Label>
													<Input bind:value={party.org_name} />
												</div>
												<div class="space-y-1.5">
													<Label for={`party-email-${party.key}`}
														>Email <span class="text-muted-foreground">(optional)</span></Label
													>
													<EmailInput
														id={`party-email-${party.key}`}
														value={party.email}
														onchange={(v) => (party.email = v)}
													/>
												</div>
											</div>
										{:else}
											<div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
												<div class="space-y-1.5">
													<Label>First name</Label>
													<Input bind:value={party.first_name} />
												</div>
												<div class="space-y-1.5">
													<Label
														>Middle name <span class="text-muted-foreground">(optional)</span
														></Label
													>
													<Input bind:value={party.middle_name} />
												</div>
												<div class="space-y-1.5">
													<Label>Surname</Label>
													<Input bind:value={party.surname} />
												</div>
												<div class="space-y-1.5">
													<Label>Date of birth</Label>
													<Input
														type="date"
														bind:value={() => party.dob, (value) => setDob(party, value)}
													/>
												</div>
												<div class="space-y-1.5">
													<Label for={`party-email-${party.key}`}>
														Email{#if isChild(party)}
															<span class="text-muted-foreground"> (optional)</span>
														{/if}
													</Label>
													<EmailInput
														id={`party-email-${party.key}`}
														value={party.email}
														onchange={(v) => (party.email = v)}
													/>
												</div>
											</div>
										{/if}
									{/if}

									{#if isChild(party)}
										{@const choices = registeredContactOptions(party)}
										{@const registered = partyOf(party.registered_contact_key ?? '')}
										<div class="border-border/70 space-y-1.5 border-t pt-4">
											<Label>Registered contact</Label>
											{#if choices.length > 1}
												<SelectMenu
													placeholder="Choose an adult"
													items={choices}
													value={party.registered_contact_key ?? ''}
													onValueChange={(value) => designateRegisteredContact(party, value)}
												/>
											{:else if registered}
												<p class="text-sm font-medium">{partyLabel(registered)}</p>
											{/if}
											<p class="text-muted-foreground text-xs">
												{registered && choices.length <= 1
													? 'Complete the other applicant’s card. They are designated as this child’s registered contact, complete KYC and receive the portal invite.'
													: 'Choose which adult applicant is this child’s registered contact. The same applicant can act for more than one child or account.'}
											</p>
										</div>
									{/if}

									{#if !personSubject && party.type === 'individual' && !isChild(party) && !party.identity_verified}
										<div
											class="border-border/70 flex flex-wrap items-center justify-between gap-2 border-t pt-4"
										>
											<Label class="flex items-center gap-1.5">
												<ShieldCheckIcon class="size-3.5" /> Identity verification
											</Label>
											<Segmented
												size="sm"
												shape="square"
												label="Identity verification"
												value={party.identity_mode}
												options={IDENTITY_OPTIONS}
												onValueChange={(value) => {
													if (isIdentityMode(value)) party.identity_mode = value;
												}}
											/>
										</div>
									{:else if !personSubject && party.type === 'individual' && !isChild(party) && party.identity_verified}
										<p class="text-success flex items-center gap-1.5 text-xs">
											<ShieldCheckIcon class="size-3.5" /> Existing identity verification will be reused.
										</p>
									{/if}

									{#if clash}
										<div class="flex flex-wrap items-center gap-2 text-xs" role="alert">
											<TriangleAlertIcon class="text-muted-foreground size-3.5" />
											<p class="text-muted-foreground">
												{#if samePersonClash(party)}
													{clientLabel(clash)} is already a client.
												{:else}
													{clientLabel(clash)} uses that email. Shared-email households are supported;
													check this is a different person.
												{/if}
											</p>
											<Button
												type="button"
												variant="outline"
												size="sm"
												onclick={() => useExisting(party, clash)}
											>
												Use client
											</Button>
										</div>
									{/if}
									<!-- Shown once the adviser has finished with this card or pressed Continue —
								     never while it is still being filled in. -->
									{#if error && (party.touched || showApplicantErrors)}
										<p class="text-destructive text-xs">{error}</p>
									{/if}
								</CardContent>
							</Card>
						{/each}
						<!-- The blank slot in the grid IS the control: click the space where the next
						     applicant would go. Two full-width buttons under the cards asked the adviser
						     to decide new-or-existing before there was a card to look at, and each card
						     already carries that choice — so this adds one and the card's own
						     "New applicant / Existing client" toggle answers the rest. -->
						{#if !personSubject || parties.length < 2}
							<button
								type="button"
								onclick={() => addParty(personSubject ? 'existing' : 'new')}
								class="border-border/70 text-muted-foreground hover:border-brand hover:bg-brand/5 hover:text-brand focus-visible:ring-ring/50 flex h-full min-h-28 w-full cursor-pointer items-center justify-center gap-2 rounded-xl border border-dashed text-sm transition-colors outline-none focus-visible:ring-2"
							>
								<PlusIcon class="size-4" />
								{personSubject
									? parties.length
										? 'Add another client'
										: 'Add a client'
									: parties.length
										? 'Add another applicant'
										: 'Add an applicant'}
							</button>
						{/if}
					</div>
				</section>
				<div class="flex items-center justify-end gap-4">
					{#if !applicantsReady}
						<p class="text-muted-foreground mr-auto text-xs">
							{personSubject
								? 'Choose one or two existing clients.'
								: adviserMissing && parties.every((party) => partyError(party) === null)
									? 'Choose an adviser for these applicants.'
									: 'Complete every applicant and designate an adult registered contact for each child.'}
						</p>
					{/if}
					<!-- Enabled even when the stage is incomplete: pressing it is how the adviser says
					     "I am done", and that is the moment to point at what is still missing. A
					     disabled button with nothing marked leaves them hunting for the reason. -->
					{#if personSubject && !needsReview}
						<Button
							type="submit"
							disabled={submitting}
							onclick={(event) => {
								if (applicantsReady) return;
								event.preventDefault();
								showApplicantErrors = true;
							}}
						>
							{submitting ? 'Starting…' : `Start ${selectedFlow.name}`}
						</Button>
					{:else}
						<Button
							type="button"
							onclick={() => {
								if (!applicantsReady) {
									showApplicantErrors = true;
									return;
								}
								wizardStep = personSubject ? 'review' : 'accounts';
							}}
						>
							{personSubject ? 'Continue to review' : 'Continue to accounts'}
						</Button>
					{/if}
				</div>
			{:else if wizardStep === 'accounts'}
				<section class="space-y-3">
					<SectionHeading title="Accounts" count={accounts.length} />
					{#each accounts as account, accountIndex (account.key)}
						{@const product = productOf(account)}
						{@const error = accountError(account)}
						<Card glass class="overflow-visible">
							<CardContent
								class="space-y-4"
								onfocusout={(event) => leftCard(event, () => (account.touched = true))}
							>
								<div class="flex items-center gap-2">
									<WalletIcon class="text-muted-foreground size-4" />
									<p class="flex-1 font-medium">Account {accountIndex + 1}</p>
									{#if accounts.length > 1}
										<Button
											type="button"
											variant="ghost"
											size="icon"
											aria-label={`Remove account ${accountIndex + 1}`}
											onclick={() => removeAccount(accountIndex)}
										>
											<Trash2Icon class="size-4" />
										</Button>
									{/if}
								</div>
								<div class="space-y-2">
									<Label>Account owners</Label>
									<div class="grid gap-2 sm:grid-cols-2">
										{#each parties as party (party.key)}
											<label
												class="border-border flex items-center gap-3 rounded-lg border p-3 text-sm"
											>
												<Checkbox
													checked={account.owner_keys.includes(party.key)}
													disabled={ownerSelectionConflicts(account, party)}
													onCheckedChange={(value) =>
														toggleOwner(account, party.key, value === true)}
												/>
												<span class="min-w-0 flex-1">
													<span class="block truncate font-medium">{partyLabel(party)}</span>
													<span class="text-muted-foreground text-xs">
														{party.type === 'organisation'
															? 'Organisation'
															: isChild(party)
																? 'Child owner'
																: isRegisteredContact(party)
																	? 'Adult · registered contact'
																	: 'Adult'}
													</span>
												</span>
											</label>
										{/each}
									</div>
									{#if account.owner_keys.length}
										<p class="text-muted-foreground text-xs">
											{accountContactCount(account)}
											{accountContactCount(account) === 1 ? 'account party' : 'account parties'}
											including required registered contacts.
										</p>
									{/if}
								</div>
								<Separator />
								<div class="grid gap-3 sm:grid-cols-3">
									<div class="space-y-1.5">
										<Label>Account type</Label>
										<SelectMenu
											placeholder={account.owner_keys.length
												? 'Choose a product'
												: 'Choose owners first'}
											items={productsFor(account).map((item) => ({
												value: String(item.id),
												label: item.name
											}))}
											value={account.product_id ? String(account.product_id) : ''}
											disabled={!account.owner_keys.length}
											onValueChange={(value) => chooseProduct(account, value)}
										/>
									</div>
									{#if product && product.custodians.length > 1}
										<div class="space-y-1.5">
											<Label>Custodian</Label>
											<SelectMenu
												items={product.custodians.map((provider) => ({
													value: provider,
													label: platformNames.get(provider) ?? provider
												}))}
												value={account.provider ?? ''}
												onValueChange={(value) => (account.provider = value || null)}
											/>
										</div>
									{:else if product && product.custodians.length === 1}
										<div class="space-y-1.5">
											<Label>Custodian</Label>
											<p class="pt-2 text-sm">
												{platformNames.get(product.custodians[0]) ?? product.custodians[0]}
											</p>
										</div>
									{/if}
									{#if product}
										{@const custodies = custodiesFor(
											product.allowed_custodies,
											account.provider,
											wizard.platforms
										)}
										{#if custodies.length > 1}
											<div class="space-y-1.5">
												<Label>Custody</Label>
												<SelectMenu
													items={custodies.map((custody) => ({
														value: custody,
														label: custody === 'onshore' ? 'Onshore' : 'Offshore'
													}))}
													value={account.custody ?? ''}
													onValueChange={(value) => {
														if (value === 'onshore' || value === 'offshore')
															account.custody = value;
													}}
												/>
											</div>
										{/if}
									{/if}
								</div>
								{#if product?.product_type === 'SIPP'}
									{@const illustrationOptions = illustrationsFor(account)}
									{@const existingOwners = account.owner_keys
										.map(partyOf)
										.filter((party): party is Party => party?.mode === 'existing')}
									{@const newOwners = account.owner_keys
										.map(partyOf)
										.filter((party): party is Party => party?.mode === 'new')}
									<div class="border-border space-y-3 rounded-lg border p-3">
										<div>
											<Label>SIPP illustration</Label>
											<p class="text-muted-foreground text-xs">
												Choose the successful illustration this account is being opened against. Its
												matching assumptions will prefill the fact-find for review.
											</p>
										</div>
										{#if illustrationOptions.length}
											<div class="-mx-1 overflow-x-auto px-1 pb-2">
												<div class="flex min-w-max snap-x gap-2">
													{#each illustrationOptions as illustration (illustration.uid)}
														<div
															class={[
																'w-64 snap-start rounded-lg border p-3',
																account.illustration_uid === illustration.uid
																	? 'border-primary bg-primary/5 ring-primary/20 ring-2'
																	: 'border-border bg-background'
															]}
														>
															<button
																type="button"
																class="w-full text-left"
																onclick={() => (account.illustration_uid = illustration.uid)}
															>
																<p class="truncate text-sm font-medium">
																	{illustration.name ??
																		illustration.quotation_type ??
																		'SIPP illustration'}
																</p>
																<p class="text-muted-foreground mt-1 line-clamp-2 text-xs">
																	{illustrationLabel(illustration)}
																</p>
															</button>
															<div class="mt-3 flex flex-wrap items-center gap-2">
																<Button
																	type="button"
																	size="sm"
																	variant={account.illustration_uid === illustration.uid
																		? 'default'
																		: 'outline'}
																	onclick={() => (account.illustration_uid = illustration.uid)}
																>
																	{account.illustration_uid === illustration.uid
																		? 'Selected'
																		: 'Use this illustration'}
																</Button>
																{#if illustration.document_uid}
																	<Button
																		type="button"
																		variant="ghost"
																		size="sm"
																		onclick={() => (previewingIllustration = illustration)}
																	>
																		<FileTextIcon class="size-3.5" />
																		Preview
																	</Button>
																{/if}
															</div>
														</div>
													{/each}
												</div>
											</div>
										{:else}
											<div class="bg-warning-subtle text-warning-strong rounded-md p-3 text-xs">
												{existingOwners.length
													? 'No unused successful SIPP illustration is available for the selected owner.'
													: 'A new applicant must first be saved as a client and have a successful SIPP illustration run.'}
											</div>
										{/if}
										<div class="flex flex-wrap gap-2">
											{#each existingOwners as owner (owner.key)}
												<Button
													type="button"
													variant="outline"
													size="sm"
													onclick={() => openIllustration(account, owner)}
												>
													Run illustration for {partyLabel(owner)}
												</Button>
											{/each}
											<Button
												type="button"
												variant="ghost"
												size="sm"
												disabled={refreshingIllustrations}
												onclick={async () => {
													refreshingIllustrations = true;
													await loadAccounts(true);
													refreshingIllustrations = false;
												}}
											>
												<RefreshCwIcon
													class={refreshingIllustrations ? 'size-3.5 animate-spin' : 'size-3.5'}
												/>
												{refreshingIllustrations ? 'Refreshing…' : 'Refresh illustrations'}
											</Button>
										</div>
										{#each newOwners as owner (owner.key)}
											<div class="bg-muted/30 space-y-3 rounded-lg p-3">
												<div>
													<p class="text-sm font-medium">
														Create {partyLabel(owner)} before illustrating
													</p>
													<p class="text-muted-foreground text-xs">
														This saves the applicant as a client, then opens the standard
														illustration form here. The client stays selected as this account’s
														owner.
													</p>
												</div>
												{#if canCreateIllustrationClient}
													<Button
														type="button"
														size="sm"
														disabled={creatingIllustrationClient === owner.key}
														onclick={() => createIllustrationClient(account, owner)}
													>
														{creatingIllustrationClient === owner.key
															? 'Creating client…'
															: 'Create client & run illustration'}
													</Button>
												{:else}
													<p class="text-destructive text-xs">
														You need client-create and illustration permissions to do this.
													</p>
												{/if}
											</div>
										{/each}
										{#if illustrationCreateError}
											<p class="text-destructive text-xs">{illustrationCreateError}</p>
										{/if}
									</div>
								{/if}
								{#if error && (account.touched || showAccountErrors)}
									<p class="text-destructive text-xs">{error}</p>
								{/if}
							</CardContent>
						</Card>
					{/each}
					<Button
						type="button"
						variant="outline"
						onclick={addAccount}
						class="border-border/70 text-muted-foreground hover:text-foreground h-auto w-full justify-center border-dashed py-3"
					>
						<PlusIcon class="size-4" /> Add account
					</Button>
				</section>
				<div class="flex items-center justify-between gap-3">
					<Button type="button" variant="outline" onclick={() => (wizardStep = 'applicants')}>
						Back to applicants
					</Button>
					{#if needsReview}
						<Button
							type="button"
							onclick={() => {
								if (!accountsReady) {
									showAccountErrors = true;
									// `accountsReady` includes the applicants: coming back and emptying a card
									// there is the one way this stage can be complete and still refuse.
									showApplicantErrors = !applicantsReady;
									return;
								}
								wizardStep = 'review';
							}}
						>
							Continue to review
						</Button>
					{:else}
						<Button
							type="submit"
							disabled={submitting}
							onclick={(event) => {
								if (accountsReady) return;
								event.preventDefault();
								showAccountErrors = true;
								showApplicantErrors = !applicantsReady;
							}}
						>
							{submitting ? 'Starting…' : `Start ${selectedFlow.name}`}
						</Button>
					{/if}
				</div>
			{:else if personSubject}
				<div class="grid gap-4 xl:grid-cols-[1fr_20rem]">
					<div class="space-y-3">
						{#each parties as party (party.key)}
							<Card glass>
								<CardContent>
									<p class="text-sm font-medium">{partyLabel(party)}</p>
									<p class="text-muted-foreground mt-1 text-xs">Existing client</p>
								</CardContent>
							</Card>
						{/each}
						<p class="text-muted-foreground text-sm">No account is opened.</p>
					</div>
					<Card glass class="h-fit xl:sticky xl:top-4">
						<CardContent class="space-y-4">
							<div>
								<p class="font-medium">Ready to start</p>
								<p class="text-muted-foreground text-xs">{selectedFlow.name}</p>
							</div>
							<p class="text-sm">No account is opened.</p>
							<Button type="submit" class="w-full" disabled={!applicantsReady || submitting}>
								{submitting ? 'Starting…' : `Start ${selectedFlow.name}`}
							</Button>
							<Button
								type="button"
								variant="outline"
								class="w-full"
								onclick={() => (wizardStep = 'applicants')}
							>
								Back to clients
							</Button>
						</CardContent>
					</Card>
				</div>
			{:else}
				<div class="grid gap-4 xl:grid-cols-[1fr_20rem]">
					<div class="space-y-3">
						{#each accounts as account, index (account.key)}
							{@const product = productOf(account)}
							<Card glass>
								<CardContent class="space-y-4">
									<div class="flex items-start justify-between gap-4">
										<div>
											<p class="font-medium">Account {index + 1} · {product?.name}</p>
											<p class="text-muted-foreground text-xs">
												{account.provider
													? (platformNames.get(account.provider) ?? account.provider)
													: 'Custodian set later'}
												· {humanise(account.custody)}
											</p>
											{#if illustrationOf(account)}
												<p class="text-muted-foreground mt-1 text-xs">
													Illustration · {illustrationLabel(illustrationOf(account)!)}
												</p>
											{/if}
										</div>
										<Badge variant="outline">
											{accountContactCount(account)}
											{accountContactCount(account) === 1 ? 'party' : 'parties'}
										</Badge>
									</div>
									<div class="grid gap-2 sm:grid-cols-2">
										{#each account.owner_keys as ownerKey (ownerKey)}
											{@const owner = partyOf(ownerKey)}
											{#if owner}
												<div class="border-border rounded-lg border p-3">
													<div class="flex flex-wrap items-center gap-2">
														<p class="text-sm font-medium">{partyLabel(owner)}</p>
														<Badge variant="outline">Owner</Badge>
														<Badge variant="secondary">
															{owner.mode === 'new' ? 'New client' : 'Existing client'}
														</Badge>
													</div>
													{#if isChild(owner) && owner.registered_contact_key}
														<p class="text-muted-foreground mt-1 text-xs">
															Registered contact: {partyLabel(
																partyOf(owner.registered_contact_key)!
															)}
														</p>
													{:else if owner.type === 'individual'}
														<p class="text-muted-foreground mt-1 flex items-center gap-1.5 text-xs">
															<ShieldCheckIcon class="size-3.5" />
															{owner.identity_verified
																? 'Existing IDV reused'
																: owner.identity_mode === 'manual'
																	? 'Manual identity verification'
																	: digitalIdvAvailable
																		? 'Automatic digital identity verification'
																		: 'Automatic route (manual while no digital provider is configured)'}
														</p>
													{/if}
												</div>
											{/if}
										{/each}
									</div>
								</CardContent>
							</Card>
						{/each}
					</div>
					<Card glass class="h-fit xl:sticky xl:top-4">
						<CardContent class="space-y-4">
							<div>
								<p class="font-medium">Ready to start</p>
								<p class="text-muted-foreground text-xs">{selectedFlow.name}</p>
							</div>
							<dl class="grid grid-cols-2 gap-3 text-xs">
								<div>
									<dt class="text-muted-foreground text-xs">Accounts</dt>
									<dd>{accounts.length}</dd>
								</div>
								<div>
									<dt class="text-muted-foreground text-xs">People and organisations</dt>
									<dd>{parties.length}</dd>
								</div>
								<div class="col-span-2">
									<dt class="text-muted-foreground text-xs">Assigned adviser</dt>
									<dd>{adviserName}</dd>
								</div>
							</dl>
							<Separator />
							<Button type="submit" class="w-full" disabled={!accountsReady || submitting}>
								{submitting ? 'Starting…' : `Start ${selectedFlow.name}`}
							</Button>
							<Button
								type="button"
								variant="outline"
								class="w-full"
								onclick={() => (wizardStep = 'accounts')}
							>
								Back to accounts
							</Button>
						</CardContent>
					</Card>
				</div>
			{/if}
		</form>
	{/if}
</div>

<DocumentPreviewSheet
	open={previewingIllustration !== null}
	name={previewingIllustration?.name ??
		previewingIllustration?.quotation_type ??
		'SIPP illustration'}
	subtitle={previewingIllustration ? illustrationLabel(previewingIllustration) : undefined}
	src={previewingIllustration?.document_uid
		? `/documents/${previewingIllustration.document_uid}/content`
		: undefined}
	contentType="application/pdf"
	downloadHref={previewingIllustration?.document_uid
		? `/documents/${previewingIllustration.document_uid}/content`
		: undefined}
	downloadName="illustration.pdf"
	onOpenChange={(open) => {
		if (!open) previewingIllustration = null;
	}}
/>

{#if illustrationParty?.contact_uid}
	<IllustrationDrawer
		bind:open={illustrationDrawerOpen}
		contactUid={illustrationParty.contact_uid}
		clientName={partyLabel(illustrationParty)}
		client={{
			type: illustrationParty.type,
			dob: illustrationParty.dob,
			retirement_age: illustrationParty.retirement_age,
			marital_status: illustrationParty.marital_status
		}}
		{models}
		existingSippTotal={illustrationSippTotal}
		navigateToDocument={false}
		onfilled={(facts) => {
			if (!illustrationParty) return;
			if (facts.retirement_age != null) illustrationParty.retirement_age = facts.retirement_age;
			if (facts.marital_status) illustrationParty.marital_status = facts.marital_status;
		}}
		ongenerated={async (result) => {
			if (
				illustrationAccount &&
				result.illustration_uid &&
				!result.placeholder &&
				result.errors.length === 0
			) {
				illustrationAccount.illustration_uid = result.illustration_uid;
				const generated =
					illustrationParty?.contact_id != null
						? {
								uid: result.illustration_uid,
								contact_id: illustrationParty.contact_id,
								contact_uid: illustrationParty.contact_uid ?? '',
								client_name: partyLabel(illustrationParty),
								name: null,
								created_at: new Date().toISOString(),
								quotation_type: null,
								existing_fund_value: illustrationSippTotal,
								output_reference: null,
								model_name: null,
								document_uid: result.document_uid
							}
						: null;
				await loadAccounts(true);
				// The reload is what makes the card appear. If it has not listed this run yet,
				// keep the one we just filed so the selection above is a card, not a bare id.
				if (generated) illustrations = keepGeneratedIllustration(illustrations, generated);
				illustrationDrawerOpen = false;
			}
		}}
	/>
{/if}

<svelte:document
	onvisibilitychange={() => {
		if (document.visibilityState === 'visible') refreshClients();
	}}
/>

<ConfirmDialog
	bind:open={stopLiveJourneyOpen}
	title="Stop the live journey?"
	description="The journey this client is already on will be cancelled, including any work done on it, and this one will start in its place."
	confirmLabel="Stop it and start this one"
	pendingLabel="Stopping…"
	destructive
	onconfirm={stopLiveJourney}
/>
