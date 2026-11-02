/** The compose-strip half of a "Load default" click. Carry the PREHEADER or an author who loads the
 *  default and publishes delivers no inbox preview line where the default they replaced carried one, and
 *  coerce an absent one to `''` or an older API mails the string "undefined" as that preview. Both fields
 *  are overwritten; `GET /email-templates/{uid}/default` 404s for a firm's own code, so null is normal. */

/** The shipped default a surface hands the editor — the API's `EmailDefaultOut`. */
export interface EmailDefaultContent {
	subject: string;
	/** The authored, token-free inbox preview line. */
	preheader: string;
	html: string;
	css: string;
	/** THE CANVAS COPY, and the one to load. `html` is render-ready, so its conditionals are Jinja TEXT: the
	 *  canvas parses them into loose chips an author can half-delete and the next save writes back as though
	 *  typed (`conditions/round-trip.test.ts`). `source` carries each as a `data-if` on the guarded element,
	 *  which the Visibility panel reads. Absent (an older deployment), the caller falls back to `html`. */
	source?: string;
}

/** The compose-strip values for a "Load default" click — `null` when there is no default, so the caller
 *  leaves the author's own subject and preheader alone. */
export function defaultComposeFields(
	content: EmailDefaultContent | null | undefined
): { subject: string; preheader: string } | null {
	if (!content) return null;
	return { subject: content.subject ?? '', preheader: content.preheader ?? '' };
}
