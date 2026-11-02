import { formatGBP, formatMoney } from '../../../money.js';
import type { ProviderPosition } from './types.js';

/** How a provider is named beside a money figure.
 *
 * Keyed by the enum's spelling. `platform.provider_code` uses `morgan-lloyd`; the underscore
 * key stays so a mount over an older API still reads as words. An unknown key is shown
 * de-underscored rather than as another provider's name.
 */
const PROVIDER_LABELS: Record<string, string> = {
	'morgan-lloyd': 'Morgan Lloyd',
	morgan_lloyd: 'Morgan Lloyd',
	seccl: 'Seccl',
	sei: 'SEI'
};

const ADMINISTRATORS = new Set(['morgan-lloyd', 'morgan_lloyd']);

export function providerLabel(source: string): string {
	return PROVIDER_LABELS[source] ?? source.replace(/[_-]/g, ' ');
}

/** Custodian cash (the lake) plus every scheme-administrator balance. A position whose cash
 * is null is not a place money sits — a custodian crystallisation row is one of those, and
 * adding it would count the lake figure twice. One place produces no breakdown. */
export function consolidatedCash(
	custodianCash: number,
	positions: ProviderPosition[] | null | undefined,
	currency = 'GBP'
): { total: number; breakdown?: string } {
	const administratorCash = (positions ?? []).filter((position) => position.cash != null);
	const total = administratorCash.reduce(
		(sum, position) => sum + (position.cash ?? 0),
		custodianCash
	);
	if (!administratorCash.length) return { total };
	const amount = (value: number) => formatMoney(value, currency);
	return {
		total,
		breakdown: [
			`${amount(custodianCash)} custodian`,
			...administratorCash.map(
				(position) => `${amount(position.cash ?? 0)} ${providerLabel(position.source)}`
			)
		].join(' · ')
	};
}

/** One crystallisation of the fund. The scheme administrator's split wins when it has stated
 * one; otherwise the custodian's. The two are not added — they describe the same money. */
export function pensionSplit(
	positions: ProviderPosition[] | null | undefined
): { crystallised: number; uncrystallised: number } | null {
	const withSplit = (positions ?? []).filter(
		(position) => position.crystallised != null || position.uncrystallised != null
	);
	const administrator = withSplit.filter((position) => ADMINISTRATORS.has(position.source));
	const rows = administrator.length ? administrator : withSplit;
	if (!rows.length) return null;
	return {
		crystallised: rows.reduce((sum, position) => sum + (position.crystallised ?? 0), 0),
		uncrystallised: rows.reduce((sum, position) => sum + (position.uncrystallised ?? 0), 0)
	};
}

export type WrapperAllowance = {
	/** What may still be subscribed. */
	available: number;
	/** The tax year, when the custodian stated one. */
	sub?: string;
	/** The amount subscribed, when the custodian stated it against a limit. */
	used: number | null;
	/** That limit. Null when only a remaining figure was stated. */
	limit: number | null;
	taxYear: string | null;
};

/** The ISA/JISA allowance tile, or null when the custodian has not stated one.
 *
 * A zero limit draws nothing: that is "not stated", and a full bar would read as fully
 * subscribed. Remaining of £0 against a real positive limit is fully subscribed and is shown.
 * Remaining on its own is the amount left, with no used figure invented beside it. */
export function wrapperAllowance(account: {
	allowance_used?: number | null;
	allowance_limit?: number | null;
	allowance_remaining?: number | null;
	allowance_tax_year?: string | null;
}): WrapperAllowance | null {
	const used = account.allowance_used ?? null;
	const limit = account.allowance_limit ?? null;
	const remaining = account.allowance_remaining ?? null;
	const available = remaining ?? (limit != null && used != null ? limit - used : null);
	if (available == null) return null;
	const hasLimit = limit != null && limit > 0;
	const hasRemaining = remaining != null && remaining > 0;
	if (!hasLimit && !hasRemaining) return null;

	const statedPair = used != null && limit != null;
	const taxYear = account.allowance_tax_year?.trim() || null;
	return {
		available,
		sub: taxYear ?? undefined,
		used: statedPair ? used : null,
		limit: statedPair ? limit : null,
		taxYear
	};
}

/** The statutory annual subscription limits, by wrapper code, for the current tax year.
 *
 * These are the ANNUAL limits a client can look up anywhere, not the client's own limit — a
 * flexible ISA lets a withdrawal be replaced on top, so SEI states 26,000 for some clients
 * (`docs/sei-data-sources.md` §8.5a). That is why `wrapperAllowance` never falls back to these:
 * "£20,000 left" would be a claim about this client. `allowanceFact` uses them for a different
 * sentence — "the annual limit is £20,000" — which is true of every ISA. */
export const STATUTORY_ALLOWANCE: Readonly<Record<string, number>> = {
	ISA: 20_000,
	JISA: 9_000,
	LISA: 4_000
};

/** "2026/27" for a date inside the UK tax year that starts on 6 April 2026. */
export function ukTaxYear(now: Date = new Date()): string {
	const startYear =
		now.getMonth() > 3 || (now.getMonth() === 3 && now.getDate() >= 6)
			? now.getFullYear()
			: now.getFullYear() - 1;
	return `${startYear}/${String((startYear + 1) % 100).padStart(2, '0')}`;
}

export type AllowanceFact = {
	/** "ISA allowance used" when the custodian stated a subscription; "ISA allowance" otherwise. */
	label: string;
	/** The amount subscribed when that was stated, otherwise the statutory annual limit. */
	value: number;
	sub?: string;
	/** Whether the figure is the custodian's for THIS client, or the public annual limit. */
	stated: boolean;
};

/** The allowance tile for a client's own account page: always present on an ISA/JISA/LISA.
 *
 * When the custodian has stated both the subscription and the limit, the tile leads with
 * the amount used and the amount left sits underneath. `wrapperAllowance`
 * answers only when the custodian has stated a subscription, and on a book whose sync has not
 * yet carried those columns that is never — so a client opening their ISA saw no allowance at
 * all, on the one wrapper where it is the question they came with. When the
 * custodian is silent this still shows the wrapper's statutory annual limit for the current tax
 * year, and says plainly that the amount used is not yet reported, rather than inventing a
 * "used" figure from transactions (a transfer in is not a subscription) or a "left" figure from
 * the limit (a flexible ISA's limit is not the statutory one). A wrapper with no allowance (GIA,
 * a pension) returns null, as before. */
export function allowanceFact(
	account: Parameters<typeof wrapperAllowance>[0] & { product_type?: string | null },
	now: Date = new Date()
): AllowanceFact | null {
	const type = (account.product_type ?? '').trim().toUpperCase();
	const stated = wrapperAllowance(account);
	if (stated && stated.used != null && stated.limit != null) {
		const left = `${formatGBP(stated.available)} left`;
		return {
			label: `${type || 'ISA'} allowance used`,
			value: stated.used,
			sub: stated.taxYear ? `${left} · ${stated.taxYear}` : left,
			stated: true
		};
	}
	if (stated) {
		return {
			label: `${type || 'ISA'} allowance left`,
			value: stated.available,
			sub: stated.sub,
			stated: true
		};
	}
	const statutory = STATUTORY_ALLOWANCE[type];
	if (statutory == null) return null;
	return {
		label: `${type} allowance`,
		value: statutory,
		sub: `Annual limit for ${ukTaxYear(now)} · amount used not yet reported`,
		stated: false
	};
}
