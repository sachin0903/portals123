// Parse + validate the per-element CSS box (the editor's "{}" raw-CSS mode). Splits a declaration
// block on TOP-LEVEL `;` only — never inside parens or quotes, so a `url("data:…;base64,…")` value
// survives intact — then validates each declaration is `property: value` with a plausible
// (kebab-case, optionally vendor- or --custom-prefixed) property name and a non-empty value.
// Malformed declarations are REPORTED (not silently dropped, as the old inline parser did). A live
// authoring aid for the CSS box, not a full CSS parser.

export interface CssParseResult {
	/** The valid declarations, ready for GrapesJS setStyle. */
	style: Record<string, string>;
	/** Human-readable problems with individual declarations ([] when the block is clean). */
	errors: string[];
}

// kebab-case, with an optional single vendor hyphen (-webkit-) or double for custom props (--var).
const PROP_RE = /^-{0,2}[a-zA-Z][a-zA-Z0-9-]*$/;

export function parseCssDeclarations(cssText: string): CssParseResult {
	// Top-level `;` split (paren/quote-aware).
	const decls: string[] = [];
	let depth = 0;
	let quote = '';
	let buf = '';
	for (const ch of cssText) {
		if (quote) {
			buf += ch;
			if (ch === quote) quote = '';
		} else if (ch === '"' || ch === "'") {
			quote = ch;
			buf += ch;
		} else if (ch === '(') {
			depth++;
			buf += ch;
		} else if (ch === ')') {
			depth = Math.max(0, depth - 1);
			buf += ch;
		} else if (ch === ';' && depth === 0) {
			decls.push(buf);
			buf = '';
		} else {
			buf += ch;
		}
	}
	if (buf.trim()) decls.push(buf);

	const style: Record<string, string> = {};
	const errors: string[] = [];
	for (const raw of decls) {
		const decl = raw.trim();
		if (!decl) continue;
		const i = decl.indexOf(':');
		if (i < 0) {
			errors.push(`"${decl}" is not a declaration — expected "property: value".`);
			continue;
		}
		const prop = decl.slice(0, i).trim();
		const val = decl.slice(i + 1).trim();
		if (!prop) {
			errors.push('A declaration is missing its property name before ":".');
			continue;
		}
		if (!PROP_RE.test(prop)) {
			errors.push(`"${prop}" is not a valid CSS property name.`);
			continue;
		}
		if (!val) {
			errors.push(`"${prop}" has no value.`);
			continue;
		}
		style[prop] = val;
	}
	return { style, errors };
}
