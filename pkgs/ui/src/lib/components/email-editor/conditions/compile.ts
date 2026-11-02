/** Authored conditions into Jinja. A SCANNER, not a DOM parser: there is no jsdom in this tree
 *  (`ui/theme-toggle/scheme.test.ts`), and the input is GrapesJS's own `getHtml()` — attribute values
 *  always double-quoted, the condition percent-encoded (`attrs.ts`). Anything it cannot make sense of
 *  is LEFT ALONE: a misplaced `{% endif %}` stops the whole template rendering. */
import {
	BRAND_ARM_ATTR,
	BRAND_ARM_LOGO,
	CHOICE_ATTR,
	decodeChoice,
	decodeFallback,
	decodeVisibility,
	EDITOR_ATTRS,
	decodeRepeat,
	FALLBACK_ATTR,
	IF_ATTR,
	REPEAT_ATTR
} from './attrs.js';
import { choiceExpression, visibilityExpression } from './model.js';

/** Void elements: a scanner looking for `</img>` runs to the end and wraps half the email. */
const VOID_TAGS: readonly string[] = [
	'area',
	'base',
	'br',
	'col',
	'embed',
	'hr',
	'img',
	'input',
	'link',
	'meta',
	'param',
	'source',
	'track',
	'wbr'
];

interface OpenTag {
	name: string;
	start: number;
	end: number;
	selfClosing: boolean;
}

/** The `"` walk stops a `>` inside an attribute value (a label reads `If x is “>5”`) ending the tag. */
export function readOpenTag(html: string, start: number): OpenTag | null {
	if (html[start] !== '<') return null;
	const nameMatch = /^<([a-zA-Z][a-zA-Z0-9-]*)/.exec(html.slice(start));
	if (!nameMatch) return null;
	const name = (nameMatch[1] ?? '').toLowerCase();
	let i = start + nameMatch[0].length;
	let quote: string | null = null;
	while (i < html.length) {
		const ch = html[i];
		if (quote) {
			if (ch === quote) quote = null;
		} else if (ch === '"' || ch === "'") {
			quote = ch;
		} else if (ch === '>') {
			const selfClosing = html[i - 1] === '/' || VOID_TAGS.includes(name);
			return { name, start, end: i + 1, selfClosing };
		}
		i += 1;
	}
	return null;
}

/** What may continue a tag NAME — so a closing tag is matched on the whole name, not a prefix. */
const NAME_CHAR = /[A-Za-z0-9-]/;

/** Depth-counted on the tag NAME, so a `<td>` inside a `<td>` closes the right one; null when unbalanced. */
export function findCloseTag(html: string, open: OpenTag): number | null {
	if (open.selfClosing) return open.end;
	const close = `</${open.name}`;
	let depth = 1;
	let i = open.end;
	while (i < html.length) {
		const next = html.indexOf('<', i);
		if (next === -1) return null;
		// The name must END here. `startsWith` alone made `</a` match `</abbr>`, closing an `<a>` at the
		// first nested `<abbr>` and dropping the rest of the element — while the OPEN side counts depth
		// on an exact name match, so nothing offset the false close. Same for `</p` and `</pre>`.
		if (html.startsWith(close, next) && !NAME_CHAR.test(html[next + close.length] ?? '')) {
			const gt = html.indexOf('>', next);
			if (gt === -1) return null;
			depth -= 1;
			if (depth === 0) return gt + 1;
			i = gt + 1;
			continue;
		}
		const nested = readOpenTag(html, next);
		if (nested && nested.name === open.name && !nested.selfClosing) depth += 1;
		i = nested ? nested.end : next + 1;
	}
	return null;
}

/** The value of `name` on the opening tag, or null. Double-quoted only — GrapesJS's own output. */
export function readAttr(tag: string, name: string): string | null {
	const match = new RegExp(`\\s${name}="([^"]*)"`).exec(tag);
	return match ? (match[1] ?? '') : null;
}

/** Stripped, not left in place: a percent-encoded condition is sizeable and Gmail clips past ~102KB.
 *  Used on ONE tag as the walk goes, and on the whole document (`email-editor.svelte::read`). */
export function stripEditorAttrs(tag: string): string {
	let out = tag;
	for (const attr of EDITOR_ATTRS) out = out.replace(new RegExp(`\\s${attr}="[^"]*"`, 'g'), '');
	return out;
}

/** Single pass, left to right. NESTING WORKS: the inner HTML is compiled by this same function. */
/** The guard an image's fallback must be written against — READ OFF ITS OWN `src`, never off the
 *  field frozen into the attribute when the template was authored.
 *
 *  THE BUG THIS FIXES: the shipped masthead stores `field: "brand.logo_url"` because that is what its
 *  `src` was. Change the Source to a hosted URL and the compiled email still asked
 *  `{% if brand.logo_url %}` — so for a firm with no stored logo the recipient got the wordmark and
 *  the author's image was silently dropped. It looked right on the canvas, which is the one failure
 *  mode this editor is built to avoid.
 *
 *  - `src="{{ x }}"` ⇒ guard on `x`: the image exists only when that value does.
 *  - a literal URL ⇒ `null`, NO guard: the image always renders, so the fallback is unreachable and
 *    emitting it would be a branch that can never be taken.
 *  - no `src` at all ⇒ the stored field, which is all there is to go on.
 */
export function fallbackGuard(src: string | null, stored: string): string | null {
	if (src === null) return stored;
	const token = src.trim().match(/^\{\{\s*([^}|]+?)\s*(\|[^}]*)?\}\}$/);
	if (token) return (token[1] ?? '').trim() || stored;
	return null;
}

export function compileConditionals(html: string): string {
	let out = '';
	let i = 0;
	while (i < html.length) {
		const next = html.indexOf('<', i);
		if (next === -1) {
			out += html.slice(i);
			break;
		}
		out += html.slice(i, next);
		const open = readOpenTag(html, next);
		if (!open) {
			out += html[next];
			i = next + 1;
			continue;
		}
		const closeEnd = findCloseTag(html, open);
		const openTag = html.slice(open.start, open.end);
		const choiceRaw = readAttr(openTag, CHOICE_ATTR);
		const ifRaw = readAttr(openTag, IF_ATTR);

		// A `data-if` is INDEPENDENT of whatever else the element is: a conditional text, a fallback, or
		// just itself. So it is computed ONCE here and wrapped around whichever branch below runs. It
		// used to be applied on the last branch only, and `stripEditorAttrs` removes the attribute on the
		// way out — so a condition authored on a conditional text or on the masthead was silently
		// discarded and the element always shipped. Unbalanced tags are left ungated for the reason the
		// last branch gives: a misplaced `{% endif %}` stops the WHOLE template rendering.
		const balanced = open.selfClosing || closeEnd !== null;
		const gate = (compiled: string): string => {
			if (ifRaw === null || !balanced) return compiled;
			const expression = visibilityExpression(
				decodeVisibility(ifRaw) ?? { action: 'show', match: 'all', rules: [] }
			);
			return expression ? `{% if ${expression} %}${compiled}{% endif %}` : compiled;
		};

		// A conditional text REPLACES its element: the children are one branch's preview, so they go.
		if (choiceRaw !== null && closeEnd !== null) {
			const expression = choiceExpression(
				decodeChoice(choiceRaw) ?? { rule: { field: '', op: 'set' }, then: '', otherwise: '' }
			);
			if (expression) {
				out += gate(expression);
				i = closeEnd;
				continue;
			}
			// Unreadable: keep the element, minus the bookkeeping, so its text still ships.
			out += gate(
				stripEditorAttrs(openTag) +
					compileConditionals(html.slice(open.end, closeEnd - `</${open.name}>`.length)) +
					`</${open.name}>`
			);
			i = closeEnd;
			continue;
		}

		// `{% for %}` OUTERMOST, because a condition inside the loop can test the item and one outside cannot.
		const repeatRaw = readAttr(openTag, REPEAT_ATTR);
		const repeat = repeatRaw === null ? null : decodeRepeat(repeatRaw);
		if (repeat) {
			const end = open.selfClosing || closeEnd === null ? open.end : closeEnd;
			const withoutRepeat = html
				.slice(open.start, end)
				.replace(new RegExp(`\\s${REPEAT_ATTR}="[^"]*"`), '');
			out +=
				`{% for ${repeat.alias} in ${repeat.field} %}` +
				compileConditionals(withoutRepeat) +
				'{% endfor %}';
			i = end;
			continue;
		}

		// The fallback's tag and style come out of the attribute, so this step never learns what a wordmark is.
		const fallbackRaw = readAttr(openTag, FALLBACK_ATTR);
		const fallback = fallbackRaw === null ? null : decodeFallback(fallbackRaw);
		if (fallback) {
			const end = open.selfClosing || closeEnd === null ? open.end : closeEnd;
			const kept =
				open.selfClosing || closeEnd === null
					? stripEditorAttrs(openTag)
					: `${stripEditorAttrs(openTag)}${compileConditionals(
							html.slice(open.end, closeEnd - `</${open.name}>`.length)
						)}</${open.name}>`;
			// THE WORDMARK BELONGS TO THE BRAND LOGO AND TO NOTHING ELSE. `data-fallback` is a generic
			// attribute, so any element carrying one — a duplicated masthead, a stray copy — compiled its
			// own `{% else %}wordmark{% endif %}` and put the firm's name where no wordmark belongs. The
			// arm marker is the identifier; without it the element compiles as itself.
			const guard =
				readAttr(openTag, BRAND_ARM_ATTR) === BRAND_ARM_LOGO
					? // GUARDED ON ITS OWN `src` — see `fallbackGuard` for what went wrong when it was not.
						fallbackGuard(readAttr(openTag, 'src'), fallback.field)
					: null;
			if (guard === null) {
				// A literal source always resolves, so there is no branch: emit the image alone.
				out += gate(kept);
				i = end;
				continue;
			}
			const tag = fallback.tag || 'div';
			const style = fallback.style ? ` style="${fallback.style}"` : '';
			out += gate(
				`{% if ${guard} %}${kept}` +
					`{% else %}<${tag}${style}>${fallback.text}</${tag}>{% endif %}`
			);
			i = end;
			continue;
		}

		const inner =
			closeEnd === null ? '' : html.slice(open.end, closeEnd - `</${open.name}>`.length);
		const body =
			balanced && !open.selfClosing
				? `${stripEditorAttrs(openTag)}${compileConditionals(inner)}</${open.name}>`
				: stripEditorAttrs(openTag);
		out += gate(body);
		i = open.selfClosing || closeEnd === null ? open.end : closeEnd;
	}
	return out;
}

export function hasAuthoredConditions(html: string): boolean {
	return (
		html.includes(`${IF_ATTR}="`) ||
		html.includes(`${CHOICE_ATTR}="`) ||
		html.includes(`${FALLBACK_ATTR}="`) ||
		html.includes(`${REPEAT_ATTR}="`)
	);
}
