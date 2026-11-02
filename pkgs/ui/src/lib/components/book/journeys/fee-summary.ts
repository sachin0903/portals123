/**
 * Human-readable pricing summary for a catalogue fee package — the kickoff select label and
 * the muted preview under it. British English, Money-style (£ / en-GB grouping).
 *
 * Returns '' when the package has no formattable rule (unknown type / empty rule); callers
 * then show the name alone rather than `Name · Name`.
 */
import { currencySymbol } from '../../../currency-symbols.js';
import type { CatalogFeePackage } from './flows.js';

type FeeTier = { from?: unknown; to?: unknown; rate?: unknown };
type FeeRule = { amount?: unknown; rate?: unknown; tiers?: unknown };

export type FeePackageSelectOption = { value: string; label: string };
export type FeePackageSelectGroup = { label: string; options: FeePackageSelectOption[] };
export type FeePackageSelectItems = FeePackageSelectOption[] | FeePackageSelectGroup[];

function asNumber(value: unknown): number | null {
	if (typeof value === 'number' && Number.isFinite(value)) return value;
	if (typeof value === 'string' && value.trim() !== '') {
		const n = Number(value);
		return Number.isFinite(n) ? n : null;
	}
	return null;
}

function currencyPrefix(code: string | null | undefined): string {
	const c = (code ?? 'GBP').toUpperCase();
	return currencySymbol(c) ?? c;
}

/** Grouped major-unit figure without a forced trailing `.00`. */
function formatAmount(amount: number): string {
	const whole = Number.isInteger(amount);
	return new Intl.NumberFormat('en-GB', {
		minimumFractionDigits: whole ? 0 : 2,
		maximumFractionDigits: 2
	}).format(amount);
}

function formatRate(rate: number): string {
	// Rates are stored 0–100 (0.5 → "0.50%").
	//
	// ALWAYS TWO DECIMALS. This used to print an integer bare and everything else to whatever
	// precision it happened to need, so one tiered package read "1%, 0.75%, 0.5%" — three bands
	// of the same fee at three different widths, which is exactly the shape a reader compares
	// down a column and cannot.
	//
	// The maximum stays at FOUR, and that is not a hedge: the editor's rate input is
	// `step="0.0001"` (`apps/admin/src/lib/catalogue/fees-section.svelte`), so a firm can enter
	// 0.0375%, and a hard two would render that as "0.04%" — an order of magnitude wrong on a
	// charge, to make a column tidy. Every rate in the data we hold is two or fewer, so the
	// extra digits appear only where somebody deliberately asked for them.
	return `${new Intl.NumberFormat('en-GB', {
		minimumFractionDigits: 2,
		maximumFractionDigits: 4
	}).format(rate)}%`;
}

function formatBand(tier: FeeTier): string {
	const from = asNumber(tier.from) ?? 0;
	const to = asNumber(tier.to);
	const rate = asNumber(tier.rate);
	const span =
		to == null ? `${formatAmount(from)} → ∞` : `${formatAmount(from)} → ${formatAmount(to)}`;
	return rate == null ? span : `${span} | ${formatRate(rate)}`;
}

function ruleOf(fp: CatalogFeePackage): FeeRule | null {
	const rule = fp.rule;
	if (rule == null || typeof rule !== 'object' || Array.isArray(rule)) return null;
	return rule as FeeRule;
}

/**
 * The charge sentence the catalogue table and Account details share: fixed `£X`, percentage `Y%`,
 * tiered `A%–B% over N bands`. Empty when there is nothing useful beyond the name.
 */
export function describeFeeRule(fee: { fee_type?: string | null; rule?: unknown }): string {
	const type = (fee.fee_type ?? '').toLowerCase();
	const rule = ruleOf(fee as CatalogFeePackage);
	if (type === 'fixed') {
		const amount = asNumber(rule?.amount);
		return amount == null ? '' : `£${formatAmount(amount)}`;
	}
	if (type === 'percentage') {
		const rate = asNumber(rule?.rate);
		return rate == null ? '' : formatRate(rate);
	}
	if (type === 'tiered') {
		const tiers = Array.isArray(rule?.tiers) ? (rule.tiers as FeeTier[]) : [];
		if (!tiers.length) return '';
		const first = tiers[0];
		const last = tiers[tiers.length - 1];
		const start = asNumber(first?.rate);
		const end = asNumber(last?.rate);
		if (start == null) return '';
		return tiers.length === 1
			? formatRate(start)
			: `${formatRate(start)}–${end == null ? formatRate(start) : formatRate(end)} over ${tiers.length} bands`;
	}
	return '';
}

/** Charge plus `initial` / `ongoing` when the package names a frequency. */
export function describeFeeCharge(fee: {
	fee_type?: string | null;
	frequency?: string | null;
	rule?: unknown;
}): string {
	const charge = describeFeeRule(fee);
	const freq = (fee.frequency ?? '').trim().toLowerCase();
	const freqBit = freq === 'initial' || freq === 'ongoing' ? freq : '';
	return [charge, freqBit].filter(Boolean).join(' ');
}

/**
 * Pricing summary for a fee package. Empty string when there is nothing useful beyond the name.
 */
export function formatFeePackageSummary(fp: CatalogFeePackage): string {
	const type = (fp.fee_type ?? '').toLowerCase();
	const rule = ruleOf(fp);

	if (type === 'fixed') {
		const amount = asNumber(rule?.amount);
		if (amount == null) return '';
		const money = `${currencyPrefix(fp.currency)}${formatAmount(amount)}`;
		const freq = (fp.frequency ?? '').trim().toLowerCase();
		return freq === 'initial' || freq === 'ongoing' ? `${money} · ${freq}` : money;
	}

	if (type === 'percentage') {
		const rate = asNumber(rule?.rate);
		return rate == null ? '' : formatRate(rate);
	}

	if (type === 'tiered') {
		const tiers = Array.isArray(rule?.tiers) ? (rule.tiers as FeeTier[]) : [];
		if (!tiers.length) return '';
		if (tiers.length === 1) return formatBand(tiers[0]!);
		return `Tiered · ${tiers.length} bands`;
	}

	return '';
}

/** One charge as it appears on an account, for a client reading what they are agreeing to. */
export type AppliedFee = {
	name: string;
	fee_type?: string | null;
	frequency?: string | null;
	charge_basis?: string | null;
	payer?: string | null;
	currency?: string | null;
	description?: string | null;
	vatable?: boolean | null;
	minimum_amount?: number | null;
	maximum_amount?: number | null;
	rule?: unknown;
};

export type AppliedFeeSummary = {
	name: string;
	/** What is charged: `0.35% of your account value`, `£50`, `Tiered by your account value`. */
	charge: string;
	/** `Ongoing` / `One-off`, or '' when the package names no frequency. */
	frequency: string;
	/** A tiered charge's bands, one readable line each. Empty for fixed and percentage. */
	bands: string[];
	/** Minimum, maximum, VAT, who pays, and the firm's own description. */
	notes: string[];
};

/**
 * Who is reading the charge. A client reads their own dossier ("your account value"); an adviser
 * reads somebody else's record, where the same possessive would name the wrong person. Only the
 * wording differs — the figures, bands and notes are one implementation, so the record and the
 * dossier cannot state a different price for one package.
 */
export type FeeVoice = 'client' | 'staff';

/** What a percentage is taken OF. '' for a basis that adds nothing to the figure. */
const PERCENTAGE_BASIS: Record<FeeVoice, Record<string, string>> = {
	client: {
		aum: 'of your account value',
		contribution: 'of each contribution',
		transaction: 'of each transaction'
	},
	staff: {
		aum: 'of the account value',
		contribution: 'of each contribution',
		transaction: 'of each transaction'
	}
};

/** What a flat sum is charged PER. '' where the sum stands on its own. */
const FIXED_BASIS: Record<string, string> = {
	contribution: 'per contribution',
	transaction: 'per transaction'
};

/**
 * `initial` / `ongoing` are the only two the catalogue holds, and neither states a period, so
 * neither is translated into one. "Ongoing" is what the firm chose; calling it "a year" would
 * invent a billing cycle the register does not record.
 */
const FEE_FREQUENCY: Record<string, string> = {
	initial: 'One-off',
	ongoing: 'Ongoing'
};

const FEE_PAYER: Record<string, string> = {
	firm: 'Paid by your firm',
	provider: 'Paid by the provider'
};

/** `Up to £10,000: 0.5%` / `£10,000 to £50,000: 0.35%` / `Over £50,000: 0.25%`. */
function clientBand(tier: FeeTier, symbol: string): string {
	const from = asNumber(tier.from) ?? 0;
	const to = asNumber(tier.to);
	const rate = asNumber(tier.rate);
	const amount = asNumber((tier as { amount?: unknown }).amount);
	const span =
		from === 0 && to != null
			? `Up to ${symbol}${formatAmount(to)}`
			: to == null
				? `Over ${symbol}${formatAmount(from)}`
				: `${symbol}${formatAmount(from)} to ${symbol}${formatAmount(to)}`;
	const charge =
		rate != null ? formatRate(rate) : amount != null ? `${symbol}${formatAmount(amount)}` : '';
	return charge ? `${span}: ${charge}` : span;
}

/**
 * A charge stated in full for the client dossier: the figure, what it is taken of, how often it
 * is charged, and the limits around it.
 *
 * The onboarding review used to list a fee package's NAME on its own — the firm's internal
 * pricing label — so a client about to agree to the application could read "Standard ongoing"
 * and learn neither the amount nor how often they would pay it. Nothing is inferred: a package
 * with no formattable rule reports an empty `charge`, and the caller shows the name alone.
 */
export function summariseAppliedFee(
	fee: AppliedFee,
	voice: FeeVoice = 'client'
): AppliedFeeSummary {
	const type = (fee.fee_type ?? '').trim().toLowerCase();
	const basis = (fee.charge_basis ?? '').trim().toLowerCase();
	const symbol = currencyPrefix(fee.currency);
	const rule = ruleOf(fee as CatalogFeePackage);
	const bands: string[] = [];
	let charge = '';

	if (type === 'fixed') {
		const amount = asNumber(rule?.amount);
		if (amount != null) {
			charge = [`${symbol}${formatAmount(amount)}`, FIXED_BASIS[basis] ?? '']
				.filter(Boolean)
				.join(' ');
		}
	} else if (type === 'percentage') {
		const rate = asNumber(rule?.rate);
		if (rate != null) {
			charge = [formatRate(rate), PERCENTAGE_BASIS[voice][basis] ?? ''].filter(Boolean).join(' ');
		}
	} else if (type === 'tiered') {
		const tiers = Array.isArray(rule?.tiers) ? (rule.tiers as FeeTier[]) : [];
		for (const tier of tiers) bands.push(clientBand(tier, symbol));
		if (bands.length) {
			charge =
				basis === 'aum'
					? voice === 'client'
						? 'Tiered by your account value'
						: 'Tiered by account value'
					: 'Tiered';
		}
	}

	const notes: string[] = [];
	const minimum = asNumber(fee.minimum_amount);
	if (minimum != null) notes.push(`Minimum ${symbol}${formatAmount(minimum)}`);
	const maximum = asNumber(fee.maximum_amount);
	if (maximum != null) notes.push(`Maximum ${symbol}${formatAmount(maximum)}`);
	if (fee.vatable) notes.push('VAT is charged on top');
	const payer = (fee.payer ?? '').trim().toLowerCase();
	if (FEE_PAYER[payer]) notes.push(FEE_PAYER[payer]);
	const description = (fee.description ?? '').trim();
	if (description) notes.push(description);

	return {
		name: (fee.name ?? '').trim(),
		charge,
		frequency: FEE_FREQUENCY[(fee.frequency ?? '').trim().toLowerCase()] ?? '',
		bands,
		notes
	};
}

/** Option label: `{name} — {charge} {frequency}` when a summary exists, else the name alone. */
export function feePackageOptionLabel(fp: CatalogFeePackage): string {
	const summary = describeFeeCharge(fp) || formatFeePackageSummary(fp);
	return summary ? `${fp.name} — ${summary}` : fp.name;
}

function groupKey(fp: CatalogFeePackage): { key: string; label: string } {
	const category = (fp.category ?? '').trim() || 'Other';
	const frequency = (fp.frequency ?? '').trim();
	const label = frequency ? `${category} · ${frequency}` : category;
	return { key: label.toLowerCase(), label };
}

/**
 * SelectMenu items for the kickoff fee picker. One package (or none) stays a flat list; two or
 * more are grouped by ``category × frequency`` (legacy DynamicFeeSection). Always leads with
 * "No fee package".
 */
export function feePackageSelectItems(packages: CatalogFeePackage[]): FeePackageSelectItems {
	const none: FeePackageSelectOption = { value: 'none', label: 'No fee package' };
	if (packages.length <= 1) {
		return [
			none,
			...packages.map((fp) => ({ value: String(fp.id), label: feePackageOptionLabel(fp) }))
		];
	}

	const buckets = new Map<string, { label: string; options: FeePackageSelectOption[] }>();
	for (const fp of packages) {
		const { key, label } = groupKey(fp);
		let bucket = buckets.get(key);
		if (!bucket) {
			bucket = { label, options: [] };
			buckets.set(key, bucket);
		}
		bucket.options.push({ value: String(fp.id), label: feePackageOptionLabel(fp) });
	}

	const groups = [...buckets.values()].sort((a, b) => a.label.localeCompare(b.label, 'en'));
	return [{ label: 'None', options: [none] }, ...groups];
}
