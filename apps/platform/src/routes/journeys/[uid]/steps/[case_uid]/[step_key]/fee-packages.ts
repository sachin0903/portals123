import { describeFeeCharge, feePackageOptionLabel } from '@graphene/ui/book/journeys';

export type FeePackageOption = {
	id: number;
	model_id?: number | null;
	name?: string;
	access?: string | null;
	is_default?: boolean;
	fee_type?: string | null;
	frequency?: string | null;
	rule?: unknown;
	currency?: string | null;
};

function selectedModelId(value: unknown): number | null {
	if (value === null || value === undefined || value === '') return null;
	const id = Number(value);
	return Number.isInteger(id) ? id : null;
}

/** A model-scoped fee must not appear until that model is selected.
 *
 * The API validates the final pair, but the account form chooses both values in one edit. Returning
 * every model's fees lets the picker construct a request that the API can only refuse.
 */
export function feesForModel<T extends FeePackageOption>(
	fees: T[] | undefined,
	modelValue: unknown
): T[] {
	const modelId = selectedModelId(modelValue);
	return (fees ?? []).filter((fee) => fee.model_id == null || fee.model_id === modelId);
}

export function isDisplayFee(fee: FeePackageOption): boolean {
	const access = (fee.access ?? 'hidden').toLowerCase();
	if (access === 'display') return true;
	return access === 'hidden' && fee.is_default === true;
}

export function isSelectFee(fee: FeePackageOption): boolean {
	return (fee.access ?? '').toLowerCase() === 'select';
}

export function displayFees<T extends FeePackageOption>(fees: T[]): T[] {
	return fees.filter(isDisplayFee);
}

export function selectFees<T extends FeePackageOption>(fees: T[]): T[] {
	return fees.filter(isSelectFee);
}

/** Drop choices hidden by a model change so an invisible stale id is never submitted. */
export function availableFeeIds(value: unknown, fees: FeePackageOption[]): string[] {
	if (!Array.isArray(value)) return [];
	const allowed = new Set(fees.map((fee) => String(fee.id)));
	return value.map(String).filter((id) => allowed.has(id));
}

/** Display packages always apply; selectable ids follow. Display ids win if both lists name one. */
export function unionDisplayFeeIds(selected: unknown, display: FeePackageOption[]): string[] {
	const displayIds = display.map((fee) => String(fee.id));
	const seen = new Set(displayIds);
	const extra = Array.isArray(selected)
		? selected.map(String).filter((id) => id && !seen.has(id))
		: [];
	return [...displayIds, ...extra];
}

export function feeChargeLabel(fee: FeePackageOption): string {
	return describeFeeCharge(fee).replaceAll(' — ', ': ');
}

export function selectableFeeOption<T extends FeePackageOption>(fee: T): T & { label: string } {
	return {
		...fee,
		label: feePackageOptionLabel({
			id: fee.id,
			name: fee.name ?? `Fee ${fee.id}`,
			provider: null,
			product_id: null,
			model_id: fee.model_id ?? null,
			fee_type: fee.fee_type,
			frequency: fee.frequency,
			rule: fee.rule,
			currency: fee.currency
		}).replaceAll(' — ', ': ')
	};
}
