/**
 * Presentation helpers internal to the account-book surfaces (deliberately NOT exported from
 * the barrel — the apps keep their own `$lib/format`).
 *
 * `clientName` has ONE implementation, in book/clients (exported from the package root); it is
 * re-exported here so the account surfaces keep their local import path.
 */

export { clientName, providerReference } from '../clients/format.js';

/** A transaction's reference as a human should read it. `crm.py`'s listing query synthesises
 *  `t.uid` as `source_system:source_transaction_id` (e.g. `seccl:AB12345`) — there is no `uid`
 *  column on `platform.transactions` itself, just the two source columns joined for display/
 *  keying. Drop the provider name for display; keep everything after the FIRST colon verbatim
 *  in case a provider's own id legitimately contains one.
 *
 *  A Graphene-originated row's id is that origin's public id. Rows written before that still
 *  carry `gxn:{origin}:{uid}`; the reference is the id either way. */
const GRAPHENE_KEY = /(?:^|:)gxn:[a-z]+:([^:]+)$/;

export function transactionReference(uid: string): string {
	const graphene = GRAPHENE_KEY.exec(uid)?.[1];
	if (graphene) return graphene;
	const i = uid.indexOf(':');
	return i === -1 ? uid : uid.slice(i + 1);
}

/** Badge variants for account/transaction lifecycle statuses (shared across the surfaces). */
export const STATUS_VARIANT: Record<string, 'secondary' | 'outline' | 'destructive'> = {
	active: 'secondary',
	draft: 'outline',
	closed: 'destructive',
	settled: 'secondary',
	pending: 'outline',
	failed: 'destructive',
	cancelled: 'destructive'
};
