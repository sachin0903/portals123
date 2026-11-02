// Mock seed data for the Contributions workbench prototype, ported from
// docs/contributions-workbench.html and extended with match/expectation fields.
import type { Contribution, RasBatch, ReconBreak, SeiTransaction } from './types';

const categorisedFor = (status: Contribution['status']) =>
	!['identified', 'awaiting_confirmation'].includes(status);

type Seed = Omit<Contribution, 'categorised' | 'expectationId' | 'matchId'>;

const SEED: Seed[] = [
	{
		id: 101,
		member: 'Margaret Whitfield',
		seiAccount: 'USB0042817',
		scheme: 'SR-104852',
		date: '24 Jun',
		net: 8000,
		type: 'personal',
		freq: 'one_off',
		source: 'Client',
		taxYear: '2026/27',
		status: 'awaiting_confirmation',
		earnings: 74000,
		age: 58,
		protection: false
	},
	{
		id: 102,
		member: 'Eleanor Voss',
		seiAccount: 'USB0039104',
		scheme: 'SR-101277',
		date: '24 Jun',
		net: 40000,
		type: 'personal',
		freq: 'one_off',
		source: 'Client',
		taxYear: '2026/27',
		status: 'awaiting_confirmation',
		earnings: 128000,
		age: 49,
		protection: false
	},
	{
		id: 103,
		member: 'David Brennan',
		seiAccount: 'USB0044620',
		scheme: 'SR-106018',
		date: '23 Jun',
		net: 2880,
		type: 'personal',
		freq: 'one_off',
		source: 'Third party',
		taxYear: '2026/27',
		status: 'awaiting_confirmation',
		earnings: 0,
		age: 41,
		protection: false
	},
	{
		id: 104,
		member: 'Pinnacle Joinery Ltd → S. Pryce',
		seiAccount: 'USB0041188',
		scheme: 'SR-103940',
		date: '23 Jun',
		net: 15000,
		type: 'employer',
		freq: 'one_off',
		source: 'Employer',
		taxYear: '2026/27',
		status: 'confirmed',
		earnings: 52000,
		age: 55,
		protection: false
	},
	{
		id: 105,
		member: 'James Okafor',
		seiAccount: 'USB0040551',
		scheme: 'SR-102665',
		date: '21 Jun',
		net: 480,
		type: 'personal',
		freq: 'regular',
		source: 'Client',
		taxYear: '2026/27',
		status: 'ml_recorded',
		earnings: 61000,
		age: 44,
		protection: false
	},
	{
		id: 106,
		member: 'Priya Nadarajah',
		seiAccount: 'USB0043302',
		scheme: 'SR-105471',
		date: '18 Jun',
		net: 12000,
		type: 'personal',
		freq: 'one_off',
		source: 'Client',
		taxYear: '2026/27',
		status: 'ras_applied',
		earnings: 96000,
		age: 52,
		protection: false
	},
	{
		id: 107,
		member: 'Aileen Forsyth',
		seiAccount: 'USB0038890',
		scheme: 'SR-100934',
		date: '14 Jun',
		net: 6400,
		type: 'personal',
		freq: 'one_off',
		source: 'Client',
		taxYear: '2026/27',
		status: 'ras_received',
		earnings: 70000,
		age: 60,
		protection: false
	},
	{
		id: 108,
		member: 'Hugh Castellano',
		seiAccount: 'USB0042003',
		scheme: 'SR-104110',
		date: '09 Jun',
		net: 9600,
		type: 'personal',
		freq: 'one_off',
		source: 'Client',
		taxYear: '2026/27',
		status: 'tax_relief_received',
		earnings: 88000,
		age: 47,
		protection: false
	},
	{
		id: 109,
		member: 'Rosalind Achebe',
		seiAccount: 'USB0039771',
		scheme: 'SR-101902',
		date: '05 Jun',
		net: 3200,
		type: 'personal',
		freq: 'regular',
		source: 'Client',
		taxYear: '2026/27',
		status: 'reconciled',
		earnings: 54000,
		age: 39,
		protection: false
	},
	{
		id: 110,
		member: 'Bartholomew Greaves',
		seiAccount: 'USB0045010',
		scheme: 'SR-106533',
		date: '03 Jun',
		net: 20000,
		type: 'personal',
		freq: 'one_off',
		source: 'Client',
		taxYear: '2026/27',
		status: 'reconciled',
		earnings: 215000,
		age: 62,
		protection: true
	}
];

export function seedContributions(): Contribution[] {
	return SEED.map((c) => ({
		...c,
		categorised: categorisedFor(c.status),
		// confirmed+ already have a matched expectation; the two awaiting rows are independent.
		expectationId: categorisedFor(c.status) ? `EXP-${c.id}` : null,
		matchId: null
	}));
}

// Incoming SEI credits awaiting allocation. SEITX-0001 will auto-match #101 (Margaret)
// once categorised; #102 (Eleanor) has NO credit (her recon break) so stays independent;
// 0003/0004 are genuinely unallocated and available for match-on-creation.
export function seedSeiPool(): SeiTransaction[] {
	return [
		{
			id: 'SEITX-0001',
			seiAccount: 'USB0042817',
			amount: 8000,
			valueDate: '24 Jun',
			reference: 'Faster Payment · M Whitfield',
			matchedContributionId: null
		},
		{
			id: 'SEITX-0002',
			seiAccount: 'USB0044620',
			amount: 2880,
			valueDate: '23 Jun',
			reference: 'Bank transfer · third party',
			matchedContributionId: null
		},
		{
			id: 'SEITX-0003',
			seiAccount: 'USB0051120',
			amount: 5000,
			valueDate: '22 Jun',
			reference: 'Faster Payment · ref 88213',
			matchedContributionId: null
		},
		{
			id: 'SEITX-0004',
			seiAccount: 'USB0050012',
			amount: 12000,
			valueDate: '20 Jun',
			reference: 'CHAPS · ref 90021',
			matchedContributionId: null
		}
	];
}

export const RAS_BATCHES: RasBatch[] = [
	{
		period: 'June 2026',
		state: 'ml_recorded',
		count: 8,
		relief: 14210,
		accruing: true,
		steps: [
			{
				state: 'done',
				title: 'Personal contributions recorded to ML',
				meta: '8 contributions recorded to ML'
			},
			{ state: 'cur', title: 'Accruing for June RAS claim', meta: 'claim window closes 30 Jun' },
			{ state: 'wait', title: 'ML reclaims from HMRC', meta: 'claim due by 19 Jul' },
			{
				state: 'wait',
				title: 'Relief received → bulk payment + breakdown',
				meta: 'expected ~21 Aug'
			},
			{
				state: 'wait',
				title: 'Bulk ACR on SEI + breakdown upload',
				meta: 'SEI loader credits accounts'
			}
		]
	},
	{
		period: 'May 2026',
		state: 'ras_applied',
		count: 11,
		relief: 18940,
		current: true,
		steps: [
			{ state: 'done', title: 'Personal contributions recorded to ML', meta: '11 contributions' },
			{ state: 'done', title: 'ML reclaimed from HMRC', meta: 'claim submitted 18 Jun' },
			{ state: 'cur', title: 'Awaiting relief from HMRC', meta: 'due ~21 Jul · 6 working days' },
			{ state: 'wait', title: 'Bulk ACR on SEI + breakdown', meta: 'CashReceiptsDirectPosting' },
			{ state: 'wait', title: 'SEI loader credits member accounts' }
		]
	},
	{
		period: 'April 2026',
		state: 'reconciled',
		count: 9,
		relief: 11380,
		steps: [
			{ state: 'done', title: 'Recorded to ML', meta: '9 contributions' },
			{ state: 'done', title: 'ML reclaimed from HMRC', meta: 'submitted 17 May' },
			{ state: 'done', title: 'Relief received from HMRC', meta: '21 Jun · £11,380' },
			{
				state: 'done',
				title: 'Bulk ACR raised on SEI + breakdown',
				meta: 'ACR #ACR-2026-04 · £11,380'
			},
			{ state: 'done', title: 'SEI loader credited 9 accounts', meta: '3-way reconciled' }
		]
	}
];

export const RECON_BREAKS: ReconBreak[] = [
	{
		subject: 'contribution #102',
		member: 'Eleanor Voss',
		scope: 'graphene-vs-SEI',
		type: 'cash_not_credited',
		detail: '£40,000 recorded to ML & expected by SEI but not yet on the member account at SEI',
		ageDays: 1,
		status: 'investigating'
	},
	{
		subject: 'contribution #103',
		member: 'David Brennan',
		scope: 'graphene-vs-ML',
		type: 'expectation_mismatch',
		detail:
			'Third-party payment £2,880 — no matching Zendesk expectation; payer needs confirming before record to ML',
		ageDays: 2,
		status: 'open'
	}
];

/** Three-way contribution-cash reconciliation, June MTD (static figures). */
export const RECON_FIGURES = {
	graphene: 287440,
	sei: 285960,
	ml: 287440,
	note: '£1,480 Graphene/ML vs SEI — one in-flight item (Eleanor Voss, awaiting credit) + relief in transit; no unexplained break.'
};
