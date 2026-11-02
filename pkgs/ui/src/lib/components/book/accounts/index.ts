// The shared account-book surfaces (accounts listing, account detail, transactions) consumed
// by BOTH the platform portal and the admin portal (the "waterfall": admin sees everything a
// tenant sees). Extracted from the apps/platform pages — data stays app-side (loaders pass
// typed props), routes stay app-side (`AccountBookLinks` builders), and every side-effectful
// affordance is an optional callback/snippet that hides when absent.
export { default as AccountsListing } from './accounts-listing.svelte';
export { default as AccountDetail } from './account-detail.svelte';
export { default as Beneficiaries } from './beneficiaries.svelte';
export { default as InstructionSheet } from './instruction-sheet.svelte';
export { default as ModelSheet } from './model-sheet.svelte';
export { default as TransactionsListing } from './transactions-listing.svelte';

export type {
	Account,
	AccountBookLinks,
	Beneficiary,
	AccountClient,
	AccountDetailData,
	ProviderPosition,
	AccountContributionSummary,
	AccountFee,
	AccountFilterOption,
	AccountSection,
	AccountView,
	BankAccount,
	FeePackageOption,
	RegularMovement,
	WithdrawalFees
} from './types.js';

export {
	CONTRIBUTION_CATEGORIES,
	PENSION_PRODUCTS,
	isPensionProduct,
	needsAction,
	subtypeDetail,
	chargeBreakdown,
	entitlementLabel
} from './transactions.js';
// The exports need it for the same reason the table does: a file that prints the raw `seccl:TX-1`
// where the screen shows `TX-1` is a file nobody can match back to what they were looking at.
export { transactionReference } from './format.js';
export {
	allowanceFact,
	consolidatedCash,
	pensionSplit,
	providerLabel,
	STATUTORY_ALLOWANCE,
	ukTaxYear,
	wrapperAllowance
} from './money-facts.js';
export type { AllowanceFact, WrapperAllowance } from './money-facts.js';
export {
	calendarDate,
	feeTypeItems,
	isAccountSheetKind,
	isCashInstruction,
	isPastDate,
	isPayInSheet,
	isRegularSchedule,
	isWithdrawSheet,
	netPayment,
	payInReference,
	payInSortCode,
	remainingCash,
	spendsCash,
	WITHDRAWAL_METHOD_FEE,
	WITHDRAWAL_METHOD_LABEL,
	withdrawalBankCaption,
	withdrawalBankHeading,
	withdrawalCash,
	withdrawalDebit,
	withdrawalDestination,
	clientBorneRailFee,
	railFeeForMethod,
	ukWithdrawalMethod,
	withdrawalMethodFee,
	withdrawalPaymentMethod,
	withdrawalTotalDebit
} from './instruction.js';
export type {
	AccountSheetKind,
	CashInstructionKind,
	InstructionSubmitBody,
	RegularScheduleKind,
	RegularSubmitBody,
	SheetSubmitBody,
	WithdrawalBankSource,
	WithdrawalPaymentMethod
} from './instruction.js';
export { asRegularMovements, movementFrequencyLabel, SCHEDULE_FREQUENCIES } from './regular.js';
// An account's SERVICE TYPE. Exported because the CSV/PDF export has to print the same word the
// column shows — the vocabulary lives once (`service-type.ts`) or the file and the screen drift.
export { serviceTypeLabel, SERVICE_TYPE_OPTIONS } from './service-type.js';
export type {
	ContributionCategory,
	PaymentMethod,
	Transaction,
	TransactionStatus,
	TransactionType
} from './transactions.js';

// `transactionFixtures` is DELIBERATELY NOT EXPORTED. Every transactions surface reads
// `GET /crm/transactions` now (it reads `platform.transactions`, with the account/client/adviser
// joins the listing needs), and an invented ledger is indistinguishable from a real one on screen
// — the admin book showed the same fixture rows under every firm for months. The corpus stays in
// `transaction-fixtures.ts` for this directory's own tests, out of reach of the apps.
