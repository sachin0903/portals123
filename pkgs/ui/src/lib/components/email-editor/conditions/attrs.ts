/** How an authored condition rides on an element: PERCENT-ENCODED JSON. GrapesJS's serialiser switches
 *  an attribute to single quotes when the value holds a double one (`altQuoteAttr`), so JSON would come
 *  out quoted two ways; encoded, it has no quote, angle bracket or space for any party to trip on. */
import {
	DEFAULT_VISIBILITY,
	describeVisibility,
	type Choice,
	type ConditionRule,
	type Visibility
} from './model.js';

export const IF_ATTR = 'data-if';
/** The human reading, for the canvas tab. Display only — the CSS reads it with `attr()`. */
export const IF_LABEL_ATTR = 'data-if-label';
export const CHOICE_ATTR = 'data-choice';

/** AN IMAGE'S FALLBACK, expanded by the compile step — a property of the image, not a condition, so half
 *  of it cannot be deleted. Its `tag`/`style` travel with it, from the palette or `email_defaults`. */
export const FALLBACK_ATTR = 'data-fallback';

/** AN ELEMENT THAT REPEATS — the compile step writes the loop. A property of the row, because no author
 *  can rebuild a hand-typed `{% for %}`: the loop has no control and `item_titles` is not insertable. */
export const REPEAT_ATTR = 'data-repeat';

export interface ItemRepeat {
	field: string;
	/** The loop variable the row's own content refers to. `item` unless an author renames it. */
	alias: string;
}

export function encodeRepeat(repeat: ItemRepeat): string {
	return encode(repeat);
}

export function decodeRepeat(raw: string | null | undefined): ItemRepeat | null {
	const parsed = decode<Partial<ItemRepeat>>(raw);
	if (!parsed || typeof parsed.field !== 'string' || !parsed.field.trim()) return null;
	const alias =
		typeof parsed.alias === 'string' && parsed.alias.trim() ? parsed.alias.trim() : 'item';
	return { field: parsed.field.trim(), alias };
}

export interface ImageFallback {
	field: string;
	text: string;
	/** The element to render it as, and its inline style. Email needs both inline. */
	tag?: string;
	style?: string;
}

export function encodeFallback(fallback: ImageFallback): string {
	return encode(fallback);
}

export function decodeFallback(raw: string | null | undefined): ImageFallback | null {
	const parsed = decode<Partial<ImageFallback>>(raw);
	if (!parsed || typeof parsed.field !== 'string' || !parsed.field.trim()) return null;
	return {
		field: parsed.field,
		text: typeof parsed.text === 'string' ? parsed.text : '',
		tag: typeof parsed.tag === 'string' ? parsed.tag : 'div',
		style: typeof parsed.style === 'string' ? parsed.style : ''
	};
}

/** THE MASTHEAD IMAGE'S IDENTIFIER. Documented once as an inert canvas hint; it is load-bearing now,
 *  because the wordmark fallback belongs to that one image and to nothing else — see the compile step.
 *  It DOES ship to the recipient, harmlessly: an unknown data attribute on an `<img>`. */
export const BRAND_ARM_ATTR = 'data-brand-arm';
export const BRAND_ARM_LOGO = 'logo';
/* There was a `BRAND_ARM_ICON = 'icon'` for a second brand block. One block, one arm: the masthead
   already wears the firm's mark when the firm's own `logo_variant` asks for it. A template saved
   while that block existed still carries `data-brand-arm="icon"` on an `<img>`, which is inert —
   an unknown data attribute, with no rule in the canvas CSS and no meaning at send. */

/** Mirrored beside the token (`create.ts::fieldSpanHtml`). Editor-only — the TOKEN is what Jinja reads. */
export const FIELD_ATTR = 'data-field';
export const FIELD_FILTER_ATTR = 'data-field-filter';
/** Editor-only too: the compiled token carries the fallback as `| fallback('…')`. */
export const FIELD_FALLBACK_ATTR = 'data-field-fallback';

/** A `contenteditable="false"` island is atomic, so the caret cannot walk in and delete a brace. */
export const UNEDITABLE_ATTR = 'contenteditable';

export const EDITOR_ATTRS: readonly string[] = [
	IF_ATTR,
	IF_LABEL_ATTR,
	CHOICE_ATTR,
	FALLBACK_ATTR,
	REPEAT_ATTR,
	FIELD_ATTR,
	FIELD_FILTER_ATTR,
	FIELD_FALLBACK_ATTR,
	UNEDITABLE_ATTR
];

function encode(value: unknown): string {
	return encodeURIComponent(JSON.stringify(value));
}

/** Null for anything we did not write: no condition, so the element renders rather than disappearing. */
function decode<T>(raw: string | null | undefined): T | null {
	if (!raw) return null;
	try {
		const parsed = JSON.parse(decodeURIComponent(raw));
		return parsed && typeof parsed === 'object' ? (parsed as T) : null;
	} catch {
		return null;
	}
}

/** A RULE, ELEMENT BY ELEMENT: `rules` being an array says nothing about what is in it, and every
 *  consumer (`ruleExpression`, `describeRule`) reads `field`/`value` as strings — a hand-mangled
 *  `data-if` holding `null` or a number there throws where the whole editor has no boundary. */
function isRule(value: unknown): value is ConditionRule {
	if (!value || typeof value !== 'object') return false;
	const rule = value as Record<string, unknown>;
	return (
		typeof rule.field === 'string' &&
		typeof rule.op === 'string' &&
		(rule.filter === undefined || typeof rule.filter === 'string') &&
		(rule.value === undefined || typeof rule.value === 'string')
	);
}

export function encodeVisibility(visibility: Visibility): string {
	return encode(visibility);
}

/** A missing `match` reads `all` and a missing `action` `show`: an element keeps behaving as it did. */
export function decodeVisibility(raw: string | null | undefined): Visibility | null {
	const parsed = decode<Partial<Visibility>>(raw);
	if (!parsed || !Array.isArray(parsed.rules) || !parsed.rules.every(isRule)) return null;
	return {
		action: parsed.action === 'hide' ? 'hide' : DEFAULT_VISIBILITY.action,
		match: parsed.match === 'any' ? 'any' : DEFAULT_VISIBILITY.match,
		rules: parsed.rules
	};
}

export function encodeChoice(choice: Choice): string {
	return encode(choice);
}

export function decodeChoice(raw: string | null | undefined): Choice | null {
	const parsed = decode<Partial<Choice>>(raw);
	if (!parsed || !isRule(parsed.rule)) return null;
	return {
		rule: parsed.rule,
		then: typeof parsed.then === 'string' ? parsed.then : '',
		otherwise: typeof parsed.otherwise === 'string' ? parsed.otherwise : ''
	};
}

/** The attributes to write, or `null` values to REMOVE them once no rule is left — the caller hands this
 *  to GrapesJS's `addAttributes`, where null clears, and a leftover empty condition keeps the canvas tab. */
export function visibilityAttributes(visibility: Visibility | null): Record<string, string | null> {
	if (!visibility || visibility.rules.length === 0) {
		return { [IF_ATTR]: null, [IF_LABEL_ATTR]: null };
	}
	return {
		[IF_ATTR]: encodeVisibility(visibility),
		[IF_LABEL_ATTR]: describeVisibility(visibility)
	};
}
