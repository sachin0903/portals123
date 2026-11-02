// Shared value formatting for the two audit/event detail panes. Some audited columns hold LARGE or
// markup values — a branding `theme` jsonb with `data:` URI logos, a GrapesJS email HTML/CSS body, a
// flow definition — and dumping them verbatim into the "Changes" summary or the raw JSON makes the
// pane unreadable. These helpers only tame how a value is SHOWN; the trail itself keeps the full
// value (this is display-only), and short values (ids, names, statuses) pass through untouched.

const INLINE_MAX = 140; // a Changes-row value longer than this is summarised, not shown whole
const JSON_STRING_MAX = 300; // a string longer than this inside the raw JSON is shortened
// A data: URI (base64 or not) — never worth showing whole (a 1.4 MB logo is not a "change" a human
// reads). Matched at the START so a value that merely mentions "data:" is untouched.
const DATA_URI = /^data:[^;,]*(;[^,]*)?,/i;

function sizeHint(len: number): string {
	return len >= 1024 ? `${Math.round(len / 1024)} KB` : `${len} chars`;
}

function dataUriKind(s: string): string {
	const mime = /^data:([^;,]*)/i.exec(s)?.[1];
	return mime ? `${mime} data` : 'data: URI';
}

/** Cap an already-stringified value: a data: URI becomes "image/png data (1.4 MB)", anything over
 *  `max` is truncated with a size hint. Short strings are returned unchanged; nullish → ''. */
export function capText(text: string | null | undefined, max = INLINE_MAX): string {
	if (!text) return '';
	if (DATA_URI.test(text)) return `${dataUriKind(text)} (${sizeHint(text.length)})`;
	return text.length > max ? `${text.slice(0, max)}… (${sizeHint(text.length)})` : text;
}

/** One change value (old/new) summarised for the compact "Changes" line: a scalar capped, a data:
 *  URI named by size, an object shown whole only if small — otherwise a shape hint ("{…} 8 fields
 *  (2 KB)"). Nullish returns the empty glyph. The full value still lives in the raw "Details" block. */
export function previewValue(value: unknown): string {
	if (value === null || value === undefined) return '∅';
	if (typeof value === 'object') {
		const json = JSON.stringify(value);
		if (json.length <= INLINE_MAX) return json;
		const shape = Array.isArray(value)
			? `${value.length} items`
			: `${Object.keys(value as object).length} fields`;
		return `{…} ${shape} (${sizeHint(json.length)})`;
	}
	return capText(String(value));
}

/** Recursively shorten the values in a raw-JSON block for display: long strings truncated, data:
 *  URIs named by size — structure preserved so the shape stays legible. Nulls are kept (an old→new
 *  of null is meaningful); use `omitEmpty` first if the top-level nulls are pure noise. */
export function shortenForJson(value: unknown): unknown {
	if (typeof value === 'string') {
		if (DATA_URI.test(value)) return `${dataUriKind(value)} (${sizeHint(value.length)})`;
		return value.length > JSON_STRING_MAX
			? `${value.slice(0, JSON_STRING_MAX)}… (${sizeHint(value.length)})`
			: value;
	}
	if (Array.isArray(value)) return value.map(shortenForJson);
	if (value && typeof value === 'object') {
		const out: Record<string, unknown> = {};
		for (const [k, v] of Object.entries(value)) out[k] = shortenForJson(v);
		return out;
	}
	return value;
}

/** Drop null/undefined top-level keys (the always-empty `target_tenant_*` / `*_icon_url` noise on a
 *  firm's own events, which are only ever filled on proxied ops rows), plus any keys named in `drop`. */
export function omitEmpty(
	obj: Record<string, unknown>,
	drop: readonly string[] = []
): Record<string, unknown> {
	const out: Record<string, unknown> = {};
	for (const [k, v] of Object.entries(obj)) {
		if (v === null || v === undefined || drop.includes(k)) continue;
		out[k] = v;
	}
	return out;
}
