/**
 * The words the Change model drawer uses to say what is about to happen.
 *
 * Pure, so the sentence can be pinned without rendering a portalled Sheet. The drawer used to
 * show a picker and nothing else: the adviser could not see which model the account was on, and
 * the button said "Change model" whether they were replacing one, adding one or clearing one.
 */
import type { CatalogModel } from '../clients/types.js';

export const NO_MODEL = 'No model';

/** A percentage-point figure off the record as `6.5%`. Numeric strings arrive via JSON. */
export function asRate(value: number | string | null | undefined): string | null {
	if (value === null || value === undefined || value === '') return null;
	const n = Number(value);
	if (!Number.isFinite(n)) return null;
	return `${Number(n.toFixed(4))}%`;
}

/** The facts worth stating beside a model's name, in reading order. Absent facts are skipped. */
export function modelFacts(model: CatalogModel | null | undefined): [string, string][] {
	if (!model) return [];
	const rows: [string, string][] = [];
	if (model.risk_rating != null && String(model.risk_rating).trim() !== '')
		rows.push(['Risk rating', String(model.risk_rating)]);
	const rate = asRate(model.target_return);
	if (rate) rows.push(['Target return', `${rate} a year`]);
	const charge = asRate(model.model_charge);
	if (charge) rows.push(['Model charge', `${charge} a year`]);
	return rows;
}

/** What the save will do, stated with both names. `null` while nothing has changed. */
export function modelChangeSummary(
	currentName: string | null,
	nextName: string | null,
	changed: boolean
): string | null {
	if (!changed) return null;
	if (nextName && currentName) return `${nextName} replaces ${currentName}.`;
	if (nextName) return `${nextName} is added. The account has no model at the moment.`;
	if (currentName) return `${currentName} is removed. The account will have no model.`;
	return null;
}

/** The button names the act: replacing, adding or removing a mandate. */
export function modelChangeAction(currentName: string | null, nextName: string | null): string {
	if (nextName && currentName) return 'Replace model';
	if (nextName) return 'Add model';
	if (currentName) return 'Remove model';
	return 'Change model';
}
