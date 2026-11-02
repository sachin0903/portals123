/**
 * The platform's focused record sidebar, re-exported for operate-as-firm.
 *
 * The section builders live in `@graphene/ui/nav` and take a `prefix`, so a client or account
 * opened under `/tenants/{uid}/operate` shows the same sections, in the same order, as the
 * platform. `docs/design-language.md` has the no-drift rule.
 */
import type { NavContext } from '@graphene/ui';
import { focusContext, type SidebarFocus } from '@graphene/ui/nav';
import BuildingIcon from '@lucide/svelte/icons/building-2';

export {
	accountSidebarFocus,
	clientSidebarFocus,
	focusContext,
	focusedNavWithPinned,
	parseFocus,
	type SidebarFocus
} from '@graphene/ui/nav';

/** Where every operate-as-firm route lives for one tenant. */
export const operateBase = (tenantUid: string): string => `/tenants/${tenantUid}/operate`;

/** What the scope header needs of a firm — the `[uid]` layout's `tenant` plus its icon. */
export interface FirmScope {
	name: string;
	/** The firm's platform-portal icon, when it has set one (`tenantIconUrl` in the layout). */
	iconUrl?: string | null;
}

/**
 * The sidebar header for a firm the operator has drilled into.
 *
 * The platform focuses its sidebar on a record — a header naming the client, a back link, then
 * that record's sections. Drilling into a firm is the same shape one level up: the header names
 * the FIRM (its own mark, or a building when it has none), the back link is the
 * tenant list, and the nav beneath is the firm's. That is why the tenant nav no longer carries an
 * "All tenants" row — the header's back link is the escape, exactly as "Dashboard" is on the
 * platform's client header.
 *
 * With a record focused inside the firm, the record keeps its own header — sections, back to
 * the firm's dashboard — and the eyebrow names the firm (`Client · Acme Wealth`), so the scope
 * never leaves the sidebar even when the tile does.
 */
export function firmContext(firm: FirmScope, focus: SidebarFocus | null): NavContext {
	if (focus) {
		const record = focusContext(focus);
		return {
			...record,
			eyebrow: record.eyebrow ? `${record.eyebrow} · ${firm.name}` : firm.name
		};
	}
	return {
		eyebrow: 'Firm',
		title: firm.name,
		back: { href: '/tenants', label: 'All tenants' },
		...(firm.iconUrl ? { image: firm.iconUrl } : {}),
		icon: BuildingIcon
	};
}
