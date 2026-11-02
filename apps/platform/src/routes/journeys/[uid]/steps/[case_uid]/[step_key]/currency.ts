export type JourneyCurrency = {
	code: string;
	is_base: boolean;
};

/** Narrow the firm's currencies to the product, retaining a usable default when its base is out. */
export function productCurrencies<T extends JourneyCurrency>(
	currencies: T[],
	allowed: unknown
): T[] {
	if (!Array.isArray(allowed) || allowed.length === 0) return currencies;
	const codes = allowed.map(String);
	const filtered = currencies
		.filter((currency) => codes.includes(currency.code))
		.sort((a, b) => codes.indexOf(a.code) - codes.indexOf(b.code));
	const defaultCode =
		filtered.find((currency) => currency.is_base)?.code ?? filtered[0]?.code ?? null;
	return filtered
		.map((currency) => ({ ...currency, is_base: currency.code === defaultCode }) as T)
		.sort((a, b) => Number(b.is_base) - Number(a.is_base));
}

/** Select the firm's base on a blank currency field without overwriting stored execution evidence. */
export function withBaseCurrency(
	answers: Record<string, unknown> | null | undefined,
	asksForCurrency: boolean,
	currencies: JourneyCurrency[]
): Record<string, unknown> {
	const next = { ...(answers ?? {}) };
	if (asksForCurrency && !String(next.currency ?? '').trim()) {
		const base = currencies.find((currency) => currency.is_base)?.code;
		if (base) next.currency = base;
	}
	return next;
}
