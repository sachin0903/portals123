/**
 * The firm's account-card designs, handed from the portal root to every surface that paints an
 * account (the deck, the chip cluster, the account cards) without threading a prop through each
 * page block. The client layout provides the resolved branding's `card_designs`; the studio preview
 * provides the design being edited. With no provider every card wears the product defaults.
 *
 * A getter rather than a value, so a consumer reading it inside `$derived` follows the studio's
 * edits live.
 */
import { getContext, hasContext, setContext } from 'svelte';
import type { CardDesign } from '@graphene/auth';

type CardDesigns = Readonly<Record<string, CardDesign>> | null | undefined;

const KEY = Symbol('card-designs');

/** Provide the card designs for this subtree. Call during component initialisation. */
export function provideCardDesigns(get: () => CardDesigns): void {
	setContext(KEY, get);
}

/** The card designs in force here, as a getter. Call during component initialisation. */
export function useCardDesigns(): () => CardDesigns {
	return hasContext(KEY) ? getContext<() => CardDesigns>(KEY) : () => null;
}
