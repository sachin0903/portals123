/** Graphene custody → SEI subfirm on the tenant connection.

The operator picks optional onshore/offshore orgs on the SEI enablement card. Blank is
onshore / the whole firm. Labels ending ` - Onshore` / ` - Offshore` are used only to
recover a map from a legacy `subfirm_ids` slice.
*/

export type Custody = 'onshore' | 'offshore';

export type SubfirmOption = {
	value: string;
	label: string;
};

const CUSTODIES: Custody[] = ['onshore', 'offshore'];

export function custodyFromLabel(label: string | null | undefined): Custody | null {
	const text = (label ?? '').trim();
	if (!text) return null;
	if (/(?:^|[\s\-–—])offshore\s*$/i.test(text)) return 'offshore';
	if (/(?:^|[\s\-–—])onshore\s*$/i.test(text)) return 'onshore';
	return null;
}

export function inferCustodyOrgs(
	subfirms: readonly SubfirmOption[],
	selectedIds: readonly string[] = []
): Partial<Record<Custody, string>> {
	const selected = selectedIds.map((id) => id.trim()).filter(Boolean);
	const pool = new Set(
		selected.length ? selected : subfirms.map((row) => row.value).filter(Boolean)
	);
	const labelled: Partial<Record<Custody, string>> = {};
	for (const row of subfirms) {
		if (!pool.has(row.value)) continue;
		const custody = custodyFromLabel(row.label);
		if (custody && !labelled[custody]) labelled[custody] = row.value;
	}
	if (Object.keys(labelled).length) return labelled;
	if (pool.size === 1) return { onshore: [...pool][0] };
	return {};
}

export const CUSTODY_KEYS = CUSTODIES;
export const UNASSIGNED_ORG = '__unassigned__';

export function subfirmIdsFromCustodyOrgs(orgs: Partial<Record<Custody, string>>): string[] {
	const ids: string[] = [];
	for (const key of CUSTODY_KEYS) {
		const id = (orgs[key] ?? '').trim();
		if (id && id !== UNASSIGNED_ORG && !ids.includes(id)) ids.push(id);
	}
	return ids;
}

/** Prefer a saved write map; otherwise recover one from a legacy `subfirm_ids` slice. */
export function hydrateCustodyOrgs(
	saved: Partial<Record<Custody, string>>,
	subfirms: readonly SubfirmOption[],
	legacySubfirmIds: readonly string[] = []
): Partial<Record<Custody, string>> {
	const attached: Partial<Record<Custody, string>> = {};
	for (const key of CUSTODY_KEYS) {
		const id = (saved[key] ?? '').trim();
		if (id && id !== UNASSIGNED_ORG) attached[key] = id;
	}
	if (attached.onshore || attached.offshore) return attached;
	if (legacySubfirmIds.some((id) => id.trim())) {
		return inferCustodyOrgs(subfirms, legacySubfirmIds);
	}
	return {};
}
