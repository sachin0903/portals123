// The branding studio's preview clients — four deterministic personas that drive both the
// preview data (PortalPage renders them directly) and the auto-variant readout (their counts feed
// VariantContext). Style and units follow ./demo.ts (values in pounds, display-string dates), but
// where demo.ts shows ONE representative client, these deliberately sit either side of every
// VARIANT_THRESHOLDS boundary: individual2 stays under them all (accounts→stack), family6 crosses
// the holdings/activity/documents lines, individual12 lands in the accounts 'cards' band (7–24),
// and trust300 crosses everything (accounts→summary).
//
// Everything is a pure literal or a seeded formula — NO Math.random/Date.now — so the studio's
// preview (and any snapshot of it) renders identically on every load.
import type { Audience } from '../components/branding/page-audience.js';
import type {
	Activity,
	ClientDocument,
	Holding,
	PortalPageData,
	Wrapper
} from '../components/investment/types.js';
import {
	activity as demoActivity,
	adviser as demoAdviser,
	documents as demoDocuments,
	holdings as demoHoldings,
	tasks as demoTasks,
	wrappers as demoWrappers
} from './demo.js';

export interface Persona {
	key: 'individual2' | 'family6' | 'individual12' | 'trust300';
	/** e.g. 'Individual · 2 accounts', 'Family · 6 accounts', 'Trust · 300 accounts' */
	label: string;
	audience: Audience;
	counts: { accountCount: number; valuedAccountCount: number };
	/** Non-null page data, like a real client's — feeds PortalPage directly. */
	data: PortalPageData;
}

/**
 * Deterministic hash → [0, 1). Integer maths only (Math.imul + shifts), so the value is identical
 * on every JS engine — a Math.sin-based hash can drift in the last ulp between engines and make
 * "deterministic" fixtures flake across platforms.
 */
function rand01(seed: number): number {
	let h = Math.imul(seed + 1, 2654435761) >>> 0;
	h ^= h >>> 13;
	h = Math.imul(h, 2246822519) >>> 0;
	h ^= h >>> 16;
	// The XOR-assign coerces to a SIGNED int32; >>>0 recovers the unsigned value, keeping the
	// result in [0, 1) rather than (-0.5, 0.5).
	return (h >>> 0) / 4294967296;
}

const round2 = (n: number): number => Math.round(n * 100) / 100;

function totalsOf(wrappers: Wrapper[]): { value: number; gain: number; gainPct: number } {
	const value = round2(wrappers.reduce((s, w) => s + w.value, 0));
	const gain = round2(wrappers.reduce((s, w) => s + w.gain, 0));
	return { value, gain, gainPct: gain / (value - gain) };
}

// Same eight months demo.ts plots — a linear walk from cost basis to today's value, which is
// plausible enough for a preview and keeps the chart's endpoint agreeing with the header total.
const PERF_LABELS = ['Nov', 'Dec', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun'];

function performanceOf(totals: {
	value: number;
	gain: number;
}): { label: string; value: number }[] {
	return PERF_LABELS.map((label, i) => ({
		label,
		value: Math.round(totals.value - totals.gain + (totals.gain * (i + 1)) / PERF_LABELS.length)
	}));
}

/* ------------------------------------------------------------------------------------------------
 * individual2 — the demo client trimmed to two accounts. Reuses demo.ts shapes (structuredClone,
 * so a preview consumer mutating persona data can never corrupt the shared demo fixture) and stays
 * UNDER every threshold: 2 accounts (stack), 4 holdings, 9 activity rows, 5 documents.
 * ---------------------------------------------------------------------------------------------- */

// ISA + SIPP — the demo client's own two largest wrappers.
const individualWrappers = structuredClone(demoWrappers.slice(0, 2));
const individualHoldings = structuredClone(
	demoHoldings.filter((h) => h.wrapper === 'ISA' || h.wrapper === 'SIPP')
);
const individualActivity = structuredClone(
	demoActivity.filter((a) => a.wrapper === 'ISA' || a.wrapper === 'SIPP')
);
const individualTotals = totalsOf(individualWrappers);

const individual2: Persona = {
	key: 'individual2',
	label: 'Individual · 2 accounts',
	audience: 'individual',
	counts: { accountCount: 2, valuedAccountCount: 2 },
	data: {
		totals: individualTotals,
		performance: performanceOf(individualTotals),
		wrappers: individualWrappers,
		allocations: [
			{ label: 'Equities', value: 64, color: 'var(--primary)' },
			{ label: 'Bonds', value: 24, color: 'var(--brand)' },
			{ label: 'Property', value: 7, color: 'var(--positive)' },
			{ label: 'Cash', value: 5, color: 'var(--muted-foreground)' }
		],
		holdings: individualHoldings,
		activity: individualActivity,
		documents: structuredClone(demoDocuments.slice(0, 5)),
		adviser: structuredClone(demoAdviser),
		tasks: structuredClone(demoTasks),
		onboarding: { done: 2, total: 5 }
	}
};

/* ------------------------------------------------------------------------------------------------
 * family6 — a household: three of Daniel's own wrappers plus a delegated spouse ISA and two
 * children's JISAs. Crosses the holdings (10 ≥ 8 → table), activity (15 ≥ 12 → table) and
 * documents (9 ≥ 8 → grouped) thresholds while accounts stays at the stack maximum (6).
 * ---------------------------------------------------------------------------------------------- */

const familyWrappers: Wrapper[] = [
	{
		type: 'ISA',
		name: 'Stocks & Shares ISA',
		owner: 'Daniel Marsh',
		value: 51230.4,
		gain: 3890.1,
		gainPct: 0.082,
		allowanceUsed: 15000,
		allowanceLimit: 20000,
		trend: [46.2, 47.1, 47.8, 47.4, 48.9, 50.1, 50.6, 51.2]
	},
	{
		type: 'SIPP',
		name: 'Self-Invested Personal Pension',
		owner: 'Daniel Marsh',
		value: 214600.0,
		gain: 15900.0,
		gainPct: 0.08,
		trend: [192, 196, 201, 198, 205, 209, 212, 214.6]
	},
	{
		type: 'GIA',
		name: 'General Investment Account',
		owner: 'Daniel Marsh',
		value: 32410.75,
		gain: -1120.5,
		gainPct: -0.033,
		trend: [34.2, 33.8, 33.4, 33.7, 33.1, 32.8, 32.5, 32.4]
	},
	{
		type: 'ISA',
		name: 'Stocks & Shares ISA',
		owner: 'Laura Marsh',
		delegated: true,
		value: 44880.2,
		gain: 2960.4,
		gainPct: 0.071,
		allowanceUsed: 8000,
		allowanceLimit: 20000,
		trend: [40.9, 41.5, 42, 41.8, 43, 43.9, 44.4, 44.9]
	},
	{
		type: 'JISA',
		name: 'Junior ISA',
		owner: 'Freya Marsh',
		delegated: true,
		value: 7420.6,
		gain: 480.6,
		gainPct: 0.069,
		allowanceUsed: 3600,
		allowanceLimit: 9000,
		trend: [6.7, 6.8, 6.9, 7, 7.1, 7.2, 7.3, 7.4]
	},
	{
		type: 'JISA',
		name: 'Junior ISA',
		owner: 'Rory Marsh',
		delegated: true,
		value: 5210.3,
		gain: 320.3,
		gainPct: 0.065,
		allowanceUsed: 2400,
		allowanceLimit: 9000,
		trend: [4.7, 4.8, 4.85, 4.9, 5, 5.05, 5.15, 5.21]
	}
];

const familyHoldings: Holding[] = [
	{
		name: 'Vanguard FTSE Global All Cap',
		ticker: 'VAFTGAG',
		wrapper: 'ISA',
		units: 498.2,
		price: 78.42,
		value: 39069,
		gainPct: 0.091
	},
	{
		name: 'iShares Core MSCI World',
		ticker: 'SWDA',
		wrapper: 'SIPP',
		units: 1890,
		price: 89.1,
		value: 168399,
		gainPct: 0.084
	},
	{
		name: 'Royal London Short Term Money Mkt',
		ticker: 'RLSTMM',
		wrapper: 'SIPP',
		units: 28400,
		price: 1.01,
		value: 28684,
		gainPct: 0.012
	},
	{
		name: 'Vanguard UK Gilt UCITS ETF',
		ticker: 'VGOV',
		wrapper: 'SIPP',
		units: 992,
		price: 17.65,
		value: 17509,
		gainPct: 0.018
	},
	{
		name: 'Fundsmith Equity',
		ticker: 'FEET',
		wrapper: 'GIA',
		units: 4280,
		price: 7.57,
		value: 32400,
		gainPct: -0.033
	},
	{
		name: 'Vanguard LifeStrategy 80%',
		ticker: 'VGLS80',
		wrapper: 'ISA',
		units: 102.1,
		price: 119.0,
		value: 12150,
		gainPct: 0.043
	},
	{
		name: 'iShares Core MSCI World',
		ticker: 'SWDA',
		wrapper: 'ISA',
		units: 503,
		price: 89.1,
		value: 44817,
		gainPct: 0.072
	},
	{
		name: 'Vanguard FTSE Global All Cap',
		ticker: 'VAFTGAG',
		wrapper: 'JISA',
		units: 94.6,
		price: 78.42,
		value: 7419,
		gainPct: 0.069
	},
	{
		name: 'Vanguard LifeStrategy 80%',
		ticker: 'VGLS80',
		wrapper: 'JISA',
		units: 43.8,
		price: 119.0,
		value: 5212,
		gainPct: 0.065
	},
	{
		name: 'L&G Global Property REIT Index',
		ticker: 'LGGP',
		wrapper: 'GIA',
		units: 4300,
		price: 2.34,
		value: 10062,
		gainPct: 0.021
	}
];

const familyActivity: Activity[] = [
	{
		date: '14 Jun 2026',
		type: 'deposit',
		description: 'Monthly contribution',
		amount: 800,
		wrapper: 'ISA'
	},
	{
		date: '14 Jun 2026',
		type: 'buy',
		description: 'Vanguard FTSE Global All Cap',
		amount: -800,
		wrapper: 'ISA'
	},
	{
		date: '12 Jun 2026',
		type: 'deposit',
		description: 'Monthly contribution',
		amount: 150,
		wrapper: 'JISA'
	},
	{
		date: '12 Jun 2026',
		type: 'buy',
		description: 'Vanguard FTSE Global All Cap',
		amount: -150,
		wrapper: 'JISA'
	},
	{
		date: '11 Jun 2026',
		type: 'dividend',
		description: 'iShares Core MSCI World',
		amount: 291.6,
		wrapper: 'SIPP'
	},
	{
		date: '5 Jun 2026',
		type: 'deposit',
		description: 'Employer pension contribution',
		amount: 1250,
		wrapper: 'SIPP'
	},
	{
		date: '5 Jun 2026',
		type: 'buy',
		description: 'iShares Core MSCI World',
		amount: -1250,
		wrapper: 'SIPP'
	},
	{ date: '1 Jun 2026', type: 'fee', description: 'Platform fee', amount: -38.7, wrapper: 'GIA' },
	{
		date: '28 May 2026',
		type: 'withdrawal',
		description: 'Withdrawal to bank',
		amount: -2500,
		wrapper: 'GIA'
	},
	{
		date: '20 May 2026',
		type: 'sell',
		description: 'Fundsmith Equity',
		amount: 2500,
		wrapper: 'GIA'
	},
	{
		date: '14 May 2026',
		type: 'deposit',
		description: 'Monthly contribution',
		amount: 400,
		wrapper: 'ISA'
	},
	{
		date: '14 May 2026',
		type: 'buy',
		description: 'iShares Core MSCI World',
		amount: -400,
		wrapper: 'ISA'
	},
	{
		date: '12 May 2026',
		type: 'dividend',
		description: 'Vanguard FTSE Global All Cap',
		amount: 118.2,
		wrapper: 'ISA'
	},
	{ date: '1 May 2026', type: 'fee', description: 'Adviser fee', amount: -107.3, wrapper: 'SIPP' },
	{
		date: '24 Apr 2026',
		type: 'dividend',
		description: 'Royal London Short Term Money Mkt',
		amount: 66.4,
		wrapper: 'SIPP'
	}
];

const familyTotals = totalsOf(familyWrappers);

const family6: Persona = {
	key: 'family6',
	label: 'Family · 6 accounts',
	audience: 'individual',
	counts: { accountCount: 6, valuedAccountCount: 6 },
	data: {
		totals: familyTotals,
		performance: performanceOf(familyTotals),
		wrappers: familyWrappers,
		allocations: [
			{ label: 'Equities', value: 68, color: 'var(--primary)' },
			{ label: 'Bonds', value: 18, color: 'var(--brand)' },
			{ label: 'Property', value: 8, color: 'var(--positive)' },
			{ label: 'Cash', value: 6, color: 'var(--muted-foreground)' }
		],
		holdings: familyHoldings,
		activity: familyActivity,
		documents: structuredClone(demoDocuments.slice(0, 9)),
		adviser: structuredClone(demoAdviser),
		tasks: [
			{
				uid: 'persona-family-review',
				title: 'Book your annual review',
				description: 'Choose a slot for this year’s household planning review',
				due_date: '2026-08-14'
			},
			{
				uid: 'persona-family-jisa',
				title: 'Top up Freya’s Junior ISA',
				description: null,
				due_date: null
			}
		],
		onboarding: { done: 4, total: 5 }
	}
};

/* ------------------------------------------------------------------------------------------------
 * individual12 — a consolidator: four accounts of her own plus eight legacy pension pots being
 * gathered in. The only persona in the accounts 'cards' band (7–24 accounts — unpreviewable
 * before this one existed): 12 accounts (11 valued — one pot is mid-transfer with no valuation
 * yet), 12 holdings (table), 14 activity rows (table), 6 documents (recent, under grouped).
 * ---------------------------------------------------------------------------------------------- */

const CONSOLIDATOR = 'Priya Shah';

const consolidatorOwnWrappers: Wrapper[] = [
	{
		type: 'ISA',
		name: 'Stocks & Shares ISA',
		owner: CONSOLIDATOR,
		value: 28440.6,
		gain: 1980.3,
		gainPct: 0.075,
		allowanceUsed: 9000,
		allowanceLimit: 20000,
		trend: [26.1, 26.5, 26.9, 26.7, 27.4, 27.9, 28.2, 28.4]
	},
	{
		type: 'LISA',
		name: 'Lifetime ISA',
		owner: CONSOLIDATOR,
		value: 12210.4,
		gain: 830.4,
		gainPct: 0.073,
		allowanceUsed: 3200,
		allowanceLimit: 4000,
		trend: [11.2, 11.3, 11.5, 11.4, 11.7, 11.9, 12.1, 12.2]
	},
	{
		type: 'GIA',
		name: 'General Investment Account',
		owner: CONSOLIDATOR,
		value: 15640.2,
		gain: -420.8,
		gainPct: -0.026,
		trend: [16.2, 16, 15.8, 15.9, 15.7, 15.5, 15.6, 15.6]
	},
	{
		type: 'SIPP',
		name: 'Self-Invested Personal Pension',
		owner: CONSOLIDATOR,
		value: 96800.0,
		gain: 6540.0,
		gainPct: 0.072,
		trend: [88, 89.5, 91, 90.2, 93, 94.8, 95.9, 96.8]
	}
];

// Eight old workplace pensions gathered in for consolidation; the last is mid-transfer and
// carries no live valuation yet (value 0, empty trend) — the studio previews that state too.
const legacyPotWrappers: Wrapper[] = Array.from({ length: 8 }, (_, i) => {
	const valued = i < 7;
	// Pot sizes ~£18k–£82k; gains −4%…+12% — a plausible spread of old default funds.
	const value = valued ? round2(18000 + rand01(i + 5000) * 64000) : 0;
	// `+ 0` normalises a rounded −0 to 0: JSON round-trips −0 as 0, which would break the
	// personas' "data is JSON-safe" contract (personas.test.ts snapshots it).
	const gainPct = valued ? Math.round((rand01(i + 6000) * 0.16 - 0.04) * 1000) / 1000 + 0 : 0;
	const gain = round2((value * gainPct) / (1 + gainPct));
	return {
		type: 'SIPP',
		name: `Legacy Pension — Plan ${i + 1}`,
		owner: CONSOLIDATOR,
		value,
		gain,
		gainPct,
		// Sparkline in thousands (demo.ts convention): a straight walk from cost basis to today.
		trend: valued
			? Array.from(
					{ length: 8 },
					(_, t) => Math.round(((value - gain + (gain * (t + 1)) / 8) / 1000) * 10) / 10
				)
			: []
	};
});

const consolidatorWrappers: Wrapper[] = [...consolidatorOwnWrappers, ...legacyPotWrappers];

const CONSOLIDATOR_FUNDS: readonly [name: string, ticker: string, price: number][] = [
	['Vanguard FTSE Global All Cap', 'VAFTGAG', 78.42],
	['iShares Core MSCI World', 'SWDA', 89.1],
	['Vanguard LifeStrategy 80%', 'VGLS80', 119.0],
	['Fundsmith Equity', 'FEET', 7.57],
	['Royal London Short Term Money Mkt', 'RLSTMM', 1.01],
	['Vanguard UK Gilt UCITS ETF', 'VGOV', 17.65]
];
const CONSOLIDATOR_WRAPPER_LABELS = ['ISA', 'SIPP', 'GIA', 'LISA'] as const;

const consolidatorHoldings: Holding[] = Array.from({ length: 12 }, (_, i) => {
	const [name, ticker, price] = CONSOLIDATOR_FUNDS[i % CONSOLIDATOR_FUNDS.length]!;
	const units = Math.round((4000 + rand01(i + 7000) * 26000) / price);
	return {
		name,
		ticker,
		wrapper: CONSOLIDATOR_WRAPPER_LABELS[i % CONSOLIDATOR_WRAPPER_LABELS.length]!,
		units,
		price,
		value: round2(units * price),
		// `+ 0` normalises a rounded −0 (see legacyPotWrappers) — this seed does hit one.
		gainPct: Math.round((rand01(i + 8000) * 0.18 - 0.04) * 1000) / 1000 + 0
	};
});

const CONSOLIDATOR_ACTIVITY: readonly [
	type: Activity['type'],
	description: string,
	sign: 1 | -1
][] = [
	['deposit', 'Monthly contribution', 1],
	['buy', 'Vanguard FTSE Global All Cap', -1],
	['dividend', 'iShares Core MSCI World', 1],
	['deposit', 'Pension transfer in — Plan 4', 1],
	['fee', 'Platform fee', -1],
	['sell', 'Fundsmith Equity', 1],
	['withdrawal', 'Withdrawal to bank', -1]
];

const consolidatorActivity: Activity[] = Array.from({ length: 14 }, (_, i) => {
	const [type, description, sign] = CONSOLIDATOR_ACTIVITY[i % CONSOLIDATOR_ACTIVITY.length]!;
	// Seven rows per month, days descending within each — reads like a real ledger.
	const day = 27 - (i % 7) * 4;
	return {
		date: `${day} ${i < 7 ? 'Jun' : 'May'} 2026`,
		type,
		description,
		amount: round2(sign * (40 + rand01(i + 9000) * 1800)),
		wrapper: CONSOLIDATOR_WRAPPER_LABELS[i % CONSOLIDATOR_WRAPPER_LABELS.length]!
	};
});

const consolidatorTotals = totalsOf(consolidatorWrappers);

const individual12: Persona = {
	key: 'individual12',
	label: 'Individual · 12 accounts',
	audience: 'individual',
	counts: { accountCount: 12, valuedAccountCount: 11 },
	data: {
		totals: consolidatorTotals,
		performance: performanceOf(consolidatorTotals),
		wrappers: consolidatorWrappers,
		allocations: [
			{ label: 'Equities', value: 58, color: 'var(--primary)' },
			{ label: 'Bonds', value: 26, color: 'var(--brand)' },
			{ label: 'Property', value: 6, color: 'var(--positive)' },
			{ label: 'Cash', value: 10, color: 'var(--muted-foreground)' }
		],
		holdings: consolidatorHoldings,
		activity: consolidatorActivity,
		documents: structuredClone(demoDocuments.slice(0, 6)),
		adviser: structuredClone(demoAdviser),
		tasks: structuredClone(demoTasks),
		onboarding: { done: 5, total: 5 }
	}
};

/* ------------------------------------------------------------------------------------------------
 * trust300 — a discretionary settlement running 300 numbered portfolios. Generated from seeded
 * formulas (compact, and 300 hand-written wrappers would be unreviewable) and crosses EVERY
 * threshold: 300 accounts (> cardsMax 24 → summary), 40 holdings, 50 activity rows, 12 documents.
 * 20 portfolios are onboarding and carry no live valuation yet (counts.valuedAccountCount 280).
 * ---------------------------------------------------------------------------------------------- */

const TRUST_OWNER = 'The Aldercroft Settlement';
const pad3 = (n: number): string => String(n).padStart(3, '0');

const TRUST_FUNDS: readonly [name: string, ticker: string, price: number][] = [
	['Vanguard FTSE Global All Cap', 'VAFTGAG', 78.42],
	['iShares Core MSCI World', 'SWDA', 89.1],
	['Fundsmith Equity', 'FEET', 7.57],
	['Vanguard UK Gilt UCITS ETF', 'VGOV', 17.65],
	['iShares Corporate Bond Index', 'ICBI', 1.52],
	['Royal London Short Term Money Mkt', 'RLSTMM', 1.01],
	['L&G Global Property REIT Index', 'LGGP', 2.34],
	['Vanguard LifeStrategy 80%', 'VGLS80', 119.0]
];

const trustWrappers: Wrapper[] = Array.from({ length: 300 }, (_, i) => {
	const valued = i < 280;
	// Value spread ~£45k–£900k per portfolio; gains −6%…+18% — a plausible mixed book.
	const value = valued ? round2(45000 + rand01(i) * 855000) : 0;
	const gainPct = valued ? Math.round((rand01(i + 1000) * 0.24 - 0.06) * 1000) / 1000 : 0;
	const gain = round2((value * gainPct) / (1 + gainPct));
	return {
		type: 'GIA',
		name: `Portfolio ${pad3(i + 1)} · GIA`,
		owner: TRUST_OWNER,
		value,
		gain,
		gainPct,
		// Sparkline in thousands (demo.ts convention): a straight walk from cost basis to today.
		trend: valued
			? Array.from(
					{ length: 8 },
					(_, t) => Math.round(((value - gain + (gain * (t + 1)) / 8) / 1000) * 10) / 10
				)
			: []
	};
});

const trustHoldings: Holding[] = Array.from({ length: 40 }, (_, i) => {
	const [name, ticker, price] = TRUST_FUNDS[i % TRUST_FUNDS.length]!;
	const units = Math.round((60000 + rand01(i + 2000) * 640000) / price);
	return {
		name,
		ticker,
		// Spread across the numbered portfolios (deterministic stride so no two rows collide).
		wrapper: `Portfolio ${pad3(((i * 7) % 280) + 1)}`,
		units,
		value: round2(units * price),
		price,
		gainPct: Math.round((rand01(i + 3000) * 0.2 - 0.04) * 1000) / 1000
	};
});

const TRUST_ACTIVITY: readonly [type: Activity['type'], description: string, sign: 1 | -1][] = [
	['deposit', 'Trustee contribution', 1],
	['buy', 'iShares Core MSCI World', -1],
	['dividend', 'Vanguard FTSE Global All Cap', 1],
	['fee', 'Discretionary management fee', -1],
	['sell', 'Fundsmith Equity', 1],
	['withdrawal', 'Distribution to beneficiaries', -1]
];
const TRUST_MONTHS = ['Jun', 'May', 'Apr', 'Mar', 'Feb'];

const trustActivity: Activity[] = Array.from({ length: 50 }, (_, i) => {
	const [type, description, sign] = TRUST_ACTIVITY[i % TRUST_ACTIVITY.length]!;
	// Ten rows per month, days descending within each — reads like a real ledger.
	const day = 28 - (i % 10) * 3;
	return {
		date: `${day} ${TRUST_MONTHS[Math.floor(i / 10)]!} 2026`,
		type,
		description,
		amount: round2(sign * (1500 + rand01(i + 4000) * 48500)),
		wrapper: `Portfolio ${pad3(((i * 11) % 280) + 1)}`
	};
});

const trustDocuments: ClientDocument[] = [
	{
		id: 'trust-stmt-q2-2026',
		name: 'Consolidated Statement — Q2 2026',
		category: 'Statements',
		date: '5 Jul 2026',
		size: '2.1 MB',
		format: 'PDF'
	},
	{
		id: 'trust-stmt-q1-2026',
		name: 'Consolidated Statement — Q1 2026',
		category: 'Statements',
		date: '6 Apr 2026',
		size: '2.0 MB',
		format: 'PDF'
	},
	{
		id: 'trust-stmt-q4-2025',
		name: 'Consolidated Statement — Q4 2025',
		category: 'Statements',
		date: '7 Jan 2026',
		size: '1.9 MB',
		format: 'PDF'
	},
	{
		id: 'trust-valuation-jun-2026',
		name: 'Portfolio Valuations — Jun 2026',
		category: 'Statements',
		date: '1 Jun 2026',
		size: '3.4 MB',
		format: 'CSV'
	},
	{
		id: 'trust-agr-ima',
		name: 'Investment Management Agreement',
		category: 'Agreements',
		date: '14 Mar 2024',
		size: '612 KB',
		format: 'PDF'
	},
	{
		id: 'trust-agr-deed',
		name: 'Deed of Appointment — 2025 Restatement',
		category: 'Agreements',
		date: '2 Sep 2025',
		size: '488 KB',
		format: 'PDF'
	},
	{
		id: 'trust-tax-2526',
		name: 'Trust Tax Pack — 2025/26',
		category: 'Tax',
		date: '12 Apr 2026',
		size: '941 KB',
		format: 'PDF'
	},
	{
		id: 'trust-tax-cgt-2526',
		name: 'CGT Realisations Schedule — 2025/26',
		category: 'Tax',
		date: '12 Apr 2026',
		size: '388 KB',
		format: 'CSV'
	},
	{
		id: 'trust-illus-drawdown',
		name: 'Distribution Strategy Illustration',
		category: 'Illustrations',
		date: '20 May 2026',
		size: '724 KB',
		format: 'PDF'
	},
	{
		id: 'trust-corr-ips-2026',
		name: 'Investment Policy Statement — 2026 Review',
		category: 'Correspondence',
		date: '28 Apr 2026',
		size: '356 KB',
		format: 'PDF'
	},
	{
		id: 'trust-corr-minutes-q2',
		name: 'Trustee Meeting Minutes — Q2 2026',
		category: 'Correspondence',
		date: '17 Jun 2026',
		size: '204 KB',
		format: 'DOCX'
	},
	{
		id: 'trust-corr-suitability',
		name: 'Suitability Report — Annual Review',
		category: 'Correspondence',
		date: '30 Apr 2026',
		size: '512 KB',
		format: 'PDF'
	}
];

const trustTotals = totalsOf(trustWrappers);

const trust300: Persona = {
	key: 'trust300',
	label: 'Trust · 300 accounts',
	audience: 'organisation',
	counts: { accountCount: 300, valuedAccountCount: 280 },
	data: {
		totals: trustTotals,
		performance: performanceOf(trustTotals),
		wrappers: trustWrappers,
		allocations: [
			{ label: 'Equities', value: 48, color: 'var(--primary)' },
			{ label: 'Bonds', value: 34, color: 'var(--brand)' },
			{ label: 'Property', value: 10, color: 'var(--positive)' },
			{ label: 'Cash', value: 8, color: 'var(--muted-foreground)' }
		],
		holdings: trustHoldings,
		activity: trustActivity,
		documents: trustDocuments,
		adviser: {
			name: 'Priya Nair',
			firm: 'Acme Advisers',
			email: 'priya.nair@acmeadvisers.co.uk',
			phone: '+44 20 7946 0345'
		},
		tasks: [
			{
				uid: 'persona-trust-minutes',
				title: 'Approve the Q2 trustee minutes',
				description: 'Review and approve the minutes before the July distribution run',
				due_date: '2026-08-07'
			}
		],
		// A long-standing settlement — onboarding complete (the block self-hides, which is exactly
		// what a firm previewing an established client should see).
		onboarding: { done: 5, total: 5 }
	}
};

export const PERSONAS: readonly Persona[] = [individual2, family6, individual12, trust300];

export function personaByKey(key: Persona['key']): Persona {
	// PERSONAS is a fixed four-element literal, so the lookup cannot miss for a well-typed key.
	return PERSONAS.find((p) => p.key === key)!;
}
