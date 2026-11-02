export type ClassificationMatch = {
	contact_types?: string[];
	any_account_service_types?: string[];
	account_service_types?: string[];
	min_value?: number | null;
	max_value?: number | null;
};

export type ClassificationCapabilities = {
	money_movement?: boolean;
};

export type ClientClassification = {
	uid: string;
	name: string;
	slug: string;
	sort_order: number;
	is_default: boolean;
	match: ClassificationMatch;
	capabilities: ClassificationCapabilities;
};

export type ClassificationDraft = {
	uid?: string;
	name: string;
	slug: string;
	sort_order: number;
	is_default: boolean;
	match: ClassificationMatch;
	capabilities: ClassificationCapabilities;
};

const SERVICE_LABEL: Record<string, string> = {
	advised: 'advised',
	execution_only: 'execution-only',
	discretionary: 'discretionary'
};

function money(value: number): string {
	return new Intl.NumberFormat('en-GB', {
		style: 'currency',
		currency: 'GBP',
		maximumFractionDigits: 0
	}).format(value);
}

function list(items: string[] | undefined, labels: Record<string, string> = {}): string {
	if (!items?.length) return '';
	return items.map((item) => labels[item] ?? item).join(', ');
}

/** One line a studio row can show instead of dumping the JSON predicates. */
export function matchSummary(match: ClassificationMatch | undefined | null): string {
	if (!match) return 'Everyone';
	const parts: string[] = [];
	const types = list(match.contact_types);
	if (types) parts.push(types);
	const any = list(match.any_account_service_types, SERVICE_LABEL);
	if (any) parts.push(`any ${any} account`);
	const all = list(match.account_service_types, SERVICE_LABEL);
	if (all) parts.push(`only ${all} accounts`);
	if (match.min_value != null) parts.push(`from ${money(match.min_value)}`);
	if (match.max_value != null) parts.push(`up to ${money(match.max_value)}`);
	return parts.length ? parts.join(' · ') : 'Everyone';
}

export function emptyDraft(sortOrder = 0): ClassificationDraft {
	return {
		name: '',
		slug: '',
		sort_order: sortOrder,
		is_default: false,
		match: {},
		capabilities: { money_movement: false }
	};
}

export function draftFrom(row: ClientClassification): ClassificationDraft {
	return {
		uid: row.uid,
		name: row.name,
		slug: row.slug,
		sort_order: row.sort_order,
		is_default: row.is_default,
		match: { ...row.match },
		capabilities: { money_movement: Boolean(row.capabilities.money_movement) }
	};
}
