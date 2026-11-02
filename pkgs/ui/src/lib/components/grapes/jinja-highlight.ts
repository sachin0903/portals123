// A MIRROR of the canvas-side highlighter (`apps/{platform,admin}/static/gjs-email-jinja-hl.js`), which cannot be imported: it is a static asset evaluated inside the canvas iframe, so nothing in the test suite can reach it. `jinja-highlight.test.ts` extracts the script's own function between its two markers and runs one case table through both copies. Nothing imports this module at run time, and that is expected. `tag` is control flow (`{% if … %}`), which decides whether a paragraph is sent at all and takes the loud code-chip treatment; `field` is `{{ … }}`, a merged value, which takes the subtler pill.

export type JinjaRunKind = 'tag' | 'field';

/** `end` is exclusive, so `text.slice(start, end)` is the run. */
export interface JinjaRun {
	start: number;
	end: number;
	kind: JinjaRunKind;
}

// Three rules a template being typed will exercise: a run needs its closer (a half-typed `{% if` must not swallow the paragraph, and scanning resumes just after the unclosed opener, so a well-formed run later still decorates); a run ends at the FIRST matching closer, so an inner opener is swallowed rather than starting a second one (`{% if x == "{{" %}` decorates once); and openers are consumed left to right, so a `{{ … }}` inside a `{% … %}` cannot double-match. Jinja comments (`{# … #}`) are deliberately not matched — they never reach a recipient. Kept ES5-plain and dependency-free, because the canvas copy is a classic script in an iframe and can import nothing.
export function jinjaRuns(text: string): JinjaRun[] {
	const runs: JinjaRun[] = [];
	if (!text) return runs;
	let i = 0;
	while (i < text.length) {
		const field = text.indexOf('{{', i);
		const tag = text.indexOf('{%', i);
		if (field === -1 && tag === -1) break;
		// The earliest opener wins; -1 means "not present", never "at the front".
		const isTag = field === -1 || (tag !== -1 && tag < field);
		const open = isTag ? tag : field;
		const close = text.indexOf(isTag ? '%}' : '}}', open + 2);
		if (close === -1) {
			i = open + 2;
			continue;
		}
		runs.push({ start: open, end: close + 2, kind: isTag ? 'tag' : 'field' });
		i = close + 2;
	}
	return runs;
}
