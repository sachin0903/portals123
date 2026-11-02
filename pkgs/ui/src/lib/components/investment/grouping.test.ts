import { describe, expect, it } from 'vitest';
import { groupLabel, groupWrappers, isBeneficiary, normaliseGroupBy } from './grouping.js';
import type { Wrapper } from './types.js';

const wrapper = (over: Partial<Wrapper>): Wrapper => ({
	type: 'ISA',
	name: 'Stocks & Shares ISA',
	owner: 'Test Owner',
	value: 1000,
	gain: 50,
	gainPct: 0.05,
	trend: [1, 1.1],
	...over
});

// The family6 shape the feature exists for: owners interleaved (Daniel ×3, Laura, two JISAs).
const family: Wrapper[] = [
	wrapper({ type: 'ISA', owner: 'Daniel Marsh', value: 51230 }),
	wrapper({ type: 'SIPP', owner: 'Daniel Marsh', value: 214600 }),
	wrapper({ type: 'ISA', owner: 'Laura Marsh', value: 44880, delegated: true }),
	wrapper({ type: 'GIA', owner: 'Daniel Marsh', value: 32410 }),
	wrapper({ type: 'JISA', name: 'Junior ISA', owner: 'Freya Marsh', value: 7420, delegated: true }),
	wrapper({ type: 'JISA', name: 'Junior ISA', owner: 'Rory Marsh', value: 5210, delegated: true })
];

describe('normaliseGroupBy', () => {
	it('accepts the three contract axes', () => {
		expect(normaliseGroupBy('none')).toBe('none');
		expect(normaliseGroupBy('wrapper')).toBe('wrapper');
		expect(normaliseGroupBy('owner')).toBe('owner');
	});

	it("degrades junk — including the dead pre-wiring 'product_type' value — to 'none'", () => {
		// Settings ride the wire as untyped JSON; a stale/hand-edited value must never crash.
		for (const junk of ['product_type', 'OWNER', 7, null, undefined, {}, true]) {
			expect(normaliseGroupBy(junk)).toBe('none');
		}
	});
});

describe('groupWrappers', () => {
	it('groups by owner in first-appearance order, preserving order within each group', () => {
		const sections = groupWrappers(family, 'owner');
		expect(sections.map((s) => s.label)).toEqual([
			'Daniel Marsh',
			'Laura Marsh',
			'Freya Marsh',
			'Rory Marsh'
		]);
		// Daniel's three keep their original relative order (ISA, SIPP, GIA) — grouping is a
		// stable permutation, not a re-sort.
		expect(sections[0]?.wrappers.map((w) => w.type)).toEqual(['ISA', 'SIPP', 'GIA']);
	});

	it('sums each section subtotal from its own wrappers', () => {
		const sections = groupWrappers(family, 'owner');
		expect(sections[0]?.subtotal).toBe(51230 + 214600 + 32410);
		expect(sections[1]?.subtotal).toBe(44880);
		// Every pound lands in exactly one section.
		const total = sections.reduce((s, sec) => s + sec.subtotal, 0);
		expect(total).toBe(family.reduce((s, w) => s + w.value, 0));
	});

	it('groups by wrapper type on the wrapper axis', () => {
		const sections = groupWrappers(family, 'wrapper');
		expect(sections.map((s) => s.label)).toEqual(['ISA', 'SIPP', 'GIA', 'JISA']);
		expect(sections[0]?.wrappers.map((w) => w.owner)).toEqual(['Daniel Marsh', 'Laura Marsh']);
		expect(sections[3]?.subtotal).toBe(7420 + 5210);
	});

	it("buckets blank owners under 'Other accounts' rather than dropping them", () => {
		const sections = groupWrappers(
			[wrapper({ owner: '' }), wrapper({ owner: '  ' }), wrapper({ owner: 'A' })],
			'owner'
		);
		expect(sections.map((s) => s.label)).toEqual(['Other accounts', 'A']);
		expect(sections[0]?.wrappers).toHaveLength(2);
	});

	it("buckets a blank product type under 'Account' (matching deriveWrapperGroups)", () => {
		expect(groupLabel(wrapper({ type: '' }), 'wrapper')).toBe('Account');
	});

	it('returns no sections for an empty slice — empty groups cannot exist', () => {
		expect(groupWrappers([], 'owner')).toEqual([]);
	});
});

describe('isBeneficiary', () => {
	it('flags a JISA with a named owner', () => {
		expect(isBeneficiary(wrapper({ type: 'JISA', owner: 'Freya Marsh' }))).toBe(true);
	});

	it('does not flag other wrappers or a JISA with no owner to name', () => {
		expect(isBeneficiary(wrapper({ type: 'ISA', owner: 'Freya Marsh' }))).toBe(false);
		expect(isBeneficiary(wrapper({ type: 'JISA', owner: '  ' }))).toBe(false);
	});
});
