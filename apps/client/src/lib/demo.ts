// FIXTURE DATA ONLY — never render any of this to a signed-in client. Every value below is
// invented (names, holdings, transactions, addresses), so showing it on a real surface is
// indistinguishable, to the user, from another firm's data leaking in.
//
// `formatDateTime` is re-exported from `$lib/format` at the bottom as a shim only.
// Real portfolio/document data comes from the API (see `$lib/api`, `$lib/documents`).
import type {
	Activity,
	Adviser,
	AllocationSlice,
	Holding,
	NewsItem,
	PerformancePoint,
	Wrapper,
	WrapperType
} from '@graphene/ui';

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

/* ── Documents (the signed-in client's library) ───────────────────────────
   Richer than a flat list: tracks unread + pending-action so the documents
   page can surface attention. Shapes mirror the planned document-management
   API so wiring real data later is a swap. */

export type DocCategory = 'report' | 'statement' | 'agreement' | 'identity' | 'tax' | 'other';

export interface ClientDoc {
	uid: string;
	name: string;
	/** The document's `document_type` — a free-text slug for real docs, a `DocCategory` here. */
	category: string;
	createdAt: string;
	sizeBytes?: number;
	/** True until the client has opened the document — drives the "unread" highlight. */
	unread?: boolean;
	/** When the client last opened the document — powers the "Last interacted with" sort. */
	lastViewedAt?: string;
	/** Static path served from /static — undefined for image docs without a PDF mock. */
	mockSrc?: string;
	contentType?: string;
	/** How the document originated ('generated' | 'uploaded') — shown as the row's status pill. */
	source?: string;
}

export const documents: ClientDoc[] = [
	{
		uid: 'doc-anr-2025',
		name: 'Annual Review 2025.pdf',
		category: 'report',
		createdAt: '2026-05-18T09:24:00Z',
		sizeBytes: 842_000,
		mockSrc: '/mock/annual-review-2025.pdf',
		contentType: 'application/pdf'
	},
	{
		uid: 'doc-suit-2026',
		name: 'Suitability Report 2026.pdf',
		category: 'report',
		createdAt: '2026-06-02T14:10:00Z',
		sizeBytes: 612_300,
		unread: true,
		mockSrc: '/mock/suitability-report-2026.pdf',
		contentType: 'application/pdf'
	},
	{
		uid: 'doc-isa-q1',
		name: 'ISA Statement Q1 2026.pdf',
		category: 'statement',
		createdAt: '2026-04-06T08:00:00Z',
		sizeBytes: 188_400,
		unread: true,
		mockSrc: '/mock/isa-statement-q1.pdf',
		contentType: 'application/pdf'
	},
	{
		uid: 'doc-pension-2025',
		name: 'Pension Statement 2025.pdf',
		category: 'statement',
		createdAt: '2026-02-14T10:30:00Z',
		sizeBytes: 241_600,
		mockSrc: '/mock/pension-statement-2025.pdf',
		contentType: 'application/pdf'
	},
	{
		uid: 'doc-fee-agr',
		name: 'Fee Agreement 2026.pdf',
		category: 'agreement',
		createdAt: '2026-06-10T11:02:00Z',
		sizeBytes: 230_900,
		unread: true,
		mockSrc: '/mock/fee-agreement-2026.pdf',
		contentType: 'application/pdf'
	},
	{
		uid: 'doc-svc-agr',
		name: 'Service Agreement.pdf',
		category: 'agreement',
		createdAt: '2024-03-01T09:00:00Z',
		sizeBytes: 198_200,
		mockSrc: '/mock/service-agreement.pdf',
		contentType: 'application/pdf'
	},
	{
		uid: 'doc-passport',
		name: 'Passport.jpg',
		category: 'identity',
		createdAt: '2026-03-21T16:45:00Z',
		sizeBytes: 1_420_000
	},
	{
		uid: 'doc-tax-cert',
		name: 'Tax Certificate 2024-25.pdf',
		category: 'tax',
		createdAt: '2025-05-10T08:00:00Z',
		sizeBytes: 145_000,
		mockSrc: '/mock/tax-cert-2024-25.pdf',
		contentType: 'application/pdf'
	}
];

export const CATEGORY_LABELS: Record<DocCategory, string> = {
	report: 'Reports',
	statement: 'Statements',
	agreement: 'Agreements',
	identity: 'Identity',
	tax: 'Tax',
	other: 'Other'
};

export function getDocument(uid: string): ClientDoc | undefined {
	return documents.find((d) => d.uid === uid);
}

/* Demo preview sources — map each document to a styled HTML mock in /static/mock. Mirrors the
   admin/platform viewers so every document renders a clean full-canvas preview rather than the
   browser's native PDF chrome. Images (e.g. the passport) fall back to the document's own mock. */
const PREVIEW_BY_NAME: Record<string, { src: string; contentType: string }> = {
	'Annual Review 2025.pdf': { src: '/mock/annual-review-2025.html', contentType: 'text/html' },
	'Suitability Report 2026.pdf': {
		src: '/mock/suitability-report-2026.html',
		contentType: 'text/html'
	},
	'ISA Statement Q1 2026.pdf': { src: '/mock/isa-statement-q1.html', contentType: 'text/html' },
	'Pension Statement 2025.pdf': {
		src: '/mock/pension-statement-2025.html',
		contentType: 'text/html'
	},
	'Fee Agreement 2026.pdf': { src: '/mock/fee-agreement-2026.html', contentType: 'text/html' },
	'Service Agreement.pdf': { src: '/mock/service-agreement.html', contentType: 'text/html' },
	'Tax Certificate 2024-25.pdf': { src: '/mock/tax-cert-2024-25.html', contentType: 'text/html' }
};

/** Preview source for a document — a name-mapped mock, falling back to the document's own. */
export function documentPreview(doc: ClientDoc): {
	src: string | undefined;
	contentType: string | undefined;
} {
	return PREVIEW_BY_NAME[doc.name] ?? { src: doc.mockSrc, contentType: doc.contentType };
}

/* The real, fixture-free formatters now live in `$lib/format` — importing them from here trained
   everyone to treat this module as legitimate. Re-exported as a shim so existing call sites keep
   working; new code must import from `$lib/format`. */
export { formatDateTime } from '$lib/format';
// Re-exported shim: the countdown helpers moved to `$lib/format` and now measure against the REAL
// clock (they used to subtract from DEMO_NOW below). Import them from `$lib/format`.
export { daysUntil, daysUntilDue, daysUntilExpiry } from '$lib/format';

/** Fixed "now" the FIXTURES below are dated around. Nothing that renders real data may measure
    against it — the countdown helpers used to, and now live in `$lib/format` on the real clock. */
export const DEMO_NOW = new Date('2026-06-22T12:00:00Z');

/* ── Tasks (client actions: acknowledge / sign / accept terms / upload) ──── */

export type TaskType = 'acknowledge' | 'sign' | 'accept_terms' | 'upload';
export type TaskStatus = 'pending' | 'overdue' | 'completed';
export type Urgency = 'high' | 'medium' | 'low';

export interface Task {
	id: string;
	type: TaskType;
	title: string;
	description: string;
	instruction: string;
	/** Stable FK to the library document this task acts on, when one exists. */
	documentUid?: string;
	documentName?: string;
	documentCategory?: DocCategory;
	/** Inline mock document content shown in the task viewer. */
	documentExcerpt?: string;
	sentBy: string;
	sentAt: string;
	dueDate?: string;
	status: TaskStatus;
	completedAt?: string;
	urgency: Urgency;
	uploadCategory?: string;
	uploadDescription?: string;
}

export const TASK_TYPE_LABELS: Record<TaskType, string> = {
	acknowledge: 'Acknowledge',
	sign: 'Sign',
	accept_terms: 'Accept terms',
	upload: 'Upload'
};

export const tasks: Task[] = [
	{
		id: 'task-fee-agr',
		type: 'acknowledge',
		title: 'Acknowledge Fee Agreement 2026',
		description:
			'Your adviser has updated the fee agreement for the coming year. Please read and acknowledge it to confirm you understand the charges that apply to your account.',
		instruction:
			'Read the document below and tick the box to confirm you have read and understood it.',
		documentUid: 'doc-fee-agr',
		documentName: 'Fee Agreement 2026.pdf',
		documentCategory: 'agreement',
		documentExcerpt: `This Fee Agreement ("Agreement") sets out the fees payable by you ("the Client") to Acme Advisers Ltd ("the Firm") in respect of the investment advisory and management services provided under the Service Agreement dated 15 January 2026.

1. ADVISORY FEE
The ongoing advisory fee is 0.75% per annum of the total value of assets under advice, calculated and collected quarterly in arrears. This fee covers portfolio reviews, annual suitability assessments, and access to your designated adviser.

2. TRANSACTION CHARGES
Transactions executed on your behalf will incur a charge of £25 per trade for listed securities. No transaction charges apply to fund switches within your existing portfolio.

3. REVIEW MEETINGS
Two formal review meetings per year are included within the ongoing advisory fee. Additional meetings requested by the Client may be charged at £150 per hour.

4. CHANGES TO FEES
The Firm reserves the right to revise fees with 30 days' written notice. You may terminate this agreement at any time by providing written notice to the Firm.

By acknowledging this document, you confirm that you have read, understood, and agree to the fee structure outlined above.`,
		sentBy: 'Sarah Blackwood',
		sentAt: '2026-06-10T09:00:00Z',
		dueDate: '2026-06-25',
		status: 'overdue',
		urgency: 'high'
	},
	{
		id: 'task-tob',
		type: 'sign',
		title: 'Sign Terms of Business',
		description:
			'We have updated our Terms of Business to reflect regulatory changes effective 1 July 2026. Your signature is required to continue receiving advisory services.',
		instruction:
			"Read the Terms of Business carefully. Enter your full name and today's date to sign.",
		documentName: 'Terms of Business v2.1.pdf',
		documentCategory: 'agreement',
		documentExcerpt: `TERMS OF BUSINESS

Acme Advisers Ltd is authorised and regulated by the Financial Conduct Authority (FCA Reference: 123456). These Terms of Business govern the relationship between Acme Advisers Ltd ("the Firm") and you ("the Client").

1. SERVICES PROVIDED
The Firm will provide discretionary and advisory investment management services, including the preparation of a personalised financial plan, ongoing portfolio monitoring, and suitability assessments in accordance with FCA rules.

2. CLIENT CATEGORISATION
You have been categorised as a Retail Client, which affords you the highest level of regulatory protection under the FCA's COBS rules.

3. CONFLICTS OF INTEREST
The Firm maintains a Conflicts of Interest Policy to identify, manage, and where necessary disclose conflicts that may affect the service provided to you. A copy is available on request.

4. COMPLAINTS
If you have a complaint, please contact compliance@acmeadvisers.co.uk. If unresolved, you may refer to the Financial Ombudsman Service (FOS). You may also be entitled to compensation from the Financial Services Compensation Scheme (FSCS) up to £85,000.

5. DATA PROTECTION
Personal data is processed in accordance with the UK GDPR and the Data Protection Act 2018. Full details are set out in our Privacy Notice, available at acmeadvisers.co.uk/privacy.

These Terms replace all previous versions. Signing below confirms your acceptance.`,
		sentBy: 'Sarah Blackwood',
		sentAt: '2026-06-15T10:00:00Z',
		dueDate: '2026-06-30',
		status: 'pending',
		urgency: 'medium'
	},
	{
		id: 'task-service-terms',
		type: 'accept_terms',
		title: 'Accept updated service terms',
		description:
			'Your adviser has shared the updated client service terms for 2026. Please read and accept these to confirm you are happy to continue under the revised terms.',
		instruction:
			'Read the terms carefully and tick the box to confirm your acceptance. Contact your adviser if you have any questions before accepting.',
		documentName: 'Client Service Terms 2026.pdf',
		documentCategory: 'agreement',
		documentExcerpt: `CLIENT SERVICE TERMS 2026

These terms govern the ongoing advisory relationship between you ("the Client") and Acme Advisers Ltd ("the Firm"), effective 1 July 2026. They replace all previous versions of the Client Service Terms.

1. SCOPE OF SERVICE
The Firm will provide you with ongoing personal financial advice, including annual suitability assessments, portfolio reviews, and access to your named adviser. Ad-hoc guidance is included within the annual service fee unless otherwise agreed in writing.

2. FEES AND CHARGES
Fees are set out in your current Fee Agreement. You will receive written notice of any changes to the fee schedule no less than 30 days before they take effect. You may terminate the service relationship at any time by providing written notice.

3. COMMUNICATION
Your adviser will contact you at least once per year for a formal review. Between reviews, you may reach your adviser by secure message, telephone, or email during business hours (Monday–Friday, 9 am–5 pm).

4. YOUR RESPONSIBILITIES
You agree to inform the Firm promptly of any material changes to your circumstances, financial position, or objectives that may affect the suitability of advice provided.

5. CONFLICTS OF INTEREST
The Firm maintains a written Conflicts of Interest Policy. Where a conflict cannot be adequately managed, the Firm will disclose it to you before proceeding.

6. GOVERNING LAW
These terms are governed by the laws of England and Wales. Any disputes will be subject to the exclusive jurisdiction of the English courts, without prejudice to your right to refer a complaint to the Financial Ombudsman Service.

By accepting these terms, you confirm that you have read, understood, and agreed to the above.`,
		sentBy: 'Sarah Blackwood',
		sentAt: '2026-06-18T14:00:00Z',
		status: 'pending',
		urgency: 'low'
	},
	{
		id: 'task-upload-id',
		type: 'upload',
		title: 'Upload proof of identity',
		description:
			'As part of our ongoing KYC requirements, we need an up-to-date copy of your photo ID. Please upload a passport or driving licence.',
		instruction: 'Upload a clear scan or photo of your passport or driving licence.',
		sentBy: 'Sarah Blackwood',
		sentAt: '2026-06-12T11:00:00Z',
		dueDate: '2026-07-01',
		status: 'pending',
		urgency: 'medium',
		uploadCategory: 'identity',
		uploadDescription: 'Passport or driving licence (photo must be clearly legible).'
	},
	{
		id: 'task-eng-letter',
		type: 'sign',
		title: 'Sign Engagement Letter',
		description: 'Initial engagement letter confirming the scope of advisory services.',
		instruction: 'Sign to confirm the engagement scope.',
		documentName: 'Engagement Letter.pdf',
		documentCategory: 'agreement',
		sentBy: 'Sarah Blackwood',
		sentAt: '2026-01-10T09:00:00Z',
		status: 'completed',
		completedAt: '2026-01-14T16:22:00Z',
		urgency: 'high'
	},
	{
		id: 'task-annual-review-2025',
		type: 'acknowledge',
		title: 'Acknowledge Annual Review Summary 2025',
		description: 'Summary of your portfolio performance and adviser recommendations for the year.',
		instruction: 'Read and acknowledge the annual review summary.',
		documentName: 'Annual Review Summary 2025.pdf',
		documentCategory: 'report',
		sentBy: 'Sarah Blackwood',
		sentAt: '2025-12-01T10:00:00Z',
		status: 'completed',
		completedAt: '2025-12-04T11:45:00Z',
		urgency: 'medium'
	},
	{
		id: 'task-risk-profile-2025',
		type: 'accept_terms',
		title: 'Accept updated risk profile terms',
		description:
			'Following your attitude to risk reassessment, please accept the updated terms reflecting your Balanced Growth profile.',
		instruction: 'Review and accept the updated risk profile terms.',
		documentName: 'Risk Profile Terms — Balanced Growth.pdf',
		documentCategory: 'agreement',
		sentBy: 'Sarah Blackwood',
		sentAt: '2025-11-12T09:30:00Z',
		status: 'completed',
		completedAt: '2025-11-13T08:10:00Z',
		urgency: 'medium'
	},
	{
		id: 'task-passport-2025',
		type: 'upload',
		title: 'Upload renewed passport',
		description:
			'Your passport on file has expired. Please upload a copy of your renewed passport to keep your KYC records current.',
		instruction: 'Upload a clear scan of your renewed passport.',
		sentBy: 'Sarah Blackwood',
		sentAt: '2025-10-08T14:00:00Z',
		dueDate: '2025-10-22',
		status: 'completed',
		completedAt: '2025-10-15T09:55:00Z',
		urgency: 'high',
		uploadCategory: 'identity',
		uploadDescription: 'Passport (photo page must be clearly legible).'
	}
];

export function getTask(id: string): Task | undefined {
	return tasks.find((t) => t.id === id);
}

/** The open (non-completed) task linked to a library document, if any. Single source
    of truth for the "action required" state shared by the list and detail views. */
export function pendingTaskForDocument(uid: string): Task | undefined {
	return tasks.find((t) => t.documentUid === uid && t.status !== 'completed');
}

// The demo SharedDocument fixtures that used to live here fed only the legacy /documents page's
// "Shared with you" placeholder; they went when that page became composed-only. The secure-link
// surface they were a stand-in for (`/share/[token]`) has since been removed outright.
