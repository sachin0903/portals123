/**
 * Which product types the Card designs panel lists: the firm's own shelf, not a fixed set of
 * wrappers. `platform.products` is one row per `product_type`, switched on or off — a firm that
 * does not offer a Junior ISA has no JISA card to design, and a third-party bond it does offer
 * does. LISA is not a member of `platform.product_type` at all.
 *
 * The card is keyed on the type code, because that is what an account card wears
 * (`/crm/home` sets `type` from `product_type`). The name is what the row is called.
 */

/** Display order, the vocabulary order. A type outside it sorts after, alphabetically. */
const TYPE_ORDER = ['GIA', 'ISA', 'JISA', 'SIPP', 'TPONBOND', 'TPOFFBOND', 'TPPENSION'] as const;

/**
 * What a wrapper is called when its shelf row has no name of its own. The enum member is a code.
 * The same words as the admin catalogue's `PRODUCT_TYPE_LABELS`.
 */
const TYPE_LABELS: Readonly<Record<string, string>> = {
	GIA: 'General Investment Account',
	ISA: 'Individual Savings Account',
	JISA: 'Junior Individual Savings Account',
	SIPP: 'Self-Invested Personal Pension',
	TPONBOND: 'Third-party Onshore Bond',
	TPOFFBOND: 'Third-party Offshore Bond',
	TPPENSION: 'Third-party Pension'
};

export interface CardProductType {
	/** The wrapper code the account card is keyed on. */
	type: string;
	/** What the row is called: the firm's own product name, else the type's label. */
	name: string;
}

/** The slice of a catalogue product this needs. `GET /catalog/products` returns it. */
export interface ShelfProduct {
	product_type?: string | null;
	name?: string | null;
	enabled?: boolean | null;
}

/** Enabled shelf rows, one per product type, in vocabulary order. */
export function cardProductTypes(products: readonly ShelfProduct[]): CardProductType[] {
	const byType = new Map<string, CardProductType>();
	for (const product of products) {
		if (product.enabled === false) continue;
		const type = product.product_type?.trim();
		if (!type || byType.has(type)) continue;
		const given = product.name?.trim();
		byType.set(type, {
			type,
			name: given && given !== type ? given : (TYPE_LABELS[type] ?? type)
		});
	}
	const rank = new Map<string, number>(TYPE_ORDER.map((type, index) => [type, index]));
	return [...byType.values()].sort((a, b) => {
		const ar = rank.get(a.type) ?? TYPE_ORDER.length;
		const br = rank.get(b.type) ?? TYPE_ORDER.length;
		return ar - br || a.type.localeCompare(b.type);
	});
}
