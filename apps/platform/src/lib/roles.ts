import type { OrgRole } from './api';

/** The role NAME goes in `label`, the description in `hint` — both old copies had it backwards,
 *  putting the description in `label` so the list read as truncated sentences, not names. */
export function roleOptions(roles: OrgRole[]): { value: string; label: string; hint?: string }[] {
	return roles.map((role) => ({
		value: role.slug,
		label: role.name,
		hint: role.description ?? undefined
	}));
}
