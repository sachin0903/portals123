import { describe, expect, it } from 'vitest';
import {
	catalogueTerms,
	partyRows,
	roleLabel,
	sortTermsByDefinitionOrder,
	stepKey,
	termsAgreeIndex
} from './onboarding';

describe('catalogueTerms', () => {
	const fees = { uid: 'trm_fees', title: 'Fee Schedule', owner: 'tenant' };
	const platform = { uid: 'trm_platform', title: 'Platform Terms', owner: 'tenant' };
	const privacy = { uid: 'trm_privacy', title: 'Privacy policy', owner: 'graphene' };

	it("lists the firm's own outstanding terms — they are what holds the account open", () => {
		// The bug: only Graphene's terms were listed, so a firm's term that no journey step pinned
		// could never be accepted, and the hand-off refused for ever.
		expect(catalogueTerms([fees, platform, privacy], [])).toEqual([fees, platform, privacy]);
	});

	it('keeps a term whose title matches a step that pins a DIFFERENT document', () => {
		// The seeded "Platform terms" step pins a library document; the firm's "Platform Terms"
		// catalogue term is a separate obligation the gate checks. A title match hid it.
		const steps = [{ term_uid: null }, { term_uid: undefined }];
		expect(catalogueTerms([platform], steps)).toEqual([platform]);
	});

	it('leaves out a term a step already pins — that step is where it is accepted', () => {
		expect(catalogueTerms([fees, platform], [{ term_uid: 'trm_platform' }])).toEqual([fees]);
	});
});

describe('stepKey', () => {
	/** The reported bug: the onboarding page keyed its {#each} on `step_key`, and a client who is a
	 *  party to more than one case receives the same key twice. Svelte 5 throws `each_key_duplicate`
	 *  and aborts hydration, blanking the page. Keys must be unique across the flattened list. */
	it('is unique when the same step_key appears in more than one case', () => {
		const steps = [
			{ step_key: 'terms_general', item_uid: 'item-a' },
			{ step_key: 'kyc_form', item_uid: 'item-b' },
			{ step_key: 'terms_general', item_uid: 'item-c' }
		];
		const keys = steps.map(stepKey);
		expect(new Set(keys).size).toBe(steps.length);
	});

	it('prefers the backing request item, so a key survives reordering', () => {
		expect(stepKey({ step_key: 'terms_general', item_uid: 'item-a' }, 0)).toBe('item:item-a');
		expect(stepKey({ step_key: 'terms_general', item_uid: 'item-a' }, 4)).toBe('item:item-a');
	});

	it('stays unique for staff steps, which carry no request item', () => {
		const steps = [
			{ step_key: 'setup_account', item_uid: null },
			{ step_key: 'setup_account', item_uid: null },
			{ step_key: 'setup_account', item_uid: undefined }
		];
		const keys = steps.map(stepKey);
		expect(new Set(keys).size).toBe(steps.length);
	});

	it('never collides a fallback key with a real item uid', () => {
		const keys = [
			stepKey({ step_key: 'a', item_uid: 'a:1' }, 0),
			stepKey({ step_key: 'a', item_uid: null }, 1)
		];
		expect(new Set(keys).size).toBe(2);
	});
});

describe('termsAgreeIndex', () => {
	it('counts in definition order after the first declaration is agreed', () => {
		const pendingFirst = [
			{ key: 'item:b', order: 20 },
			{ key: 'item:c', order: 30 },
			{ key: 'item:a', order: 10 }
		];
		expect(sortTermsByDefinitionOrder(pendingFirst).map((s) => s.key)).toEqual([
			'item:a',
			'item:b',
			'item:c'
		]);
		expect(termsAgreeIndex(pendingFirst, 'item:a')).toBe(1);
		expect(termsAgreeIndex(pendingFirst, 'item:b')).toBe(2);
		expect(termsAgreeIndex(pendingFirst, 'item:c')).toBe(3);
	});
});

describe('partyRows', () => {
	/** THE REGRESSION: a JISA names the child as owner and the adult who acts for them as the
	 *  registered contact, and the dossier listed both under one "Owners" row — to the parent,
	 *  who is the one reading it. A child owning the money and being unable to reach it until 18
	 *  is the whole of what a JISA is. */
	it('keeps a JISA’s registered contact off the owner row', () => {
		expect(
			partyRows({
				owners: ['Ivy Client'],
				parties: [
					{ name: 'Ivy Client', role: 'owner' },
					{ name: 'Rachel Client', role: 'registered_contact' }
				]
			})
		).toEqual([
			{ label: 'Owner', value: 'Ivy Client' },
			{ label: 'Registered contact', value: 'Rachel Client' }
		]);
	});

	it('pluralises a joint account’s owners', () => {
		expect(
			partyRows({
				owners: ['Alice Able', 'Bob Baker'],
				parties: [
					{ name: 'Alice Able', role: 'owner' },
					{ name: 'Bob Baker', role: 'owner' }
				]
			})
		).toEqual([{ label: 'Owners', value: 'Alice Able, Bob Baker' }]);
	});

	it('falls back to the flat owner list when roles are absent', () => {
		expect(partyRows({ owners: ['Alice Able'] })).toEqual([
			{ label: 'Owner', value: 'Alice Able' }
		]);
		expect(partyRows({})).toEqual([]);
	});

	it('shows a role it has no wording for rather than dropping the person', () => {
		expect(partyRows({ parties: [{ name: 'Cole Trustee', role: 'settlor' }] })).toEqual([
			{ label: 'Settlor', value: 'Cole Trustee' }
		]);
	});
});

describe('roleLabel', () => {
	it('reads a role in the portal’s own words', () => {
		expect(roleLabel('registered_contact')).toBe('Registered contact');
		expect(roleLabel('owner')).toBe('Owner');
		expect(roleLabel('')).toBe('Named on the application');
	});
});
