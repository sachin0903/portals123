// Pretty-prints GrapesJS's one-line `getHtml()`, reusing the compile step's scanner (`readOpenTag`/
// `findCloseTag`) rather than a second one. THE ONE RULE: an element with text (or `<style>`/`<script>`)
// is never broken open — whitespace is safe between ELEMENTS (GrapesJS drops empty text nodes) but a newline inside TEXT changes what a client renders.
import { findCloseTag, readOpenTag } from '../conditions/compile.js';

/** Two spaces — email markup nests deeply (table → tr → td → …), so tabs would push content off the pane by the fifth level. */
const INDENT = '  ';

/** A pathological document degrades to "less prettily formatted", never a hang — a real email comes nowhere near either ceiling. */
const MAX_LINES = 20_000;
const MAX_DEPTH = 40;

/** Is there a non-whitespace TEXT node among this fragment's own children? */
export function hasTopLevelText(fragment: string): boolean {
	let i = 0;
	let guard = 0;
	while (i < fragment.length && guard++ < MAX_LINES) {
		const next = fragment.indexOf('<', i);
		if (next === -1) return fragment.slice(i).trim().length > 0;
		if (fragment.slice(i, next).trim().length > 0) return true;
		if (fragment.startsWith('<!--', next)) {
			const end = fragment.indexOf('-->', next);
			i = end === -1 ? fragment.length : end + 3;
			continue;
		}
		// A DECLARATION — `<!DOCTYPE html>` and friends. Skipped like a comment, because
		// `readOpenTag` matches `<` + a letter and returns null for `<!`, which the `if (!open)`
		// branch below reads as "there is text here" — and an element with text is never broken
		// open. So one leading doctype kept an entire stored email on a single unreadable line.
		if (fragment.startsWith('<!', next)) {
			const end = fragment.indexOf('>', next);
			i = end === -1 ? fragment.length : end + 1;
			continue;
		}
		const open = readOpenTag(fragment, next);
		if (!open) {
			// A stray `<` in copy — "a < b". It IS text, so the element holding it must stay on one line.
			return true;
		}
		if (open.selfClosing) {
			i = open.end;
			continue;
		}
		const closeEnd = findCloseTag(fragment, open);
		i = closeEnd ?? open.end;
	}
	return false;
}

/** The close tag ending at `closeEnd` — the last `<` at or before it can only be the close tag's own. */
function closeTagAt(html: string, closeEnd: number): { start: number; tag: string } {
	const start = html.lastIndexOf('<', closeEnd - 1);
	return start === -1 ? { start: closeEnd, tag: '' } : { start, tag: html.slice(start, closeEnd) };
}

function walk(html: string, depth: number, out: string[]): void {
	const pad = INDENT.repeat(Math.min(depth, MAX_DEPTH));
	let i = 0;
	while (i < html.length) {
		if (out.length >= MAX_LINES) return;
		const next = html.indexOf('<', i);
		if (next === -1) {
			const tail = html.slice(i).trim();
			if (tail) out.push(pad + tail);
			return;
		}
		const lead = html.slice(i, next).trim();
		if (lead) out.push(pad + lead);

		// A comment is opaque — its own text, on its own line, unindented inside.
		if (html.startsWith('<!--', next)) {
			const end = html.indexOf('-->', next);
			out.push(pad + html.slice(next, end === -1 ? html.length : end + 3));
			i = end === -1 ? html.length : end + 3;
			continue;
		}
		// The same declaration skip as `hasTopLevelText` — emitted on its own line so a stored
		// email opens with `<!DOCTYPE html>` and then a formatted document, rather than both on one.
		if (html.startsWith('<!', next)) {
			const close = html.indexOf('>', next);
			const stop = close === -1 ? html.length : close + 1;
			out.push(pad + html.slice(next, stop).trim());
			i = stop;
			continue;
		}

		const open = readOpenTag(html, next);
		if (!open) {
			// Not a tag — emit the rest as text rather than guess where it ends ("leave what you can't read alone").
			const tail = html.slice(next).trim();
			if (tail) out.push(pad + tail);
			return;
		}

		const openTag = html.slice(open.start, open.end);
		if (open.selfClosing) {
			out.push(pad + openTag);
			i = open.end;
			continue;
		}

		const closeEnd = findCloseTag(html, open);
		if (closeEnd === null) {
			// Unbalanced: emit the opening tag and carry on past it, never wrapping to the document's end.
			out.push(pad + openTag);
			i = open.end;
			continue;
		}
		const close = closeTagAt(html, closeEnd);
		const inner = html.slice(open.end, close.start);
		const opaque =
			open.name === 'style' ||
			open.name === 'script' ||
			open.name === 'pre' ||
			open.name === 'textarea';

		if (!inner.trim() || opaque || hasTopLevelText(inner) || depth >= MAX_DEPTH) {
			out.push(pad + openTag + inner + close.tag);
		} else {
			out.push(pad + openTag);
			walk(inner, depth + 1, out);
			out.push(pad + close.tag);
		}
		i = closeEnd;
	}
}

/** `html`, indented for reading — idempotent on its own output (element-only), since each fragment's leading indent is trimmed before re-emitting. */
export function formatHtml(html: string): string {
	const out: string[] = [];
	walk(html ?? '', 0, out);
	return out.join('\n');
}
