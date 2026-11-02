import { describe, expect, it } from 'vitest';
import {
	describeFeeCharge,
	describeFeeRule,
	feePackageSelectItems,
	formatFeePackageSummary,
	summariseAppliedFee
} from './fee-summary.js';
import type { CatalogFeePackage } from './flows.js';

function fp(
	partial: Partial<CatalogFeePackage> & Pick<CatalogFeePackage, 'name'>
): CatalogFeePackage {
	return {
		id: 1,
		// `provider`, not `platform_id`: 0138 dropped the registry serial for the code. This
		// fixture carried the deleted field and omitted the required one, and neither gate saw it
		// — `tsconfig.json` excluded test files from svelte-check and vitest strips types — so
		// these two functions were proven against a row shape `GET /catalog/fee-packages` no
		// longer sends. The exclude is gone now.
		provider: 'seccl',
		product_id: null,
		model_id: null,
		...partial
	};
}

describe('formatFeePackageSummary', () => {
	it('formats a fixed GBP fee with frequency', () => {
		expect(
			formatFeePackageSummary(
				fp({ name: 'Setup', fee_type: 'fixed', frequency: 'initial', rule: { amount: 50 } })
			)
		).toBe('£50 · initial');
	});

	it('formats a fixed fee without frequency and with decimals', () => {
		expect(
			formatFeePackageSummary(fp({ name: 'Flat', fee_type: 'fixed', rule: { amount: 12.5 } }))
		).toBe('£12.50');
	});

	it('uses seeded symbols and falls back to an unknown currency code', () => {
		expect(
			formatFeePackageSummary(
				fp({ name: 'E', fee_type: 'fixed', currency: 'EUR', rule: { amount: 100 } })
			)
		).toBe('€100');
		expect(
			formatFeePackageSummary(
				fp({ name: 'U', fee_type: 'fixed', currency: 'USD', rule: { amount: 25 } })
			)
		).toBe('$25');
		expect(
			formatFeePackageSummary(
				fp({ name: 'J', fee_type: 'fixed', currency: 'JPY', rule: { amount: 1000 } })
			)
		).toBe('¥1,000');
		expect(
			formatFeePackageSummary(
				fp({ name: 'X', fee_type: 'fixed', currency: 'XXX', rule: { amount: 1000 } })
			)
		).toBe('XXX1,000');
	});

	it('formats a percentage rate', () => {
		expect(
			formatFeePackageSummary(fp({ name: 'AUM', fee_type: 'percentage', rule: { rate: 0.5 } }))
		).toBe('0.50%');
		expect(
			formatFeePackageSummary(fp({ name: 'AUM', fee_type: 'percentage', rule: { rate: 1 } }))
		).toBe('1.00%');
	});

	it('summarises a single tiered band like the legacy picker', () => {
		expect(
			formatFeePackageSummary(
				fp({
					name: 'Banded',
					fee_type: 'tiered',
					rule: { tiers: [{ from: 0, to: 10000, rate: 0.5 }] }
				})
			)
		).toBe('0 → 10,000 | 0.50%');
	});

	it('summarises multiple tiers as a band count', () => {
		expect(
			formatFeePackageSummary(
				fp({
					name: 'Banded',
					fee_type: 'tiered',
					rule: {
						tiers: [
							{ from: 0, to: 10000, rate: 0.5 },
							{ from: 10000, to: null, rate: 0.35 }
						]
					}
				})
			)
		).toBe('Tiered · 2 bands');
	});

	it('describeFeeRule matches the catalogue table wording', () => {
		expect(describeFeeRule({ fee_type: 'fixed', rule: { amount: 50 } })).toBe('£50');
		expect(describeFeeRule({ fee_type: 'percentage', rule: { rate: 0.35 } })).toBe('0.35%');
	});

	it('gives every rate the same two decimals, so a band list lines up', () => {
		// It printed an integer bare and everything else to whatever precision it needed, so one
		// tiered package read "1%, 0.75%, 0.5%" — three bands of one fee at three widths, down a
		// column a reader is comparing.
		const bands = describeFeeRule({
			fee_type: 'tiered',
			rule: {
				tiers: [
					{ from: 0, to: 10000, rate: 1 },
					{ from: 10000, to: 50000, rate: 0.75 },
					{ from: 50000, to: null, rate: 0.5 }
				]
			}
		});
		expect(bands).toContain('1.00%');
		expect(bands).toContain('0.50%');

		// …and a rate the editor genuinely allows (`step="0.0001"`) keeps its precision rather
		// than rounding to 0.04% to make the column tidy.
		expect(describeFeeRule({ fee_type: 'percentage', rule: { rate: 0.0375 } })).toBe('0.0375%');
		expect(
			describeFeeRule({
				fee_type: 'tiered',
				rule: {
					tiers: [
						{ from: 0, to: 10000, rate: 0.5 },
						{ from: 10000, to: null, rate: 0.35 }
					]
				}
			})
		).toBe('0.50%–0.35% over 2 bands');
	});

	it('describeFeeCharge appends initial/ongoing', () => {
		expect(
			describeFeeCharge({
				fee_type: 'percentage',
				frequency: 'ongoing',
				rule: { rate: 0.35 }
			})
		).toBe('0.35% ongoing');
	});

	it('returns empty for unknown types so the UI can fall back to the name alone', () => {
		expect(formatFeePackageSummary(fp({ name: 'Odd', fee_type: 'mystery', rule: {} }))).toBe('');
		expect(formatFeePackageSummary(fp({ name: 'Bare' }))).toBe('');
		expect(formatFeePackageSummary(fp({ name: 'Fixed empty', fee_type: 'fixed', rule: {} }))).toBe(
			''
		);
	});
});

describe('feePackageSelectItems', () => {
	it('stays flat for a single package', () => {
		const items = feePackageSelectItems([
			fp({ id: 7, name: 'Setup', fee_type: 'fixed', frequency: 'initial', rule: { amount: 50 } })
		]);
		expect(items).toEqual([
			{ value: 'none', label: 'No fee package' },
			{ value: '7', label: 'Setup — £50 initial' }
		]);
	});

	it('groups by category × frequency when there are multiple packages', () => {
		const items = feePackageSelectItems([
			fp({
				id: 1,
				name: 'Ongoing A',
				category: 'Adviser',
				frequency: 'ongoing',
				fee_type: 'percentage',
				rule: { rate: 0.5 }
			}),
			fp({
				id: 2,
				name: 'Setup',
				category: 'Adviser',
				frequency: 'initial',
				fee_type: 'fixed',
				rule: { amount: 100 }
			}),
			fp({
				id: 3,
				name: 'Platform',
				category: 'Platform',
				frequency: 'ongoing',
				fee_type: 'percentage',
				rule: { rate: 0.2 }
			})
		]);
		expect(items).toEqual([
			{ label: 'None', options: [{ value: 'none', label: 'No fee package' }] },
			{
				label: 'Adviser · initial',
				options: [{ value: '2', label: 'Setup — £100 initial' }]
			},
			{
				label: 'Adviser · ongoing',
				options: [{ value: '1', label: 'Ongoing A — 0.50% ongoing' }]
			},
			{
				label: 'Platform · ongoing',
				options: [{ value: '3', label: 'Platform — 0.20% ongoing' }]
			}
		]);
	});
});

describe('summariseAppliedFee', () => {
	/** THE REGRESSION: the client dossier listed a fee package's NAME on its own, so somebody
	 *  agreeing to an application read "Standard ongoing" and learned neither the figure nor how
	 *  often they would pay it. */
	it('states a percentage charge, what it is taken of, and how often', () => {
		expect(
			summariseAppliedFee({
				name: 'Ongoing advice',
				fee_type: 'percentage',
				frequency: 'ongoing',
				charge_basis: 'aum',
				rule: { rate: 0.35 }
			})
		).toEqual({
			name: 'Ongoing advice',
			charge: '0.35% of your account value',
			frequency: 'Ongoing',
			bands: [],
			notes: []
		});
	});

	it('states a flat charge with its limits, VAT and payer', () => {
		expect(
			summariseAppliedFee({
				name: 'Set-up',
				fee_type: 'fixed',
				frequency: 'initial',
				charge_basis: 'contribution',
				minimum_amount: 50,
				maximum_amount: 2000,
				vatable: true,
				payer: 'firm',
				rule: { amount: 125.5 }
			})
		).toEqual({
			name: 'Set-up',
			charge: '£125.50 per contribution',
			frequency: 'One-off',
			bands: [],
			notes: ['Minimum £50', 'Maximum £2,000', 'VAT is charged on top', 'Paid by your firm']
		});
	});

	it('reads a tiered charge as bands a client can find themselves in', () => {
		const summary = summariseAppliedFee({
			name: 'Banded',
			fee_type: 'tiered',
			charge_basis: 'aum',
			rule: {
				tiers: [
					{ from: 0, to: 10000, rate: 0.5 },
					{ from: 10000, to: 50000, rate: 0.35 },
					{ from: 50000, to: null, rate: 0.25 }
				]
			}
		});
		expect(summary.charge).toBe('Tiered by your account value');
		expect(summary.bands).toEqual([
			'Up to £10,000: 0.50%',
			'£10,000 to £50,000: 0.35%',
			'Over £50,000: 0.25%'
		]);
	});

	/** The account record reads the same charge about somebody else's account, where "your" names
	 *  the wrong person. Only the possessive moves: the figures, bands and notes are one
	 *  implementation, so a record and a dossier cannot price one package differently. */
	it('drops the client possessive for a staff reader', () => {
		const fee = {
			name: 'Ongoing advice',
			fee_type: 'percentage',
			frequency: 'ongoing',
			charge_basis: 'aum',
			minimum_amount: 250,
			rule: { rate: 0.35 }
		};
		const staff = summariseAppliedFee(fee, 'staff');
		expect(staff.charge).toBe('0.35% of the account value');
		expect(staff.notes).toEqual(['Minimum £250']);
		expect(staff.frequency).toBe(summariseAppliedFee(fee).frequency);

		const tiered = {
			name: 'Platform',
			fee_type: 'tiered',
			charge_basis: 'aum',
			rule: { tiers: [{ from: 0, to: 100000, rate: 0.3 }] }
		};
		expect(summariseAppliedFee(tiered, 'staff').charge).toBe('Tiered by account value');
		expect(summariseAppliedFee(tiered).charge).toBe('Tiered by your account value');
		expect(summariseAppliedFee(tiered, 'staff').bands).toEqual(summariseAppliedFee(tiered).bands);
	});

	/** Nothing is invented: a charge the catalogue cannot price leaves `charge` empty so the
	 *  caller shows the name alone, and a frequency the register does not hold is not guessed. */
	it('leaves the charge empty when the rule cannot be priced', () => {
		const summary = summariseAppliedFee({ name: 'Odd', fee_type: 'mystery', rule: {} });
		expect(summary.charge).toBe('');
		expect(summary.frequency).toBe('');
		expect(summary.bands).toEqual([]);
	});
});
