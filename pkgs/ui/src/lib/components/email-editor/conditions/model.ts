/** Authored conditionals: a BLOCK condition (`Visibility`, carried as `data-if`, compiled to a wrapping
 *  `{% if %}`) and an INLINE one (`Choice`, compiled to `{{ 'a' if c else 'b' }}` — ONE expression, so
 *  one chip where `{% if %}a{% else %}b{% endif %}` is five). Pure: there is no jsdom in this tree. */

export type ConditionOp = 'set' | 'unset' | 'eq' | 'ne' | 'contains';

export const OPERATOR_LABELS: Record<ConditionOp, string> = {
	set: 'has a value',
	unset: 'is empty',
	eq: 'is',
	ne: 'is not',
	contains: 'contains'
};

export const OPERATORS_WITH_VALUE: readonly ConditionOp[] = ['eq', 'ne', 'contains'];

export interface ConditionRule {
	field: string;
	op: ConditionOp;
	value?: string;
	/** A filter applied BEFORE the test — `message | paragraphs`: the note box guards on the RENDERED note,
	 *  since whitespace-only renders to nothing where a bare `{% if message %}` is true. Kept through edits. */
	filter?: string;
}

export interface Visibility {
	action: 'show' | 'hide';
	match: 'all' | 'any';
	rules: ConditionRule[];
}

export interface Choice {
	rule: ConditionRule;
	then: string;
	otherwise: string;
}

export const DEFAULT_VISIBILITY: Visibility = { action: 'show', match: 'all', rules: [] };

/** `field: ''` marks it undescribed — a guessed default would compile a condition nobody chose. */
export const DEFAULT_CHOICE: Choice = { rule: { field: '', op: 'set' }, then: '', otherwise: '' };

/** A Jinja string literal, single-quoted. An unescaped apostrophe closes it early and the email then
 *  fails to render at all; the backslash is escaped FIRST, or its own replacement re-escapes it. */
export function jinjaString(value: string): string {
	return `'${(value ?? '').replace(/\\/g, '\\\\').replace(/'/g, "\\'")}'`;
}

/** A field reference, or null. An allowlist because this lands in a Jinja expression: dotted paths only. */
export function jinjaField(field: string): string | null {
	const name = (field ?? '').trim();
	return /^[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)*$/.test(name) ? name : null;
}

/** One rule as a Jinja test, or null when it is not usable yet. `set`/`unset` compile to plain
 *  truthiness, not `is defined`: the send contexts pass an empty value far more often than no key. */
export function ruleExpression(rule: ConditionRule): string | null {
	const bare = jinjaField(rule.field);
	if (!bare) return null;
	// On the FIELD, before the test, and validated like a field reference rather than interpolated raw.
	const filter = rule.filter ? jinjaField(rule.filter) : null;
	const field = filter ? `${bare} | ${filter}` : bare;
	switch (rule.op) {
		case 'set':
			return field;
		case 'unset':
			return `not ${field}`;
		case 'eq':
		case 'ne':
		case 'contains': {
			// A MISSING value refuses; an explicitly empty one compiles, because `x == ''` is a real question.
			if (rule.value === undefined) return null;
			const literal = jinjaString(rule.value);
			if (rule.op === 'eq') return `${field} == ${literal}`;
			if (rule.op === 'ne') return `${field} != ${literal}`;
			// `contains` COERCES ITS SUBJECT, and the coercion is the whole point. A bare
			// `'x' in field` raises `TypeError: argument of type 'NoneType' is not a container` the
			// moment the field is null — and null is ordinary: `first_name` comes straight off a
			// nullable column on the invite path. The publish gate cannot catch it, because it renders
			// against the sample context where every field is a string, so the condition publishes
			// clean and then answers 500 with no email sent for the first recipient who has no name.
			//
			// `is not none` rather than `or ''`: a field holding 0 must still contain '0', and a falsy
			// test would coerce it away. `| string` then covers a numeric field, which raises the same
			// way. Guarding on none rather than stringifying unconditionally also stops a null
			// answering TRUE for `contains "None"`.
			return `${literal} in ((${field} if ${field} is not none else '') | string)`;
		}
		default:
			return null;
	}
}

/** The whole condition, or null. Each rule is PARENTHESISED before joining — Jinja's `and` binds tighter
 *  than `or` — and `hide` negates the join, never each rule (`not (a or b)`, never `not a or not b`). */
export function visibilityExpression(visibility: Visibility): string | null {
	const parts = (visibility.rules ?? []).map(ruleExpression).filter((p): p is string => p !== null);
	if (parts.length === 0) return null;
	const joiner = visibility.match === 'any' ? ' or ' : ' and ';
	const joined = parts.length === 1 ? parts[0] : parts.map((p) => `(${p})`).join(joiner);
	if (visibility.action !== 'hide') return joined as string;
	return `not (${joined})`;
}

/** Untouched when there is no condition, so a half-described rule cannot silently drop the element. */
export function wrapVisibility(html: string, visibility: Visibility | null): string {
	const expression = visibility && visibilityExpression(visibility);
	return expression ? `{% if ${expression} %}${html}{% endif %}` : html;
}

/** ONE `{{ }}`. The `else` is always emitted — without it an unmatched expression renders `Undefined`. */
export function choiceExpression(choice: Choice): string | null {
	const test = ruleExpression(choice.rule);
	if (!test) return null;
	return `{{ ${jinjaString(choice.then)} if ${test} else ${jinjaString(choice.otherwise ?? '')} }}`;
}

export function describeRule(rule: ConditionRule): string {
	const field = (rule.field ?? '').trim() || 'a field';
	const label = OPERATOR_LABELS[rule.op] ?? rule.op;
	return OPERATORS_WITH_VALUE.includes(rule.op)
		? `${field} ${label} “${rule.value ?? ''}”`
		: `${field} ${label}`;
}

export function describeVisibility(visibility: Visibility | null): string {
	const rules = visibility?.rules ?? [];
	if (!visibility || rules.length === 0) return 'Always shown';
	const lead = visibility.action === 'hide' ? 'Unless' : 'If';
	const joiner = visibility.match === 'any' ? ' or ' : ' and ';
	return `${lead} ${rules.map(describeRule).join(joiner)}`;
}
