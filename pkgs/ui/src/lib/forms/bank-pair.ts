/**
 * A UK sort code and account number, checked together once both are complete.
 *
 * The pair is what the directory validates. A sort code on its own, or an account number
 * beside a different prefix (a pension contribution, for instance), is not a pair.
 */

export type BankFieldRef = {
	fieldKey: string;
	fieldType?: string | null;
};

export type BankPair = {
	prefix: string;
	sortKey: string;
	accountKey: string;
	nameKey: string | null;
	/** Whichever of the pair appears later. The check renders under that field. */
	anchorKey: string;
};

const SORT_LEAVES = new Set(['sort_code', 'sortCode']);
const ACCOUNT_LEAVES = new Set(['account_number', 'accountNumber']);

function leafOf(key: string): string {
	const dot = key.lastIndexOf('.');
	return dot < 0 ? key : key.slice(dot + 1);
}

function prefixOf(key: string): string | null {
	const dot = key.lastIndexOf('.');
	return dot < 0 ? null : key.slice(0, dot);
}

export function bankPairs(fields: BankFieldRef[]): BankPair[] {
	type Slot = {
		sort?: string;
		account?: string;
		name?: string;
		sortAt: number;
		accountAt: number;
	};
	const slots = new Map<string, Slot>();
	fields.forEach((field, index) => {
		const prefix = prefixOf(field.fieldKey);
		if (prefix == null) return;
		const leaf = leafOf(field.fieldKey);
		const type = field.fieldType ?? '';
		const slot = slots.get(prefix) ?? { sortAt: -1, accountAt: -1 };
		if (SORT_LEAVES.has(leaf) || type === 'sortCode') {
			slot.sort = field.fieldKey;
			slot.sortAt = index;
		} else if (
			ACCOUNT_LEAVES.has(leaf) ||
			type === 'bankAccountNumber' ||
			type === 'accountNumber'
		) {
			slot.account = field.fieldKey;
			slot.accountAt = index;
		} else if (leaf === 'bank_name' || leaf === 'bank') {
			slot.name = field.fieldKey;
		}
		slots.set(prefix, slot);
	});
	const pairs: BankPair[] = [];
	for (const [prefix, slot] of slots) {
		if (!slot.sort || !slot.account) continue;
		pairs.push({
			prefix,
			sortKey: slot.sort,
			accountKey: slot.account,
			nameKey: slot.name ?? null,
			anchorKey: slot.accountAt >= slot.sortAt ? slot.account : slot.sort
		});
	}
	return pairs;
}

/** The field a rejection is about: the sort code when the directory does not know it. */
export function rejectedKey(pair: BankPair, state: string | null | undefined): string {
	return state === 'sort_code_unknown' ? pair.sortKey : pair.accountKey;
}
