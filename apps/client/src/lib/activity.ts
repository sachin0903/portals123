/**
 * Activity projection shared by the legacy /activity listing and the composed-page loader.
 * The sign inference and type bucketing live HERE, once — two copies of "is this money out?"
 * is how a movement shows as a credit on one surface and a debit on another. Both mirror the
 * API's own `_signed` / `_activity_type` (apps/api/app/routers/crm.py, the /crm/home shaping),
 * so a row that came pre-shaped from /crm/home and a row this module shaped from
 * /crm/transactions read identically.
 */
import type { Transaction } from '$lib/api';
import { ACTIVITY_LABELS, activityLabel, sentenceCase, type Activity } from '@graphene/ui';

/** The instrument name GET /crm/transactions actually returns. */
export function instrumentName(t: Transaction): string | null {
	return t.asset ?? t.asset_name ?? null;
}

/** The custodian's own words, beside the canonical `type` bucket. */
export function sourceSubtype(t: Transaction): string | null {
	return t.subtype ?? t.sub_type ?? null;
}

function kindHaystack(t: Transaction): string {
	return `${t.movement_type ?? ''} ${t.type ?? ''} ${sourceSubtype(t) ?? ''}`;
}

/**
 * Money out (a sell / withdrawal / fee) shows negative; the source lacks a strict sign, so
 * infer from the movement/type when present.
 *
 * NULL WHEN THERE IS NO AMOUNT. This used to render as 0 — the comment said "an unknown movement
 * must render as zero, never NaN", and only the second half of that was right. A pending
 * units-denominated "At Best" sell has no monetary value until it executes: Seccl publishes the
 * quantity and nothing else, and £0.00 against it states the movement moved nothing where the
 * truth is that nobody knows yet. `Money` already renders an em-dash for a null amount.
 */
export function signedAmount(t: Transaction): number | null {
	if (t.signed_amount != null) return t.signed_amount;
	if (t.amount == null) return null;
	const amt = Math.abs(t.amount);
	const out = /sell|withdraw|debit|out|fee|charge/i.test(kindHaystack(t));
	return out ? -amt : amt;
}

/** Needle → activity icon kind, FIRST match wins — the order is the API's `_activity_type`
 * order, so keep them in lockstep (e.g. 'div' must beat 'buy': a reinvested dividend often
 * carries both words). Only a custodian's own words reach this; a canonical kind is matched
 * outright in `activityType`. */
const TYPE_BUCKETS: readonly [needle: string, kind: string][] = [
	['div', 'dividend'],
	['interest', 'interest'],
	['fee', 'fee'],
	['charge', 'fee'],
	['withdraw', 'withdrawal'],
	['deposit', 'deposit'],
	['sell', 'sell'],
	['redem', 'sell'],
	['buy', 'buy'],
	['subscr', 'buy']
];

/** Bucket a raw transaction into one of the activity block's icon types (fallback 'buy').
 *
 * A `platform.transaction_type` value on any key wins outright — `type` from /crm/transactions
 * IS the enum, and so is the lake's `movement_type`. Interest used to fall through the needle
 * search to 'buy', so a client read "+£0.01 Buy" against their cash. */
export function activityType(t: Transaction): string {
	for (const key of [t.movement_type, t.type, sourceSubtype(t)]) {
		const canonical = key?.toLowerCase();
		if (canonical && canonical in ACTIVITY_LABELS) return canonical;
	}
	const s = kindHaystack(t).toLowerCase();
	for (const [needle, kind] of TYPE_BUCKETS) if (s.includes(needle)) return kind;
	return 'buy';
}

/**
 * The custodian's own words for the movement, when they say more than the kind does —
 * "Platform Fee" under a row titled Fee, "Employer" under Payment. Null when the subtype is
 * absent, is a bare provider code (`20043`), or only repeats the kind: a detail column that
 * echoes the title is the emptiness it was meant to fill. Mirrors the API's `_activity_detail`.
 */
export function activityDetail(t: Transaction): string | null {
	const words = (sourceSubtype(t) ?? '').trim();
	if (!words || /^\d+$/.test(words)) return null;
	const detail = sentenceCase(words);
	const kind = activityType(t);
	const own = new Set([activityLabel(kind).toLowerCase(), kind.replace(/_/g, ' ')]);
	return own.has(detail.toLowerCase().replace(/_/g, ' ')) ? null : detail;
}

/** Project one raw /crm/transactions row into the shape the activity block renders — the same
 * projection /crm/home applies server-side to its six-row recent slice. */
export function toActivity(t: Transaction): Activity {
	const kind = activityType(t);
	return {
		date: t.date ?? '',
		type: kind,
		// A cash movement names no instrument, so this fallback IS the title for every payment,
		// fee and dividend on the feed — which is why it may not be the raw key. It was, so a
		// payment read "deposit" directly above a subtitle saying "Payment": one row, two words
		// for one thing (docs/design-language.md — a value is named where it is produced).
		description: instrumentName(t) ?? (t.type ? activityLabel(kind) : 'Transaction'),
		amount: signedAmount(t),
		wrapper: t.account_name ?? '',
		currency: t.currency ?? 'GBP',
		// The row's detail, drawn only where the feed is wide enough (pkgs/ui `activity-list`).
		detail: activityDetail(t),
		quantity: t.quantity ?? null,
		price: t.price ?? null,
		isin: t.isin ?? null
	};
}

/**
 * The provider's own movement words for one transaction, printed as words.
 *
 * `sentenceCase`, never the raw key and never `class="capitalize"` (docs/design-language.md) —
 * the account record was rendering `sell` and `transfer_in` exactly as the custodian spells them.
 * There is no label map here on purpose: unlike the six activity ICON kinds above, `type` is the
 * closed enum and `subtype` is the custodian's own open vocabulary, so a map would name a handful
 * and leave the rest raw. That is precisely the case `sentenceCase` is the fallback for.
 */
export function movementDetail(t: Transaction): string {
	const subtype = sourceSubtype(t);
	return [t.type, subtype && subtype !== t.type ? subtype : null]
		.filter((v): v is string => !!v)
		.map(sentenceCase)
		.join(' · ');
}
