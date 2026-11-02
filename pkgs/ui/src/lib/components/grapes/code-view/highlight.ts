// No new highlighter dependency — the input is GrapesJS's own machine-written output (attribute values always double-quoted, see `html-scan.ts`), so a scanner suffices. Renders as `HighlightSeg` (per-line `{text,cls}` spans, borrowed from the audit pane's JSON highlighter) rather than `{@html}` (forbidden in this package); `text` concatenates back byte-for-byte, which the tests pin. The MOUNTING EDITOR's own markers are coloured out so a hand-editing author can see what not to drop; which attributes those are differs per editor, so they are passed in rather than hardcoded here.
import type { HighlightSeg } from '../../audit/highlight-json.js';
import { readOpenTag } from './html-scan.js';
import { jinjaRuns } from '../jinja-highlight.js';

/** Tone tokens only, so nothing needs a `dark:` variant (the rule `highlight-json.ts` follows). */
const PUNCT = 'text-muted-foreground/60';
const NAME = 'text-info';
const ATTR = 'text-muted-foreground';
/** An attribute the panel depends on — `warning`, not `destructive`, since it isn't broken (the legend above the code says what the colour means). */
const MARKER = 'text-warning font-semibold';
const VALUE = 'text-success';
const COMMENT = 'text-muted-foreground/60 italic';
/** `{{ … }}`/`{% … %}` share the canvas overlay's brand tone, so a Jinja run reads the same in both places. */
const JINJA = 'text-brand-strong';
const TEXT = '';

/** Exported so a test can assert the colouring is a closed set, not whatever a refactor left behind. */
export const CODE_CLASSES = { PUNCT, NAME, ATTR, MARKER, VALUE, COMMENT, JINJA, TEXT } as const;

/**
 * The attributes THIS editor owns, lower-cased — HTML attribute names are case-insensitive and
 * GrapesJS echoes back whatever case it was given.
 *
 * Passed in rather than hardcoded: the email editor's markers are its condition/repeat/field
 * attributes and a document template's are its own, so a fixed list would colour one editor's
 * bookkeeping and miss the other's entirely — which is worse than colouring none, because the
 * colour is what tells a hand-editing author which attributes not to drop.
 */
function markerSet(attrs: readonly string[]): Set<string> {
	return new Set(attrs.map((a) => a.toLowerCase()));
}

function push(out: HighlightSeg[], text: string, cls: string): void {
	if (text) out.push({ text, cls });
}

/** Emits `text` at `cls`, lifting any Jinja run to the Jinja tone — used for both copy and attribute values (`href="{{ portal_url }}"` is the common case). */
function pushWithJinja(out: HighlightSeg[], text: string, cls: string): void {
	let at = 0;
	for (const run of jinjaRuns(text)) {
		push(out, text.slice(at, run.start), cls);
		push(out, text.slice(run.start, run.end), JINJA);
		at = run.end;
	}
	push(out, text.slice(at), cls);
}

/** Tokenise ONE opening (or closing) tag, `<…>` included. */
function pushTag(out: HighlightSeg[], tag: string, markers: Set<string>): void {
	const nameMatch = /^<\/?([a-zA-Z][a-zA-Z0-9-]*)/.exec(tag);
	if (!nameMatch) {
		push(out, tag, TEXT);
		return;
	}
	const lead = tag.startsWith('</') ? '</' : '<';
	push(out, lead, PUNCT);
	push(out, nameMatch[1] ?? '', NAME);
	let i = nameMatch[0].length;
	let guard = 0;
	while (i < tag.length && guard++ < 4096) {
		const ch = tag[i] as string;
		if (/\s/.test(ch)) {
			const start = i;
			while (i < tag.length && /\s/.test(tag[i] as string)) i += 1;
			push(out, tag.slice(start, i), TEXT);
			continue;
		}
		if (ch === '=' || ch === '>' || ch === '/') {
			push(out, ch, PUNCT);
			i += 1;
			continue;
		}
		if (ch === '"' || ch === "'") {
			const close = tag.indexOf(ch, i + 1);
			const end = close === -1 ? tag.length : close + 1;
			pushWithJinja(out, tag.slice(i, end), VALUE);
			i = end;
			continue;
		}
		const start = i;
		while (i < tag.length && !/[\s=>/'"]/.test(tag[i] as string)) i += 1;
		const name = tag.slice(start, i);
		push(out, name, markers.has(name.toLowerCase()) ? MARKER : ATTR);
	}
}

/** Every segment of `src`, in order, before it is split into lines. Segments may contain newlines. */
function segments(src: string, markers: Set<string>): HighlightSeg[] {
	const out: HighlightSeg[] = [];
	let i = 0;
	let guard = 0;
	while (i < src.length && guard++ < 200_000) {
		const next = src.indexOf('<', i);
		if (next === -1) {
			pushWithJinja(out, src.slice(i), TEXT);
			break;
		}
		pushWithJinja(out, src.slice(i, next), TEXT);
		if (src.startsWith('<!--', next)) {
			const end = src.indexOf('-->', next);
			const stop = end === -1 ? src.length : end + 3;
			push(out, src.slice(next, stop), COMMENT);
			i = stop;
			continue;
		}
		const open = readOpenTag(src, next);
		if (!open) {
			// A close tag (`</name>`, no attributes — tokenised by the same routine) or a stray `<` in copy.
			const close = /^<\/[a-zA-Z][a-zA-Z0-9-]*\s*>/.exec(src.slice(next));
			if (close) {
				pushTag(out, close[0], markers);
				i = next + close[0].length;
			} else {
				push(out, '<', TEXT);
				i = next + 1;
			}
			continue;
		}
		pushTag(out, src.slice(open.start, open.end), markers);
		i = open.end;
		// `<style>`/`<script>` content is NOT markup (`a > b { … }` would misread as a tag) — emit verbatim, resume after the close tag.
		if (open.name === 'style' || open.name === 'script') {
			const marker = `</${open.name}`;
			const at = src.toLowerCase().indexOf(marker, i);
			if (at !== -1) {
				push(out, src.slice(i, at), TEXT);
				i = at;
			}
		}
	}
	return out;
}

/**
 * `src`, tokenised per line — the same shape `highlightJson` returns; concatenating a line's `text`
 * values reproduces that line exactly.
 *
 * `markerAttrs` is the mounting editor's own bookkeeping attributes (see `markerSet`). Defaulting to
 * NONE is deliberate: an editor that has not said which attributes are its own gets them coloured as
 * ordinary attributes, which is honest, where a borrowed list would confidently colour the wrong
 * ones.
 */
export function highlightHtml(src: string, markerAttrs: readonly string[] = []): HighlightSeg[][] {
	const lines: HighlightSeg[][] = [[]];
	for (const seg of segments(src ?? '', markerSet(markerAttrs))) {
		const parts = seg.text.split('\n');
		parts.forEach((part, idx) => {
			if (idx > 0) lines.push([]);
			if (part) (lines[lines.length - 1] as HighlightSeg[]).push({ text: part, cls: seg.cls });
		});
	}
	return lines;
}

export type { HighlightSeg };
