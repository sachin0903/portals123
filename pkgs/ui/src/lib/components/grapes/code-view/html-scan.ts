/**
 * A SCANNER over HTML tags, not a DOM parser: there is no jsdom in this tree
 * (`ui/theme-toggle/scheme.test.ts`), and the input is GrapesJS's own `getHtml()` — machine-written,
 * with attribute values always double-quoted. Anything it cannot make sense of is LEFT ALONE.
 *
 * Extracted from the email editor's `conditions/compile.ts`, where these three were the only parts
 * the code view needed. They are about HTML, not about conditions or about email, so they belong to
 * the shared layer — importing the whole compiler would have dragged that editor's whole condition
 * vocabulary in behind them.
 */

/** Void elements: a scanner looking for `</img>` runs to the end and wraps half the document. */
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

export interface OpenTag {
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

/** Depth-counted on the tag NAME, so a `<td>` inside a `<td>` closes the right one; null when unbalanced. */
export function findCloseTag(html: string, open: OpenTag): number | null {
	if (open.selfClosing) return open.end;
	const close = `</${open.name}`;
	let depth = 1;
	let i = open.end;
	while (i < html.length) {
		const next = html.indexOf('<', i);
		if (next === -1) return null;
		if (html.startsWith(close, next)) {
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
