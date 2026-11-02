<script lang="ts">
	// The account record: header (value/growth/cash + action entry points), then
	// overview (account info/bank/fees/regular movements) · holdings (lake) · transactions.
	// Extracted verbatim from the platform portal's /accounts/[id] page.
	//
	// Everything with a side effect is injected, and hidden when absent:
	//  - `links.newTransfer` — href builder for the transfer journey (a link, but an entry
	//    point to an action, so optional like the callbacks).
	//  - `ondrawdown` — the pension drawdown action. The drawer it opens stays app-side (it mints
	//    an SSO session), so the component only owns the TRIGGER button. Contribution booking
	//    lives on the Payment drawer (type + relief at source), not a separate button.
	//  - `onSaveBank` / `onSaveFees` / `onSaveMovements` — bank and fees stay pencil-editors;
	//    regular payments and withdrawals add from the heading Actions menu. The pencil on a
	//    row updates that instruction (amount, frequency, next date); cancelling it lives in
	//    the same dialog. Absent ⇒ read-only.
	//  - `actionRequired` — the categorisation queue above the transactions table; rendered
	//    with the outstanding items. The snippet owns what clicking an item does.
	// An app that injects none of these gets a read-only record — the admin default.
	import type { Snippet } from 'svelte';
	import { Badge } from '../../ui/badge/index.js';
	import { Button } from '../../ui/button/index.js';
	import type { DataTableSort } from '../../ui/data-table/index.js';
	import * as DropdownMenu from '../../ui/dropdown-menu/index.js';
	import { ConfirmDialog } from '../../ui/confirm-dialog/index.js';
	import { Card, CardAction, CardContent, CardHeader, CardTitle } from '../../ui/card/index.js';
	import EmptyState from '../../ui/empty-state/empty-state.svelte';
	import { Checkbox } from '../../ui/checkbox/index.js';
	import PendingReview from '../reviews/pending-review.svelte';
	import { Textarea } from '../../ui/textarea/index.js';
	import { Input } from '../../ui/input/index.js';
	import { Label } from '../../ui/label/index.js';
	import { Money } from '../../ui/money/index.js';
	import MoneyInput from '../../ui/money-input/money-input.svelte';
	import { SelectMenu } from '../../ui/select-menu/index.js';
	import * as Dialog from '../../ui/dialog/index.js';
	import { SectionHeading } from '../../ui/section-heading/index.js';
	import AllocationDonut from '../../data/allocation-donut.svelte';
	import RecordHero from '../../data/record-hero.svelte';
	import StatTile from '../../data/stat-tile.svelte';
	import { CopyButton } from '../../ui/copy-button/index.js';
	import BankMark from '../../../forms/field-inputs/bank-mark.svelte';
	import { toast } from 'svelte-sonner';
	import PieChartIcon from '@lucide/svelte/icons/pie-chart';
	import HoldingsTable from '../../investment/holdings-table.svelte';
	// The client record's own donut geometry, not a second copy: "the percentages add up to 100" is
	// asserted in one place, and the two records cannot shade an asset class differently.
	import { allocationSegments, type Allocation } from '../clients/allocation.js';
	import ListingToolbar from '../listing/listing-toolbar.svelte';
	import FilterSelect from '../listing/filter-select.svelte';
	import { ALL } from '../listing/filters.js';
	import {
		holdingClass,
		holdingMatches,
		type HoldingsFilter
	} from '../../investment/holdings-view.js';
	// The SAME classifier the summary endpoint runs, for the fallback below — a second set of
	// regexes here is how "Cash" comes to mean two things on one record.
	import { classifyHolding } from '../clients/lake.js';
	import { allocationBaseOf } from '../../investment/allocation.js';
	import TransactionsListing from './transactions-listing.svelte';
	import Beneficiaries from './beneficiaries.svelte';
	import { conversionCaption, isPensionProduct } from './transactions.js';
	import type { Beneficiary } from './types.js';
	import ExportMenu from '../listing/export-menu.svelte';
	import UiPagination from '../../ui/pagination/pagination.svelte';
	import type { Holding } from '../../investment/types.js';
	import { formatMoney, formatSignedMoney } from '../../../money.js';
	import {
		consolidatedCash,
		pensionSplit,
		providerLabel,
		wrapperAllowance
	} from './money-facts.js';
	import { cn } from '../../../utils.js';
	import { LINK_RECORD, WRAP_NAME, WRAP_TOKEN } from '../../../text-shell.js';
	import { StatusBadge } from '../../ui/status-badge/index.js';
	import { statusTone } from '../listing/status-tone.js';
	import ArrowDownToLineIcon from '@lucide/svelte/icons/arrow-down-to-line';
	import ArrowLeftRightIcon from '@lucide/svelte/icons/arrow-left-right';
	import ArrowUpFromLineIcon from '@lucide/svelte/icons/arrow-up-from-line';
	import EllipsisIcon from '@lucide/svelte/icons/ellipsis';
	import CalendarIcon from '@lucide/svelte/icons/calendar';
	import HashIcon from '@lucide/svelte/icons/hash';
	import LandmarkIcon from '@lucide/svelte/icons/landmark';
	import LayersIcon from '@lucide/svelte/icons/layers';
	import ReceiptIcon from '@lucide/svelte/icons/receipt';
	import RepeatIcon from '@lucide/svelte/icons/repeat';
	import SendIcon from '@lucide/svelte/icons/send';
	import UserIcon from '@lucide/svelte/icons/user';
	import UserCogIcon from '@lucide/svelte/icons/user-cog';
	import PencilIcon from '@lucide/svelte/icons/pencil';
	import XIcon from '@lucide/svelte/icons/x';
	import { humanise } from '../listing/filters.js';
	import type { ListingQuery } from '../listing/query.js';
	import {
		isFlaggedVulnerable,
		VULNERABLE_CARD_CLASS,
		VulnerabilityBanner,
		bankDirectoryError,
		bankDisplayRows,
		bankSchemeOf,
		validateBank,
		type BankInput
	} from '../clients/index.js';
	import type { BankCheckResult, BankCheckStatus } from '../../ui/bank-account-check/index.js';
	import RecentInstructions from '../clients/recent-instructions.svelte';
	import type { InstructionRow } from '../clients/types.js';
	import type { CashInstructionKind, RegularScheduleKind } from './instruction.js';
	// One formatter for a charge across the estate: the same function the client's own application
	// dossier renders, so the record and the dossier cannot describe one fee differently.
	import { summariseAppliedFee } from '../journeys/fee-summary.js';
	import { clientName, providerReference } from './format.js';
	import { formatDate } from '../../../dates.js';
	import { serviceTypeLabel } from './service-type.js';
	import { needsAction, type Transaction } from './transactions.js';
	import type {
		AccountBookLinks,
		AccountContributionSummary,
		AccountDetailData,
		AccountView,
		FeePackageOption,
		RegularMovement
	} from './types.js';
	import {
		asRegularMovements,
		movementFrequencyLabel,
		movementScheduleCaption,
		scheduleFrequencyChoices
	} from './regular.js';
	import { accountBankSaveBody, resolvedBank, usesClientBank } from './bank.js';
	import BankAccountFields from '../clients/bank-fields.svelte';
	import type { ReviewRow } from '../reviews/types.js';

	let {
		account,
		holdings,
		allocations = [],
		transactions,
		links,
		canDrawdown = false,
		canContribute = false,
		section = 'all',
		contained = undefined,
		instructions = [],
		contributionSummary = null,
		transactionFilter = null,
		transactionListing = null,
		transactionTotal = null,
		transactionPageHref,
		transactionFacets,
		transactionBounds,
		transactionExportHref = null,
		holdingsExportHref = null,
		ontransactionquery,
		oninstruction,
		disabledActions = [],
		onchangemodel,
		onschedule,
		ontransfer,
		ondrawdown,
		onpushtoadministrator,
		onSaveBank,
		onCancelReview,
		justificationRequired = [],
		pendingReviews = [],
		onSaveFees,
		onSaveMovements,
		feePackages = [],
		canPushToAdministrator = false,
		actionRequired,
		beneficiaries = [],
		beneficiaryActions
	}: {
		account: AccountDetailData;
		holdings: Holding[];
		/** How this account's money is ARRANGED, by asset class — `GET /crm/holdings/summary` keyed
		 *  by `account_id`, the same read the client record's overview makes for the whole
		 *  portfolio. Absent or empty ⇒ no card: an account nobody has valued yet has nothing to
		 *  draw, and a ring of one grey segment states less than nothing. Classified on the SERVER
		 *  (`classifyHolding`), because the mapped `Holding` has dropped the provider's
		 *  `asset_type` by the time it reaches this component. */
		allocations?: Allocation[];
		/** Already overlaid with any in-session categorisations — the app owns that state. */
		transactions: Transaction[];
		links: AccountBookLinks;
		/** Whether this pension's scheme is Morgan Lloyd-administered, so Drawdown can mint a
		 *  DCube session. The app still supplies `ondrawdown` only when the caller may move
		 *  money; this flag is the administrator gate. A pension that is not administered here
		 *  must not offer a session that then 409s. */
		canDrawdown?: boolean;
		/** Whether this is a pension the caller may move money on. Gates the note under regular
		 *  payments (contributions are categorised from Transactions), not a Contribution button —
		 *  that booking now lives on the Payment drawer. Defaults off so a read-only mount does
		 *  not inherit Drawdown's narrower administrator gate. */
		canContribute?: boolean;
		/** This account's beneficiary nominations (`crm.beneficiaries`, 0127). Pension accounts
		 *  only — an ISA or a GIA passes under the will and through probate, so a nomination there
		 *  would be a wish with no effect. */
		beneficiaries?: Beneficiary[];
		/** The app's own form-action names plus its `enhance`, per the portal waterfall. Absent ⇒
		 *  the panel is a read-only list, which is how the admin portal mounts it. */
		beneficiaryActions?: {
			enhance: (node: HTMLFormElement) => { destroy(): void };
			add: string;
			remove: string;
		};
		/** Which band to render. The three used to be tabs here; the platform routes them now and
		 *  the sidebar is the strip, so it mounts this once per section with the header riding
		 *  along. Defaults to `all` — a mount with no routes for the bands (the admin portal's
		 *  read-only one) gets the whole record stacked rather than a third of it. */
		section?: AccountView;
		/**
		 * Fill the height the parent gives us and scroll the band's rows inside it, rather than
		 * growing the page. Defaults to "yes for a single band, no when all three are stacked";
		 * pass it explicitly to override (an embedded preview wants `false` whatever the section).
		 */
		contained?: boolean;
		instructions?: InstructionRow[];
		contributionSummary?: AccountContributionSummary | null;
		transactionFilter?: string | null;
		transactionListing?: ListingQuery | null;
		transactionTotal?: number | null;
		transactionPageHref?: (page: number) => string;
		/** Per-filter options computed server-side over the match set. */
		transactionFacets?: Record<string, string[]>;
		transactionBounds?: Record<string, number[]>;
		/** Export endpoint for this account's transactions. Absent ⇒ no export menu on the band. */
		transactionExportHref?: string | null;
		/** Export endpoint for this account's holdings. Absent ⇒ no export menu on that band. */
		holdingsExportHref?: string | null;
		/** Apply a search/filter change — the app turns it into a navigation. Absent ⇒ the toolbar's
		 *  controls do nothing, so `TransactionsListing` renders them inert; supply it to enable them. */
		ontransactionquery?: (next: ListingQuery) => void;
		oninstruction?: (kind: CashInstructionKind) => void;
		/** Action keys the firm has switched off (`transaction.payment`, `transaction.withdrawal`,
		 *  `transaction.fee`). A missing policy is not in the list, so the control stays. */
		disabledActions?: string[];
		/** Opens the mandate picker. Absent ⇒ no Change model in the action bar. */
		onchangemodel?: () => void;
		/** Opens the money drawer as a regular payment or withdrawal (frequency on top of the
		 *  one-off shape). Absent ⇒ no Add buttons on the schedule card. */
		onschedule?: (kind: RegularScheduleKind) => void;
		/** Saves this account's own bank details (`platform.accounts.bank_accounts`).
		 *  Clearing falls back to the client's. Returns an error string, or nothing on success. */
		onSaveBank?: (
			payload: Record<string, unknown>
		) => Promise<string | null | void> | string | null | void;
		onCancelReview?: (uid: string) => Promise<string | null | void> | string | null | void;
		/** Catalogue keys whose save asks for a reason. Approval can be on without one. */
		justificationRequired?: string[];
		pendingReviews?: ReviewRow[];
		/** Catalogue options for the fees editor. Absent / empty ⇒ the picker says so. */
		feePackages?: FeePackageOption[];
		/** The reason travels when the firm's reason switch is on for Change fee packages. */
		onSaveFees?: (
			feePackageIds: number[],
			justification?: string
		) => Promise<string | null | void> | string | null | void;
		onSaveMovements?: (payload: {
			payments: RegularMovement[];
			withdrawals: RegularMovement[];
			justification?: string;
		}) => Promise<string | null | void> | string | null | void;
		/** Opens the transfer-in drawer. Absent ⇒ falls back to `links.newTransfer`, and with
		 *  neither the affordance is hidden (the admin read-only posture). */
		ontransfer?: () => void;
		/** Opens the app's drawdown drawer (DCube SSO). Absent ⇒ no Drawdown button. */
		ondrawdown?: () => void;
		/** Pushes this account to the scheme administrator — the client, then the scheme and its
		 *  policy. Absent ⇒ no button, which is how the admin book mounts read-only. */
		onpushtoadministrator?: () => void;
		/** Gates the push separately from `canDrawdown`: creating a person and a scheme is a
		 *  record-keeping act (`contacts:write`) where minting a live drawdown session is money
		 *  movement (`instructions:write`), and one role legitimately holds only the first. */
		canPushToAdministrator?: boolean;
		/** The work queue above the transactions table, given the outstanding items. */
		actionRequired?: Snippet<[Transaction[]]>;
	} = $props();

	const a = $derived(account);
	/** The account's base currency. Figures on this record are in that currency, not always sterling. */
	const baseCurrency = $derived(a.currency || 'GBP');
	const money = (amount: number) => formatMoney(amount, baseCurrency);
	const signedMoney = (amount: number) => formatSignedMoney(amount, baseCurrency);
	const title = $derived(a.name ?? providerReference(a.uid) ?? 'Account');
	const reference = $derived(providerReference(a.uid) ?? null);
	const owner = $derived(a.client.id == null ? null : clientName(a.client));

	// ── Cash, in every place it sits ────────────────────────────────────────────────────
	//
	// `a.cash` is the custodian's, derived from the lake's positions. Scheme-administrator
	// cash (Morgan Lloyd's bank balance, for a SIPP) is added: SEI does not know about money
	// that has been passed to the scheme. A value is not summed across those sources — the
	// administrator's fund figure is the valuation we pushed it. The breakdown uses the
	// account's base currency, the same denomination as the tile.
	const cash = $derived(consolidatedCash(a.cash, a.provider_positions, baseCurrency));
	const split = $derived(pensionSplit(a.provider_positions));
	const allowance = $derived(wrapperAllowance(a));
	// The large figure is what has been subscribed. The limit stays in the line under it,
	// as what is left. A remaining figure on its own (no subscription stated) is that amount.
	const allowanceTile = $derived.by(() => {
		if (!allowance) return null;
		if (allowance.used != null && allowance.limit != null) {
			const left = `${money(allowance.available)} left`;
			return {
				label: 'Allowance used',
				value: money(allowance.used),
				sub: allowance.taxYear ? `${left} · ${allowance.taxYear}` : left
			};
		}
		return {
			label: 'Available allowance',
			value: money(allowance.available),
			sub: allowance.sub
		};
	});

	/** WHO HOLDS THE MONEY, from whichever of the two columns carries it. `custodian` is the
	 *  provider key the feed is keyed by; `platform_name` is what the firm calls that platform, and
	 *  plenty of accounts have only the second. The header is the one place this is stated now, so
	 *  reading only `custodian` left the custodian off the record entirely for those accounts. */
	const custodianName = $derived(
		account.custodian ? providerLabel(account.custodian) : (account.platform_name ?? null)
	);

	// ── The four figures, on tiles above the detail ─────────────────────────────────────
	//
	// The same row the CLIENT record states: what is held, what is liquid, what it cost and what
	// that has become. They used to be three bare `Stat`s in the top-right of the header card, which
	// is the layout the client record looked at and rejected — a headline figure reads as a figure
	// when it is on a tile of its own beside its peers, not as a caption trailing the account's name.
	//
	// BOOK COST IS ALL-OR-NOTHING. `bookCost` rides each holding and the custodian does not always
	// give one, so a sum over the rows that HAVE one is a cost for part of the account set against a
	// value for all of it — which then reads as a gain nobody made. Every holding or none, and an em
	// dash otherwise, the same posture the client record takes when its loader supplies no basis.
	const HERO_COPY_CLASS =
		'text-muted-foreground h-auto px-0 py-0 text-xs hover:bg-transparent hover:text-foreground';
	const bookCost = $derived.by(() => {
		if (holdings.length === 0) return null;
		if (holdings.some((h) => h.bookCost == null)) return null;
		return holdings.reduce((sum, h) => sum + (h.bookCost ?? 0), 0);
	});
	/** The ACCOUNT's own growth, not a sum of the holdings' `unrealisedGain`. The two are struck
	 *  from different places and the account-level figure is the one every other surface prints, so
	 *  summing here would put a second answer to one question on the same screen. */
	const unrealisedGain = $derived(a.growth);

	const outstanding = $derived(needsAction(transactions));

	/**
	 * Holdings page LOCALLY, and that is honest here where it would not be on the ledger: the layout
	 * loads an account's positions whole (there are tens, not thousands), so this slices a complete
	 * set rather than one page of a keyset feed. A client-side page over a server-paged listing is
	 * the lie `book/listing/query.ts` exists to avoid; over a complete one it is just a slice.
	 */
	const HOLDINGS_PER_PAGE = 25;
	/** The split the SERVER classified, or one read off the positions this page already holds.
	 *
	 *  The fallback is not a nicety: `GET /crm/holdings/summary` aggregates the lake, and an
	 *  account whose rows the lake has not summarised answers with nothing — which drew a Holdings
	 *  card with a list and no ring, the one state that looks broken rather than empty.
	 *
	 *  IT IS A WEAKER ANSWER, and knowingly so. The server classifies on `asset_type` AND
	 *  `asset_name`; the mapped `Holding` has dropped the type by the time it reaches this
	 *  component, so the fallback sees half the input and a cash line named "GBP" lands in
	 *  Equities. That is why it runs `classifyHolding` rather than a second set of regexes — the
	 *  rule is the same rule, reading less — and why the server's answer always wins when there is
	 *  one. */
	const allocSegments = $derived(
		allocationSegments(
			allocations.length
				? allocations
				: Object.entries(
						holdings.reduce<Record<string, number>>((acc, h) => {
							const klass = classifyHolding({ asset_name: h.name });
							acc[klass] = (acc[klass] ?? 0) + (h.value ?? 0);
							return acc;
						}, {})
					)
						.map(([label, value]) => ({ label, value }))
						.sort((x, y) => y.value - x.value)
		)
	);
	/** EVERY position, biggest first — the card scrolls rather than truncating. Five with "13
	 *  more under Holdings" under them made the reader leave the page to see a list that fits in a
	 *  column they were already looking at. */
	const sortedHoldings = $derived([...holdings].sort((x, y) => (y.value ?? 0) - (x.value ?? 0)));
	/** The denominator for a position's share: the POSITIONS, not the account's value. The two are
	 *  struck at different times by different systems — a custodian's account value against the
	 *  lake's holdings — and where they disagree, dividing by the account printed shares like
	 *  "743% of account". A share of the holdings is always a share of something on the screen. */
	const holdingsTotal = $derived(holdings.reduce((sum, h) => sum + (h.value ?? 0), 0));

	/** The LEDGER, newest first, beside the positions. "What is in this account" and "what has
	 *  happened to it lately" are the two questions asked of an account, and the second one used to
	 *  cost a band change. Scrolls like the positions do, and states the movement's own words —
	 *  the band next door is where a full ledger is filtered and paged. */
	const recentTransactions = $derived(
		[...transactions].sort((x, y) => (y.date ?? '').localeCompare(x.date ?? ''))
	);

	/** THE BAND'S OWN QUERY. Client-side, and honest about it: the account's positions are loaded
	 *  whole (there is no paged holdings endpoint), so there is no "not on this page" for a filter
	 *  to lie about — the trap `book/listing/query.ts` exists to avoid. Asset class comes from the
	 *  same `classifyHolding` the allocation ring is drawn from, so a reader who clicks "Equities"
	 *  gets the rows that made the Equities segment. */
	let holdingsQuery = $state('');
	let holdingsClass = $state(ALL);
	let holdingsCurrency = $state(ALL);
	/** `HoldingsTable`'s own sort, bound rather than read-only: the export link below needs today's
	 *  order, and the table's sort is local state with nothing in the URL to read it from. */
	let holdingsSort = $state<DataTableSort | null>({ key: 'value', dir: 'desc' });
	const classOf = holdingClass;
	const holdingsClassOptions = $derived(
		[...new Set(holdings.map(classOf))].sort().map((value) => ({ value, label: value }))
	);
	const holdingsCurrencyOptions = $derived(
		[...new Set(holdings.map((h) => h.currency).filter((c): c is string => !!c))]
			.sort()
			.map((value) => ({ value, label: value }))
	);
	const holdingsFilter = $derived<HoldingsFilter>({
		q: holdingsQuery.trim(),
		assetClass: holdingsClass === ALL ? '' : holdingsClass,
		currency: holdingsCurrency === ALL ? '' : holdingsCurrency
	});
	const filteredHoldings = $derived(holdings.filter((h) => holdingMatches(h, holdingsFilter)));
	const holdingsDirty = $derived(
		holdingsQuery.trim() !== '' || holdingsClass !== ALL || holdingsCurrency !== ALL
	);
	function clearHoldings() {
		holdingsQuery = '';
		holdingsClass = ALL;
		holdingsCurrency = ALL;
	}

	let holdingsPage = $state(1);
	const holdingsPageCount = $derived(
		Math.max(1, Math.ceil(filteredHoldings.length / HOLDINGS_PER_PAGE))
	);
	// A shorter list after a reload OR a filter must not strand the reader on a page that no longer
	// exists — narrowing eight pages to one used to leave the table blank on page four.
	$effect(() => {
		if (holdingsPage > holdingsPageCount) holdingsPage = 1;
	});
	const pagedHoldings = $derived(
		filteredHoldings.slice((holdingsPage - 1) * HOLDINGS_PER_PAGE, holdingsPage * HOLDINGS_PER_PAGE)
	);
	/**
	 * What each position's Allocation column is a share OF: every holding on the account, not the
	 * page being shown — page two of a book would otherwise report weights that add to 100% twice.
	 *
	 * The holdings' OWN sum rather than `account.value`, deliberately. The two come from different
	 * places (the lake versus the OLTP row) and are struck on different dates, so dividing one by
	 * the other can put a single position over 100%; summed from the same rows the column renders,
	 * the weights are consistent and add up.
	 */
	const allocationBase = $derived(allocationBaseOf(holdings));
	/**
	 * The export link, carrying today's search, filters and SORT. They are local `$state` with
	 * nothing in the URL, so the only way the export route can learn them is appended here — without
	 * it the file held every position, value-descending, whatever the table showed. The page's own
	 * query is dropped: on this page it is the LEDGER's, and its search is not this band's.
	 */
	const holdingsExportLink = $derived.by(() => {
		if (!holdingsExportHref) return holdingsExportHref;
		const params = new URLSearchParams();
		if (holdingsFilter.q) params.set('q', holdingsFilter.q);
		if (holdingsFilter.assetClass) params.set('class', holdingsFilter.assetClass);
		if (holdingsFilter.currency) params.set('currency', holdingsFilter.currency);
		if (holdingsSort) {
			params.set('sort', holdingsSort.key);
			params.set('dir', holdingsSort.dir);
		}
		const path = holdingsExportHref.split('?')[0];
		return params.size ? `${path}?${params}` : path;
	});
	/** Stacked mode needs its own headings; a single band is named by the nav that got you there. */
	/** One definition of "is this a pension", shared with the contributions workbench and the client
	 *  record — the same three wrappers decide drawdown, the MPAA and a beneficiary nomination. */
	// `is_pension` from the shelf, with the wrapper set as the fallback: this component is mounted
	// by the admin book over an older API too, and an absent field must not turn a real SIPP's
	// Beneficiaries panel off. See client-record.svelte for why the column beats the set.
	const isPension = $derived(account.is_pension ?? isPensionProduct(account.product_type ?? null));

	const stacked = $derived(section === 'all');

	/**
	 * ONE BAND FILLS THE SCREEN, and its rows scroll inside it — the posture the clients and
	 * transactions listings already take. Derived rather than asked for: a single band is the only
	 * case where the page has one thing in it to fill with, and a stacked mount (the admin book's
	 * read-only record) legitimately scrolls the page through all three.
	 *
	 * Safe in an unbounded parent, which is what makes it derivable at all: `flex-1` outside a flex
	 * container does nothing and a `min-h-0` box with auto height clips nothing, so a mount that
	 * does not give this component a height simply gets today's behaviour.
	 */
	const fills = $derived(contained ?? (section !== 'all' && section !== 'overview'));

	/** Account-scoped version of the firm ledger: the same order and cell rules, with Client and
	 * Account omitted because the record already names both immediately above the table. */

	// The jsonb columns are free-form; render only what's actually populated.
	const fees = $derived(a.fees ?? []);
	const payments = $derived(asRegularMovements(a.payments));
	const withdrawals = $derived(asRegularMovements(a.withdrawals));
	// Account first, client second — the same order `app/bank_accounts.py` resolves a payment.
	// The fallback sentence is only for the client branch; an account with its own details must
	// not keep saying it uses the client's after a save.
	const inheritingBank = $derived(usesClientBank(a));
	const bank = $derived(resolvedBank(a));
	const bankViewRows = $derived(
		bank ? bankDisplayRows(bank).filter(([, value]) => value != null) : []
	);
	const holdingsAsOf = $derived(
		holdings
			.map((holding) => holding.asOf)
			.filter((value): value is string => !!value)
			.sort()
			.at(-1) ?? null
	);

	// The OWNER's vulnerability, stated on the account too (FCA FG21/1, 0054). This page is a
	// place an adviser moves someone's money without necessarily having opened their record, so
	// the flag has to travel with the account. Read-only here — the same strip, without its
	// Update button: recording it belongs on the person, not on one of their accounts.
	const vulnerable = $derived(isFlaggedVulnerable(a.client));
	const vulnRecorded = $derived(
		a.client.vulnerability_recorded_at ? formatDate(a.client.vulnerability_recorded_at) : null
	);

	type OverviewEditor = 'bank' | 'fees';
	let editing = $state<OverviewEditor | null>(null);
	let editorSaving = $state(false);
	let editorError = $state<string | null>(null);
	let bankSchemeDraft = $state('bacs');
	let bankNameDraft = $state('');
	let bankSortDraft = $state('');
	let bankNumberDraft = $state('');
	let bankIbanDraft = $state('');
	let bankBicDraft = $state('');
	let bankMethodDraft = $state('bacs');
	let bankErrors = $state<Partial<Record<keyof BankInput, string>>>({});
	let bankDirectoryStatus = $state<BankCheckStatus>('idle');
	let bankDirectoryResult = $state<BankCheckResult | null>(null);
	let bankJustification = $state('');
	/** Why the fee packages change — asked when the firm's reason switch is on for that action. */
	let feeJustification = $state('');
	let bankJustificationError = $state<string | null>(null);
	let feePackageIds = $state<number[]>([]);
	let pendingRemove = $state<{
		side: 'payments' | 'withdrawals';
		index: number;
	} | null>(null);
	let removeOpen = $state(false);
	let editingMovement = $state<{
		side: 'payments' | 'withdrawals';
		index: number;
	} | null>(null);
	let moveAmount = $state<number | null>(null);
	let moveFrequency = $state('');
	let moveDate = $state('');
	let moveSaving = $state(false);
	let moveError = $state<string | null>(null);

	/** Catalogue plus any packages already on the account that the picker does not list — a
	 *  stale assignment must stay visible so it can be unticked rather than silently kept. */
	const feeEditorOptions = $derived.by(() => {
		const seen = new Set(feePackages.map((item) => item.id));
		// An assignment the catalogue no longer lists keeps whatever pricing the account carries,
		// so the option that can only be unticked still says what it costs.
		return [...feePackages, ...a.fee_packages.filter((item) => !seen.has(item.id))];
	});

	const pendingBank = $derived(
		pendingReviews.find((r) => r.action_key === 'account.bank_details_changed') ?? null
	);
	const pendingModel = $derived(
		pendingReviews.find((r) => r.action_key === 'account.model_changed') ?? null
	);
	const bankNeedsReview = $derived(justificationRequired.includes('account.bank_details_changed'));
	const feesNeedReason = $derived(justificationRequired.includes('account.fee_packages_changed'));
	const cancelPending = (uid: string) => {
		void onCancelReview?.(uid);
	};

	/** THE ACTION BAR IS GONE — same reason the client record lost its row of outline buttons. A
	 *  heading of Payment, Withdraw, Ad hoc fee, Transfer and Change model made the first thing on
	 *  an account a list of things you could do to it. They live in the header's overflow menu now,
	 *  the same three-dot control the client hero uses for its rare acts. */
	const showPayment = $derived(!!oninstruction && !disabledActions.includes('transaction.payment'));
	const showWithdraw = $derived(
		!!oninstruction && !isPension && !disabledActions.includes('transaction.withdrawal')
	);
	const showFee = $derived(!!oninstruction && !disabledActions.includes('transaction.fee'));
	const overflowActions = $derived(
		!!(
			showPayment ||
			showWithdraw ||
			showFee ||
			onschedule ||
			(onchangemodel && !pendingModel) ||
			ontransfer ||
			links.newTransfer ||
			(canDrawdown && ondrawdown) ||
			(canPushToAdministrator && onpushtoadministrator)
		)
	);

	function beginEdit(kind: OverviewEditor) {
		editorError = null;
		bankErrors = {};
		bankJustification = '';
		bankJustificationError = null;
		bankDirectoryStatus = 'idle';
		bankDirectoryResult = null;
		if (kind === 'bank') {
			bankSchemeDraft = bankSchemeOf(bank);
			bankNameDraft = bank?.name ?? '';
			bankSortDraft = bank?.sort_code ?? '';
			bankNumberDraft = bank?.account_number ?? '';
			bankIbanDraft = bank?.iban ?? '';
			bankBicDraft = bank?.bic ?? '';
			bankMethodDraft = bank?.method === 'faster_payment' ? 'faster_payment' : 'bacs';
		} else if (kind === 'fees') {
			feePackageIds = a.fee_packages.map((item) => item.id);
		}
		editing = kind;
	}

	function toggleEdit(kind: OverviewEditor) {
		if (editing === kind) {
			editing = null;
			editorError = null;
			return;
		}
		beginEdit(kind);
	}

	async function runSave(work: () => Promise<string | null | void> | string | null | void) {
		editorSaving = true;
		editorError = null;
		try {
			const err = await work();
			if (typeof err === 'string' && err) {
				editorError = err;
				return;
			}
			editing = null;
		} catch {
			editorError = 'Could not save the change.';
		} finally {
			editorSaving = false;
		}
	}

	function saveBank() {
		const checked = validateBank({
			scheme: bankSchemeDraft === 'iban' ? 'iban' : 'bacs',
			name: bankNameDraft,
			sort_code: bankSortDraft,
			account_number: bankNumberDraft,
			iban: bankIbanDraft,
			bic: bankBicDraft,
			method: bankMethodDraft === 'faster_payment' ? 'faster_payment' : 'bacs'
		});
		if (!checked.ok) {
			bankErrors = { [checked.field]: checked.error };
			return;
		}
		const directory = bankDirectoryError(
			bankSchemeDraft === 'iban' ? 'iban' : 'bacs',
			bankDirectoryStatus,
			bankDirectoryResult?.state,
			bankDirectoryResult?.message
		);
		if (directory) {
			bankErrors = { [directory.field]: directory.error };
			return;
		}
		if (bankNeedsReview && !bankJustification.trim()) {
			bankJustificationError = 'Say why this change is needed.';
			return;
		}
		bankErrors = {};
		bankJustificationError = null;
		const body = accountBankSaveBody(checked.value, a.bank_accounts);
		void runSave(() =>
			onSaveBank?.(
				bankNeedsReview && bankJustification.trim()
					? { ...body, justification: bankJustification.trim() }
					: body
			)
		);
	}

	function setFeePackage(id: number, checked: boolean) {
		feePackageIds = checked
			? [...feePackageIds, id]
			: feePackageIds.filter((feeId) => feeId !== id);
	}

	const removeTarget = $derived.by(() => {
		if (!pendingRemove) return null;
		const rows = pendingRemove.side === 'payments' ? payments : withdrawals;
		return rows[pendingRemove.index] ?? null;
	});

	const moveChoices = $derived(scheduleFrequencyChoices(moveFrequency || null));

	function beginMovementEdit(side: 'payments' | 'withdrawals', index: number) {
		const rows = side === 'payments' ? payments : withdrawals;
		const row = rows[index];
		if (!row) return;
		const choices = scheduleFrequencyChoices(row.frequency);
		moveAmount = row.amount ?? null;
		moveFrequency =
			row.frequency && choices.some((item) => item.value === row.frequency) ? row.frequency : '';
		moveDate = (row.next_date ?? '').slice(0, 10);
		moveError = null;
		moveSaving = false;
		editingMovement = { side, index };
	}

	/** The PUT body. A schedule is not a four-eye door: sending it (`payment.regular` /
	 *  `withdrawal.regular`) is where a firm's approvers hold it. */
	function movementsBody(payments: RegularMovement[], withdrawals: RegularMovement[]) {
		return { payments, withdrawals };
	}

	async function saveMovementEdit() {
		if (!editingMovement) return;
		if (moveAmount == null || !Number.isFinite(moveAmount) || moveAmount <= 0) {
			moveError = 'Enter an amount greater than zero.';
			return;
		}
		const { side, index } = editingMovement;
		const source = side === 'payments' ? payments : withdrawals;
		const current = source[index];
		if (!current) return;
		moveSaving = true;
		moveError = null;
		const next = source.map((row, i) =>
			i === index
				? {
						amount: moveAmount,
						frequency: moveFrequency.trim() || null,
						next_date: moveDate.trim() || null,
						method: current.method ?? null,
						reference: current.reference ?? null
					}
				: row
		);
		try {
			const err = await onSaveMovements?.(
				movementsBody(
					side === 'payments' ? next : payments,
					side === 'withdrawals' ? next : withdrawals
				)
			);
			if (typeof err === 'string' && err) {
				moveError = err;
				return;
			}
			editingMovement = null;
		} catch {
			moveError = 'Could not save the change.';
		} finally {
			moveSaving = false;
		}
	}

	function askCancelMovement() {
		if (!editingMovement) return;
		pendingRemove = editingMovement;
		editingMovement = null;
		removeOpen = true;
	}

	async function confirmRemoveMovement() {
		if (!pendingRemove) return;
		const { side, index } = pendingRemove;
		const err = await onSaveMovements?.(
			movementsBody(
				side === 'payments' ? payments.filter((_, i) => i !== index) : payments,
				side === 'withdrawals' ? withdrawals.filter((_, i) => i !== index) : withdrawals
			)
		);
		if (typeof err === 'string' && err) {
			editorError = err;
			return;
		}
		editorError = null;
		pendingRemove = null;
	}
</script>

<!-- `space-y-6` while stacked; a flex column when one band fills the screen, because the band's
     card has to be told to take the leftover height and `space-y` cannot say that. `min-h-0` is the
     load-bearing class on both this and the card inside it: without it a flex child refuses to
     shrink below its content, so the table grows and nothing scrolls. -->
<div
	class={fills
		? 'flex min-w-0 flex-col gap-6 md:min-h-0 md:flex-1 md:overflow-hidden'
		: 'space-y-6'}
>
	<!-- THE HEADER IS A SNIPPET because WHERE it goes depends on the band. On the overview it
	     sits inside the main column, so the rail beside it starts at the top of the page and runs
	     its full height — the shape the CLIENT record has, where Team sits level with the client's
	     own name. On a table band there is no rail, so it spans the width as before. Rendered
	     twice, defined once: two copies of two hundred lines of header is how the bands come to
	     disagree about an account. -->
	{#snippet recordHeader()}
		<Card
			glass={!vulnerable}
			class={cn(vulnerable ? VULNERABLE_CARD_CLASS : undefined, fills && 'shrink-0')}
		>
			{#if vulnerable}
				<VulnerabilityBanner
					reasons={a.client.vulnerability_reasons}
					characteristics={a.client.vulnerability_characteristics ?? []}
					isTemporary={a.client.vulnerability_is_temporary}
					recordedAt={vulnRecorded}
					notes={a.client.vulnerability_notes}
				/>
			{/if}
			<CardContent>
				<!-- `RecordHero`, the same component the CLIENT record's head is: the disc, the name,
				     the badge row, the facts row and the actions are one definition, so the two records
				     cannot drift on the weight of a name or the gap between two facts — which is exactly
				     how they had drifted. What stays here is what an ACCOUNT says about itself —
				     starting with the disc: a wallet, not the owner's person glyph. -->
				<RecordHero name={title} kind="account">
					{#snippet badges()}
						{#if a.product_type}<Badge variant="outline">{a.product_type}</Badge>{/if}
						{#if a.discretionary}
							{#if a.model_name}
								<!-- Not a Badge: those are `h-5 whitespace-nowrap overflow-hidden` with a
									     14rem ceiling, so a real model name hard-clips mid-glyph. A wrapping
									     chip keeps the same look for a short name and lets a long one wrap. -->
								<span
									class="bg-secondary text-secondary-foreground inline-flex max-w-full rounded-full px-2 py-0.5 text-xs font-medium {WRAP_NAME}"
									title={a.model_name}>{a.model_name}</span
								>
							{:else}
								<Badge variant="secondary">Discretionary</Badge>
							{/if}
						{/if}
						<StatusBadge tone={statusTone(a.status)}>{humanise(a.status)}</StatusBadge>
					{/snippet}
					<!-- Reference, owner, adviser, opened and custodian, each an icon and a clause; hover
					     or focus reveals a copy icon on the reference, click copies and toasts. The shelf
					     item lives on Account info, not here: repeating "Self-Invested Personal Pension"
					     beside a SIPP badge is the full name of a type the heading already showed. The
					     adviser keeps its words — an owner and an adviser are both a person's name, so an
					     icon alone cannot say which of the two you are looking at. -->
					{#snippet facts()}
						<!-- `pe-5` on a fact that is not a copy control matches the width the copy
						     button reserves for its hover glyph. Without it the reference sits further
						     from the owner than the owner sits from the custodian. -->
						{#if reference}
							<CopyButton
								value={reference}
								label="Copy account reference"
								reveal="hover"
								class={HERO_COPY_CLASS}
								oncopy={() => toast.success('Account reference copied')}
							>
								<HashIcon class="size-3 shrink-0" />
								<span class="min-w-0 font-mono break-words {WRAP_TOKEN}">{reference}</span>
							</CopyButton>
						{/if}
						{#if owner}
							<span class="flex max-w-full items-center gap-1.5 pe-5">
								<UserIcon class="size-3 shrink-0" />
								{#if a.client.uid != null}
									<!-- MUTED AND UNBOLDED, like every other fact on this line. The owner used to
										     be `text-foreground font-medium`, which made one clause of a row of
										     facts shout — the client record's own header states its email, mobile
										     and address in one weight, and this row is that row. The link is still
										     a link: `LINK_RECORD` gives it the underline and the hover. -->
									<a
										href={links.client(a.client.uid)}
										class="min-w-0 break-words {LINK_RECORD} {WRAP_NAME}">{owner}</a
									>
								{:else}
									<span class="min-w-0 break-words {WRAP_NAME}">{owner}</span>
								{/if}
							</span>
						{/if}
						{#if a.adviser}
							<span class="flex max-w-full items-center gap-1.5 pe-5">
								<UserCogIcon class="size-3 shrink-0" />
								<span class="min-w-0 break-words {WRAP_NAME}">Adviser: {a.adviser}</span>
							</span>
						{/if}
						{#if a.opened_at}
							<span class="flex max-w-full items-center gap-1.5 pe-5">
								<CalendarIcon class="size-3 shrink-0" />
								<span>Opened {formatDate(a.opened_at)}</span>
							</span>
						{/if}
						{#if custodianName}
							<span class="flex max-w-full items-center gap-1.5 pe-5">
								<LandmarkIcon class="size-3 shrink-0" />
								<span class="min-w-0 break-words">{custodianName}</span>
							</span>
						{/if}
					{/snippet}
					<!-- AN OUTLINE "ACTIONS" CONTROL, not a mute kebab. The three dots alone did not
					     say they were the way into Payment / Withdraw / the rest; the word does.
					     `data-action` on each item is the structural fact a test can assert — a closed
					     DropdownMenu paints nothing at SSR, so the body's only visible signal is the
					     trigger. Regular-schedule adds live here too, not as buttons in the rail card. -->
					{#snippet actions()}
						{#if overflowActions}
							<DropdownMenu.Root>
								<DropdownMenu.Trigger>
									{#snippet child({ props })}
										<Button variant="outline" size="sm" aria-label="Account actions" {...props}>
											<EllipsisIcon class="size-4" />
											Actions
										</Button>
									{/snippet}
								</DropdownMenu.Trigger>
								<DropdownMenu.Content align="end" class="w-56">
									{#if showPayment || showWithdraw || showFee}
										<!-- "Payment", not "Pay in": the money's direction is already what the word
										     says. Invest and Sell (`model_topup` / `model_withdrawal`) stay off this
										     record — dealing against the model is not something to fire from the
										     page you came to read. A switched-off action is absent. -->
										{#if showPayment}
											<DropdownMenu.Item
												data-action="instruction-expected_in"
												onclick={() => oninstruction?.('expected_in')}
											>
												<ArrowDownToLineIcon class="text-brand size-4" />
												Payment
											</DropdownMenu.Item>
										{/if}
										{#if showWithdraw}
											<DropdownMenu.Item
												data-action="instruction-withdrawal"
												onclick={() => oninstruction?.('withdrawal')}
											>
												<ArrowUpFromLineIcon class="text-brand size-4" />
												Withdraw
											</DropdownMenu.Item>
										{/if}
										{#if showFee}
											<DropdownMenu.Item
												data-action="instruction-adhoc_fee"
												onclick={() => oninstruction?.('adhoc_fee')}
											>
												<ReceiptIcon class="text-brand size-4" />
												Ad hoc fee
											</DropdownMenu.Item>
										{/if}
									{/if}
									{#if onchangemodel && !pendingModel}
										<DropdownMenu.Item data-action="change-model" onclick={onchangemodel}>
											<LayersIcon class="text-brand size-4" />
											Change model
										</DropdownMenu.Item>
									{/if}
									{#if ontransfer}
										<DropdownMenu.Item onclick={ontransfer} data-action="new-transfer">
											<ArrowLeftRightIcon class="text-brand size-4" />
											Transfer
										</DropdownMenu.Item>
									{:else if links.newTransfer}
										{@const transferHref = links.newTransfer(a.uid)}
										<DropdownMenu.Item data-action="new-transfer">
											{#snippet child({ props })}
												<a {...props} href={transferHref}>
													<ArrowLeftRightIcon class="text-brand size-4" />
													Transfer
												</a>
											{/snippet}
										</DropdownMenu.Item>
									{/if}
									{#if canDrawdown && ondrawdown}
										<DropdownMenu.Item onclick={ondrawdown} data-action="drawdown">
											<LandmarkIcon class="text-brand size-4" />
											Drawdown
										</DropdownMenu.Item>
									{/if}
									{#if canPushToAdministrator && onpushtoadministrator}
										<DropdownMenu.Item
											onclick={onpushtoadministrator}
											data-action="push-to-administrator"
										>
											<SendIcon class="text-brand size-4" />
											Push to administrator
										</DropdownMenu.Item>
									{/if}
									{#if onschedule}
										<DropdownMenu.Separator />
										<!-- Payment / Withdraw above are one-offs. The word Regular
										     on the item is what tells these apart — no extra heading. -->
										<DropdownMenu.Item
											data-action="regular_payment"
											onclick={() => onschedule?.('regular_payment')}
										>
											<RepeatIcon class="text-brand size-4" />
											Regular payment
										</DropdownMenu.Item>
										{#if !isPension}
											<DropdownMenu.Item
												data-action="regular_withdrawal"
												onclick={() => onschedule?.('regular_withdrawal')}
											>
												<RepeatIcon class="text-brand size-4" />
												Regular withdrawal
											</DropdownMenu.Item>
										{/if}
									{/if}
								</DropdownMenu.Content>
							</DropdownMenu.Root>
						{/if}
					{/snippet}
				</RecordHero>
			</CardContent>
		</Card>

		<!-- The figures directly under the header — the same row the client record states: what is
		     held, what is liquid, and, when the custodian has stated them, the allowance still
		     available and how a pension splits between crystallised and uncrystallised. They were
		     three bare `Stat`s in the corner of the header card, which is the layout the client
		     record looked at and rejected: a headline figure reads as a figure on a tile beside its
		     peers, not as a caption trailing the account's name.

		     OUTSIDE THE BAND, so every section keeps them. The header carried Value, Growth and Cash on
		     Holdings and Transactions too, and those are exactly the bands where "what is this account
		     worth" is the question you are holding in your head while you read the rows.

		     `StatTile` rather than a hand-rolled Card + Stat: rebuilding the tile at the call site is
		     what let platform and admin drift apart, which is the component's own standing note. -->
		<!-- auto-fit, not a fixed four columns. The overview puts this row in the main
		     column, beside the rail, and a pension with an allowance can be six tiles.
		     Four columns there clip the figures into each other. -->
		<div
			class={cn('grid grid-cols-[repeat(auto-fit,minmax(12rem,1fr))] gap-4', fills && 'shrink-0')}
		>
			<StatTile label="Value" value={money(a.value ?? 0)} />
			<!-- CASH IN TWO PLACES, stated as a total with the breakdown under it (0117). A
		     pension's money sits in the custodian's account AND in the scheme's own bank
		     account at the administrator, and until now the portals showed only the first — so a
		     client reading "Cash £2,500" was reading part of it. The total is what they want to
		     know; where it is is what makes it checkable. A non-pension account has no
		     administrator row, so this renders exactly as it did before. -->
			<StatTile label="Cash" value={money(cash.total)} sub={cash.breakdown} />
			{#if allowanceTile}
				<StatTile label={allowanceTile.label} value={allowanceTile.value} sub={allowanceTile.sub} />
			{/if}
			{#if split}
				<StatTile label="Crystallised" value={money(split.crystallised)} />
				<StatTile label="Uncrystallised" value={money(split.uncrystallised)} />
			{/if}
			<StatTile label="Book cost" value={bookCost == null ? '—' : money(bookCost)} />
			<!-- The one directional figure of the four: a gain and a loss are different news, and
		     this is the tile read for which of the two it is. -->
			<StatTile
				label="Unrealised gain / loss"
				value={signedMoney(unrealisedGain)}
				valueTone={unrealisedGain === 0 ? undefined : unrealisedGain > 0 ? 'positive' : 'negative'}
			/>
		</div>
	{/snippet}

	<!-- ON A TABLE BAND ALONE the header spans the width: there is no rail on Holdings or
	     Transactions, so nothing is beside it to run level with. NOT on `all`, which renders the
	     overview band too and would then draw the header twice — once full width and once inside
	     the overview's column, which is exactly what it looked like. -->
	{#if section === 'holdings' || section === 'transactions'}
		{@render recordHeader()}
	{/if}

	<!-- One band per route. The tab strip that used to sit here is the SIDEBAR now (the record's
	     sections are published as `sidebarFocus`), which is how the client record already reads —
	     and it means a band is linkable, back/forward works, and a reload keeps the reader where
	     they were. -->
	{#if section === 'overview' || section === 'all'}
		{#if stacked}<SectionHeading title="Overview" />{/if}
		<!-- TWO FLEX COLUMNS from `lg`, the shape the CLIENT record settled on and the reason this
		     band was rebuilt: the account record was a single stack of equal-weight cards, so a fee
		     package and the account's own identity shouted at the same volume and the reader had to
		     scroll the whole thing to find either. Figures and the account's own facts on the left,
		     the standing arrangements about it on the right.

		     FLEX, NOT GRID, for the reason client-record.svelte records: grid rows share height across
		     columns, so a tall left column opens a gap in the right one even with `items-start`.
		     Below `lg` both wrappers are `display: contents`, so the three blocks become one column in
		     `order-*` sequence — the account's own facts, then the arrangements standing on it. -->
		<!-- THREE GRIDS, not a main column and a rail. What an account IS, what its money
			     is doing and what it costs read as three questions of equal rank, so they sit
			     side by side rather than one of them being demoted to a 22rem column. Each grid
			     collapses to a single column below `lg`, in the order they are asked. -->
		{#snippet editPencil(isEditing: boolean, toggle: () => void, label: string)}
			<Button
				variant="ghost"
				size="icon-sm"
				class={isEditing ? undefined : 'text-muted-foreground'}
				onclick={toggle}
				aria-pressed={isEditing}
				title={isEditing ? `Stop editing ${label}` : `Edit ${label}`}
			>
				{#if isEditing}
					<XIcon class="size-4" />
				{:else}
					<PencilIcon class="size-4" />
				{/if}
				<span class="sr-only">{isEditing ? `Stop editing ${label}` : `Edit ${label}`}</span>
			</Button>
		{/snippet}

		<!-- Chromeless EmptyState inside the card — a dashed box here is a second card,
		     and faded bars read as a loader. The icon is the filled widget in miniature.
		     Actions stay in the heading menu / the pencil, not here. -->
		{#snippet quietEmpty(
			title: string,
			description: string | undefined = undefined,
			icon: typeof PieChartIcon | undefined = undefined
		)}
			<EmptyState compact chromeless {title} {description} {icon} />
		{/snippet}
		{#snippet saveBar(label = 'Save')}
			<div class="flex items-center justify-end gap-2 pt-1 col-span-full">
				<Button
					type="button"
					variant="ghost"
					disabled={editorSaving}
					onclick={() => (editing = null)}
				>
					Cancel
				</Button>
				<Button type="submit" disabled={editorSaving}>{editorSaving ? 'Saving…' : label}</Button>
			</div>
		{/snippet}

		<!-- A MAIN COLUMN AND A RAIL, the shape the client record settled on. FLEX, not grid: grid
		     rows share height across columns, so a tall left column opens a gap in the right one
		     even with `items-start`. Below `lg` both wrappers are `display: contents`, so the blocks
		     become one column in `order-*` sequence.

		     THE HEAD IS INSIDE THE MAIN COLUMN, not a full-width strip above the row. That is what
		     makes the rail run the FULL height of the record — it starts level with the account's
		     name, the way the client record's Team and Compliance start level with the client's,
		     rather than beginning below a band the rail plays no part in. On a table band the head
		     is rendered full width instead, because there is no rail there to line up with. -->
		<div class="flex flex-col gap-6 lg:flex-row lg:items-start">
			<div class="contents lg:flex lg:min-w-0 lg:flex-1 lg:flex-col lg:gap-6">
				<div class="order-1 min-w-0 space-y-4">
					{@render recordHeader()}

					<!-- WHAT THE ACCOUNT IS, beside HOW ITS MONEY IS ARRANGED: two questions of equal
					     rank, so they share the width rather than stacking. -->
					<div class="grid gap-4 xl:grid-cols-2">
						<!-- NO HEADING ON THE ALLOCATION CARD: a ring with a named legend beside it is not
						     mistakable for anything else, and the heading cost a line of a card that is
						     mostly whitespace around a small circle. Account info keeps its heading —
						     five bare label/value rows need to be told apart from the bank's. -->
						<Card glass class="gap-2">
							<CardHeader><CardTitle class="text-base">Account info</CardTitle></CardHeader>
							<CardContent>
								<!-- FIVE FACTS, and only five. Product, custody, the custodian and its reference were nine
								     lines of a card that is half the width now — the header states the reference and the
								     custodian, the product type is a badge beside the name, and the rest is on the account's
								     own row in the accounts listing.

								     THE MODEL FIRST AND FULL WIDTH — how the money is actually being run, and the
								     one fact here that is a sentence rather than a code. At a heavier weight it read
								     as a SECOND heading under "Account info", so it is stated at the weight of the
								     four beside it and takes its rank from position alone. Those four are a 2×2
								     block, label ABOVE value rather than across from it: at two columns a
								     right-aligned value ended up against the next column's label, so
								     "Discretionary" and "Currency" read as one row. -->
								<dl class="grid grid-cols-2 gap-x-6 gap-y-3">
									<div class="col-span-2">
										<dt class="text-muted-foreground text-xs">Model</dt>
										<dd class="text-xs font-medium">{a.model_name ?? 'No model'}</dd>
									</div>
									<div>
										<!-- The SERVICE the firm provides, which is not the same statement as the model:
										     "Advisory" was being printed here for every account without one, so an
										     execution-only account read as advised — a claim about a service agreement,
										     made by a missing model_id. -->
										<dt class="text-muted-foreground text-xs">Service type</dt>
										<dd class="text-xs font-medium">{serviceTypeLabel(a.service_type) ?? '—'}</dd>
									</div>
									<div>
										<dt class="text-muted-foreground text-xs">Currency</dt>
										<dd class="text-xs font-medium">{a.currency ?? 'GBP'}</dd>
									</div>
									<div>
										<dt class="text-muted-foreground text-xs">Risk level</dt>
										<dd class="text-xs font-medium">{a.risk_level ?? '—'}</dd>
									</div>
									<div>
										<dt class="text-muted-foreground text-xs">Opened date</dt>
										<dd class="text-xs font-medium">{formatDate(a.opened_at)}</dd>
									</div>
									{#if a.scheme_administrator || a.scheme_administrator_name || a.third_party_pension_provider}
										<!-- Who runs the scheme when it is not the custodian. Prefer the shelf's
										     scheme-administrator link; fall back to the older third-party name. -->
										<div>
											<dt class="text-muted-foreground text-xs">Administrator</dt>
											<dd class="text-xs font-medium">
												{a.scheme_administrator_name ??
													(a.scheme_administrator
														? providerLabel(a.scheme_administrator)
														: a.third_party_pension_provider)}
											</dd>
										</div>
									{/if}
								</dl>
								{#if pendingModel}
									<div class="mt-4">
										<PendingReview
											review={pendingModel}
											href={links.review?.(pendingModel.uid) ?? null}
											showAccount={false}
											oncancel={pendingModel.is_mine && onCancelReview ? cancelPending : null}
										/>
									</div>
								{/if}
							</CardContent>
						</Card>
						<!-- ALWAYS THE CARD. An account nobody has valued still owns this half of
						     the row — hiding it left Account info alone in a two-column grid.
						     The donut draws its own ghost ring at the filled size. -->
						<Card glass>
							<CardContent
								class={!allocSegments.length ? 'flex flex-1 items-center justify-center' : ''}
							>
								<AllocationDonut
									segments={allocSegments}
									amount={(seg) => seg.value}
									currency={baseCurrency}
									size="lg"
								/>
							</CardContent>
						</Card>
					</div>

					<!-- WHAT THE MONEY IS DOING: the positions, and what has lately moved through
					     them. Two lists of short rows, so they share the width too. -->
					<div class="grid gap-4 xl:grid-cols-2">
						<Card glass class="gap-2">
							<CardHeader class="flex items-center justify-between gap-2">
								<CardTitle class="text-base">Holdings</CardTitle>
								{#if holdings.length}
									<span class="text-muted-foreground text-xs font-medium tabular-nums">
										{holdings.length}
									</span>
								{/if}
							</CardHeader>
							<CardContent
								class={!sortedHoldings.length
									? 'flex flex-1 flex-col items-center justify-center'
									: 'space-y-4'}
							>
								{#if !sortedHoldings.length}
									{@render quietEmpty(
										'No holdings yet',
										'Positions appear as the custodian’s feed brings them in.',
										PieChartIcon
									)}
								{/if}
								{#if sortedHoldings.length}
									<!-- ONE LINE PER POSITION: the name, its share, its value. It was two lines —
								     the ISIN under the name, the share under the value — for a row that is
								     really "what, and how much", and eighteen of those is a column of text
								     nobody reads twice. The ISIN is on the Holdings band, where a row has the
								     room to state it.

								     EACH ROW IS THE WAY INTO THAT POSITION, the same road the band's own rows
								     take (`Holding.href` — this account's transactions, narrowed to the
								     instrument). Plain text without one: a mount whose mapper builds no href
								     must not offer a dead link.

								     SCROLLS, capped at ten rows of height, so a sixty-line portfolio cannot
								     push the rest of the overview off the page. -->
									<ul class="divide-border/60 max-h-72 divide-y overflow-y-auto text-xs">
										<!-- KEYED BY INDEX. `instrumentId` is absent on any lake row without an
									     `asset_id` and the name falls back to the literal "Holding", so two
									     unnamed cash legs produced one key and Svelte threw
									     `each_key_duplicate` — a crashed record for a feed's missing field.
									     The list is derived and re-sorted whole, so an index key costs
									     nothing. -->
										{#each sortedHoldings as h, i (i)}
											<li class="flex items-baseline justify-between gap-3 py-1.5">
												{#if h.href}
													<a href={h.href} class="min-w-0 flex-1 truncate font-medium {LINK_RECORD}"
														>{h.name}</a
													>
												{:else}
													<span class="min-w-0 flex-1 truncate font-medium">{h.name}</span>
												{/if}
												{#if holdingsTotal > 0}
													<span class="text-muted-foreground shrink-0 tabular-nums">
														{((h.value / holdingsTotal) * 100).toFixed(1)}%
													</span>
												{/if}
												<span class="shrink-0 text-right">
													<span class="block tabular-nums">
														<Money amount={h.value} currency={h.currency || baseCurrency} />
													</span>
													<!-- GROWTH UNDER THE VALUE, the one figure on this row where up and
													     down is the fact being read — so it takes the directional tint,
													     as the account's own gain tile does. Absent when the custodian
													     gives no basis to compute it from: an unknown cost is not a
													     position that has neither gained nor lost. -->
													{#if h.unrealisedGain != null && h.unrealisedGain !== 0}
														<span class="block text-[11px] font-medium tabular-nums">
															<Money
																amount={h.unrealisedGain}
																currency={h.currency || baseCurrency}
																signed
																tone="directional"
															/>
														</span>
													{/if}
												</span>
											</li>
										{/each}
									</ul>
								{/if}
							</CardContent>
						</Card>
						<Card glass class="gap-2">
							<CardHeader>
								<CardTitle class="text-base">Recent transactions</CardTitle>
							</CardHeader>
							<CardContent>
								{#if !recentTransactions.length}
									{@render quietEmpty(
										'No transactions yet',
										'Movements appear as the custodian’s feed brings them in.',
										ArrowLeftRightIcon
									)}
								{:else}
									<ul class="divide-border max-h-80 divide-y overflow-y-auto text-xs">
										{#each recentTransactions as t (t.uid)}
											{@const caption = conversionCaption(t)}
											<li class="flex items-center justify-between gap-3 py-2">
												<span class="min-w-0">
													<span class="block truncate font-medium">
														{t.asset ?? humanise(t.subtype || t.type)}
													</span>
													<span class="text-muted-foreground block truncate">
														{formatDate(t.date)} · {humanise(t.type)}
													</span>
												</span>
												<span class="shrink-0 text-right">
													<Money amount={t.amount} currency={t.currency ?? undefined} />
													{#if caption}
														<span class="text-muted-foreground block font-normal">
															<Money
																amount={caption.amount}
																currency={caption.currency}
																class="font-normal"
															/>
														</span>
													{/if}
													{#if t.status !== 'settled'}
														<span class="text-muted-foreground block">{humanise(t.status)}</span>
													{/if}
												</span>
											</li>
										{/each}
									</ul>
								{/if}
							</CardContent>
						</Card>
					</div>

					<!-- A PENSION'S CONTRIBUTIONS THIS YEAR, and only a pension's: the loader asks
					     for the summary for no other wrapper. -->
					{#if contributionSummary}
						<Card glass class="gap-2">
							<CardHeader>
								<CardTitle class="text-base">Contributions</CardTitle>
							</CardHeader>
							<CardContent class="space-y-3">
								<div>
									<p class="text-muted-foreground text-xs">
										This account · {contributionSummary.taxYear}
									</p>
									<p class="font-medium">
										<Money amount={contributionSummary.accountGross} currency={baseCurrency} />
									</p>
								</div>
								<p class="text-muted-foreground text-xs">
									{contributionSummary.count} recorded on this account.
								</p>
							</CardContent>
						</Card>
					{/if}
				</div>
			</div>

			<!-- RAIL: what the account COSTS, where its money goes out to, what moves in and out on a
			     schedule, and who inherits it. -->
			<div class="contents lg:flex lg:w-[22rem] lg:shrink-0 lg:flex-col lg:gap-6">
				<div class="order-2 flex min-w-0 flex-col gap-4">
					<Card glass size="sm" class="gap-2">
						<CardHeader>
							<CardTitle class="text-base">Fees</CardTitle>
							{#if onSaveFees}
								<CardAction>
									{@render editPencil(editing === 'fees', () => toggleEdit('fees'), 'fees')}
								</CardAction>
							{/if}
						</CardHeader>
						<CardContent>
							{#if editing === 'fees'}
								<form
									class="space-y-3"
									onsubmit={(event) => {
										event.preventDefault();
										void runSave(() => {
											if (feesNeedReason && !feeJustification.trim()) {
												return 'Say why this change is needed.';
											}
											return onSaveFees?.(
												feePackageIds,
												feesNeedReason ? feeJustification.trim() : undefined
											);
										});
									}}
								>
									<p class="text-muted-foreground text-xs">
										Every selected package applies equally.
									</p>
									{#if feeEditorOptions.length}
										<div class="divide-border divide-y rounded-lg border">
											{#each feeEditorOptions as fee (fee.id)}
												{@const option = summariseAppliedFee(fee, 'staff')}
												<label class="flex cursor-pointer items-start gap-3 px-3 py-2.5">
													<Checkbox
														checked={feePackageIds.includes(fee.id)}
														onCheckedChange={(checked) => setFeePackage(fee.id, checked === true)}
													/>
													<span class="min-w-0">
														<span class="block text-xs font-medium">{fee.name}</span>
														{#if option.charge || option.frequency}
															<span class="text-muted-foreground block text-xs">
																{[option.charge, option.frequency].filter(Boolean).join(' · ')}
															</span>
														{/if}
														{#each option.bands as band (band)}
															<span class="text-muted-foreground block text-xs tabular-nums"
																>{band}</span
															>
														{/each}
														{#if option.notes.length}
															<span class="text-muted-foreground block text-xs"
																>{option.notes.join(' · ')}</span
															>
														{/if}
													</span>
												</label>
											{/each}
										</div>
									{:else}
										<p class="text-muted-foreground text-sm">
											No fee packages available for this account.
										</p>
									{/if}
									{#if feesNeedReason}
										<div class="space-y-1.5">
											<Label for="fee-justification">Why this change</Label>
											<Textarea id="fee-justification" bind:value={feeJustification} rows={2} />
											<p class="text-muted-foreground text-xs">
												A second person must approve before the fee packages change.
											</p>
										</div>
									{/if}
									{#if editorError}<p class="text-destructive text-sm">{editorError}</p>{/if}
									{@render saveBar('Apply fee packages')}
								</form>
							{:else}
								{#if a.fee_packages.length}
									<!-- The CHARGE, not the firm's label for it. This was a list of package names,
									     so the one question an adviser opens this card to answer — what is this
									     client paying, and how often — could only be answered by leaving the record
									     for the fee catalogue. `summariseAppliedFee` is the same formatter the
									     client's own application dossier uses, so the two cannot word it
									     differently. A package whose rule cannot be priced still shows its name. -->
									<div class="mb-3">
										<ul class="divide-border divide-y text-xs">
											{#each a.fee_packages as feePackage (feePackage.id)}
												{@const charge = summariseAppliedFee(feePackage, 'staff')}
												<li class="py-2 first:pt-0">
													<div class="flex flex-wrap items-baseline justify-between gap-x-3">
														<span class="font-medium">{charge.name}</span>
														{#if charge.charge}
															<span class="tabular-nums">{charge.charge}</span>
														{/if}
													</div>
													{#if charge.frequency}
														<p class="text-muted-foreground text-xs">{charge.frequency}</p>
													{/if}
													{#if charge.bands.length}
														<ul class="text-muted-foreground mt-0.5 space-y-0.5 text-xs">
															{#each charge.bands as band (band)}
																<li class="tabular-nums">{band}</li>
															{/each}
														</ul>
													{/if}
													{#if charge.notes.length}
														<p class="text-muted-foreground mt-0.5 text-xs">
															{charge.notes.join(' · ')}
														</p>
													{/if}
												</li>
											{/each}
										</ul>
										<p class="text-muted-foreground mt-2 text-xs">
											Agreed fee packages · all apply equally
										</p>
									</div>
								{/if}
								{#if fees.length}
									<ul class="divide-border divide-y text-xs">
										{#each fees as fee, i (i)}
											<li class="flex items-center justify-between gap-3 py-2">
												<span class="min-w-0">
													<span class="block truncate">{fee.name ?? humanise(fee.type)}</span>
													<span class="text-muted-foreground text-xs"
														>{humanise(fee.frequency)}</span
													>
												</span>
												<span class="tabular-nums">
													{#if fee.rate != null}{(fee.rate * 100).toFixed(2)}%
													{:else if fee.amount != null}<Money
															amount={fee.amount}
															currency={baseCurrency}
														/>
													{:else}—{/if}
												</span>
											</li>
										{/each}
									</ul>
								{:else if !a.fee_packages.length}
									{@render quietEmpty('No fees recorded on this account.', undefined, ReceiptIcon)}
								{/if}
							{/if}
						</CardContent>
					</Card>
					<Card glass size="sm" class="gap-2">
						<CardHeader>
							<CardTitle class="text-base">Bank details</CardTitle>
							{#if a.bank_details_allowed !== false && onSaveBank && !pendingBank}
								<CardAction>
									{@render editPencil(editing === 'bank', () => toggleEdit('bank'), 'bank details')}
								</CardAction>
							{/if}
						</CardHeader>
						<CardContent>
							{#if a.bank_details_allowed === false}
								<p class="text-muted-foreground text-xs">
									Bank details do not apply to this product because withdrawals are not available.
								</p>
							{:else}
								{#if inheritingBank && (pendingBank || bankViewRows.length)}
									<p class="text-muted-foreground mb-2 text-xs">
										From
										{#if links.client && a.client.uid && owner}
											<a href={links.client(a.client.uid)} class={LINK_RECORD}>{owner}</a>
										{:else}
											the client
										{/if}
									</p>
								{/if}
								{#if pendingBank}
									<PendingReview
										review={pendingBank}
										href={links.review?.(pendingBank.uid) ?? null}
										showAccount={false}
										oncancel={pendingBank.is_mine && onCancelReview ? cancelPending : null}
									/>
								{:else if bankViewRows.length}
									<!-- LABEL | VALUE, one fact per row. When the account has none of its
									     own, these ARE the client's — named above, so the reader is not
									     left wondering whose digits they are looking at. -->
									<dl class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs">
										{#each bankViewRows as row (row[0])}
											<dt class="text-muted-foreground">{row[0]}</dt>
											<dd
												class="flex min-w-0 items-center gap-1.5 {row[0] === 'Account name'
													? WRAP_NAME
													: 'tabular-nums'} {row[1] ? 'font-medium' : 'text-muted-foreground/60'}"
											>
												{#if row[0] === 'Bank' && row[1]}
													<BankMark name={row[1]} bic={null} />
												{/if}
												<span class="min-w-0">{row[1] ?? '—'}</span>
											</dd>
										{/each}
									</dl>
								{:else if inheritingBank}
									{@render quietEmpty(
										'No bank details on the client',
										'Set at client or account level',
										LandmarkIcon
									)}
								{:else}
									{@render quietEmpty('No bank details on this account', undefined, LandmarkIcon)}
								{/if}
							{/if}
						</CardContent>
					</Card>
					<!-- The card sits in a 22rem rail, which cannot hold the three-column editor the
					     client record uses. The dialog is that same editor, so the two cannot drift. -->
					{#if editing === 'bank'}
						<Dialog.Root
							open
							onOpenChange={(open) => {
								if (!open) editing = null;
							}}
						>
							<Dialog.Content class="sm:max-w-2xl">
								<Dialog.Title>Bank details</Dialog.Title>
								<form
									autocomplete="off"
									onsubmit={(event) => {
										event.preventDefault();
										saveBank();
									}}
								>
									<BankAccountFields
										bind:scheme={bankSchemeDraft}
										bind:name={bankNameDraft}
										bind:sortCode={bankSortDraft}
										bind:accountNumber={bankNumberDraft}
										bind:iban={bankIbanDraft}
										bind:bic={bankBicDraft}
										bind:method={bankMethodDraft}
										bind:directoryStatus={bankDirectoryStatus}
										bind:directoryResult={bankDirectoryResult}
										errors={bankErrors}
										idPrefix="account-bank-"
										onSchemeChange={() => (bankErrors = {})}
									>
										{#snippet footer()}
											<p class="text-muted-foreground col-span-full text-xs">
												These details belong to this account. Clearing every field falls back to the
												client’s.
											</p>
											{#if bankNeedsReview}
												<div class="col-span-full space-y-1">
													<Label for="account-bank-justification">Why this change</Label>
													<Textarea
														id="account-bank-justification"
														bind:value={bankJustification}
														rows={2}
														required
														aria-invalid={bankJustificationError ? 'true' : undefined}
													/>
													{#if bankJustificationError}
														<p class="text-destructive text-xs">{bankJustificationError}</p>
													{:else}
														<p class="text-muted-foreground text-xs">
															A second person must approve before these details change.
														</p>
													{/if}
												</div>
											{/if}
											{#if editorError}
												<p class="text-destructive col-span-full text-sm">{editorError}</p>
											{/if}
											{@render saveBar()}
										{/snippet}
									</BankAccountFields>
								</form>
							</Dialog.Content>
						</Dialog.Root>
					{/if}
					<Card glass size="sm" class="gap-2">
						<!-- NO ACTIONS IN THIS CARD. Add payment / Add withdrawal live in the heading
						     Actions menu — a dashed EmptyState plus an outline button in a 22rem rail
						     is a widget that is mostly the absence it names. -->
						<CardHeader>
							<CardTitle class="text-base">Regular payments &amp; withdrawals</CardTitle>
						</CardHeader>
						<CardContent class="space-y-3">
							{#each [{ label: 'Payments in', side: 'payments' as const, rows: payments, empty: 'No payments set up' }, { label: 'Withdrawals', side: 'withdrawals' as const, rows: withdrawals, empty: 'No withdrawals set up' }] as group (group.side)}
								{@const canEdit =
									Boolean(onSaveMovements) && (group.side === 'payments' || !isPension)}
								{@const canAdd = Boolean(onschedule) && (group.side === 'payments' || !isPension)}
								<div>
									<p class="text-muted-foreground mb-1 text-xs font-medium">
										{group.label}
									</p>
									{#if group.rows.length}
										<ul class="divide-border divide-y text-xs">
											{#each group.rows as m, i (i)}
												{@const caption = movementScheduleCaption(
													m.frequency,
													m.next_date,
													formatDate
												)}
												<!-- ONE LINE. The rail is 22rem: frequency on its own line, then the
												     method and the next date, then the amount, wrapped the figure
												     onto a third. The method is not on this line — a missing one
												     printed as an em dash beside "next". -->
												<li class="flex items-center gap-1 py-1.5">
													<span class="flex min-w-0 flex-1 items-baseline gap-x-1.5">
														{#if m.amount != null}
															<Money
																amount={m.amount}
																currency={baseCurrency}
																class="shrink-0 font-medium"
															/>
														{/if}
														{#if caption}
															<span class="text-muted-foreground min-w-0 truncate">{caption}</span>
														{/if}
													</span>
													{#if canEdit}
														<Button
															type="button"
															variant="ghost"
															size="icon-sm"
															class="shrink-0"
															onclick={() => beginMovementEdit(group.side, i)}
															aria-label="Update this {group.side === 'payments'
																? 'regular payment'
																: 'regular withdrawal'}"
														>
															<PencilIcon class="text-brand size-4" />
														</Button>
													{/if}
												</li>
											{/each}
										</ul>
									{:else}
										<!-- Ghost of one schedule row, then the sentence. On a SIPP,
										     withdrawals cannot be added here at all, so that side names
										     the absence and points at the administrator. -->
										{@render quietEmpty(
											group.empty,
											!canAdd && group.side === 'withdrawals'
												? 'Ask the administrator to set one up.'
												: undefined,
											RepeatIcon
										)}
									{/if}
								</div>
							{/each}
							{#if editorError}<p class="text-destructive text-sm">{editorError}</p>{/if}
							{#if canContribute}
								<p class="text-muted-foreground border-t pt-3 text-xs">
									Contributions into this pension are categorised from the Transactions tab.
								</p>
							{/if}
						</CardContent>
					</Card>
					<!-- BENEFICIARIES — a pension's expression of wish, and pensions only. An ISA or a GIA
					     passes under the will and through probate, so offering a nomination there would invite
					     a firm to record a wish that has no effect.
					     Here rather than on the client record because that is the grain the fact has:
					     `cam_createbeneficiaries` is addressed by scheme reference, a member with two pensions
					     can nominate differently on each, and an expression-of-wish form is signed per
					     scheme. -->
					{#if isPension}
						<Beneficiaries
							{beneficiaries}
							enhance={beneficiaryActions?.enhance}
							addAction={beneficiaryActions?.add}
							removeAction={beneficiaryActions?.remove}
						/>
					{/if}

					<RecentInstructions
						{instructions}
						accountHref={links.account}
						currencyOf={() => baseCurrency}
						limit={5}
					/>
				</div>
			</div>
		</div>
	{/if}
	{#if section === 'holdings' || section === 'all'}
		{#if stacked}<SectionHeading title="Holdings" />{/if}
		<Card
			glass
			class={fills ? 'flex min-w-0 flex-col md:min-h-0 md:flex-1 md:overflow-hidden' : undefined}
		>
			<CardContent
				class={fills ? 'flex min-w-0 flex-col md:min-h-0 md:flex-1 md:overflow-hidden' : undefined}
			>
				{#if holdingsAsOf}
					<p class="text-muted-foreground mb-3 shrink-0 text-xs">
						Holdings as at {formatDate(holdingsAsOf)}
					</p>
				{/if}
				<!-- THE BOOK'S OWN TOOLBAR, not a bespoke box: `ListingToolbar` with `FilterSelect`s is
				     what every other listing in the product wears, so the search behaves the same here
				     as it does on Transactions beside it. A twenty-line account never needed one; a
				     model portfolio with sixty positions is unreadable without. -->
				{#if holdings.length > HOLDINGS_PER_PAGE || holdingsDirty}
					<!-- `bind:query`, NOT `onsearch`: the toolbar's own contract is that a bound query
					     filters locally and `onsearch` means server-driven, which debounces every
					     keystroke by 300ms before handing the term up. These rows are already in memory,
					     so the debounce bought a lag and nothing else. -->
					<ListingToolbar
						class="mb-3 shrink-0"
						bind:query={holdingsQuery}
						placeholder="Search holdings, ticker or ISIN"
						dirty={holdingsDirty}
						onclear={clearHoldings}
					>
						{#snippet filters()}
							<FilterSelect
								bind:value={holdingsClass}
								options={holdingsClassOptions}
								allLabel="All asset classes"
								label="Filter by asset class"
							/>
							{#if holdingsCurrencyOptions.length > 1}
								<FilterSelect
									bind:value={holdingsCurrency}
									options={holdingsCurrencyOptions}
									allLabel="All currencies"
									label="Filter by currency"
								/>
							{/if}
						{/snippet}
					</ListingToolbar>
				{/if}
				<!-- `showWrapper={false}`: every row here belongs to the account this page IS, so the
				     pill repeated its name down the whole column and took the width the figures needed.
				     It stays on a CLIENT's holdings, where it is the only thing telling two accounts'
				     positions apart. The empty state is HoldingsTable's own now rather than a sibling
				     paragraph — one component decides what "no rows" looks like. -->
				<HoldingsTable
					holdings={pagedHoldings}
					showWrapper={false}
					{allocationBase}
					scroll={fills}
					bind:sort={holdingsSort}
					empty={holdingsDirty
						? 'No holdings match that search.'
						: 'No holdings for this account. Positions appear once the custodian feed links it.'}
				/>

				{#if holdings.length > 0}
					<!-- The same foot strip the ledgers have: range on the left, pager right, export
					     furthest right. One place for the export on every card in the product. -->
					<div class="-mx-4 mt-3 flex shrink-0 items-center gap-3 border-t px-4 pt-3">
						<!-- COUNTS THE MATCH SET, and says so when it is narrower than the account: a
						     range reading "1–10 of 60" under a filtered table is a statement about rows
						     the reader cannot see. -->
						<p class="text-muted-foreground shrink-0 text-xs tabular-nums" aria-live="polite">
							{Math.min(
								(holdingsPage - 1) * HOLDINGS_PER_PAGE + 1,
								filteredHoldings.length
							)}–{Math.min(holdingsPage * HOLDINGS_PER_PAGE, filteredHoldings.length)} of {filteredHoldings.length}{holdingsDirty
								? ` matching, ${holdings.length} in total`
								: ''}
						</p>
						{#if holdingsPageCount > 1}
							<div class="ml-auto min-w-0">
								<UiPagination bind:page={holdingsPage} pageCount={holdingsPageCount} />
							</div>
						{/if}
						{#if holdingsExportLink}
							<div class:ml-auto={holdingsPageCount <= 1} class="shrink-0">
								<ExportMenu href={holdingsExportLink} label="Export" />
							</div>
						{/if}
					</div>
				{/if}
			</CardContent>
		</Card>
	{/if}
	{#if section === 'transactions' || section === 'all'}
		{#if stacked}<SectionHeading title="Transactions" />{/if}
		<div
			class={fills
				? 'flex min-w-0 flex-col gap-4 md:min-h-0 md:flex-1 md:overflow-y-auto'
				: 'space-y-4'}
		>
			{#if transactionFilter}
				<div
					class="bg-brand-subtle flex shrink-0 items-center justify-between gap-3 rounded-lg px-3 py-2 text-sm"
				>
					<span>Showing transactions for <strong>{transactionFilter}</strong></span>
					{#if links.accountTransactions}
						<Button variant="ghost" size="sm" href={links.accountTransactions(a.uid)}>Clear</Button>
					{/if}
				</div>
			{/if}
			<!--
			  The queue takes its natural height ABOVE the table, and the table keeps its own: with
			  both sharing one `flex-1` column the ledger visibly shrank the moment a categorisation
			  landed, so the same account showed ten rows one minute and six the next. A table whose
			  height depends on an unrelated alert is a table you cannot learn the shape of.

			  So the band scrolls (`overflow-y-auto` below) and the card holds a floor instead. On a
			  tall screen nothing moves and nothing scrolls; on a short one the page scrolls, which is
			  the honest trade — the alternative is a four-row table.
			-->
			{#if actionRequired && outstanding.length > 0}
				<div class={fills ? 'shrink-0' : undefined}>{@render actionRequired(outstanding)}</div>
			{/if}
			<!-- The SAME component the standalone ledger uses, embedded. This band was a bare
			     DataTable, which is why it had no search, no type filter, no date range and no
			     export while the client-level page had all four — one missing component reading as
			     four missing features. It also means the columns here and there cannot drift. -->
			<TransactionsListing
				embedded
				{transactions}
				{links}
				showClient={false}
				showAccount={false}
				showAdviserFilter={false}
				{fills}
				showDateFilter
				searchPlaceholder="Search reference, type, asset or ISIN"
				exportHref={transactionExportHref}
				minHeight="min-h-[30rem]"
				listing={transactionListing}
				matchCount={transactionTotal}
				facets={transactionFacets}
				bounds={transactionBounds}
				onquery={ontransactionquery}
				pageHref={transactionPageHref}
			/>
		</div>
	{/if}
</div>

{#if editingMovement}
	{@const cancelling = editingMovement.side === 'withdrawals'}
	<Dialog.Root
		open
		onOpenChange={(open) => {
			if (!open) editingMovement = null;
		}}
	>
		<Dialog.Content class="sm:max-w-md">
			<Dialog.Header>
				<Dialog.Title>
					{cancelling ? 'Update regular withdrawal' : 'Update regular payment'}
				</Dialog.Title>
				<Dialog.Description>
					The amount, how often it runs, and the next date. Cancelling stops the instruction.
				</Dialog.Description>
			</Dialog.Header>
			<form
				class="space-y-3"
				onsubmit={(event) => {
					event.preventDefault();
					void saveMovementEdit();
				}}
			>
				<div class="space-y-1.5">
					<Label for="movement-amount">Amount</Label>
					<MoneyInput
						id="movement-amount"
						currency={a.currency ?? 'GBP'}
						bind:value={moveAmount}
						disabled={moveSaving}
					/>
				</div>
				<div class="space-y-1.5">
					<Label for="movement-frequency">Frequency</Label>
					<SelectMenu
						id="movement-frequency"
						items={moveChoices}
						bind:value={moveFrequency}
						placeholder="Not set"
						portal={false}
						disabled={moveSaving}
					/>
				</div>
				<div class="space-y-1.5">
					<Label for="movement-next">Next date</Label>
					<Input id="movement-next" type="date" bind:value={moveDate} disabled={moveSaving} />
				</div>
				{#if moveError}<p class="text-destructive text-sm">{moveError}</p>{/if}
				<Dialog.Footer class="sm:justify-between">
					<Button
						type="button"
						variant="ghost"
						class="text-destructive hover:text-destructive"
						disabled={moveSaving}
						onclick={askCancelMovement}
					>
						{cancelling ? 'Cancel withdrawal' : 'Cancel payment'}
					</Button>
					<div class="flex gap-2">
						<Button
							type="button"
							variant="ghost"
							disabled={moveSaving}
							onclick={() => (editingMovement = null)}
						>
							Close
						</Button>
						<Button type="submit" disabled={moveSaving}>
							{moveSaving ? 'Saving…' : 'Save'}
						</Button>
					</div>
				</Dialog.Footer>
			</form>
		</Dialog.Content>
	</Dialog.Root>
{/if}

<ConfirmDialog
	bind:open={removeOpen}
	title={pendingRemove?.side === 'withdrawals'
		? 'Cancel this regular withdrawal?'
		: 'Cancel this regular payment?'}
	description={removeTarget
		? `${money(removeTarget.amount ?? 0)}${removeTarget.frequency ? ` ${movementFrequencyLabel(removeTarget.frequency).toLowerCase()}` : ''} will stop. Nothing already collected is affected.`
		: undefined}
	confirmLabel={pendingRemove?.side === 'withdrawals' ? 'Cancel withdrawal' : 'Cancel payment'}
	cancelLabel="Keep it"
	pendingLabel="Cancelling…"
	destructive
	onconfirm={confirmRemoveMovement}
/>
