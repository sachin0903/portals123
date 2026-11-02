/**
 * Relief-at-source maths for pension contributions.
 *
 * Choosing "personal" vs "employer" changes the gross amount claimed and the relief the scheme
 * reclaims. Personal (and third-party) contributions are paid NET of basic-rate relief.
 * Employer contributions are paid GROSS — the employer takes the corporation-tax deduction
 * instead, so there is nothing to reclaim.
 */
import type { ContributionCategory } from './types.js';

/** Basic rate for 2026/27. Relief at source is always at basic rate regardless of the member's
 *  marginal rate — higher-rate relief is claimed through self-assessment, not by the scheme. */
export const RELIEF_RATE = 0.2;

/** Categories that are member contributions paid net, so relief is reclaimable. */
const NET_PAID: ReadonlySet<ContributionCategory> = new Set([
	'personal_contribution',
	'third_party_contribution'
]);

export interface ContributionInput {
	/** The amount actually paid in. Net for personal/third-party, gross for employer. */
	amount: number;
	category: ContributionCategory;
}

/** The amount that lands in the pension. Personal and third-party payments are grossed up. */
export function grossOf(input: ContributionInput): number {
	if (!NET_PAID.has(input.category)) return input.amount;
	return input.amount / (1 - RELIEF_RATE);
}

/** Basic-rate relief the scheme reclaims. Zero unless paid net. */
export function reliefOf(input: ContributionInput): number {
	return NET_PAID.has(input.category) ? grossOf(input) - input.amount : 0;
}

/** UK tax years, most recent first, for the tax-year picker. */
export function taxYearOptions(now: Date, count = 2): string[] {
	// A UK tax year starts on 6 April, so anything before that belongs to the previous one.
	const startYear =
		now.getMonth() > 3 || (now.getMonth() === 3 && now.getDate() >= 6)
			? now.getFullYear()
			: now.getFullYear() - 1;
	return Array.from(
		{ length: count },
		(_, i) => `${startYear - i}/${String((startYear - i + 1) % 100).padStart(2, '0')}`
	);
}
