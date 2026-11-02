/**
 * SSR coverage for the contributions and transfers workbenches.
 *
 * `svelte-check` types the templates; only rendering proves they run. What matters here is the
 * extraction contract plus the handful of rules that are safety-relevant rather than cosmetic:
 *
 *  - actions are OPTIONAL — no callback ⇒ the control is absent, which is how the admin portal
 *    mounts these read-only over a firm's book;
 *  - a SEI relief credit is never offered as a classifiable contribution (classifying it would
 *    double-count the member's annual allowance and reclaim relief on relief);
 *  - an unadvised transfer value renders as "not yet advised", not £0.00;
 *  - the transfers rail renders the REAL six-stage saga, not the prototype's invented nine.
 *
 * Interaction (tab switches, per-row selects, sorting) is client-side and out of SSR's reach.
 */
import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import ContributionsWorkbench from './contributions-workbench.svelte';
import CreditQueue from './credit-queue.svelte';
import ReliefMonitor from './relief-monitor.svelte';
import { emptyQuery } from '../listing/query.js';
import ReconciliationPanel from './reconciliation-panel.svelte';
import MlStatusBadge from './ml-status-badge.svelte';
import TransfersWorkbench from '../transfers/transfers-workbench.svelte';
import DetailPanel from '../transfers/detail-panel.svelte';
import SagaRail from '../transfers/saga-rail.svelte';
import { ML_STATUS, type Contribution, type UnclassifiedCredit } from './types.js';
import type { ApiTransfer } from '../transfers/api.js';

const CONTRIBUTION: Contribution = {
	id: 1,
	uid: 'con_1',
	account_id: 10,
	contact_id: 5,
	expectation_id: 7,
	transaction_id: null,
	category: 'personal_contribution',
	amount: 800,
	gross_amount: 1000,
	relief_amount: 200,
	prefunded_ras_amount: null,
	currency: 'GBP',
	tax_year: '2026/27',
	frequency: 'single',
	paid_by: 'client',
	contribution_date: '2026-06-01',
	status: 'awaiting_confirmation',
	ml_contribution_id: null,
	ml_recorded_at: null,
	ml_status: null,
	ml_status_at: null,
	cancelled_at: null,
	cancelled_reason: null,
	note: null,
	created_at: '2026-06-01T09:00:00Z',
	updated_at: '2026-06-01T09:00:00Z',
	account_name: "Sophie's SIPP",
	account_uid: 'acc_1',
	contact_uid: 'ct_9',
	product_type: 'SIPP',
	client_name: 'Sophie Halloway',
	adviser: 'Dan Reeve',
	client_employer_name: null,
	expectation_uid: 'exp_1',
	expectation_status: 'instructed',
	expectation_due_date: '2026-06-05',
	expectation_amount: 800,
	expectation_matched_at: null,
	expectation_reference: 'SWP-991',
	expectation_error: null,
	matched_by_hand: null,
	transaction_amount: null,
	transaction_date: null,
	transaction_currency: null,
	transaction_source: null,
	transaction_reference: null,
	transaction_type_label: null,
	transaction_description_code: null,
	transaction_method: null,
	reconciliation: { status: 'pending', legs: [], variance: null, reason: null },
	relief: { status: 'awaited', expected: 200, received: null, outstanding: 200 },
	relief_received: null,
	employer_name: null,
	address_line1: null,
	address_line2: null,
	address_line3: null,
	city: null,
	county: null,
	postcode: null,
	phone: null,
	email: null
};

const CREDIT: UnclassifiedCredit = {
	id: 501,
	source_system: 'sei',
	source_transaction_id: '368445711',
	type: 'deposit',
	source_type: 'Cash Receipt',
	source_subtype: '21121',
	amount: 800,
	currency: 'GBP',
	transaction_date: '2026-06-03',
	settlement_date: null,
	status: null,
	account_id: 10,
	account_uid: 'acc_1',
	account_name: "Sophie's SIPP",
	product_type: 'SIPP',
	contact_id: 5,
	contact_uid: 'ct_1',
	client_name: 'Sophie Halloway',
	adviser: 'Dan Reeve',
	client_employer_name: null,
	is_relief_payment: false,
	suggested_category: 'personal_contribution',
	provider_description: 'Funding Transaction Member Net Contribution',
	method: null
};

const RELIEF_CREDIT: UnclassifiedCredit = {
	...CREDIT,
	id: 502,
	source_transaction_id: '368445712',
	source_subtype: '21122',
	amount: 200,
	is_relief_payment: true,
	suggested_category: null,
	provider_description: 'Funding Member Basic Rate Tax Relief on Contribution'
};

const TRANSFER: ApiTransfer = {
	id: 1,
	uid: 'trf_1',
	account_id: 10,
	contact_id: 5,
	expectation_id: 7,
	transaction_id: null,
	ceding_provider: 'Aviva',
	policy_number: 'AV-9912',
	transfer_type: 'pension',
	basis: 'cash',
	extent: 'full',
	crystallised_value: null,
	uncrystallised_value: null,
	total_value: 25_000,
	crystallisation: null,
	pension_type: null,
	safeguarded_benefits: null,
	currency: 'GBP',
	status: 'in_progress',
	ml_status: 2,
	ml_status_label: 'Awaiting action from ceding scheme',
	ml_status_at: '2026-06-10',
	ceding_status: null,
	ml_transfer_id: null,
	ml_recorded_at: null,
	expected_date: null,
	completed_at: null,
	note: null,
	created_at: '2026-06-01T09:00:00Z',
	updated_at: '2026-06-01T09:00:00Z',
	account_name: "Sophie's SIPP",
	account_uid: 'acc_1',
	product_type: 'SIPP',
	client_name: 'Sophie Halloway',
	contact_uid: 'con_5',
	adviser: 'Dan Reeve',
	expectation_uid: 'exp_1',
	expectation_status: 'instructed',
	expectation_due_date: '2026-06-20',
	expectation_amount: 25_000,
	expectation_matched_at: null,
	matched_by_hand: null,
	transaction_amount: null,
	transaction_date: null,
	transaction_source: null,
	transaction_reference: null,
	reconciliation: { status: 'pending', legs: [], variance: null, reason: null }
};

describe('the contributions workbench', () => {
	it('renders the pipeline with rows', () => {
		const { body } = render(ContributionsWorkbench, {
			props: { contributions: [CONTRIBUTION], credits: [] }
		});
		expect(body).toContain('Sophie Halloway');
	});

	it('says what is waiting in both queues', () => {
		const { body } = render(ContributionsWorkbench, {
			props: { contributions: [CONTRIBUTION], credits: [CREDIT] }
		});
		expect(body).toContain('To classify');
	});

	it('counts the detected credits into the pager, since they are on the screen it describes', () => {
		// The API's total is the BOOKED rows only and the queue lists the detected credits above
		// them, so "1 result" sat under a list of two. The pager counts what the reader can see.
		const listing = emptyQuery([], 25);
		const { body } = render(ContributionsWorkbench, {
			props: {
				contributions: [CONTRIBUTION],
				credits: [CREDIT, RELIEF_CREDIT],
				listing,
				matchCount: 1,
				onquery: () => {}
			}
		});
		// One booked + one detected (the relief credit is never queued).
		expect(body).toContain('2 results');
		expect(body).not.toContain('1 result<');

		// And on a later page the detected rows are not repeated, but still shift the range.
		const later = render(ContributionsWorkbench, {
			props: {
				contributions: [CONTRIBUTION],
				credits: [CREDIT],
				listing: { ...listing, offset: 25 },
				matchCount: 26,
				onquery: () => {}
			}
		}).body;
		expect(later).toContain('27–27 of 27');
		expect(later.match(/Sophie Halloway/g)?.length).toBe(1);
	});

	it('offers no matcher button at all', () => {
		// `POST /contributions/match` joins an open expectation to the credit that fulfilled it —
		// a reconciliation sweep, safe to re-run, with no per-row decision in it. That is a job, not
		// an adviser's act, and the button only asked somebody to remember to press it. The endpoint
		// and the page's form action stay; the control does not.
		for (const actions of [undefined, { classify: () => {} }]) {
			const { body } = render(ContributionsWorkbench, {
				props: { contributions: [CONTRIBUTION], credits: [], actions }
			});
			expect(body).not.toContain('Match payments');
		}
	});

	it('states the money at each stage, and who has it', () => {
		const { body } = render(ContributionsWorkbench, {
			props: { contributions: [CONTRIBUTION], credits: [CREDIT] }
		});
		// The figure is the MONEY, with the row count demoted to the sub-line: £3 stuck at the
		// administrator and £30,000 stuck there are the same 1 in a count.
		expect(body).toContain('To classify');
		// "Pending relief", not "With scheme administrator": the administrator holding it is a
		// custody fact, where what a reader is waiting on is the basic-rate reclaim.
		expect(body).toContain('Pending relief');
		expect(body).toContain('Relief in transit');
		expect(body).toContain('Not lodged');
		// The caption names who moves it on — half these stages are waiting on somebody else.
		expect(body).toContain('Custodian feed');
		expect(body).toContain('HMRC → Morgan Lloyd');
	});

	it('states the WHOLE BOOK when the API sends its totals, not the page it was handed', () => {
		// The rows are one page. A strip derived from them says "£800 is awaiting classification"
		// when the truth is "£800 of the twenty-five rows you can see" — a statement about the
		// scroll position in the shape of a figure a compliance reviewer would quote.
		const { body } = render(ContributionsWorkbench, {
			props: {
				contributions: [CONTRIBUTION],
				credits: [],
				summary: {
					awaiting_classification: { amount: 41_200, count: 17 },
					with_administrator: { amount: 38_400, count: 9 },
					relief_claimed: { amount: 0, count: 0 },
					relief_in_transit: { amount: 0, count: 0 },
					relief_credited: { amount: 0, count: 0 },
					not_lodged: { amount: 0, count: 0 }
				}
			}
		});
		expect(body).toContain('£41,200');
		expect(body).toContain('17 transactions');
		expect(body).toContain('£38,400');
		expect(body).toContain('9 contributions');
	});

	it('adds the detected credits on top, since the summary cannot know them', () => {
		// They have no `platform.contributions` row yet and come from a second, unpaged endpoint —
		// so dropping them would understate the one figure an adviser opened the page to act on.
		const { body } = render(ContributionsWorkbench, {
			props: {
				contributions: [],
				credits: [CREDIT],
				summary: { awaiting_classification: { amount: 1_000, count: 2 } }
			}
		});
		expect(body).toContain(`£${(1_000 + CREDIT.amount).toLocaleString('en-GB')}`);
		expect(body).toContain('3 transactions');
	});

	it('falls back to the rows on screen when no summary is sent', () => {
		// An older API, or a mount that reads the bare list. Worse, and not broken.
		const { body } = render(ContributionsWorkbench, {
			props: { contributions: [CONTRIBUTION], credits: [] }
		});
		expect(body).toContain('£800');
		expect(body).toContain('1 transaction');
	});

	it('links the client name to their record, in the queue and in the panel', () => {
		// The commonest next question about any row is "who is this?", and answering it used to mean
		// leaving the workbench, searching the client, and losing both the queue position and the
		// selected row.
		const { body } = render(ContributionsWorkbench, {
			props: {
				contributions: [CONTRIBUTION],
				credits: [],
				focus: CONTRIBUTION.uid,
				links: { client: (uid: string) => `/clients/${uid}` }
			}
		});
		expect(body).toContain(`href="/clients/${CONTRIBUTION.contact_uid}"`);
		// A real href, not a click handler: middle-click and "open in new tab" are the whole point
		// of asking that question without abandoning the queue.
		expect(body).toMatch(/<a[^>]+href="\/clients\/[^"]+"[^>]*>[\s\S]{0,80}Sophie Halloway/);
		// The line under the name is the contribution, not the client's id — there is no
		// transaction on a row booked ahead of the money.
		expect(body).toContain('Contribution:');
		expect(body).toContain(CONTRIBUTION.uid);
		expect(body).not.toContain(`ID: ${CONTRIBUTION.contact_uid}`);
	});

	it('puts every fact the row has in the panel, the adviser among them', () => {
		// The adviser is what the queue's search and filter match on, and the panel is where an
		// adviser is looking; a fact you can filter by and not read is a fact the screen has lost.
		// The panel alone: the queue beside it has an Adviser column of its own now.
		const panel = (c: Contribution) => {
			const page = render(ContributionsWorkbench, {
				props: { contributions: [c], credits: [], focus: c.uid }
			}).body;
			return page.slice(page.indexOf('aria-label="Selected contribution"'));
		};
		const body = panel({ ...CONTRIBUTION, transaction_reference: 'TXN-42' });
		expect(body).not.toBe('');
		// With the face the queue gives them: Dan Reeve's initials, then the name.
		expect(body).toMatch(
			/Adviser<\/span>[\s\S]{0,500}?aria-hidden="true"[^>]*>DR<\/span>[\s\S]{0,120}?Dan Reeve/
		);
		expect(body).toMatch(/Transaction<\/span>\s*<span[^>]*>TXN-42/);
		expect(body).toMatch(/Booked<\/span>\s*<span[^>]*>1 Jun 2026/);
		expect(panel({ ...CONTRIBUTION, adviser: null })).toMatch(
			/Adviser<\/span>[\s\S]{0,40}?<span[^>]*>Unassigned/
		);
	});

	it('addresses the client by uid, never by the serial the row also carries', () => {
		// A URL is published — bookmarked, pasted into a ticket, read out loud — and a serial says
		// how many rows preceded it. This projection carried `account_uid` and no client counterpart
		// until Sept 2026, which is exactly how the transactions listing came to link `/clients/42`.
		const { body } = render(ContributionsWorkbench, {
			props: {
				contributions: [CONTRIBUTION],
				credits: [],
				links: { client: (uid: string) => `/clients/${uid}` }
			}
		});
		expect(body).not.toContain(`href="/clients/${CONTRIBUTION.contact_id}"`);
	});

	it('renders the name as plain text when no client route is injected', () => {
		// The admin portal's read-only mount has no client route in that context; a dead link is
		// worse than a name, because it looks like something that should work.
		const { body } = render(ContributionsWorkbench, {
			props: { contributions: [CONTRIBUTION], credits: [] }
		});
		expect(body).toContain('Sophie Halloway');
		expect(body).not.toContain('href="/clients/');
	});

	it('links a detected credit to its client too', () => {
		// A credit has no `platform.contributions` row yet, but it has a contact — and it is the row
		// an adviser is most likely to be asking "who is this?" about.
		const { body } = render(ContributionsWorkbench, {
			props: {
				contributions: [],
				credits: [CREDIT],
				links: { client: (uid: string) => `/clients/${uid}` }
			}
		});
		expect(body).toContain(`href="/clients/${CREDIT.contact_uid}"`);
		expect(body).toContain('Transaction:');
		expect(body).toContain(CREDIT.source_transaction_id);
	});

	it('keeps a long transaction reference inside the client column', () => {
		// The id is one nowrap token in a 16% column. Without a width cap it paints across Account.
		// The label stays; the value ellipsizes and is still the tooltip's text.
		const reference = 'demo-ten_d558803fc7a54b89-1-1-tx-1';
		const { body } = render(ContributionsWorkbench, {
			props: {
				contributions: [{ ...CONTRIBUTION, transaction_reference: reference }],
				credits: []
			}
		});
		const at = body.indexOf('Transaction:');
		expect(at).toBeGreaterThan(-1);
		const line = body.slice(Math.max(0, at - 800), at + reference.length + 400);
		expect(line).toContain(reference);
		expect(line).toContain('shrink-0');
		expect(line).toContain('min-w-0');
		expect(line).toContain('max-w-full');
		expect(line).toContain('truncate');
		expect(line).toContain('w-full');
		expect(line).toContain('data-slot="tooltip-trigger"');
	});

	it('links the account beside the client, so a row addresses both records it names', () => {
		// Same reasoning as the client link: a queue row names two records, and answering "which
		// account is this?" used to mean leaving the workbench. The name is the link; the uid is
		// what the route is addressed by, and it is not printed under the name.
		const { body } = render(ContributionsWorkbench, {
			props: {
				contributions: [CONTRIBUTION],
				credits: [],
				links: {
					client: (uid: string) => `/clients/${uid}`,
					account: (uid: string) => `/accounts/${uid}`
				}
			}
		});
		expect(body).toContain(`href="/accounts/${CONTRIBUTION.account_uid}"`);
		// Never the serial — a URL is published, and a serial says how many rows preceded it.
		expect(body).not.toContain(`href="/accounts/${CONTRIBUTION.account_id}"`);
	});

	it('leaves the account plain when the mount injects no account route', () => {
		// `account` is OPTIONAL where `client` is required, so a mount that can address a person and
		// not an account links the one it can rather than dead-linking both.
		const { body } = render(ContributionsWorkbench, {
			props: {
				contributions: [CONTRIBUTION],
				credits: [],
				links: { client: (uid: string) => `/clients/${uid}` }
			}
		});
		expect(body).toContain(`href="/clients/${CONTRIBUTION.contact_uid}"`);
		expect(body).toContain("Sophie's SIPP");
		expect(body).not.toContain(`ID: ${CONTRIBUTION.account_uid}`);
		expect(body).not.toContain('href="/accounts/');
	});

	it('renders no clickable pipeline strip beneath the metrics', () => {
		// It was a second row of the same counts AND a second way to narrow by stage — two answers
		// to one question, which had already drifted apart. The filter moved into the queue's own
		// toolbar, where the other filters live.
		const { body } = render(ContributionsWorkbench, {
			props: { contributions: [CONTRIBUTION], credits: [] }
		});
		expect(body).not.toContain('aria-pressed');
		// And the queue offers the stage filter in its place.
		expect(body).toContain('Filter by status');
	});

	it('tells an empty book where to start', () => {
		const { body } = render(ContributionsWorkbench, {
			props: { contributions: [], credits: [] }
		});
		expect(body).toContain('Record a contribution');
	});

	it('gives the queue the whole width until a row is picked', () => {
		// The panel used to hold a fixed 420px column with nothing in it but "Select a contribution",
		// and the queue beside it overlapped itself on a laptop. Nothing picked, no panel.
		const { body } = render(ContributionsWorkbench, {
			props: { contributions: [CONTRIBUTION], credits: [CREDIT] }
		});
		expect(body).not.toContain('aria-label="Selected contribution"');
		expect(body).not.toContain('Select a contribution');
		expect(body).not.toContain('Close panel');
	});

	it('opens the panel for the picked row, with a way back out', () => {
		const { body } = render(ContributionsWorkbench, {
			props: { contributions: [CONTRIBUTION], credits: [], focus: CONTRIBUTION.uid }
		});
		expect(body).toContain('aria-label="Selected contribution"');
		expect(body).toContain('aria-label="Close panel"');
	});

	it('opens the detected row a transaction link names, on first paint', () => {
		// The Action required strips link with the TRANSACTION's uid, and the panel opens before
		// hydration, so the first paint has to translate it the way the deep-link effect does.
		const { body } = render(ContributionsWorkbench, {
			props: {
				contributions: [],
				credits: [CREDIT],
				focus: `${CREDIT.source_system}:${CREDIT.source_transaction_id}`
			}
		});
		expect(body).toContain('aria-label="Selected contribution"');
		expect(body).toContain('Classify Payment');
	});

	it('folds the account under the client when the table is too narrow for both', () => {
		// The panel takes ~400px when it opens, so the same screen holds a 1100px table or a 700px
		// one. Below 64rem the Account column steps out and the account takes the reference's line
		// under the client, so a narrow table loses a column rather than overlapping itself.
		const { body } = render(ContributionsWorkbench, {
			props: {
				contributions: [CONTRIBUTION],
				credits: [],
				links: {
					client: (uid: string) => `/clients/${uid}`,
					account: (uid: string) => `/accounts/${uid}`
				}
			}
		});
		expect(body).toContain('hidden @5xl:table-cell');
		expect(body).toContain('@5xl:hidden');
		// The account is named twice: its own column, and the stand-in under the client.
		expect(body.split(`href="/accounts/${CONTRIBUTION.account_uid}"`)).toHaveLength(3);
	});

	it('caps a long name at its column instead of printing over the next one', () => {
		// `w-fit` alone on a truncating box resolves to the text's full width, which is how a long
		// client or account name ran into the column beside it.
		const { body } = render(ContributionsWorkbench, {
			props: {
				contributions: [CONTRIBUTION],
				credits: [],
				links: { client: (uid: string) => `/clients/${uid}` }
			}
		});
		expect(body).toMatch(/class="[^"]*w-fit max-w-full font-medium[^"]*"[^>]*>[\s\S]{0,40}Sophie/);
	});
});

describe('the credit queue', () => {
	it('shows the custodian description code label as provenance', () => {
		// An adviser accepting a pre-selected category should be able to see what it was based on.
		const { body } = render(CreditQueue, { props: { credits: [CREDIT] } });
		expect(body).toContain('Funding Transaction Member Net Contribution');
		expect(body).toContain('368445711');
	});

	it('marks a relief credit and does not offer to classify it', () => {
		const { body } = render(CreditQueue, {
			props: { credits: [RELIEF_CREDIT], onclassify: () => {} }
		});
		expect(body).toContain('Tax relief credit');
		// The Classify button belongs to classifiable rows only — this credit is the basic-rate
		// relief already reclaimed, and booking it again double-counts the annual allowance.
		expect(body).not.toContain('>Classify<');
	});

	it('offers Classify for a real credit only when a callback exists', () => {
		expect(render(CreditQueue, { props: { credits: [CREDIT] } }).body).not.toContain('>Classify<');
		expect(
			render(CreditQueue, { props: { credits: [CREDIT], onclassify: () => {} } }).body
		).toContain('Classify');
	});

	it('says so plainly when there is nothing to classify', () => {
		const { body } = render(CreditQueue, { props: { credits: [] } });
		expect(body).toContain('Nothing unclassified');
	});

	it('links the account when a route is injected, and not when one is not', () => {
		// Which account the money landed in is what decides whether a credit is this client's to
		// classify at all.
		const linked = render(CreditQueue, {
			props: {
				credits: [CREDIT],
				links: { client: (uid: string) => `/clients/${uid}`, account: (uid) => `/accounts/${uid}` }
			}
		});
		expect(linked.body).toContain(`href="/accounts/${CREDIT.account_uid}"`);
		const plain = render(CreditQueue, { props: { credits: [CREDIT] } });
		expect(plain.body).toContain("Sophie's SIPP");
		expect(plain.body).not.toContain('href="/accounts/');
	});
});

describe('the relief monitor', () => {
	it('reports relief due and outstanding', () => {
		const { body } = render(ReliefMonitor, { props: { rows: [CONTRIBUTION] } });
		expect(body).toContain('basic-rate relief due');
		expect(body).toContain('not yet credited');
	});

	it('excludes employer contributions, which earn no relief', () => {
		// Listing them as "relief awaited" would leave a row nobody can ever clear.
		const employer: Contribution = {
			...CONTRIBUTION,
			category: 'employer_contribution',
			relief: { status: 'not_applicable', expected: 0, received: 0, outstanding: 0 }
		};
		const { body } = render(ReliefMonitor, { props: { rows: [employer] } });
		expect(body).toContain('paid gross');
	});

	it('links the account a relief position is credited against', () => {
		// `received` is at account grain — SEI reclaims in a monthly batch against the scheme — so
		// the account is the record a short or awaited row is actually chased on.
		const { body } = render(ReliefMonitor, {
			props: {
				rows: [CONTRIBUTION],
				links: { client: (uid: string) => `/clients/${uid}`, account: (uid) => `/accounts/${uid}` }
			}
		});
		expect(body).toContain(`href="/accounts/${CONTRIBUTION.account_uid}"`);
		expect(render(ReliefMonitor, { props: { rows: [CONTRIBUTION] } }).body).not.toContain(
			'href="/accounts/'
		);
	});
});

describe('the reconciliation panel', () => {
	it('renders an absent leg as a dash, never as zero', () => {
		const { body } = render(ReconciliationPanel, {
			props: {
				reconciliation: {
					status: 'pending',
					legs: [
						{ key: 'graphene', label: 'Booked', value: 800 },
						{ key: 'custodian', label: 'Received', value: null }
					],
					variance: null,
					reason: 'No credit has been matched to this contribution yet.'
				}
			}
		});
		expect(body).toContain('Awaiting the money');
		expect(body).not.toContain('£0.00');
	});

	it('names the variance on a break', () => {
		const { body } = render(ReconciliationPanel, {
			props: {
				reconciliation: {
					status: 'break',
					legs: [{ key: 'custodian', label: 'Received', value: 750 }],
					variance: -50,
					reason: 'The credit that landed is 50.00 less than the contribution booked.'
				}
			}
		});
		expect(body).toContain('Does not reconcile');
		expect(body).toContain('Variance');
	});
});

describe('the Morgan Lloyd badge', () => {
	it('renders nothing until the contribution is pushed', () => {
		// Svelte emits hydration markers even for an empty branch, so assert on visible text
		// rather than an empty string.
		const { body } = render(MlStatusBadge, { props: { status: null } });
		expect(body.replace(/<!--.*?-->/g, '').trim()).toBe('');
	});

	it('names the vendor status', () => {
		const { body } = render(MlStatusBadge, { props: { status: ML_STATUS.application } });
		expect(body).toContain('Application');
	});
});

describe('the transfers workbench', () => {
	it('keeps the type and status badges inside their columns', () => {
		// FB-20260921-09 on Transfers: "In-specie" and the stage word printed over the next column
		// beside the always-open panel. Both are capped now, with the whole word on hover.
		const { body } = render(TransfersWorkbench, {
			props: { transfers: [{ ...TRANSFER, basis: 'in_specie' }] }
		});
		expect(body).toMatch(/max-w-full min-w-0[^"]*"[^>]*>[\s\S]{0,300}?In-specie/);
		expect(body).toMatch(/data-slot="status-badge"[^>]*max-w-full min-w-0/);
	});

	it('renders real rows', () => {
		const { body } = render(TransfersWorkbench, { props: { transfers: [TRANSFER] } });
		expect(body).toContain('Aviva');
		expect(body).toContain('Sophie Halloway');
		expect(body).toContain("Sophie's SIPP");
		expect(body).toContain('Aviva');
		expect(body).toContain('Policy:');
		expect(body).toContain('AV-9912');
		expect(body).toContain('Aviva');
	});

	it('links the client and the account a transfer names', () => {
		// The two records a transfer row is ABOUT. Both were plain text, so answering "who is this?"
		// or "which account?" meant leaving the workbench and searching for the name by hand.
		const { body } = render(TransfersWorkbench, {
			props: {
				transfers: [TRANSFER],
				links: {
					client: (uid: string) => `/clients/${uid}`,
					account: (uid: string) => `/accounts/${uid}`
				}
			}
		});
		expect(body).toContain(`href="/clients/${TRANSFER.contact_uid}"`);
		expect(body).toContain(`href="/accounts/${TRANSFER.account_uid}"`);
		// By uid, never by the serial the row also carries: a URL is published — bookmarked, pasted
		// into a ticket — and `/clients/5` says how many contacts preceded this one, not who it is.
		expect(body).not.toContain(`href="/clients/${TRANSFER.contact_id}"`);
		expect(body).not.toContain(`href="/accounts/${TRANSFER.account_id}"`);
	});

	it('leaves both names plain when no routes are injected', () => {
		// How the admin portal mounts this: read-only, with neither route in that context. A dead
		// link is worse than a name, because it looks like something that should work.
		const { body } = render(TransfersWorkbench, { props: { transfers: [TRANSFER] } });
		expect(body).toContain('Sophie Halloway');
		expect(body).toContain("Sophie's SIPP");
		expect(body).not.toContain('href="/clients/');
		expect(body).not.toContain('href="/accounts/');
	});

	it('leaves a detected row’s client name plain — there is no contact uid behind it', () => {
		// A feed row carries the name off the transaction projection but no contact join, so the
		// uid a client URL needs is simply absent. The account still links: that uid is there.
		const { body } = render(TransfersWorkbench, {
			props: {
				transfers: [{ ...TRANSFER, uid: 'detected:7', detected: true, contact_uid: null }],
				links: {
					client: (uid: string) => `/clients/${uid}`,
					account: (uid: string) => `/accounts/${uid}`
				}
			}
		});
		expect(body).toContain('Sophie Halloway');
		expect(body).not.toContain('href="/clients/');
		expect(body).toContain(`href="/accounts/${TRANSFER.account_uid}"`);
	});

	it('names a detected row reference without putting a feed badge beside the client', () => {
		const { body } = render(TransfersWorkbench, {
			props: {
				transfers: [
					{
						...TRANSFER,
						uid: 'detected:txn_7',
						detected: true,
						policy_number: null,
						transaction_reference: 'CASH-7781',
						status: 'completed'
					}
				]
			}
		});
		expect(body).toContain('Transaction:');
		expect(body).toContain('CASH-7781');
		expect(body).not.toContain('From feed');
	});

	it('keeps a long transaction reference inside the client column', () => {
		// Same column contract as the contributions queue: the reference truncates, the label does not.
		const reference = 'demo-ten_d558803fc7a54b89-1-1-tx-12';
		const { body } = render(TransfersWorkbench, {
			props: {
				transfers: [{ ...TRANSFER, policy_number: null, transaction_reference: reference }]
			}
		});
		const at = body.indexOf('Transaction:');
		expect(at).toBeGreaterThan(-1);
		const line = body.slice(Math.max(0, at - 800), at + reference.length + 400);
		expect(line).toContain(reference);
		expect(line).toContain('shrink-0');
		expect(line).toContain('min-w-0');
		expect(line).toContain('max-w-full');
		expect(line).toContain('truncate');
		expect(line).toContain('w-full');
		expect(line).toContain('data-slot="tooltip-trigger"');
	});

	it('says "not yet advised" rather than £0.00 for an unvalued transfer', () => {
		const { body } = render(TransfersWorkbench, {
			props: {
				transfers: [{ ...TRANSFER, total_value: null, expectation_amount: null }]
			}
		});
		expect(body).toContain('Not yet advised');
	});

	it('hides New transfer and the matcher when neither is injected', () => {
		const { body } = render(TransfersWorkbench, { props: { transfers: [TRANSFER] } });
		expect(body).not.toContain('New transfer');
		expect(body).not.toContain('Match payments');
		const client = body.indexOf('>Client<');
		const account = body.indexOf('>Account<');
		const provider = body.indexOf('>Ceding provider<');
		expect(client).toBeGreaterThan(-1);
		expect(client).toBeLessThan(account);
		expect(account).toBeLessThan(provider);
	});

	it('tells an empty book where a transfer starts', () => {
		const { body } = render(TransfersWorkbench, { props: { transfers: [] } });
		expect(body).toContain('the account it is transferring into');
	});

	it('gives the queue the whole width until a row is picked', () => {
		// The panel used to hold a fixed 420px column with nothing in it but "Select a transfer", and
		// the queue beside it printed its badges over the next column. Nothing picked, no panel.
		const { body } = render(TransfersWorkbench, { props: { transfers: [TRANSFER] } });
		expect(body).not.toContain('aria-label="Selected transfer"');
		expect(body).not.toContain('Select a transfer');
		expect(body).not.toContain('Close panel');
		expect(body).not.toContain('xl:grid-cols-[minmax(0,1fr)_420px]');
	});

	it('states the six stages in one row of tiles', () => {
		// An `auto-fit` grid dropped the sixth tile to a second row at 1440 with the sidebar open.
		const { body } = render(TransfersWorkbench, { props: { transfers: [TRANSFER] } });
		expect(body).toContain('grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6');
		expect(body).not.toContain('auto-fit');
		// Sentence case, not capitals: "AT ADMINISTRATOR" is wider than a tile at 1280.
		expect(body).toContain('At administrator');
		expect(body).not.toContain('tracking-wide uppercase');
	});

	it('folds its columns by the table’s own width, with a stand-in for each', () => {
		// Full width until a row is picked, ~400px narrower once the panel pushes in. Each folded
		// column leaves its value on another line, so a narrow table loses a column, not the data.
		const { body } = render(TransfersWorkbench, {
			props: {
				transfers: [{ ...TRANSFER, basis: 'in_specie' }],
				links: {
					client: (uid: string) => `/clients/${uid}`,
					account: (uid: string) => `/accounts/${uid}`
				}
			}
		});
		// Adviser and Age below 64rem, Account and Type below 56rem, Ceding provider below 42rem.
		expect(body).toContain('hidden @5xl:table-cell');
		expect(body).toContain('hidden @4xl:table-cell');
		expect(body).toContain('hidden @2xl:table-cell');
		// The age under the date, the account under the client, the type under the value, and the
		// ceding provider under the status.
		expect(body).toMatch(/block @5xl:hidden[^"]*"[^>]*>\s*(today|\d+(d|w|mo) ago)/);
		expect(body.split(`href="/accounts/${TRANSFER.account_uid}"`)).toHaveLength(3);
		expect(body).toMatch(/block @4xl:hidden[^"]*"[^>]*>\s*In-specie/);
		expect(body).toContain('from Aviva');
		expect(body).toContain('block @2xl:hidden');
		// The reference gives way to the account while it is folded under the client.
		expect(body).toContain('hidden w-full min-w-0 @4xl:block');
	});

	it('never folds Raised, and gives the short columns fixed widths', () => {
		// Raised is the first cell, so it carries the selected row's leading rule.
		const { body } = render(TransfersWorkbench, { props: { transfers: [TRANSFER] } });
		const raised = body.match(/<th[^>]*>(?:(?!<\/th>)[\s\S])*Raised/)?.[0] ?? '';
		expect(raised).not.toBe('');
		expect(raised).not.toMatch(/class="[^"]*\bhidden\b/);
		expect(raised).toContain('width: 6.5rem');
		expect(body).toContain('width: 9.25rem');
	});
});

describe('the transfer detail panel', () => {
	it('states the adviser and when it was raised, which the queue folds away beside it', () => {
		// The Adviser column steps out below 64rem, which is exactly when this panel is open, and
		// the row carries no stand-in for it — so the panel is the only place left to read it.
		const panel = (transfer: ApiTransfer) => render(DetailPanel, { props: { transfer } }).body;
		const body = panel(TRANSFER);
		expect(body).toMatch(
			/Adviser<\/dt>[\s\S]{0,500}?aria-hidden="true"[^>]*>DR<\/span>[\s\S]{0,120}?Dan Reeve/
		);
		expect(body).toMatch(/Raised<\/dt>\s*<dd[^>]*>\s*1 Jun 2026 · \S+/);
		expect(panel({ ...TRANSFER, adviser: null })).toMatch(
			/Adviser<\/dt>\s*<dd[^>]*>[\s\S]{0,40}?Unassigned/
		);
	});

	it('names the client, the account and the one next action', () => {
		const { body } = render(DetailPanel, {
			props: {
				transfer: {
					...TRANSFER,
					route: 'ml_sei',
					status: 'requested',
					custodian: 'sei',
					scheme_administrator: 'morgan-lloyd',
					sei_activity_id: 'ACT-9'
				},
				onstep: () => {}
			}
		});
		expect(body).toContain('Sophie Halloway');
		expect(body).toContain("Sophie's SIPP");
		expect(body).toContain('SIPP');
		expect(body).toContain('Cash');
		expect(body).toContain('SEI');
		expect(body).toContain('ACT-9');
		expect(body).toContain('Chain of custody');
		expect(body).toContain('uppercase');
		const chain = body.indexOf('Chain of custody');
		const reconciliation = body.indexOf('Reconciliation');
		const notes = body.indexOf('>Notes</h4>');
		expect(chain).toBeLessThan(reconciliation);
		expect(reconciliation).toBeLessThan(notes);
		expect(body).toContain('Awaiting action from ceding scheme');
		expect(body).toContain('Requested');
		expect(body).toContain('Transferred');
		expect(body).toContain('Scheme admin');
		expect(body).toContain('Submit to Morgan Lloyd');
	});

	it('places a raised Seccl transfer and refreshes one already in flight', () => {
		const raised = render(DetailPanel, {
			props: { transfer: { ...TRANSFER, route: 'seccl', status: 'requested' }, onstep: () => {} }
		});
		expect(raised.body).toContain('Place at Seccl');
		expect(raised.body).not.toContain('At administrator');

		const flying = render(DetailPanel, {
			props: {
				transfer: {
					...TRANSFER,
					route: 'seccl',
					status: 'in_progress',
					seccl_transfer_id: '00009BN1G',
					ceding_status: 'Onboarding Assets',
					provider_party: 'Seccl',
					provider_native: 'Onboarding Assets',
					provider_stage: 'in_progress'
				},
				onstep: () => {}
			}
		});
		expect(flying.body).toContain('Onboarding Assets');
		expect(flying.body).toContain('Refresh from Seccl');
		expect(flying.body).not.toContain('Request cancellation');
	});

	it('lodges SEI cash and does not invent a cancel', () => {
		const { body } = render(DetailPanel, {
			props: { transfer: { ...TRANSFER, route: 'sei', status: 'requested' }, onstep: () => {} }
		});
		expect(body).toContain('Lodge anticipated receipt');
		expect(body).not.toContain('Cancel');
	});

	it('offers cancellation on a submitted feed row Seccl still holds', () => {
		const { body } = render(DetailPanel, {
			props: {
				transfer: {
					...TRANSFER,
					uid: 'detected:7',
					detected: true,
					route: 'seccl',
					status: 'submitted',
					ceding_status: 'With Previous Provider',
					transaction_amount: 1000
				},
				onstep: () => {}
			}
		});
		expect(body).toContain('Request cancellation');
	});

	it('keeps a detected row read-only', () => {
		const { body } = render(DetailPanel, {
			props: {
				transfer: { ...TRANSFER, uid: 'detected:7', status: 'funds_sent', route: 'seccl' },
				onstep: () => {}
			}
		});
		expect(body).toContain('Completed');
		expect(body).toContain('No actions available');
		expect(body).not.toContain('Place at Seccl');
	});

	it('hides the footer when the mount is read-only', () => {
		const { body } = render(DetailPanel, {
			props: { transfer: { ...TRANSFER, route: 'seccl', status: 'requested' } }
		});
		expect(body).not.toContain('Place at Seccl');
		expect(body).not.toContain('No actions available');
	});

	it('carries a way back out only when the mount can close it', () => {
		// The workbench opens the panel for a picked row and passes `onclose`; the X is that way out.
		const closable = render(DetailPanel, {
			props: { transfer: TRANSFER, onclose: () => {} }
		});
		expect(closable.body).toContain('aria-label="Close panel"');
		expect(closable.body).toContain('Close (Esc)');

		const fixed = render(DetailPanel, { props: { transfer: TRANSFER } });
		expect(fixed.body).not.toContain('Close panel');
	});
});

describe('the saga rail', () => {
	it('renders the SEI SIPP stages and ends at the custodian', () => {
		const { body } = render(SagaRail, {
			props: { transfer: { ...TRANSFER, route: 'ml_sei' } }
		});
		for (const label of [
			'Raised',
			'Submitted',
			'At ceding scheme',
			'At administrator',
			'Completed'
		]) {
			expect(body).toContain(label);
		}
		expect(body).not.toContain('>Complete<');
	});

	it('highlights a cancellation request at the stage the transfer had reached', () => {
		const { body } = render(SagaRail, {
			props: {
				transfer: {
					...TRANSFER,
					route: 'seccl',
					status: 'submitted',
					ceding_status: 'Pending Cancellation'
				}
			}
		});
		const at = body.indexOf('Cancellation requested');
		expect(at).toBeGreaterThan(-1);
		expect(body.slice(Math.max(0, at - 180), at)).toContain('text-info-strong');
		expect(body).not.toContain('>Complete<');
	});

	it('lists the holdings on an in-specie transfer', () => {
		const { body } = render(DetailPanel, {
			props: {
				transfer: {
					...TRANSFER,
					basis: 'in_specie',
					route: 'seccl',
					instruments: [
						{
							name: 'Fidelity Index UK Fund P Acc',
							code: 'GB00BJS8SF95',
							quantity: 6332.25,
							status: 'completed'
						}
					]
				}
			}
		});
		expect(body).toContain('Instruments');
		expect(body).toContain('Fidelity Index UK Fund P Acc');
		expect(body).toContain('GB00BJS8SF95');
		expect(body).toContain('6,332.25');
		expect(body).toContain('Completed');
	});

	it('keeps the provider`s own words off the rail', () => {
		// Those sit in the detail header and the chain, so a stale custodian read stays visible
		// without being folded into a stage label.
		const { body } = render(SagaRail, {
			props: { transfer: { ...TRANSFER, ceding_status: 'With previous provider' } }
		});
		expect(body).not.toContain('Awaiting action from ceding scheme');
		expect(body).not.toContain('With previous provider');
	});

	it('shows the custodian status on the transfer, not on the rail', () => {
		const { body } = render(DetailPanel, {
			props: {
				transfer: {
					...TRANSFER,
					route: 'seccl',
					custodian: 'seccl',
					ceding_status: 'With previous provider'
				}
			}
		});
		expect(body).toContain('With previous provider');
		expect(body).toContain('Seccl');
		expect(body).not.toContain('At administrator');
	});

	it('renders a cancelled transfer as stopped, not failed', () => {
		const { body } = render(SagaRail, {
			props: { transfer: { ...TRANSFER, status: 'cancelled' } }
		});
		expect(body).toContain('Cancelled');
		expect(body).not.toContain('Needs attention');
	});
});
