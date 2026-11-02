// Mock seed transfers spanning all three routes (ml_sei / intra_sei / intra_seccl),
// ported + extended from docs/transfers-workbench.html. Breaks are modelled as
// transfers at manual_intervention with a recon mismatch; the store derives the
// breaks list + board figures from these.
import type { Asset, Transfer } from './types';

const A = (isin: string, name: string, qty: number, price: number, book: number | null): Asset => ({
	isin,
	name,
	qty,
	price,
	value: Math.round(qty * price * 100) / 100,
	book,
	autoBook: false
});

export function seedTransfers(): Transfer[] {
	return [
		// ── ML + SEI (SIPP) ──────────────────────────────────────────────
		{
			id: 'tr_9f2a71',
			client: 'Eleanor Whitfield',
			account: 'acc_4471',
			firm: 'Castlebridge Advisers',
			firmId: 'firm_castlebridge',
			route: 'ml_sei',
			custodian: 'sei',
			schemeAdmin: 'ml',
			provider: 'Aviva',
			plan: 'AV-44820913',
			wrapper: 'SIPP',
			value: 128540,
			basis: 'cash',
			scope: 'full',
			status: 'manual_intervention',
			risk: true,
			inStage: '2d',
			created: '07 Jun 2026',
			ml: { scheme: 'ML-SCH-0098', ref: 'cam-8f3a21d0', settle: '19 Jun 2026' },
			custody: { acct: 'SEI-AC-44120', ref: 'ACR-2026-00713' },
			custodyStatus: 'SETTLED',
			recon: {
				expected: 128540,
				legs: [
					{ key: 'graphene', label: 'Graphene', value: 128540 },
					{ key: 'ml', label: 'Morgan Lloyd', value: 128540 },
					{ key: 'sei', label: 'SEI', value: 128427.65 }
				]
			},
			breakType: 'settlement_value_mismatch',
			breakStatus: 'open',
			events: [
				{
					code: 'transfer.created',
					det: 'Recorded by Castlebridge · cash, full',
					ts: '07 Jun · 11:04',
					kind: ''
				},
				{
					code: 'ml.complete',
					det: 'Morgan Lloyd reported settlement',
					ts: '18 Jun · 14:22',
					kind: 'acc'
				},
				{
					code: 'transfer.reconciliation_break',
					det: 'SEI credit short of ML advised by £112.35',
					ts: '23 Jun · 06:30',
					kind: 'bad'
				}
			]
		},
		{
			id: 'tr_5b22c9',
			client: 'Raymond Castellano',
			account: 'acc_3380',
			firm: 'Marchwood Wealth',
			firmId: 'firm_marchwood',
			route: 'ml_sei',
			custodian: 'sei',
			schemeAdmin: 'ml',
			provider: 'Standard Life',
			plan: 'SL-77120044',
			wrapper: 'SIPP',
			value: 203500,
			basis: 'cash',
			scope: 'full',
			status: 'manual_intervention',
			risk: true,
			inStage: '4d',
			created: '02 Jun 2026',
			ml: { scheme: 'ML-SCH-0102', ref: 'cam-71c0aa9f', settle: '15 Jun 2026' },
			custody: { acct: 'SEI-AC-44201', ref: 'ACR-2026-00690' },
			custodyStatus: 'SETTLED',
			recon: {
				expected: 203500,
				legs: [
					{ key: 'graphene', label: 'Graphene', value: 203500 },
					{ key: 'ml', label: 'Morgan Lloyd', value: 203500 },
					{ key: 'sei', label: 'SEI', value: 203061.2 }
				]
			},
			breakType: 'settlement_value_mismatch',
			breakStatus: 'investigating',
			events: [
				{
					code: 'transfer.created',
					det: 'Recorded by Marchwood · cash, full',
					ts: '02 Jun · 09:40',
					kind: ''
				},
				{
					code: 'transfer.reconciliation_break',
					det: 'SEI credit short of ML advised by £438.80',
					ts: '16 Jun · 06:30',
					kind: 'bad'
				}
			]
		},
		{
			id: 'tr_7c10b8',
			client: 'Marcus Trelawney',
			account: 'acc_3381',
			firm: 'Marchwood Wealth',
			firmId: 'firm_marchwood',
			route: 'ml_sei',
			custodian: 'sei',
			schemeAdmin: 'ml',
			provider: 'Standard Life',
			plan: 'SL-99204170',
			wrapper: 'SIPP',
			value: 342180,
			basis: 'in_specie',
			scope: 'full',
			status: 'in_flight',
			risk: true,
			inStage: '9d',
			created: '12 Jun 2026',
			ml: { scheme: 'ML-SCH-0077', ref: 'cam-3b29e110', settle: null },
			custody: { acct: 'SEI-AC-43980', ref: null },
			cedingProgress: 'Onboarding assets',
			actionRequired: true,
			recon: null,
			assets: [
				A('GB00B3X7QG63', 'Fundsmith Equity I Acc', 18420.55, 6.42, null),
				A('GB00BJRCFR40', 'Vanguard FTSE Dev World ex-UK', 9910.12, 4.18, null),
				A('IE00B4L5Y983', 'iShares Core MSCI World ETF', 1840, 78.5, 132100),
				A('GB00BD3RZ368', 'Royal London Corporate Bond', 25202.5, 1.51, 39900)
			],
			events: [
				{
					code: 'transfer.created',
					det: 'Recorded by Marchwood · in-specie, full',
					ts: '12 Jun · 15:18',
					kind: ''
				},
				{
					code: 'ml.poll',
					det: 'Status: in_flight — 7 in-specie lines outstanding',
					ts: '23 Jun · 02:00',
					kind: ''
				}
			]
		},
		{
			id: 'tr_2d77e0',
			client: 'Priya Anand',
			account: 'acc_5012',
			firm: 'Castlebridge Advisers',
			firmId: 'firm_castlebridge',
			route: 'ml_sei',
			custodian: 'sei',
			schemeAdmin: 'ml',
			provider: 'Aviva',
			plan: 'AV-44120555',
			wrapper: 'SIPP',
			value: 58900,
			basis: 'cash',
			scope: 'partial',
			status: 'settlement_date_advised',
			risk: false,
			inStage: '0d',
			created: '18 Jun 2026',
			settleToday: true,
			ml: { scheme: 'ML-SCH-0110', ref: 'cam-2d77e0aa', settle: '25 Jun 2026' },
			custody: { acct: 'SEI-AC-44088', ref: null },
			recon: null,
			events: [
				{
					code: 'ml.settlement_advised',
					det: 'Morgan Lloyd advised settlement 25 Jun',
					ts: '24 Jun · 16:02',
					kind: 'acc'
				}
			]
		},
		{
			id: 'tr_8e41a2',
			client: 'George Okonkwo',
			account: 'acc_4490',
			firm: 'Marchwood Wealth',
			firmId: 'firm_marchwood',
			route: 'ml_sei',
			custodian: 'sei',
			schemeAdmin: 'ml',
			provider: 'Scottish Widows',
			plan: 'SW-31200781',
			wrapper: 'SIPP',
			value: 31200,
			basis: 'cash',
			scope: 'full',
			status: 'reconciled',
			risk: false,
			inStage: '1d',
			created: '05 Jun 2026',
			ml: { scheme: 'ML-SCH-0061', ref: 'cam-8e41a2bd', settle: '12 Jun 2026' },
			custody: { acct: 'SEI-AC-44012', ref: 'ACR-2026-00671' },
			custodyStatus: 'SETTLED',
			recon: {
				expected: 31200,
				legs: [
					{ key: 'graphene', label: 'Graphene', value: 31200 },
					{ key: 'ml', label: 'Morgan Lloyd', value: 31200 },
					{ key: 'sei', label: 'SEI', value: 31200 }
				]
			},
			events: [
				{
					code: 'transfer.reconciled',
					det: 'SEI credit matched ML advised within tolerance',
					ts: '13 Jun · 06:30',
					kind: 'good'
				}
			]
		},
		{
			id: 'tr_1f0093',
			client: 'Helena Brandt',
			account: 'acc_5510',
			firm: 'Castlebridge Advisers',
			firmId: 'firm_castlebridge',
			route: 'ml_sei',
			custodian: 'sei',
			schemeAdmin: 'ml',
			provider: 'Royal London',
			plan: 'RL-55102290',
			wrapper: 'SIPP',
			value: 86750,
			basis: 'cash',
			scope: 'full',
			status: 'entered',
			risk: false,
			inStage: '0d',
			created: '24 Jun 2026',
			ml: { scheme: null, ref: null, settle: null },
			custody: { acct: null, ref: null },
			recon: null,
			events: [
				{
					code: 'transfer.created',
					det: 'Drafted by Castlebridge · cash, full',
					ts: '24 Jun · 10:11',
					kind: ''
				}
			]
		},

		// ── SEI only (intra-SEI free movement) ───────────────────────────
		{
			id: 'tr_se3120',
			client: 'Dominic Hale',
			account: 'acc_7001',
			firm: 'Northgate Capital',
			firmId: 'firm_northgate',
			route: 'intra_sei',
			custodian: 'sei',
			schemeAdmin: null,
			provider: 'SEI a/c USB0033210',
			plan: null,
			wrapper: 'GIA',
			value: 74250,
			basis: 'cash',
			scope: 'full',
			status: 'in_flight',
			risk: false,
			inStage: '1d',
			created: '20 Jun 2026',
			ml: null,
			custody: { acct: 'SEI-AC-70010', ref: 'SEI-FM-44021' },
			custodyStatus: 'IN PROCESS — RECEIVED AT COUNTERPARTY',
			recon: null,
			events: [
				{
					code: 'sei.initiated',
					det: 'Free movement submitted (inter-account cash)',
					ts: '20 Jun · 13:05',
					kind: 'acc'
				}
			]
		},
		{
			id: 'tr_se9904',
			client: 'Yusuf Demir',
			account: 'acc_7002',
			firm: 'Northgate Capital',
			firmId: 'firm_northgate',
			route: 'intra_sei',
			custodian: 'sei',
			schemeAdmin: null,
			provider: 'SEI a/c USB0041902',
			plan: null,
			wrapper: 'ISA',
			value: 19800,
			basis: 'cash',
			scope: 'full',
			status: 'sei_acr_created',
			risk: false,
			inStage: '0d',
			created: '23 Jun 2026',
			ml: null,
			custody: { acct: 'SEI-AC-70022', ref: 'SEI-FM-44044' },
			custodyStatus: 'SETTLED',
			recon: null,
			events: [
				{
					code: 'sei.pending_status',
					det: 'getPendingTransfersStatus → SETTLED',
					ts: '24 Jun · 08:30',
					kind: 'acc'
				}
			]
		},

		// ── Seccl only (intra-Seccl product/stock transfer) ──────────────
		{
			id: 'tr_sc7740',
			client: 'Amara Nwosu',
			account: 'acc_8800',
			firm: 'Brightwater Planning',
			firmId: 'firm_brightwater',
			route: 'intra_seccl',
			custodian: 'seccl',
			schemeAdmin: null,
			provider: 'Transact',
			plan: 'TR-88401120',
			wrapper: 'ISA',
			value: 96400,
			basis: 'in_specie',
			scope: 'full',
			status: 'submitted_to_ml',
			risk: false,
			inStage: '3d',
			created: '21 Jun 2026',
			ml: null,
			custody: { acct: 'SECCL-AC-8800', ref: 'SECCL-PT-90014' },
			cedingProgress: 'With previous provider',
			actionRequired: true,
			recon: null,
			assets: [
				A('GB00B41YBW71', 'Legal & General UK Index', 12050.4, 3.11, null),
				A('IE00B3RBWM25', 'Vanguard FTSE All-World', 980.5, 110.2, 102000)
			],
			events: [
				{
					code: 'seccl.placed',
					det: 'Product Transfer placed → With previous provider',
					ts: '21 Jun · 09:50',
					kind: 'acc'
				}
			]
		},
		{
			id: 'tr_sc2218',
			client: 'Bethan Rees',
			account: 'acc_8801',
			firm: 'Brightwater Planning',
			firmId: 'firm_brightwater',
			route: 'intra_seccl',
			custodian: 'seccl',
			schemeAdmin: null,
			provider: 'AJ Bell',
			plan: 'AJB-22180044',
			wrapper: 'GIA',
			value: 42300,
			basis: 'cash',
			scope: 'full',
			status: 'reconciled',
			risk: false,
			inStage: '1d',
			created: '09 Jun 2026',
			ml: null,
			custody: { acct: 'SECCL-AC-8801', ref: 'SECCL-PT-89902' },
			cedingProgress: 'Completed',
			recon: {
				expected: 42300,
				legs: [
					{ key: 'graphene', label: 'Graphene', value: 42300 },
					{ key: 'seccl', label: 'Seccl', value: 42300 }
				]
			},
			events: [
				{
					code: 'transfer.reconciled',
					det: 'Seccl ledger matched advised within tolerance',
					ts: '12 Jun · 06:30',
					kind: 'good'
				}
			]
		}
	];
}
