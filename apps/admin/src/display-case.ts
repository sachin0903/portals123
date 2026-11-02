/**
 * "A value is printed in the words the product uses for it" — the value half of the sentence-case
 * rule (`docs/design-language.md`), as data.
 *
 * NODE-ONLY, imported by `display-case.test.ts` alone. It reads the filesystem, so like
 * `route-addressing.ts` it deliberately sits outside `$lib` — nothing in the app bundle should be
 * able to reach it.
 *
 * THE RULE IS ABOUT ONE UTILITY. Tailwind's `capitalize` is `text-transform: capitalize`, which is
 * TITLE case: it uppercases the first letter of EVERY word and cannot touch a separator. Reached
 * for as a way to make an API enum presentable, it produces exactly the inconsistency it was meant
 * to hide —
 *
 *   · `not_a_contribution` → "Not_a_contribution" (the underscore survives, so nothing is fixed);
 *   · `bank transfer` → "Bank Transfer", beside a neighbouring badge whose label came from a map
 *     and reads "Bank transfer";
 *   · `Fixed income`, already a proper label, → "Fixed Income".
 *
 * The fix is always the same and always upstream: name the value where it is produced — a label
 * map, or `humanise`/`sentenceCase` from `@graphene/ui` — so every surface showing that value
 * shows the same words. A CSS transform is per-surface by construction, which is why the estate
 * ended up with one screen's "Owner" and another screen's "owner" for one role.
 *
 * `lowercase` and `uppercase` are NOT banned: those are typographic registers a look opts into
 * wholesale (the client portal's `finish: ledger` small caps, the ink look's `· pending`), applied
 * to an already-correct label rather than standing in for one.
 */
import { globSync, readFileSync } from 'node:fs';

/** The trees this rule covers, relative to `apps/admin` (where vitest runs). */
export const SCANNED = [
	'../client/src',
	'../platform/src',
	'../admin/src',
	'../../pkgs/ui/src'
] as const;

/**
 * `capitalize` as a standalone class token.
 *
 * The lookarounds are what keep `autocapitalize="none"` (a real attribute on several inputs) and a
 * hyphenated utility out of it.
 */
export const CAPITALIZE_TOKEN = /(?<![\w-])capitalize(?![\w-])/;

/**
 * Comments stripped before matching.
 *
 * Not optional: this file, `sentence-case.ts` and three call sites all EXPLAIN the rule by quoting
 * the banned string, and `switch-columns.test.ts` records the same scan tripping over the comment
 * that documents it. A guard that fails on its own explanation gets deleted rather than obeyed.
 */
export function stripComments(source: string): string {
	return source
		.replace(/<!--[\s\S]*?-->/g, '')
		.replace(/\/\*[\s\S]*?\*\//g, '')
		.replace(/(^|[^:])\/\/[^\n]*/g, '$1');
}

/** Every `.svelte` file under the scanned trees, excluding co-located tests. */
export function svelteFiles(): string[] {
	// FORWARD SLASHES, always. `globSync` hands back the platform's own separator, so on Windows
	// every path came back backslashed — matching neither the roots the guard-on-the-guard looks
	// for nor a single key in `RAW_STATUS_ALLOWED`. The scan then reported three allowlisted badges
	// as offences and, in the same run, claimed it could not see the packages it had just read.
	// Green in CI, red on every Windows machine, and about the separator rather than about the code.
	return SCANNED.flatMap((root) => globSync(`${root}/**/*.svelte`))
		.map((f) => f.replaceAll('\\', '/'))
		.filter((f) => !f.includes('.test.'));
}

export type CaseOffence = { file: string; line: number; text: string };

/** Where the banned utility survives, with enough context to fix it. */
export function capitalizeOffences(files: string[] = svelteFiles()): CaseOffence[] {
	const out: CaseOffence[] = [];
	for (const file of files) {
		const source = stripComments(readFileSync(file, 'utf8'));
		source.split('\n').forEach((text, i) => {
			if (CAPITALIZE_TOKEN.test(text)) out.push({ file, line: i + 1, text: text.trim() });
		});
	}
	return out;
}

/**
 * A badge whose entire content is a bare status-ish expression.
 *
 * THE SECOND HALF OF THE SAME RULE, and the half the first sweep left open. Banning `capitalize`
 * stopped the Title-Casing; it said nothing about the other habit, which is to render the API's key
 * straight into a pill — so the same vocabulary kept coming out lowercase in one place and named in
 * another ("completed" in a list beside "Completed" in a header). A pill is the one element in the
 * product whose whole content is a status, so it is also the one place this can be detected
 * cheaply and without false positives.
 *
 * MATCHES ONLY A STATUS-ISH NAME. A badge holding a wrapper (`{w.type}` → ISA), an ISIN, an id or a
 * person's name is content, not a code, and is left alone — that is why this keys on the property
 * name rather than on "a badge containing any expression".
 */
export const BADGE_RAW_STATUS =
	/<(StatusBadge|Badge)\b[^>]*>\s*\{((?:[A-Za-z_$][\w$]*[.?]{1,2})*(?:status|state|stage|phase|outcome|decision|priority|severity|trigger))\}\s*<\/\1>/g;

/**
 * Where a raw status is deliberate, with the reason. Keyed `file:expression`.
 *
 * Each of these is a case where naming the value would be WRONG, not merely unnecessary — which is
 * why they are listed rather than exempted by a pattern.
 */
export const RAW_STATUS_ALLOWED: Record<string, string> = {
	// An HTTP status CODE. 200 is not a word, and `humanise(200)` does not type-check anyway —
	// svelte-check caught this when the first sweep tried it.
	//
	// The STREAM's entry is gone: the trail only ever records a 2xx, so a status badge on every row
	// said the same word for ever, which teaches a reader to stop looking at the column. The code
	// survives in the DETAIL, beside the method and route, where it is part of reading the request.
	'../../pkgs/ui/src/lib/components/audit/event-detail.svelte:event.status': 'HTTP status code',
	// Seccl's own `transferStatus`, stored verbatim and never parsed (docs: the prototype's four
	// `CedingProgress` values were unverifiable). Showing the custodian's string as the custodian
	// wrote it is the point; sentence-casing it would imply we had understood it.
	'../../pkgs/ui/src/lib/components/book/transfers/detail-panel.svelte:transfer.ceding_status':
		"the custodian's own string, deliberately unparsed",
	// A branding PREVIEW over fixture rows that are already written as words ("Active", "Review
	// due"), not an API vocabulary.
	'../../pkgs/ui/src/lib/components/branding/shell-preview.svelte:client.status':
		'preview fixture, already sentence case'
};

/** Badges rendering a status straight from the API, minus the documented exceptions. */
export function rawStatusBadges(files: string[] = svelteFiles()): CaseOffence[] {
	const out: CaseOffence[] = [];
	for (const file of files) {
		const source = stripComments(readFileSync(file, 'utf8'));
		for (const m of source.matchAll(BADGE_RAW_STATUS)) {
			if (RAW_STATUS_ALLOWED[`${file}:${m[2]}`]) continue;
			out.push({
				file,
				line: source.slice(0, m.index).split('\n').length,
				text: m[0].replace(/\s+/g, ' ').trim()
			});
		}
	}
	return out;
}
