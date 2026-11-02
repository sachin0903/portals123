/**
 * The platform sidebar's information architecture, as data.
 *
 * This was inline in `routes/+layout.svelte` as a nest of `can(...) ? {…} : null` ternaries. Two
 * reasons it now lives here:
 *
 *  1. **The permission map is the security surface.** A nav item that loses its gate is a 403 trap
 *     (it renders for someone the route will refuse); one that gains a gate silently disappears for
 *     someone who is entitled to it. Neither is caught by a type-checker, so the mapping is pure and
 *     unit-tested — see `nav.test.ts`, which pins the gate of every item in a table.
 *  2. The IA itself is now five-tier — Dashboard (the hub), three daily-work groups, and **Firm
 *     settings** (what you configure once, pinned to the bottom and `solo`). Firm settings is split
 *     into four visual subsections so fourteen unrelated rows do not read as one flat list. The
 *     disclosure / pinning / exclusivity flags are part of the group data rather than markup.
 *
 * Daily-work groups: Investments (the book), Pensions (contributions), Work (cases and ops).
 *
 * | Item          | Group          | Gate                    |
 * | ------------- | -------------- | ----------------------- |
 * | Dashboard     | —              | none                    |
 * | Clients       | Investments    | none                    |
 * | Accounts      | Investments    | none                    |
 * | Transactions  | Investments    | none                    |
 * | Transfers     | Investments    | none                    |
 * | Reports       | Investments    | `reports:read`          |
 * | Contributions | Pensions       | `contributions:read`    |
 * | Journeys      | Work           | `cases:read`            |
 * | Reviews       | Work           | none                    |
 * | Messages      | Work           | `messages:read`         |
 * | Documents     | Work           | `documents:read` AND `documents:show` |
 * | Team          | Firm settings  | `teams:read`            |
 * | Profiles      | Firm settings  | `profiles:read`         |
 * | Roles         | Firm settings  | `roles:write`           |
 * | Actions       | Firm settings  | `actions:read`          |
 * | Studio        | Firm settings  | `branding:write`        |
 * | Types         | Firm settings  | `document_types:read`   |
 * | Flows         | Firm settings  | `flows:read`            |
 * | Automations   | Firm settings  | `automations:read`      |
 * | Templates     | Firm settings  | `templates:read`        |
 * | Emails        | Firm settings  | `email_templates:read`  |
 * | Content       | Firm settings  | `content:read`          |
 * | Terms         | Firm settings  | `terms:read`            |
 * | Forms         | Firm settings  | `forms:read`            |
 * | Datasets      | Firm settings  | `datasets:read`         |
 * | Communications| Firm settings  | `activity:read`         |
 * | Activity      | Firm settings  | `activity:read`         |
 * | Data sources  | Firm settings  | `data_sources:read`     |
 *
 * A nav item is shown only when the caller can read the page behind it — hiding a tab the route
 * would 403 rather than dangling a trap. `Documents`/`Templates`/`Forms` now carry the same
 * `:read` gate their own loaders enforce (they were ungated here before, relying on the route
 * gate — a documents/templates/forms:read-less user reached the tab and hit an empty/403 page).
 *
 * `Transactions` and `Transfers` stay UNGATED on purpose: their routes enforce no read permission
 * (any authenticated staffer can open them — they are the firm's own book), so there is nothing to
 * gate the tab on; hiding them would hide a page the user can actually read. Gating them would mean
 * first adding a `transactions:read`/`transfers:read` permission to the routes — a product decision.
 * `Reviews` is ungated for the same reason: who may run or approve an action is the role list on
 * that action, and an empty list means anyone.
 *
 * The only *visibility* change beyond that is Flows: it was in the user popout (`menuItems`) rather
 * than the sidebar, on the same `flows:read` gate, and the restructure moves it into Firm settings.
 *
 * **Team/Profiles/Types are gated on their own resources now, not `members:*`/`documents:admin`.**
 * `teams:read`/`profiles:read`/`document_types:read` (+ their `:write` counterparts) are NAV-LAYER
 * splits only — `GET/POST/PUT/PATCH/DELETE /members` still accepts the old `members:read`/`write`
 * too (`require_any_permission`), so a role holding only the legacy permission keeps working; the
 * new resources just let a role see (or manage) Team without Profiles, or vice versa, which the
 * shared endpoint never distinguished before.
 *
 * **`firm_settings:show` gates the WHOLE group, on top of each item's own gate.** Every row above
 * still needs its own permission to appear; `firm_settings:show` is an additional AND across the
 * lot — a role missing it sees no Firm settings section at all, regardless of what else it holds.
 * This is the one deliberate group-level gate in the whole nav (contrast the doc comment on
 * `buildNav` below: every OTHER group's visibility is emergent from its children, never explicit).
 *
 * **`documents:show` is the same idea for one row.** Documents needs it on top of
 * `documents:read`: a role with `documents:read` alone reads documents on a client's record (the
 * Documents tab, the record, downloads) but has no firm-wide library — neither the row nor the
 * page (`routes/documents/+page.server.ts` refuses it too).
 */

import type { Component } from 'svelte';
import type { NavGroup, NavItem } from '@graphene/ui';
import { can } from '@graphene/api-client/permissions';
import LayoutDashboardIcon from '@lucide/svelte/icons/layout-dashboard';
import UsersIcon from '@lucide/svelte/icons/users';
import WalletIcon from '@lucide/svelte/icons/wallet';
import BanknoteIcon from '@lucide/svelte/icons/banknote';
import ChartColumnIcon from '@lucide/svelte/icons/chart-column';
import PiggyBankIcon from '@lucide/svelte/icons/piggy-bank';
import ArrowLeftRightIcon from '@lucide/svelte/icons/arrow-left-right';
import WorkflowIcon from '@lucide/svelte/icons/workflow';
import EyeIcon from '@lucide/svelte/icons/scan-eye';
import FileTextIcon from '@lucide/svelte/icons/file-text';
import UserCogIcon from '@lucide/svelte/icons/user-cog';
import ContactRoundIcon from '@lucide/svelte/icons/contact-round';
import ShieldIcon from '@lucide/svelte/icons/shield-check';
import PaletteIcon from '@lucide/svelte/icons/palette';
import TagsIcon from '@lucide/svelte/icons/tags';
import RouteIcon from '@lucide/svelte/icons/route';
import ZapIcon from '@lucide/svelte/icons/zap';
import LayoutTemplateIcon from '@lucide/svelte/icons/layout-template';
import NewspaperIcon from '@lucide/svelte/icons/newspaper';
import FileSignatureIcon from '@lucide/svelte/icons/file-signature';
import ListChecksIcon from '@lucide/svelte/icons/list-checks';
// The admin portal's Platforms section uses the same icon: it is the same subject seen from
// the other side, and an operator moving between portals should recognise it.
import NetworkIcon from '@lucide/svelte/icons/network';
import ActivityIcon from '@lucide/svelte/icons/activity';
import MailIcon from '@lucide/svelte/icons/mail';
import MessageCircleIcon from '@lucide/svelte/icons/message-circle';
import CirclePlayIcon from '@lucide/svelte/icons/circle-play';
import { PLATFORM_NAV_STRUCTURE, type TenantCapabilityId } from '@graphene/ui/nav';

export {
	FOCUS_ICONS,
	focusContext,
	focusedNavWithPinned,
	focusNav,
	parseFocus,
	type FocusSection,
	type SidebarFocus
} from '@graphene/ui/nav';

/** The signed-in principal, as the layout has it from `+layout.server.ts`. */
export interface NavPrincipal {
	permissions: readonly string[];
	/** Passed through to `can` for call-site compatibility; `can` deliberately does not trust it. */
	role: string | null;
	/** Any conversation this adviser hasn't read yet (the exact same staff-read-receipt
	 * definition the DM inbox's own per-row dots use — see `GET /messaging/unread`). Seeded once
	 * from the layout load, then kept fresh by a poll — see `+layout.svelte`. */
	chatsUnread?: boolean;
}

/** Per-concept href / icon / gate. The structure file owns order and sections; this map owns
 *  portal-local routing and the permission that reveals each row. */
type ConceptNav = {
	label: string;
	href: string;
	icon: Component;
	/** `null` = ungated. A list means any one slug reveals the row (Templates). */
	gate: string | readonly string[] | null;
	exact?: boolean;
};

const CONCEPT_NAV: Record<TenantCapabilityId, ConceptNav> = {
	dashboard: { label: 'Dashboard', href: '/', icon: LayoutDashboardIcon, gate: null },
	clients: { label: 'Clients', href: '/clients', icon: UsersIcon, gate: null },
	accounts: { label: 'Accounts', href: '/accounts', icon: WalletIcon, gate: null },
	transactions: { label: 'Transactions', href: '/transactions', icon: BanknoteIcon, gate: null },
	transfers: { label: 'Transfers', href: '/transfers', icon: ArrowLeftRightIcon, gate: null },
	reports: { label: 'Reports', href: '/reports', icon: ChartColumnIcon, gate: 'reports:read' },
	contributions: {
		label: 'Contributions',
		href: '/contributions',
		icon: PiggyBankIcon,
		gate: 'contributions:read'
	},
	journeys: { label: 'Journeys', href: '/journeys', icon: WorkflowIcon, gate: 'cases:read' },
	reviews: { label: 'Reviews', href: '/reviews', icon: EyeIcon, gate: null },
	messages: { label: 'Messages', href: '/chats', icon: MessageCircleIcon, gate: 'messages:read' },
	documents: { label: 'Documents', href: '/documents', icon: FileTextIcon, gate: 'documents:read' },
	team: { label: 'Team', href: '/team', icon: UserCogIcon, gate: 'teams:read', exact: true },
	profiles: { label: 'Profiles', href: '/profiles', icon: ContactRoundIcon, gate: 'profiles:read' },
	roles: { label: 'Roles', href: '/team/roles', icon: ShieldIcon, gate: 'roles:write' },
	actions: {
		label: 'Actions',
		href: '/actions',
		icon: CirclePlayIcon,
		gate: 'actions:read'
	},
	studio: { label: 'Studio', href: '/branding', icon: PaletteIcon, gate: 'branding:write' },
	forms: { label: 'Forms', href: '/forms', icon: ListChecksIcon, gate: 'forms:read' },
	flows: { label: 'Flows', href: '/flows', icon: RouteIcon, gate: 'flows:read' },
	automations: {
		label: 'Automations',
		href: '/automations',
		icon: ZapIcon,
		gate: 'automations:read'
	},
	// ONE ROW FOR BOTH KINDS. Shown on either gate; href is resolved in `buildNav` so a role with
	// only `email_templates:read` lands on the emails tab rather than an empty documents tab.
	templates: {
		label: 'Templates',
		href: '/templates',
		icon: LayoutTemplateIcon,
		gate: ['templates:read', 'email_templates:read']
	},
	content: { label: 'Content', href: '/content', icon: NewspaperIcon, gate: 'content:read' },
	terms: { label: 'Terms', href: '/terms', icon: FileSignatureIcon, gate: 'terms:read' },
	emails: {
		label: 'Emails',
		href: '/emails',
		icon: MailIcon,
		gate: 'activity:read'
	},
	'document-types': {
		label: 'Types',
		href: '/documents/types',
		icon: TagsIcon,
		gate: 'document_types:read'
	},
	activity: {
		label: 'Activity',
		href: '/activity',
		icon: ActivityIcon,
		gate: 'activity:read'
	},
	'data-sources': {
		label: 'Data sources',
		href: '/data-sources',
		icon: NetworkIcon,
		gate: 'data_sources:read'
	}
};

/**
 * The sidebar groups for a principal, in render order: Dashboard, the three daily-work groups,
 * then Firm settings (pinned). Group order and Firm settings sections come from
 * `@graphene/ui/nav`; gates and hrefs stay here.
 *
 * A group with no visible items is dropped entirely — an empty heading reads as a section
 * that failed to load. Pensions and Work are fully gated, so they drop for a member with
 * no permissions; Investments stays because the book listings are ungated.
 */
export function buildNav({ permissions, role, chatsUnread }: NavPrincipal): NavGroup[] {
	const allowed = (slug: string) => can(permissions, role, slug);
	const firmSettingsVisible = allowed('firm_settings:show');

	return PLATFORM_NAV_STRUCTURE.map((group) => {
		const items: NavItem[] = [];
		for (const entry of group.items) {
			if (group.id === 'firm-settings' && !firmSettingsVisible) continue;
			const concept = CONCEPT_NAV[entry.id];
			const gate = concept.gate;
			const gated =
				gate === null
					? true
					: typeof gate === 'string'
						? allowed(gate)
						: gate.some((slug) => allowed(slug));
			if (!gated) continue;
			if (entry.id === 'documents' && !allowed('documents:show')) continue;

			let href = concept.href;
			if (entry.id === 'templates') {
				href = allowed('templates:read') ? '/templates' : '/templates?kind=emails';
			}

			items.push({
				label: concept.label,
				href,
				icon: concept.icon,
				...(concept.exact ? { exact: true } : {}),
				...(entry.section ? { section: entry.section } : {}),
				...(entry.id === 'messages' ? { unread: chatsUnread ?? false } : {})
			});
		}
		return {
			...(group.label ? { label: group.label } : {}),
			...(group.collapsible ? { collapsible: true } : {}),
			...(group.defaultOpen != null ? { defaultOpen: group.defaultOpen } : {}),
			...(group.pinned ? { pinned: true } : {}),
			...(group.solo ? { solo: true } : {}),
			items
		} satisfies NavGroup;
	}).filter((g) => g.items.length > 0);
}
