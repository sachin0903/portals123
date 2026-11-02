/** Inserting a merge field into a plain text INPUT — the subject and the preview line.
 *
 *  The canvas has its own path (`insertToken` in `email-editor.svelte` reaches into the frame); these
 *  two are ordinary inputs, so the caret is a pair of offsets and the whole job is pure string work.
 *  Kept out of the component so it can be tested per case: every off-by-one here is silent, and the
 *  symptom is a subject that reads `Your {{ brand.na{{ brand.name }}me }}`.
 */

export interface CaretInsert {
	value: string;
	/** Where the caret belongs afterwards — just past the inserted token, so typing continues after it. */
	caret: number;
}

/** Insert `token` into `value` over the selection `[start, end)`.
 *
 *  An out-of-range or unknown caret (a fresh page, an input never focused) appends, because the end of
 *  the line is where someone who has not placed a caret is looking. Exactly the token and no padding:
 *  a space added here cannot be seen in the inbox but is inherited by every subject the firm sends.
 */
export function insertAtCaret(
	value: string,
	token: string,
	start?: number | null,
	end?: number | null
): CaretInsert {
	const text = value ?? '';
	const from = clamp(start ?? text.length, text.length);
	const to = clamp(end ?? from, text.length);
	const [lo, hi] = from <= to ? [from, to] : [to, from];
	return { value: text.slice(0, lo) + token + text.slice(hi), caret: lo + token.length };
}

function clamp(n: number, max: number): number {
	if (!Number.isFinite(n) || n < 0) return max;
	return Math.min(Math.trunc(n), max);
}
