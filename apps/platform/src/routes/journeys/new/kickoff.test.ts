import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import {
	adviserChoiceRequired,
	completedAge,
	custodiesFor,
	defaultCustody,
	keepGeneratedIllustration,
	openableCustodies,
	signedInIsAdviser
} from './kickoff';

const source = readFileSync(new URL('./kickoff-wizard.svelte', import.meta.url), 'utf8');
const server = readFileSync(new URL('./+page.server.ts', import.meta.url), 'utf8');
const listing = readFileSync(new URL('../+page.svelte', import.meta.url), 'utf8');

describe('applicant-first journey kickoff', () => {
	it('selects the flow before kickoff and presents three ordered stages', () => {
		expect(listing).toContain('newJourneys: inbox.startFlows.map');
		expect(listing).toContain('?flow=');
		expect(source).toContain('wizard.selectedFlowCode');
		expect(source).toContain("{ value: 'applicants', label: '1 · Applicants' }");
		expect(source).toContain("{ value: 'accounts', label: '2 · Accounts' }");
		expect(source).toContain("{ value: 'review', label: '3 · Review' }");
		expect(source).not.toContain('I confirm this setup is accurate and ready to start.');
		expect(source).not.toContain('adviserDeclared');
	});

	it('fills the first card with the client the start came from, once the clients load', () => {
		// `?client=<uid>` is read on the server and applied in the wizard AFTER the applicants
		// fetch — the client list is not there at first paint — and only into an untouched card.
		expect(server).toContain("url.searchParams.get('client')");
		expect(source).toContain('wizard.prefillClientUid');
		expect(source).toContain('prefillClient();');
		expect(source).toContain('useExisting(first, client);');
		expect(source).toContain('first.contact_id !== null || first.first_name || first.surname');
	});

	it('gathers canonical applicants before assigning account roles', () => {
		expect(source).toContain("type WizardStep = 'applicants' | 'accounts' | 'review'");
		expect(source).toContain("title={personSubject ? 'Clients' : 'Applicants'}");
		expect(source).toContain('owner_keys: string[]');
		expect(source).toContain('checked={account.owner_keys.includes(party.key)}');
		expect(source).toContain('toggleOwner(account, party.key, value === true)');
		expect(source).toContain('Choose one or more owners before selecting a product.');
		expect(source).not.toContain('blankAccount(primaryPartyKey');
	});

	it('requires complete new and existing individual records', () => {
		expect(source).toContain('Enter the applicant’s first name and surname.');
		expect(source).toContain('Enter the applicant’s date of birth.');
		expect(source).toContain('Enter the adult applicant’s email.');
		expect(source).toContain('This client has no date of birth recorded.');
		expect(source).toContain('This adult client has no email recorded.');
		expect(source).toContain("party.type === 'organisation'");
	});

	// Two full-width "Add new applicant" / "Add existing applicant" buttons sat under the cards and
	// asked the adviser to choose the source before there was a card to look at — which each card's
	// own toggle already asks. The next applicant is added by clicking the blank slot in the grid.
	it('adds an applicant from the empty slot in the grid, not a pair of buttons below it', () => {
		expect(source).toContain('Add another applicant');
		expect(source).toContain('border-dashed');
		expect(source).not.toContain('Add new applicant');
		expect(source).not.toContain('Add existing applicant');
	});

	// A blank card is incomplete, not wrong: every new applicant used to render "Enter the
	// applicant’s first name and surname." in destructive red the moment it appeared, which reads as
	// a rejected submission. The error waits until focus has LEFT the card or Continue has been
	// pressed — and Continue stays enabled precisely so pressing it can say what is outstanding.
	it('holds each card’s error back until it is left or Continue is pressed', () => {
		expect(source).toContain('{#if error && (party.touched || showApplicantErrors)}');
		expect(source).toContain('{#if error && (account.touched || showAccountErrors)}');
		expect(source).toContain(
			'onfocusout={(event) => leftCard(event, () => (party.touched = true))}'
		);
		expect(source).toContain('function leftCard');
		// Focus moving BETWEEN this card's own fields must not count as leaving it.
		expect(source).toContain('card.contains(next)');
		expect(source).toContain('showApplicantErrors = true;');
		expect(source).toContain('showAccountErrors = true;');
		expect(source).not.toContain('disabled={!applicantsReady}');
		expect(source).not.toContain('disabled={!accountsReady} onclick');
	});

	it('skips accounts when the published flow gathers people', () => {
		expect(source).toContain("intake.subject === 'person'");
		expect(source).toContain('clients:');
		expect(source).toContain('No account is opened.');
		expect(source).toContain("{ value: 'accounts', label: '2 · Accounts' }");
		expect(source).toContain('const needsReview = $derived(intake.review ?? !personSubject)');
		expect(source).toContain('{#if wizardSteps.length > 1}');
		expect(source).toContain('{#if personSubject && !needsReview}');
		expect(source).toContain("{ value: 'applicants' as const, label: '1 · Clients' }");
		expect(source).toContain("title={personSubject ? 'Clients' : 'Applicants'}");
		expect(source).toContain('Add another client');
		expect(source).toContain('(advisers.length || adviserRequired) && !personSubject');
		expect(source).toContain(
			"{#if !personSubject && party.type === 'individual' && !isChild(party) && !party.identity_verified}"
		);
		expect(source).toContain('flow_code: flowCode,\n\t\t\t\t\taccounts: [],');
	});

	it('keeps duplicate-email selection and shared-email guidance', () => {
		expect(source).toContain('function samePersonClash');
		expect(source).toContain('Use client');
		expect(source).toContain('Shared-email households are supported');
	});

	// Left blank, the adviser picker falls to the applicants' own adviser, then to the signed-in
	// user when they are marked an adviser. An administrator starting a NEW client had neither,
	// and the kickoff went to the API to be refused (and, before that, to 500). The wizard now
	// asks for the choice up front in exactly that case.
	it('requires an adviser when nobody on the application brings one and the user is not one', () => {
		const newClient = { mode: 'new', adviser_id: null } as const;
		const orphan = { mode: 'existing', adviser_id: null } as const;
		const looked_after = { mode: 'existing', adviser_id: 'adv-1' } as const;
		expect(adviserChoiceRequired([newClient], false)).toBe(true);
		expect(adviserChoiceRequired([orphan], false)).toBe(true);
		expect(adviserChoiceRequired([newClient, orphan], false)).toBe(true);
		// One applicant with an adviser is enough — the API gives the journey to that adviser.
		expect(adviserChoiceRequired([newClient, looked_after], false)).toBe(false);
		// A signed-in adviser is the fallback, so the picker stays optional for them.
		expect(adviserChoiceRequired([newClient], true)).toBe(false);
		expect(adviserChoiceRequired([], false)).toBe(true);
	});

	it('knows the signed-in user by email, without regard to case', () => {
		const advisers = [{ email: 'amy@firm.test' }, { email: null }];
		expect(signedInIsAdviser('Amy@Firm.test', advisers)).toBe(true);
		expect(signedInIsAdviser(' amy@firm.test ', advisers)).toBe(true);
		expect(signedInIsAdviser('ops@firm.test', advisers)).toBe(false);
		expect(signedInIsAdviser(null, advisers)).toBe(false);
		expect(signedInIsAdviser('', advisers)).toBe(false);
	});

	it('wires the rule into the applicants step', () => {
		expect(source).toContain('adviserChoiceRequired(parties, signedInIsAdviser)');
		expect(source).toContain('party.adviser_id = client.adviser_id ?? null;');
		expect(source).toContain('signedInIsAdviser = body.signedInIsAdviser === true;');
		// It gates Continue, and the picker says why when it is required.
		expect(source).toContain('!adviserMissing &&');
		expect(source).toContain('required={adviserRequired}');
		expect(source).toContain(
			'Choose an adviser. These applicants have none yet, and you are not marked as an adviser.'
		);
		expect(source).toContain('Nobody at this firm is marked as an adviser.');
		// Placeholders never promise a fallback that does not exist.
		expect(source).toContain(
			"placeholder={adviserRequired ? 'Choose an adviser' : 'The client’s own adviser'}"
		);
	});
});

describe('SIPP illustration selection', () => {
	it('presents owner-scoped runs as a carousel and carries one through review', () => {
		expect(source).toContain("product?.product_type === 'SIPP'");
		expect(source).toContain('function illustrationsFor(account: Account)');
		expect(source).toContain("party?.mode === 'existing'");
		expect(source).toContain('snap-x');
		expect(source).toContain('Use this illustration');
		expect(source).toContain('Preview');
		expect(source).toContain('DocumentPreviewSheet');
		expect(source).toContain('previewingIllustration = illustration');
		expect(source).toContain('/documents/${previewingIllustration.document_uid}/content');
		expect(source).toContain('Run a successful SIPP illustration for an existing owner');
		expect(source).toContain('account.illustration_uid = null');
		expect(source).toContain('illustration_uid: account.illustration_uid');
		expect(source).toContain('Illustration · {illustrationLabel');
		expect(source).toContain('Refresh illustrations');
	});

	it('can persist a new applicant, run the standard form in-place and select its result', () => {
		expect(source).toContain('Create client & run illustration');
		expect(source).toContain('<IllustrationDrawer');
		expect(source).toContain('navigateToDocument={false}');
		expect(source).toContain('illustrationAccount.illustration_uid = result.illustration_uid');
		expect(source).toContain('keepGeneratedIllustration');
		expect(source).toContain("party.mode = 'existing'");
		expect(source).toContain('existingSippTotal={illustrationSippTotal}');
		expect(source).toContain('/journeys/new/sipp-total?uid=');
		expect(server).toContain('createIllustrationClient: async');
		expect(server).toContain('`${apiUrl()}/firm/contacts`');
		// The new client is created without a retirement age or marital status. The illustration
		// form asks for the gap and generating writes it back — asking here as well was a second
		// form for the same two facts.
		expect(server).not.toContain('retirement_age: retirementAge');
		expect(source).toContain('onfilled=');
		expect(source).not.toContain('Retirement age</Label>');
	});

	it('keeps a just-generated illustration selected when the reload has not listed it', () => {
		const generated = { uid: 'ill_new', contact_id: 9 };
		expect(keepGeneratedIllustration([], generated)).toEqual([generated]);
		expect(keepGeneratedIllustration([{ uid: 'ill_old', contact_id: 9 }], generated)[0]).toEqual(
			generated
		);
		// The catalogue's own row wins once it has arrived, so we do not duplicate it.
		expect(keepGeneratedIllustration([generated], generated)).toEqual([generated]);
	});

	it('keeps kickoff itself a named action and the create out of the wizard form', () => {
		// SvelteKit refuses a page holding both a default and a named action, so adding
		// `createIllustrationClient` turned every "Start onboarding" press into a 500.
		expect(server).toContain('start: async');
		expect(server).not.toContain('default: async');
		expect(source).toContain('action="?/start"');
		// And the create posts on its own: a <form> inside the wizard's form is invalid HTML, and
		// the bubbling submit made the outer enhance post the whole draft journey.
		expect(source).toContain("fetch('?/createIllustrationClient'");
		expect(source).not.toContain('action="?/createIllustrationClient"');
	});
});

describe('registered contacts and identity', () => {
	it('links each child to a reusable eligible adult', () => {
		expect(source).toContain('registered_contact_key: string | null');
		expect(source).toContain('function isEligibleRegisteredContact');
		expect(source).toContain('age >= 18 && !!party.email.trim()');
		expect(source).toContain('The same applicant can act for more than one child or account.');
		expect(source).toContain("ownerPayload(registered, 'registered_contact', childKeys)");
		expect(source).toContain('registered_contact_for: registeredContactFor');
		expect(source).toContain('New applicant');
		expect(source).toContain("addRegisteredContact(party, 'new')");
		expect(source).toContain('lg:grid-cols-2');
		expect(source).toContain('setDob(party, value)');
		expect(source).toContain('if (!isChild(party) || party.registered_contact_key) return;');
		expect(source).toContain('party.registered_contact_key = existingAdult.key');
		expect(source).toContain('designateRegisteredContact(party, value)');
		expect(source).toContain('setPartyType(party, value)');
		expect(source).not.toContain('PartyPurpose');
	});

	it('offers the registered-contact picker only when there is another adult to pick', () => {
		expect(source).toContain('{#if choices.length > 1}');
		expect(source).toContain('{:else if registered}');
		expect(source).toContain('{partyLabel(registered)}');
	});

	it('counts automatically attached registered contacts against max parties', () => {
		expect(source).toContain('function accountContactCount');
		expect(source).toContain('account.owner_keys.length + registeredContacts.size');
		expect(source).toContain('accountContactCount(account) > product.max_parties');
		expect(source).toContain('hasOwnerRegisteredContactConflict(account)');
	});

	it('records one identity choice per adult and never asks a child', () => {
		expect(source).toContain("type IdentityMode = 'default' | 'manual'");
		expect(source).toContain("party.type === 'individual' && !isChild(party)");
		expect(source).toContain('!party.identity_verified');
		expect(source).toContain('Existing identity verification will be reused.');
		expect(source).toContain('!isChild(party) &&');
	});
});

describe('product eligibility', () => {
	it('requires every owner type and age to fit the product', () => {
		expect(source).toContain(
			'owners.some((owner) => !product.allowed_client_types.includes(owner.type))'
		);
		expect(source).toContain('owners.every((owner) => ownerAgeError(product, owner) === null)');
		expect(source).toContain('product.min_age');
		expect(source).toContain('product.max_age');
	});

	it('clears stale product, custodian and custody selections', () => {
		expect(source).toContain('function invalidateProduct');
		expect(source).toContain('account.product_id = null');
		expect(source).toContain('account.provider = null');
		expect(source).toContain('account.custody = null');
		expect(source).toContain('invalidateProduct(account);');
	});

	it('selects the product when the chosen owners leave exactly one eligible option', () => {
		expect(source).toContain('const eligible = productsFor(account);');
		expect(source).toContain('if (eligible.length === 1)');
		expect(source).toContain('account.product_id = eligible[0].id;');
		expect(source).toContain('normaliseAccount(account);');
	});

	it('keeps provider and custody account-specific', () => {
		expect(source).toContain('provider: account.provider');
		expect(source).toContain('custody: account.custody');
		expect(source).toContain('custodians.length === 1 ? custodians[0] : null');
		expect(source).toContain('custodiesFor(product.allowed_custodies');
		expect(source).toContain('defaultCustody(custodies)');
		expect(source).toContain('Custodian set later');
	});
});

describe('owner age calculation', () => {
	it('handles future and leap-day dates', () => {
		expect(completedAge('2027-09-15', new Date(2026, 8, 15))).toBe(-1);
		expect(completedAge('2008-02-29', new Date(2026, 1, 28))).toBe(17);
		expect(completedAge('2008-02-29', new Date(2026, 2, 1))).toBe(18);
		expect(completedAge('not-a-date', new Date(2026, 8, 15))).toBeNull();
	});
});

describe('custodian × product custody', () => {
	it('keeps Seccl onshore-only and defaults the picker to onshore', () => {
		expect(
			openableCustodies('seccl', { id: 'seccl', openable_custodies: ['onshore', 'offshore'] })
		).toEqual(['onshore']);
		expect(
			custodiesFor(['onshore', 'offshore'], 'seccl', [
				{ id: 'seccl', openable_custodies: ['onshore'] }
			])
		).toEqual(['onshore']);
		expect(
			custodiesFor(['onshore', 'offshore'], 'sei', [{ id: 'sei', openable_custodies: ['onshore'] }])
		).toEqual(['onshore']);
		expect(custodiesFor(['onshore', 'offshore'], 'sei', [{ id: 'sei' }])).toEqual([
			'onshore',
			'offshore'
		]);
		expect(defaultCustody(['onshore', 'offshore'])).toBe('onshore');
		expect(defaultCustody(['offshore'])).toBe('offshore');
	});
});
