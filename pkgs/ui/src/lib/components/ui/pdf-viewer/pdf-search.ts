/**
 * Finding text in a PDF's extracted page text.
 *
 * Split out of the viewer because it is the part with judgement in it and no DOM: what counts as a
 * match in a document whose text came out of a PDF. Three things a naive `indexOf` gets wrong on
 * real documents:
 *
 *  1. **Whitespace is arbitrary.** PDF text extraction emits whatever spacing the layout produced —
 *     a line break mid-sentence, a run of spaces where the typesetter kerned. "annual review" must
 *     match "annual\n  review".
 *  2. **Typography is not what people type.** A suitability report is full of curly quotes and en
 *     dashes; nobody types those into a search box. "client's" must find "client’s".
 *  3. **Soft hyphens and zero-width joiners are invisible.** They sit inside words and would break
 *     a literal match for no reason a reader could see.
 *
 * Offsets come back in the ORIGINAL page string, because that is what the caller walks to build a
 * DOM Range over the rendered text layer — the folding here must never leak into those numbers.
 */

/** Characters folded to an ASCII equivalent so a plainly-typed query finds typeset text. */
const FOLD: Record<string, string> = {
	'‘': "'", // ‘
	'’': "'", // ’
	'‚': "'",
	'‛': "'",
	'′': "'", // ′
	'“': '"', // “
	'”': '"', // ”
	'„': '"',
	'″': '"', // ″
	'‐': '-', // ‐
	'‑': '-', // non-breaking hyphen
	'‒': '-',
	'–': '-', // –
	'—': '-', // —
	'―': '-',
	'−': '-' // minus sign
};

/** Characters dropped entirely: invisible in the rendered page, so they must be invisible here. */
const DROP = new Set(['­', '​', '‌', '‍', '⁠', '﻿']);

export interface Normalised {
	/** The folded text: lower-cased, whitespace collapsed to single spaces, typography flattened. */
	text: string;
	/** `map[i]` is the index in the ORIGINAL string that produced `text[i]`. One entry per output
	 *  character, so a match's original span is `[map[start], map[end - 1] + 1)`. */
	map: number[];
}

/** Fold one page's text for matching, keeping a way back to the original offsets. */
export function normalise(input: string): Normalised {
	let text = '';
	const map: number[] = [];
	let pendingSpace = false;
	for (let i = 0; i < input.length; i++) {
		// `charAt`, not `input[i]`: under `noUncheckedIndexedAccess` the index form is `string |
		// undefined`, and every char here is in range by construction.
		const raw = input.charAt(i);
		if (DROP.has(raw)) continue;
		const folded = FOLD[raw] ?? raw;
		if (/\s/.test(folded)) {
			// Collapse a run to one space, and only emit it if something precedes it — a page that
			// starts with a newline should not start with a space, or every offset shifts by one.
			pendingSpace = text.length > 0;
			continue;
		}
		if (pendingSpace) {
			text += ' ';
			// The space stands for the whitespace run, so point it at the character that follows.
			map.push(i);
			pendingSpace = false;
		}
		// `toLowerCase` can lengthen a character (İ → i̇). Every output char maps to the same source
		// index, so the span stays correct whichever one a match starts or ends on.
		const lower = folded.toLowerCase();
		for (const ch of lower) {
			text += ch;
			map.push(i);
		}
	}
	return { text, map };
}

/** One hit, in the coordinates of the page string it was found in. */
export interface PdfMatch {
	/** Zero-based page index. */
	page: number;
	/** Half-open span in the ORIGINAL page text. */
	start: number;
	end: number;
}

export interface SearchResult {
	matches: PdfMatch[];
	/** True when the cap bit — the caller must say so rather than present a short list as complete. */
	truncated: boolean;
}

/**
 * Every occurrence of `query` across `pages`, in reading order.
 *
 * `limit` caps the result set: a one-character query over a long document can match thousands of
 * times, and neither the highlight machinery nor the reader benefits from all of them. Hitting it
 * is reported, never silently swallowed.
 */
export function findMatches(pages: string[], query: string, limit = 500): SearchResult {
	const needle = normalise(query).text.trim();
	if (!needle) return { matches: [], truncated: false };

	const matches: PdfMatch[] = [];
	let capped = false;
	pages.forEach((pageText, page) => {
		const { text, map } = normalise(pageText);
		let from = 0;
		for (;;) {
			const at = text.indexOf(needle, from);
			if (at === -1) break;
			if (matches.length >= limit) {
				capped = true;
				return;
			}
			const start = map[at];
			const last = map[at + needle.length - 1];
			// Unreachable — a match's first and last characters are both inside `text`, and `map` has
			// one entry per character of it — but the index type says otherwise, and inventing a 0
			// would silently highlight the top of the page.
			if (start === undefined || last === undefined) break;
			matches.push({ page, start, end: last + 1 });
			// Advance by one so overlapping occurrences ("aa" in "aaa") are both found.
			from = at + 1;
		}
	});
	return { matches, truncated: capped };
}
