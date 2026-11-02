export type TermItem = {
	uid: string;
	owner: string;
	code: string;
	title: string;
	audience: string;
	required_at: string[];
	applicability: Record<string, unknown>;
	status: string;
	body_kind: string;
	published_at: string | null;
	current_version_uid: string | null;
	current_version_number: number | null;
	current_version_published_at: string | null;
	has_file: boolean;
	created_at: string;
	updated_at: string;
	body_md?: string | null;
	body_html?: string | null;
	file_content_type?: string | null;
	file_size?: number | null;
};

export type TermAcceptance = {
	uid: string;
	term_uid: string;
	title: string;
	code: string;
	version_number: number;
	version_uid: string;
	accepted_at: string;
	channel: string;
};

/** A product type the applicability can key off, as the editor offers it. */
export type ProductChoice = { value: string; label: string; onShelf: boolean };

/**
 * The product types a term can be scoped to: the firm's OWN enabled shelf, not a fixed wrapper list.
 *
 * `applies()` compares the spec's `products` against `crm.accounts.product` — the `product_type`
 * enum — so the shelf is the only honest source of what a term could ever match. A hardcoded
 * ISA/JISA/GIA/SIPP list offered a firm scopes that can never fire (it has no JISA) and hid the ones
 * that can (a bond, an offshore wrapper).
 *
 * `selected` is merged in so a scope saved before a product left the shelf still renders — it is
 * live on existing accounts, and dropping the checkbox would silently widen the term on next save.
 */
export function productChoices(
	products: { product_type?: string | null; enabled?: boolean }[],
	selected: string[] = []
): ProductChoice[] {
	const shelf = new Set(
		products
			.filter((product) => product.enabled !== false)
			.flatMap((product) => {
				const type = product.product_type?.trim();
				return type ? [type] : [];
			})
	);
	return [...new Set([...shelf, ...selected.filter(Boolean)])]
		.sort((a, b) => a.localeCompare(b, 'en-GB', { numeric: true, sensitivity: 'base' }))
		.map((value) => ({
			value,
			label: value.replace(/_/g, ' ').replace(/^./, (letter) => letter.toUpperCase()),
			onShelf: shelf.has(value)
		}));
}

export type TermDraft = {
	title: string;
	audience: 'client';
	required_at: string[];
	body_kind: string;
	body_md: string;
	applicability: {
		always: boolean;
		products: string[];
		has_transfer: boolean;
	};
};

/** Turn the editor's explicit all/conditional choice into the API shape. A conditional scope with
 * no conditions must not fall through to the API's backwards-compatible "empty means all" rule. */
export function termDraftFromForm(form: FormData): TermDraft | { error: string } {
	const requiredAt = form.getAll('required_at').map(String).filter(Boolean);
	if (requiredAt.length === 0) return { error: 'Choose when clients must agree to these terms.' };

	const scopeMode = String(form.get('scope_mode') ?? 'all');
	const products = form.getAll('products').map(String).filter(Boolean);
	const hasTransfer = form.get('has_transfer') === 'on';
	if (scopeMode === 'conditions' && products.length === 0 && !hasTransfer) {
		return { error: 'Choose at least one product or the transfer condition.' };
	}

	return {
		title: String(form.get('title') ?? '').trim(),
		audience: 'client',
		required_at: requiredAt,
		body_kind: String(form.get('body_kind') ?? 'html'),
		body_md: String(form.get('body_md') ?? ''),
		applicability: {
			always: scopeMode === 'all',
			products: scopeMode === 'conditions' ? products : [],
			has_transfer: scopeMode === 'conditions' && hasTransfer
		}
	};
}
