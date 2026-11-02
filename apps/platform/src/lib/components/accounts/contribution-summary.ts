import type { AccountContributionSummary } from '@graphene/ui';

const num = (x: unknown): number => (typeof x === 'number' ? x : Number(x) || 0);

/**
 * `GET /contributions/summary` as the account overview paints it.
 *
 * The API speaks snake_case; the shared card speaks camelCase. Both keys are read so a mount over
 * either shape still fills the figures rather than going blank.
 */
export function asContributionSummary(payload: unknown): AccountContributionSummary | null {
	if (payload == null || typeof payload !== 'object') return null;
	const row = payload as Record<string, unknown>;
	const taxYear = String(row.tax_year ?? row.taxYear ?? '');
	if (!taxYear) return null;
	return {
		taxYear,
		accountGross: num(row.account_gross ?? row.accountGross),
		count: num(row.count)
	};
}
