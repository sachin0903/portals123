// Static demo data for the advised-investment client portal — used both as the client app's
// placeholder content (until the domain model lands) and by the branding studio's live preview, so
// the editor renders the *real* components with representative data. Kept in @graphene/ui so both
// the app and the in-package preview share one source of truth.
import type { AllocationSlice } from '../components/charts/allocation-donut.svelte';
import type { PerformancePoint } from '../components/charts/performance-chart.svelte';
import type {
	Activity,
	Adviser,
	ClientDocument,
	Holding,
	NewsItem,
	PortalActionItem,
	PortalOnboarding,
	Wrapper,
	WrapperType
} from '../components/investment/types.js';

export const wrappers: Wrapper[] = [
	{
		type: 'ISA',
		name: 'Stocks & Shares ISA',
		owner: 'James Whitfield',
		value: 42870.12,
		gain: 3120.44,
		gainPct: 0.078,
		allowanceUsed: 12000,
		allowanceLimit: 20000,
		trend: [38, 39, 38.5, 40, 41.2, 40.8, 42.1, 42.9]
	},
	{
		type: 'SIPP',
		name: 'Self-Invested Personal Pension',
		owner: 'James Whitfield',
		value: 128450.0,
		gain: 9240.0,
		gainPct: 0.078,
		trend: [112, 115, 118, 116, 121, 124, 126, 128.5]
	},
	{
		type: 'GIA',
		name: 'General Investment Account',
		owner: 'James Whitfield',
		value: 18250.0,
		gain: -640.25,
		gainPct: -0.034,
		trend: [19.4, 19.1, 18.8, 19, 18.6, 18.4, 18.1, 18.25]
	},
	{
		type: 'JISA',
		name: 'Junior ISA',
		owner: 'Sophie Whitfield',
		delegated: true,
		value: 6120.5,
		gain: 410.5,
		gainPct: 0.072,
		allowanceUsed: 4200,
		allowanceLimit: 9000,
		trend: [5.5, 5.6, 5.7, 5.8, 5.9, 6, 6.05, 6.12]
	}
];

export const totals = {
	value: wrappers.reduce((s, w) => s + w.value, 0),
	gain: wrappers.reduce((s, w) => s + w.gain, 0),
	get gainPct() {
		return this.gain / (this.value - this.gain);
	}
};

export const performance: PerformancePoint[] = [
	{ label: 'Nov', value: 176000 },
	{ label: 'Dec', value: 178500 },
	{ label: 'Jan', value: 181200 },
	{ label: 'Feb', value: 179800 },
	{ label: 'Mar', value: 184600 },
	{ label: 'Apr', value: 188100 },
	{ label: 'May', value: 191400 },
	{ label: 'Jun', value: 195690 }
];

const allocColor: Record<string, string> = {
	Equities: 'var(--primary)',
	Bonds: 'var(--brand)',
	Property: 'var(--positive)',
	Cash: 'var(--muted-foreground)'
};
const slice = (label: string, value: number): AllocationSlice => ({
	label,
	value,
	color: allocColor[label] ?? 'var(--muted-foreground)'
});

/** Asset-class split per account (percentages); `all` is the whole portfolio. */
export const allocations: Record<'all' | WrapperType, AllocationSlice[]> = {
	all: [slice('Equities', 62), slice('Bonds', 20), slice('Property', 11), slice('Cash', 7)],
	ISA: [slice('Equities', 76), slice('Bonds', 14), slice('Cash', 10)],
	SIPP: [slice('Equities', 54), slice('Bonds', 29), slice('Property', 14), slice('Cash', 3)],
	GIA: [slice('Equities', 80), slice('Property', 12), slice('Cash', 8)],
	JISA: [slice('Equities', 85), slice('Bonds', 10), slice('Cash', 5)]
};

export const holdings: Holding[] = [
	{
		name: 'Vanguard FTSE Global All Cap',
		ticker: 'VAFTGAG',
		wrapper: 'ISA',
		units: 412.8,
		price: 78.42,
		value: 32370,
		gainPct: 0.094
	},
	{
		name: 'iShares Core MSCI World',
		ticker: 'SWDA',
		wrapper: 'SIPP',
		units: 1240,
		price: 89.1,
		value: 110484,
		gainPct: 0.081
	},
	{
		name: 'Royal London Short Term Money Mkt',
		ticker: 'RLSTMM',
		wrapper: 'SIPP',
		units: 17800,
		price: 1.01,
		value: 17966,
		gainPct: 0.012
	},
	{
		name: 'Fundsmith Equity',
		ticker: 'FEET',
		wrapper: 'GIA',
		units: 2410,
		price: 7.57,
		value: 18250,
		gainPct: -0.034
	},
	{
		name: 'Vanguard LifeStrategy 80%',
		ticker: 'VGLS80',
		wrapper: 'ISA',
		units: 88.2,
		price: 119.0,
		value: 10500,
		gainPct: 0.041
	},
	{
		name: 'Vanguard FTSE Global All Cap',
		ticker: 'VAFTGAG',
		wrapper: 'JISA',
		units: 78.1,
		price: 78.42,
		value: 6125,
		gainPct: 0.072
	}
];

export const activity: Activity[] = [
	{
		date: '14 Jun 2026',
		type: 'deposit',
		description: 'Monthly contribution',
		amount: 500,
		wrapper: 'ISA'
	},
	{
		date: '14 Jun 2026',
		type: 'buy',
		description: 'Vanguard FTSE Global All Cap',
		amount: -500,
		wrapper: 'ISA'
	},
	{
		date: '11 Jun 2026',
		type: 'dividend',
		description: 'iShares Core MSCI World',
		amount: 184.2,
		wrapper: 'SIPP'
	},
	{
		date: '5 Jun 2026',
		type: 'deposit',
		description: 'Employer pension contribution',
		amount: 750,
		wrapper: 'SIPP'
	},
	{
		date: '5 Jun 2026',
		type: 'buy',
		description: 'iShares Core MSCI World',
		amount: -750,
		wrapper: 'SIPP'
	},
	{ date: '1 Jun 2026', type: 'fee', description: 'Platform fee', amount: -22.4, wrapper: 'GIA' },
	{
		date: '28 May 2026',
		type: 'withdrawal',
		description: 'Withdrawal to bank',
		amount: -1500,
		wrapper: 'GIA'
	},
	{
		date: '20 May 2026',
		type: 'sell',
		description: 'Fundsmith Equity',
		amount: 1500,
		wrapper: 'GIA'
	},
	{
		date: '14 May 2026',
		type: 'deposit',
		description: 'Monthly contribution',
		amount: 200,
		wrapper: 'JISA'
	},
	{
		date: '14 May 2026',
		type: 'buy',
		description: 'Vanguard FTSE Global All Cap',
		amount: -200,
		wrapper: 'JISA'
	},
	{
		date: '12 May 2026',
		type: 'dividend',
		description: 'Vanguard FTSE Global All Cap',
		amount: 96.4,
		wrapper: 'ISA'
	},
	{
		date: '6 May 2026',
		type: 'deposit',
		description: 'Monthly contribution',
		amount: 500,
		wrapper: 'ISA'
	},
	{ date: '1 May 2026', type: 'fee', description: 'Adviser fee', amount: -64.5, wrapper: 'SIPP' },
	{
		date: '24 Apr 2026',
		type: 'dividend',
		description: 'Royal London Short Term Money Mkt',
		amount: 41.8,
		wrapper: 'SIPP'
	}
];

export const adviser: Adviser = {
	name: 'Eleanor Hughes',
	firm: 'Acme Advisers',
	email: 'eleanor.hughes@acmeadvisers.co.uk',
	phone: '+44 20 7946 0123'
};

export const news: NewsItem[] = [
	{
		id: 'boe-holds-rate',
		source: 'MarketWatch',
		headline: 'UK gilt yields steady as investors weigh the next rate move',
		summary:
			'Bond markets held a narrow range after fresh inflation data kept focus on policy timing.',
		time: '18 min ago',
		href: '/activity',
		tag: 'Markets'
	},
	{
		id: 'global-equities',
		source: 'FT Adviser',
		headline: 'Global equities edge higher as earnings outlook improves',
		summary: 'Diversified funds saw modest gains, led by technology and healthcare names.',
		time: '42 min ago',
		href: '/holdings',
		tag: 'Portfolio'
	},
	{
		id: 'isa-deadline',
		source: 'Investor Daily',
		headline: 'New ISA year prompts renewed focus on allowance planning',
		summary: 'Advisers are reviewing contribution schedules before the summer planning window.',
		time: '1 hr ago',
		href: '/documents',
		tag: 'Planning'
	}
];

/** ISO date (local calendar, not UTC — a late-evening viewer must not see tomorrow) `days` days
 * from now, computed once at module load. */
const isoInDays = (days: number): string => {
	const d = new Date();
	d.setDate(d.getDate() + days);
	const pad = (n: number) => String(n).padStart(2, '0');
	return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
};

/** Open action items for the actionItems block — the studio preview needs a representative card
 * (a firm placing the block must see it, not an empty hide). Due dates are computed relative to
 * today, unlike the rest of this file's static dates: a fixed date would age into a permanent
 * red "Overdue" as the studio's first impression. */
export const tasks: PortalActionItem[] = [
	{
		uid: 'demo-task-agreement',
		title: 'Sign your client agreement',
		description: 'Review and sign the updated terms of business',
		due_date: isoInDays(5)
	},
	{
		uid: 'demo-task-contact',
		title: 'Confirm your contact details',
		description: null,
		due_date: isoInDays(12)
	}
];

/** Mid-journey onboarding progress, so the onboardingProgress block previews as a live CTA
 * (done >= total would make it self-hide in the studio). `stages` lets the 'milestones' look
 * preview its real stepper rather than falling back to the flat bar every other look already
 * demonstrates. */
export const onboarding: PortalOnboarding = {
	done: 2,
	total: 5,
	stages: [
		{ key: 'details', label: 'Getting your details', status: 'done' },
		{ key: 'verification', label: 'Verification', status: 'done' },
		{ key: 'sign', label: 'Review & sign', status: 'current' },
		{ key: 'setup', label: 'Account setup', status: 'upcoming' },
		{ key: 'complete', label: 'Complete', status: 'upcoming' }
	]
};

/** Outstanding document requests, so the documents block's `show_requests` banner previews as
 * a live affordance in the studio (a real client with no fetched count renders NO banner —
 * resolveSlices' leak rule). */
export const requestsOutstanding = 2;

/** Demo profile for the account area. Email is overridden by the signed-in user. */
export const profile = {
	firstName: 'James',
	lastName: 'Whitfield',
	phone: '+44 7700 900482',
	address: {
		line1: '14 Cornhill',
		line2: '',
		city: 'London',
		postcode: 'EC3V 3ND',
		country: 'United Kingdom'
	}
};

export const documents: ClientDocument[] = [
	{
		id: 'stmt-q1-2026',
		name: 'Quarterly Statement — Q1 2026',
		category: 'Statements',
		date: '5 Apr 2026',
		size: '412 KB',
		format: 'PDF'
	},
	{
		id: 'stmt-q4-2025',
		name: 'Quarterly Statement — Q4 2025',
		category: 'Statements',
		date: '6 Jan 2026',
		size: '398 KB',
		format: 'PDF'
	},
	{
		id: 'stmt-valuation-jun-2026',
		name: 'Portfolio Valuation — Jun 2026',
		category: 'Statements',
		date: '1 Jun 2026',
		size: '286 KB',
		format: 'PDF'
	},
	{
		id: 'illus-isa-2627',
		name: 'ISA Illustration — 2026/27',
		category: 'Illustrations',
		date: '12 Jun 2026',
		size: '248 KB',
		format: 'PDF'
	},
	{
		id: 'illus-sipp-retirement',
		name: 'SIPP Projection — Retirement at 65',
		category: 'Illustrations',
		date: '12 Jun 2026',
		size: '264 KB',
		format: 'PDF'
	},
	{
		id: 'agr-client-terms',
		name: 'Client Agreement & Terms of Business',
		category: 'Agreements',
		date: '3 Feb 2025',
		size: '521 KB',
		format: 'PDF'
	},
	{
		id: 'agr-discretionary',
		name: 'Discretionary Management Agreement',
		category: 'Agreements',
		date: '3 Feb 2025',
		size: '347 KB',
		format: 'PDF'
	},
	{
		id: 'tax-annual-2526',
		name: 'Annual Tax Statement — 2025/26',
		category: 'Tax',
		date: '10 Apr 2026',
		size: '198 KB',
		format: 'PDF'
	},
	{
		id: 'tax-costs-charges-2025',
		name: 'Costs & Charges Disclosure — 2025',
		category: 'Tax',
		date: '18 Jan 2026',
		size: '156 KB',
		format: 'CSV'
	},
	{
		id: 'corr-suitability-may-2026',
		name: 'Suitability Report — May 2026',
		category: 'Correspondence',
		date: '22 May 2026',
		size: '301 KB',
		format: 'PDF'
	}
];

/** All client-portal demo data as one bundle (re-exported by apps/client's `$lib/demo`). */
export const clientDemo = {
	wrappers,
	totals,
	performance,
	allocations,
	holdings,
	activity,
	adviser,
	news,
	documents,
	tasks,
	onboarding,
	requestsOutstanding,
	profile
};
