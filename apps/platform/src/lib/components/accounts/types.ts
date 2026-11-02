/** What the account record needs, as the section layout plus the band's own page load it.
 *
 * Declared here rather than taken from a route's `PageData` because three routes mount the same
 * component (`account-record.svelte`) and a generated `./$types` belongs to one of them. The layout
 * publishes the account and the permission flags; each band fills in the slice it paints — see
 * `routes/accounts/[uid]/+layout.server.ts` and the section `+page.server.ts` files.
 */
import type {
	Beneficiary,
	AccountContributionSummary,
	FeePackageOption,
	Holding,
	InstructionRow,
	Transaction
} from '@graphene/ui';
import type { ListingQuery } from '@graphene/ui/book/listing';
import type { ReviewRow } from '@graphene/ui/book/reviews';
import type { AccountDetail } from '$lib/api';

export interface AccountRecordData {
	/** This account's beneficiary nominations (`crm.beneficiaries`, 0139). Pension accounts only —
	 *  the loader does not ask for any other wrapper, because an ISA or a GIA passes under the will
	 *  and a nomination there would be a wish with no effect. */
	beneficiaries?: Beneficiary[];
	account: AccountDetail;
	holdings: Holding[];
	/** This account's asset-class split, from `GET /crm/holdings/summary?account_id=` — classified
	 *  server-side, because the mapping `holdings` goes through drops the provider's `asset_type`.
	 *  Absent on the bands that do not load it, and on a failed read. */
	allocations?: { label: string; value: number }[];
	transactions: Transaction[];
	transactionQuery: ListingQuery;
	transactionTotal: number;
	/** Per-filter options over the match set, so the band's type filter offers the types this
	 *  account actually has. Absent on the bands that do not load transactions. */
	transactionFacets?: Record<string, string[]>;
	instructions: InstructionRow[];
	feePackages: FeePackageOption[];
	contributionSummary: AccountContributionSummary | null;
	canReadInstructions: boolean;
	transactionFilter?: string | null;
	/** Pension + `instructions:write` — gates the contribution and drawdown triggers. */
	canDrawdown: boolean;
	/** Whether a contribution may be raised — a pension, and the caller may move money. Separate
	 *  from `canDrawdown`, which additionally needs Morgan Lloyd to administer the scheme. */
	canContribute: boolean;
	/** Whether this account can be synced to Morgan Lloyd — a pension, and the caller holds
	 *  `contacts:sync_morgan_lloyd`. Broader than `canDrawdown`: the sync creates the scheme, so
	 *  it cannot wait for the shelf to already name Morgan Lloyd as administrator. Not implied
	 *  by `contacts:write`. */
	canPushToMorganLloyd: boolean;
	/** `instructions:write` — gates the transfer-in trigger. */
	canMoveMoney: boolean;
	/** Fee catalogue read + `instructions:write`; hidden if the picker vocabulary is unavailable. */
	canEditFees: boolean;
	/** `contacts:write` — gates beneficiary nominations on a pension. */
	canEditContact: boolean;
	/** The holdings or transactions read failed. Rendered as a retry banner rather than left to
	 *  read as an empty band — "no movements" and "we could not reach the ledger" are different
	 *  answers, and a blank table states the wrong one. */
	loadError?: boolean;
	pendingReviews?: ReviewRow[];
	justificationRequired?: string[];
	/** Money actions the firm has switched off. A missing policy is not in the list. */
	disabledActions?: string[];
}

/** What `POST /crm/accounts/{uid}/sync-to-morgan-lloyd` answers with.
 *
 * A PER-STEP REPORT, not an outcome. Five records are created — the client's adviser, the
 * contact, the scheme, the investment (policy) and each beneficiary nomination — and each can
 * fail on its own, so an adviser needs to read which half worked rather than a single success
 * that would claim more than happened. `skipped` is a SUCCESS: it means the identifier already
 * existed, which is what makes pressing twice safe.
 */
export type MorganLloydPush = {
	adviser: { status: 'created' | 'skipped' | 'failed'; external_id: string | null; error?: string };
	contact: { status: 'created' | 'skipped'; external_id: string | null };
	accounts: {
		account_id: number;
		account_uid: string;
		status?: 'created' | 'skipped' | 'failed';
		scheme_reference?: string | null;
		error?: string;
		policy_status?: 'created' | 'skipped' | 'failed';
		policy_reference?: string | null;
		policy_error?: string;
		beneficiaries?: {
			uid: string;
			name: string;
			status: 'created' | 'skipped' | 'failed';
			external_id?: string;
			error?: string;
		}[];
	}[];
	warnings: string[];
};
