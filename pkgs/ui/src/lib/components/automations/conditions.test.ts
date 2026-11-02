/**
 * The condition builder's model, and the round trip that has to be lossless.
 *
 * These conversions sit between a firm's stored automation and the form they edit it in, so a bug
 * here silently REWRITES a rule the author never touched — the worst class of failure this feature
 * has. The properties worth pinning are therefore the boring ones: what goes in comes out, an
 * operator's polarity survives, a value keeps the shape its operator wants, and a half-built row is
 * dropped rather than serialised as something the engine would evaluate.
 */
import { describe, expect, it } from 'vitest';
import {
	addChild,
	blankGroup,
	blankLeaf,
	emptyRoot,
	inputToValue,
	leafCount,
	removeNode,
	setOperator,
	toJson,
	toModel,
	togglePolarity,
	updateNode,
	valueToInput
} from './conditions.js';
import { OPERATOR_SPECS } from './contract.js';

describe('toModel', () => {
	it('wraps a bare leaf in a root group, so the builder always has somewhere to add to', () => {
		const model = toModel({ field: 'contact.type', operator: 'equals', value: 'individual' });
		expect(model.kind).toBe('group');
		expect(model.children).toHaveLength(1);
		const leaf = model.children[0];
		expect(leaf).toMatchObject({
			kind: 'leaf',
			field: 'contact.type',
			op: 'equals',
			negate: false,
			value: 'individual'
		});
	});

	it('reads a NEGATED operator as its positive twin plus polarity', () => {
		const model = toModel({ field: 'contact.type', operator: 'notEquals', value: 'trust' });
		expect(model.children[0]).toMatchObject({ op: 'equals', negate: true });
	});

	it('keeps an unknown operator rather than silently rewriting it to equals', () => {
		// A definition in the wild using an operator this build doesn't know must come back out
		// unchanged if the author only edits the name — quietly turning it into `equals` would
		// change what the rule does.
		const model = toModel({ field: 'contact.type', operator: 'teleports', value: 'x' });
		expect(model.children[0]).toMatchObject({ op: 'teleports', negate: false });
		expect(toJson(model)).toEqual({ field: 'contact.type', operator: 'teleports', value: 'x' });
	});

	it('reads nested groups', () => {
		const model = toModel({
			logic: 'or',
			conditions: [
				{ field: 'a', operator: 'equals', value: 1 },
				{ logic: 'and', conditions: [{ field: 'b', operator: 'isEmpty' }] }
			]
		});
		expect(model.logic).toBe('or');
		expect(model.children).toHaveLength(2);
		expect(model.children[1]?.kind).toBe('group');
	});

	it('treats null, an empty object and junk as "no conditions"', () => {
		expect(toModel(null).children).toEqual([]);
		expect(toModel({}).children).toEqual([]);
	});

	it('renders a list value as a comma-separated string for the input', () => {
		const model = toModel({ field: 'contact.type', operator: 'in', value: ['a', 'b'] });
		expect(model.children[0]).toMatchObject({ value: 'a, b' });
	});
});

describe('toJson', () => {
	it('returns null for an empty tree — the column stores null for "unconditional"', () => {
		expect(toJson(emptyRoot())).toBeNull();
		expect(toJson(toModel(null))).toBeNull();
	});

	it('drops a half-built row rather than serialising a fieldless condition', () => {
		// A blank row is what you see the instant you click "Condition". Serialising it would store
		// `{field: '', operator: 'equals'}`, which the evaluator reads as a field it cannot resolve.
		const model = addChild(emptyRoot(), '', blankLeaf());
		expect(toJson(toModel(null))).toBeNull();
		expect(toJson(model)).toBeNull();
	});

	it('unwraps a group of one, so saving does not add a layer of nesting each time', () => {
		let model = emptyRoot();
		model = addChild(model, model.id, { ...blankLeaf(), field: 'contact.type', value: 'x' });
		expect(toJson(model)).toEqual({ field: 'contact.type', operator: 'equals', value: 'x' });
	});

	it('writes the NEGATED operator back out when polarity is set', () => {
		let model = toModel({ field: 'contact.type', operator: 'equals', value: 'trust' });
		const leafId = model.children[0]!.id;
		model = togglePolarity(model, leafId);
		expect(toJson(model)).toEqual({
			field: 'contact.type',
			operator: 'notEquals',
			value: 'trust'
		});
	});

	it('omits the value entirely for a valueless operator', () => {
		let model = toModel({ field: 'contact.tags', operator: 'equals', value: 'stale' });
		model = setOperator(model, model.children[0]!.id, 'isEmpty');
		expect(toJson(model)).toEqual({ field: 'contact.tags', operator: 'isEmpty' });
	});
});

describe('the round trip', () => {
	// The property that matters: load → save must not change a stored rule. Each case is written in
	// the shape the API stores (a group of one collapses, so the fixtures are already canonical).
	const cases: Record<string, unknown>[] = [
		{ field: 'contact.type', operator: 'equals', value: 'individual' },
		{ field: 'contact.type', operator: 'notEquals', value: 'trust' },
		{ field: 'contact.tags', operator: 'in', value: ['vip', 'chase'] },
		{ field: 'contact.tags', operator: 'notIn', value: ['dormant'] },
		{ field: 'contact.nationality', operator: 'isEmpty' },
		{ field: 'contact.nationality', operator: 'isNotEmpty' },
		{ field: 'account.value', operator: 'greaterThan', value: 50000 },
		{ field: 'case.started_at', operator: 'olderThanDays', value: 30 },
		{ field: 'memory.reason', operator: 'matches', value: 'annual **' },
		{
			logic: 'or',
			conditions: [
				{ field: 'contact.type', operator: 'equals', value: 'individual' },
				{
					logic: 'and',
					conditions: [
						{ field: 'contact.enabled', operator: 'equals', value: 'true' },
						{ field: 'contact.tags', operator: 'contains', value: 'vip' }
					]
				}
			]
		}
	];

	for (const original of cases) {
		it(`survives ${JSON.stringify(original).slice(0, 60)}`, () => {
			expect(toJson(toModel(original))).toEqual(original);
			// …and is stable under a second pass, which is what an author does by opening a rule
			// twice without changing anything.
			expect(toJson(toModel(toJson(toModel(original))))).toEqual(original);
		});
	}
});

describe('value coercion', () => {
	it('splits a list operator on commas and drops the blanks', () => {
		expect(inputToValue('a, b ,, c', OPERATOR_SPECS.in)).toEqual(['a', 'b', 'c']);
		expect(inputToValue('  ', OPERATOR_SPECS.in)).toEqual([]);
	});

	it('numbers a numeric operator', () => {
		expect(inputToValue('50000', OPERATOR_SPECS.greaterThan)).toBe(50000);
		expect(inputToValue('30', OPERATOR_SPECS.withinDays)).toBe(30);
	});

	it('passes a non-numeric entry through AS TEXT so the API can refuse it properly', () => {
		// Coercing to NaN (or to null) would store a value that quietly matches nothing. Letting the
		// text through means `condition_issues` answers with "needs a number", which is actionable.
		expect(inputToValue('soon', OPERATOR_SPECS.withinDays)).toBe('soon');
	});

	it('drops the value for a valueless operator', () => {
		expect(inputToValue('anything', OPERATOR_SPECS.isEmpty)).toBeUndefined();
	});

	it('round-trips a list through the input representation', () => {
		expect(valueToInput(['x', 'y'], OPERATOR_SPECS.in)).toBe('x, y');
		expect(valueToInput(null, OPERATOR_SPECS.equals)).toBe('');
	});
});

describe('mutations are pure', () => {
	// These trees live in `$state`; `structuredClone` throws on a state proxy, and in-place mutation
	// of a nested node is exactly where that reflex gets reached for. Every op returns a new tree.
	it('updateNode leaves the original untouched', () => {
		const model = toModel({ field: 'contact.type', operator: 'equals', value: 'a' });
		const next = updateNode(model, model.children[0]!.id, { value: 'b' });
		expect(model.children[0]).toMatchObject({ value: 'a' });
		expect(next.children[0]).toMatchObject({ value: 'b' });
		expect(next).not.toBe(model);
	});

	it('addChild appends to the named group only', () => {
		let model = emptyRoot();
		const group = blankGroup();
		model = addChild(model, model.id, group);
		model = addChild(model, group.id, { ...blankLeaf(), field: 'a' });
		expect(model.children).toHaveLength(1);
		expect((model.children[0] as { children: unknown[] }).children).toHaveLength(2);
	});

	it('removeNode prunes at any depth, and never removes the root', () => {
		const model = toModel({
			logic: 'and',
			conditions: [
				{ field: 'a', operator: 'equals', value: 1 },
				{ logic: 'or', conditions: [{ field: 'b', operator: 'equals', value: 2 }] }
			]
		});
		const nested = model.children[1] as { id: string; children: { id: string }[] };
		const pruned = removeNode(model, nested.children[0]!.id);
		expect((pruned.children[1] as { children: unknown[] }).children).toHaveLength(0);
		expect(removeNode(model, model.id)).toBe(model);
	});

	it('togglePolarity is a no-op for an operator with no twin', () => {
		// greaterThan/lessThan are NOT complements (equality is false for both), so there is nothing
		// honest to flip to and the builder hides the toggle. The model must agree.
		const model = toModel({ field: 'account.value', operator: 'greaterThan', value: 1 });
		expect(togglePolarity(model, model.children[0]!.id).children[0]).toMatchObject({
			negate: false
		});
	});

	it('setOperator keeps a value the new operator can still read, and drops one it cannot', () => {
		let model = toModel({ field: 'contact.type', operator: 'equals', value: 'individual' });
		const id = model.children[0]!.id;
		model = setOperator(model, id, 'in');
		expect(model.children[0]).toMatchObject({ op: 'in', value: 'individual' });
		model = setOperator(model, id, 'isEmpty');
		expect(model.children[0]).toMatchObject({ op: 'isEmpty', value: '' });
	});

	it('setOperator clears polarity when the new operator has no twin', () => {
		let model = toModel({ field: 'contact.type', operator: 'notEquals', value: 'x' });
		const id = model.children[0]!.id;
		model = setOperator(model, id, 'greaterThan');
		expect(model.children[0]).toMatchObject({ negate: false });
	});
});

describe('leafCount', () => {
	it('counts only complete rows', () => {
		let model = emptyRoot();
		model = addChild(model, model.id, { ...blankLeaf(), field: 'a' });
		model = addChild(model, model.id, blankLeaf());
		expect(leafCount(model)).toBe(1);
	});
});
