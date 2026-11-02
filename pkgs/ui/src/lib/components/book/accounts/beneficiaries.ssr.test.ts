/**
 * Beneficiary nominations — the panel a member's death benefits are read off.
 *
 * Read once, years later, by somebody who cannot ask the member what they meant. So the two things
 * pinned here are both about being CHECKABLE without help: the running total is stated in words
 * (nothing else can tell a firm the set is unfinished — the API deliberately does not refuse a
 * short one), and a nomination is added and withdrawn rather than edited in place.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import Beneficiaries from './beneficiaries.svelte';
import type { Beneficiary } from './types.js';

/** The component's own source, line endings normalised — the add form is behind client state, so
 *  the title control is pinned as text the way the client record's is. */
const componentSource = (): string =>
	readFileSync(join(import.meta.dirname, 'beneficiaries.svelte'), 'utf8').replace(/\r\n/g, '\n');

const enhance = () => ({ destroy() {} });
const actions = { enhance, addAction: '?/addBeneficiary', removeAction: '?/removeBeneficiary' };

const ben = (over: Partial<Beneficiary> = {}): Beneficiary => ({
	uid: 'ben_1',
	title: 'Mrs',
	name: 'Jamie Halloway',
	relationship: 'Spouse',
	proportion: 60,
	...over
});

describe('the beneficiaries panel', () => {
	it('names the consequence of nominating nobody', () => {
		// "No beneficiaries" is a statement about a form. What a firm needs to know is what happens
		// if it stays that way.
		const { body } = render(Beneficiaries, { props: {} });
		expect(body).toContain('No beneficiaries nominated');
		expect(body).toContain('discretion');
	});

	it('states the running total, and names the shortfall', () => {
		// The API does NOT refuse a set under 100%: it is built a row at a time, so demanding 100
		// would make the first insert fail. That makes this line the only thing that can tell a firm
		// it has not finished.
		const { body } = render(Beneficiaries, {
			props: { beneficiaries: [ben({ proportion: 60 })] }
		});
		expect(body).toContain('60%');
		expect(body).toContain('40% is unallocated');
	});

	it('reads a complete set as complete, including thirds', () => {
		// Three 33.333% nominations total 99.999 — a complete set entered as precisely as
		// numeric(6,3) allows, which must not read as a 0.001% shortfall.
		const { body } = render(Beneficiaries, {
			props: {
				beneficiaries: [
					ben({ uid: 'a', proportion: 33.333 }),
					ben({ uid: 'b', proportion: 33.333 }),
					ben({ uid: 'c', proportion: 33.333 })
				]
			}
		});
		expect(body).toContain('Fully allocated');
		expect(body).not.toContain('unallocated');
	});

	it('shows the title with the name, which Morgan Lloyd has no field for', () => {
		// The vendor takes ONE `cam_nameofbeneficiary`; a firm's expression-of-wish form asks for a
		// title, and dropping what the client wrote because one vendor has no column would lose it.
		const { body } = render(Beneficiaries, { props: { beneficiaries: [ben()] } });
		expect(body).toContain('Mrs Jamie Halloway');
		expect(body).toContain('Spouse');
	});

	it('says so when a relationship was not stated', () => {
		const { body } = render(Beneficiaries, {
			props: { beneficiaries: [ben({ relationship: null })] }
		});
		expect(body).toContain('Relationship not stated');
	});

	it('offers add and withdraw, and no edit', () => {
		// A nomination is REPLACED by withdrawing it and making a new one — which is what an
		// expression-of-wish form is. An edit in place would let a share move under a name with the
		// audit row saying only "updated".
		const { body } = render(Beneficiaries, {
			props: { beneficiaries: [ben()], ...actions }
		});
		expect(body).toContain('action="?/removeBeneficiary"');
		expect(body).toContain('Withdraw this nomination');
		// The Add button's label reaches SSR with Svelte's own comment markers interleaved between
		// the icon and the text, so it is matched by NAME rather than by adjacency to the tag.
		expect(body).toContain('Add');
		expect(body).not.toContain('Edit');
	});

	it('asks for a title with the same dropdown the client record uses', () => {
		// A free-text title was the one place a beneficiary could be "Mrs." or "mrs" beside a
		// contact stored as "Mrs". The list is PERSON_TITLE_ITEMS, shared with the client record
		// and the onboarding applicant form; the control is SelectMenu, never a native select.
		const source = componentSource();
		expect(source).toContain('items={PERSON_TITLE_ITEMS}');
		expect(source).toContain('id="ben-title"');
		expect(source).toContain('name="title"');
		expect(source).toContain('placeholder="Select title"');
		expect(source).not.toContain('<Input id="ben-title"');
	});

	it('is a read-only list with no actions injected', () => {
		// The admin portal's mount: the nominations are visible, they just cannot be changed.
		const { body } = render(Beneficiaries, { props: { beneficiaries: [ben()] } });
		expect(body).toContain('Jamie Halloway');
		// No form and no Add: the whole write half is behind `writable`, which needs both the app's
		// `enhance` and an action name.
		expect(body).not.toContain('<form');
		expect(body).not.toContain('Add');
	});
});
