/** The clients listing's derived status. Derived, not stored: a client is a Lead until they
 *  hold an account, Suspended when the custodian says so, Closed once the firm closes them.
 *  Kept as one function so the badge, the filter and the sort can't disagree. */

export type ClientStatus = 'active' | 'lead' | 'suspended' | 'closed';

/** Provider words that mean the custodian has suspended the person. Seccl publishes `suspended`;
 *  Morgan Lloyd maps Dataverse inactive onto `inactive`. Case-insensitive on purpose: Seccl's
 *  raw payload is title case, the conformed column is already lower. */
const SUSPENDED = new Set(['suspended', 'inactive']);

export function clientStatus(c: {
	account_count: number;
	/** Accounts this person acts on as registered contact, without pretending they own the AUM. */
	represented_account_count?: number;
	deleted_at?: string | null;
	provider_status?: string | null;
}): ClientStatus {
	if (c.deleted_at) return 'closed';
	const raw = c.provider_status?.trim().toLowerCase();
	if (raw && SUSPENDED.has(raw)) return 'suspended';
	return c.account_count === 0 && !c.represented_account_count ? 'lead' : 'active';
}

/** Close is offered only when there is nothing to leave open: no accounts, or every account
 *  is worth zero. A client with money still on the book is not closeable from here. */
export function canCloseClient(accounts: { value?: number | null }[]): boolean {
	return accounts.every((a) => (a.value ?? 0) === 0);
}
