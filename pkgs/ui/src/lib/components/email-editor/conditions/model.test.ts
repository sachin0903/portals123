import { describe, expect, it } from 'vitest';
import {
	choiceExpression,
	describeRule,
	describeVisibility,
	jinjaField,
	jinjaString,
	OPERATOR_LABELS,
	OPERATORS_WITH_VALUE,
	ruleExpression,
	visibilityExpression,
	wrapVisibility,
	type ConditionOp,
	type Visibility
} from './model';

/** The compiler for authored conditionals. Two failures reach a real inbox: a badly ESCAPED literal is
 *  a Jinja syntax error, so nothing renders; a badly PARENTHESISED join renders the WRONG branch. */

const rule = (over: Partial<{ field: string; op: ConditionOp; value: string }> = {}) => ({
	field: 'is_reminder',
	op: 'set' as ConditionOp,
	...over
});

const vis = (over: Partial<Visibility> = {}): Visibility => ({
	action: 'show',
	match: 'all',
	rules: [rule()],
	...over
});

describe('jinjaString', () => {
	it('single-quotes a plain value', () => {
		expect(jinjaString('Reminder')).toBe("'Reminder'");
	});

	it('escapes an apostrophe', () => {
		expect(jinjaString("adviser's note")).toBe("'adviser\\'s note'");
	});

	it('escapes a backslash BEFORE the quote', () => {
		expect(jinjaString('a\\b')).toBe("'a\\\\b'");
		expect(jinjaString("a\\'b")).toBe("'a\\\\\\'b'");
	});

	it('treats a missing value as empty', () => {
		expect(jinjaString(undefined as unknown as string)).toBe("''");
	});
});

describe('jinjaField', () => {
	it('accepts a name and a dotted path', () => {
		expect(jinjaField('is_reminder')).toBe('is_reminder');
		expect(jinjaField('client.full_name')).toBe('client.full_name');
		expect(jinjaField('  brand.support_email  ')).toBe('brand.support_email');
	});

	it('refuses anything that is not a plain reference', () => {
		for (const bad of [
			'',
			'   ',
			'1abc',
			'a b',
			'a-b',
			'x()',
			'x|safe',
			"x['y']",
			'x.',
			'.x',
			'x..y',
			'a b|c'
		]) {
			expect(jinjaField(bad), `${bad} should be refused`).toBeNull();
		}
	});
});

describe('ruleExpression', () => {
	it('compiles set and unset to plain truthiness', () => {
		// Not `is defined`: the send contexts pass an empty value far more often than they omit the key.
		expect(ruleExpression(rule({ op: 'set' }))).toBe('is_reminder');
		expect(ruleExpression(rule({ op: 'unset' }))).toBe('not is_reminder');
	});

	it('compiles the comparisons', () => {
		expect(ruleExpression(rule({ field: 'client.full_name', op: 'eq', value: 'xyz' }))).toBe(
			"client.full_name == 'xyz'"
		);
		expect(ruleExpression(rule({ op: 'ne', value: 'x' }))).toBe("is_reminder != 'x'");
		// `contains` coerces its subject: a bare `'Ltd' in is_reminder` raises TypeError on a null or
		// numeric field, which the publish gate cannot see (it renders against string samples) and
		// which answers 500 with no email sent. `is not none` rather than a falsy test so a field of 0
		// still contains '0'; `| string` for the numeric case.
		expect(ruleExpression(rule({ op: 'contains', value: 'Ltd' }))).toBe(
			"'Ltd' in ((is_reminder if is_reminder is not none else '') | string)"
		);
	});

	it('refuses a rule with no field', () => {
		expect(ruleExpression(rule({ field: '' }))).toBeNull();
		expect(ruleExpression(rule({ field: 'x|safe' }))).toBeNull();
	});

	it('refuses a comparison with no value typed, but allows an explicitly empty one', () => {
		// A half-typed rule must not silently become `x == ''`; both readings have to stay reachable.
		expect(ruleExpression({ field: 'x', op: 'eq' })).toBeNull();
		expect(ruleExpression({ field: 'x', op: 'eq', value: '' })).toBe("x == ''");
	});

	it('refuses an operator it does not know', () => {
		expect(ruleExpression({ field: 'x', op: 'regex' as ConditionOp, value: 'y' })).toBeNull();
	});
});

describe('visibilityExpression', () => {
	it('is null when nothing usable was described', () => {
		expect(visibilityExpression(vis({ rules: [] }))).toBeNull();
		expect(visibilityExpression(vis({ rules: [rule({ field: '' })] }))).toBeNull();
	});

	it('leaves a single rule unparenthesised', () => {
		expect(visibilityExpression(vis())).toBe('is_reminder');
	});

	it('parenthesises each rule before joining', () => {
		const two = vis({
			match: 'any',
			rules: [rule({ op: 'eq', value: 'a' }), rule({ field: 'x', op: 'set' })]
		});
		expect(visibilityExpression(two)).toBe("(is_reminder == 'a') or (x)");
	});

	it('joins with and for “all”', () => {
		const two = vis({ rules: [rule(), rule({ field: 'x' })] });
		expect(visibilityExpression(two)).toBe('(is_reminder) and (x)');
	});

	it('negates the WHOLE condition for hide', () => {
		// `not (a or b)`, never `not a or not b`, which is true whenever the two disagree.
		const two = vis({
			action: 'hide',
			match: 'any',
			rules: [rule(), rule({ field: 'x' })]
		});
		expect(visibilityExpression(two)).toBe('not ((is_reminder) or (x))');
		expect(visibilityExpression(vis({ action: 'hide' }))).toBe('not (is_reminder)');
	});

	it('drops an unusable rule and keeps the rest', () => {
		const mixed = vis({ rules: [rule({ field: '' }), rule({ field: 'x' })] });
		expect(visibilityExpression(mixed)).toBe('x');
	});
});

describe('wrapVisibility', () => {
	it('wraps in an if/endif pair', () => {
		expect(wrapVisibility('<td>Hi</td>', vis())).toBe('{% if is_reminder %}<td>Hi</td>{% endif %}');
	});

	it('leaves the html ALONE when there is no usable condition', () => {
		expect(wrapVisibility('<td>Hi</td>', null)).toBe('<td>Hi</td>');
		expect(wrapVisibility('<td>Hi</td>', vis({ rules: [] }))).toBe('<td>Hi</td>');
		expect(wrapVisibility('<td>Hi</td>', vis({ rules: [rule({ field: '' })] }))).toBe(
			'<td>Hi</td>'
		);
	});
});

describe('choiceExpression', () => {
	it('is ONE expression, not a five-node if/else', () => {
		expect(choiceExpression({ rule: rule(), then: 'Reminder', otherwise: 'New task' })).toBe(
			"{{ 'Reminder' if is_reminder else 'New task' }}"
		);
	});

	it('always emits the else, even when it is empty', () => {
		// Without an `else`, an unmatched inline conditional renders `Undefined`.
		expect(choiceExpression({ rule: rule(), then: 'Yes', otherwise: '' })).toBe(
			"{{ 'Yes' if is_reminder else '' }}"
		);
		expect(
			choiceExpression({ rule: rule(), then: 'Yes' } as Parameters<typeof choiceExpression>[0])
		).toBe("{{ 'Yes' if is_reminder else '' }}");
	});

	it('escapes both branches', () => {
		expect(choiceExpression({ rule: rule(), then: "adviser's", otherwise: "client's" })).toBe(
			"{{ 'adviser\\'s' if is_reminder else 'client\\'s' }}"
		);
	});

	it('refuses an unusable rule', () => {
		expect(choiceExpression({ rule: rule({ field: '' }), then: 'a', otherwise: 'b' })).toBeNull();
	});
});

describe('the author-facing wording', () => {
	it('has a label for every operator', () => {
		const ops: ConditionOp[] = ['set', 'unset', 'eq', 'ne', 'contains'];
		for (const op of ops) expect(OPERATOR_LABELS[op], op).toBeTruthy();
		expect(Object.keys(OPERATOR_LABELS).sort()).toEqual([...ops].sort());
	});

	it('knows which operators need a value box', () => {
		expect([...OPERATORS_WITH_VALUE].sort()).toEqual(['contains', 'eq', 'ne']);
		expect(OPERATORS_WITH_VALUE).not.toContain('set');
		expect(OPERATORS_WITH_VALUE).not.toContain('unset');
	});

	it('reads a rule as a sentence', () => {
		expect(describeRule(rule())).toBe('is_reminder has a value');
		expect(describeRule(rule({ op: 'eq', value: 'x' }))).toBe('is_reminder is “x”');
	});

	it('reads the block’s tab from the AUTHOR’s words, not the compiled Jinja', () => {
		expect(describeVisibility(vis())).toBe('If is_reminder has a value');
		expect(describeVisibility(vis({ action: 'hide' }))).toBe('Unless is_reminder has a value');
		expect(
			describeVisibility(vis({ match: 'any', rules: [rule(), rule({ field: 'x', op: 'unset' })] }))
		).toBe('If is_reminder has a value or x is empty');
	});

	it('says so when there is no condition', () => {
		expect(describeVisibility(null)).toBe('Always shown');
		expect(describeVisibility(vis({ rules: [] }))).toBe('Always shown');
	});
});
