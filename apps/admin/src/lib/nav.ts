/**
 * The Internal Operations sidebar's GLOBAL information architecture, as data.
 *
 * This was thirteen `can(...) ? {…} : null` ternaries in one unlabelled group, inline in
 * `routes/+layout.svelte`. It moves here for the two reasons the platform's own `$lib/nav.ts`
 * gives, the first of which applies with more force on this surface:
 *
 *  1. **The permission map is the security surface.** An item that loses its gate is a 403 trap
 *     (it renders for an operator the route will refuse); one that gains a gate silently vanishes
 *     for somebody entitled to it. A type-checker sees neither, so the mapping is a pure function
 *     with every item's gate pinned in `nav.test.ts`. It matters more here than in the platform
 *     portal because admin's gates PARTITION the operator population — a pipeline engineer holds
 *     `platforms:*`, a compliance operator `aml:*`, an account manager `tenants:*` — so a wrong
 *     gate does not merely misplace a row, it hands one job's surface to another job.
 *  2. **Thirteen ungrouped rows is not an information architecture.** They were in the order they
 *     happened to be written in, so Model tenant (a provisioning preset) sat between AML and
 *     Contributions, and the two audit trails were separated from the AML queue they evidence.
 *
 * The groups are by SUBJECT, the same principle the platform portal uses (Investments / Pensions /
 * Work) rather than by page shape or by permission family:
 *
 *  - **Firms** — the tenants themselves, the people who log into them, and the preset a new one
 *    clones. Model tenant is here and not with Graphene's own settings because it is part of how a
 *    FIRM gets set up, not part of how we run.
 *  - **Data** — everything that gets information in, and the record of what we sent and were
 *    answered. Look-through is here and not with the custodians: it is an IFSL workbook import
 *    feeding the client and adviser portals, i.e. reference data arriving, which is the same
 *    errand as a custodian pipeline even though the transport is a spreadsheet.
 *  - **Money** — the cross-firm contribution and transfer workbenches.
 *  - **Oversight** — Graphene's supervisory function as the regulated platform provider: who our
 *    firms' clients are (AML screening is OUR responsibility, not the firm's — see docs/aml.md),
 *    what everybody did (`audit.actions`), and what changed (the record trail). `aml:read` and
 *    `audit:read` tend to be held by the same operator, so the group appears and disappears whole.
 *  - **Internal operations** — Graphene acting as the platform operator: our own staff and roles,
 *    and the estate-wide automation kill switch. Pinned to the bottom, as it already was.
 *
 * Groups are NOT named for the portal ("Operations") or for their own first row ("Platforms"), and
 * they do not repeat the pinned group's name — an earlier cut did all three and read as chrome that
 * had been generated rather than written.
 *
 * The table is the contract. A regrouping must not move a gate by accident; a deliberate change
 * (the Money rows taking `contributions:read` and `transfers:read`) is written here and in the test.
 *
 * | Item          | Group               | Gate                       |
 * | ------------- | ------------------- | -------------------------- |
 * | Dashboard     | —                   | `tenants:read`             |
 * | Tenants       | Firms               | `tenants:read`             |
 * | Users         | Firms               | `tenants:read`             |
 * | Model tenant  | Firms               | `model:read`               |
 * | Platforms     | Data                | `platforms:read`           |
 * | API log       | Data                | `platforms:read`           |
 * | Look-through  | Data                | `lookthrough:read`         |
 * | Integrations  | Data                | `integrations:read`        |
 * | Contributions | Money               | `contributions:read`       |
 * | Transfers     | Money               | `transfers:read`           |
 * | AML           | Oversight           | `aml:read`                 |
 * | Logs          | Oversight           | `audit:read`               |
 * | Audit log     | Oversight           | `audit:read`               |
 * | Staff         | Internal operations | `operations:read`          |
 * | Roles         | Internal operations | `operations:read`          |
 * | Terms         | Internal operations | `platform_terms:read`      |
 * | Reports       | Internal operations | `mi_reports:read`          |
 * | Automations   | Internal operations | `automations:global_pause` |
 *
 * Contributions and Transfers take the same read permissions the platform workbenches already
 * enforce (`contributions:read`, `transfers:read`). Both pages are still prototypes over mock
 * data; the gate is so a signed-in operator with no money permission does not land on them.
 *
 * The focused sidebars (inside a tenant, inside a platform) are deliberately NOT here. They are
 * derived from the URL and from `page.data`, so they belong to the layout that has both.
 */

import type { Component } from 'svelte';
import type { NavGroup, NavItem } from '@graphene/ui';
import { can } from '$lib/permissions';
import LayoutDashboardIcon from '@lucide/svelte/icons/layout-dashboard';
import BuildingIcon from '@lucide/svelte/icons/building-2';
import UsersIcon from '@lucide/svelte/icons/users';
// Our own staff take `user-cog` and the cross-tenant login directory keeps `users`. They were the
// same glyph, which is the worse of the two collisions this nav had: both rows are lists of people,
// so the icon was the only thing distinguishing "everybody who signs into any firm" from "the
// fifteen of us". The platform portal already spells its own staff row this way.
import UserCogIcon from '@lucide/svelte/icons/user-cog';
import CopyIcon from '@lucide/svelte/icons/copy';
import NetworkIcon from '@lucide/svelte/icons/network';
// The outbound-call log takes `webhook` rather than the `scroll-text` it used to share with Audit
// log. Two identical glyphs in one sidebar put two meanings on one symbol — the objection
// `@graphene/ui/nav`'s FOCUS_ICONS registry records — and of the two it is the API log
// that is not a document: it is every call we made to a custodian and what came back.
import WebhookIcon from '@lucide/svelte/icons/webhook';
import FileSpreadsheetIcon from '@lucide/svelte/icons/file-spreadsheet';
import BanknoteIcon from '@lucide/svelte/icons/banknote';
import ArrowLeftRightIcon from '@lucide/svelte/icons/arrow-left-right';
// `shield-check` is spoken for by Roles, so AML takes `shield-alert` — which is the truer icon for
// a sanctions/PEP queue anyway. Model tenant, which used to be the third claimant on the shield,
// takes `copy`: it is the preset every new firm clones.
import ShieldAlertIcon from '@lucide/svelte/icons/shield-alert';
import ActivityIcon from '@lucide/svelte/icons/activity';
import MailIcon from '@lucide/svelte/icons/mail';
import ScrollTextIcon from '@lucide/svelte/icons/scroll-text';
import BookOpenIcon from '@lucide/svelte/icons/book-open';
import ShieldIcon from '@lucide/svelte/icons/shield-check';
import OctagonXIcon from '@lucide/svelte/icons/octagon-x';
import HomeIcon from '@lucide/svelte/icons/house';
import WalletIcon from '@lucide/svelte/icons/wallet';
import BoxesIcon from '@lucide/svelte/icons/boxes';
import ChartPieIcon from '@lucide/svelte/icons/chart-pie';
import CoinsIcon from '@lucide/svelte/icons/coins';
import TypeIcon from '@lucide/svelte/icons/type';
import SettingsIcon from '@lucide/svelte/icons/settings';
import PaletteIcon from '@lucide/svelte/icons/palette';
import Trash2Icon from '@lucide/svelte/icons/trash-2';
import WorkflowIcon from '@lucide/svelte/icons/workflow';
import ListChecksIcon from '@lucide/svelte/icons/list-checks';
import ZapIcon from '@lucide/svelte/icons/zap';
import FileTextIcon from '@lucide/svelte/icons/file-text';
import LayoutTemplateIcon from '@lucide/svelte/icons/layout-template';
import NewspaperIcon from '@lucide/svelte/icons/newspaper';
import FileSignatureIcon from '@lucide/svelte/icons/file-signature';
import ChartColumnIcon from '@lucide/svelte/icons/chart-column';
// One glyph, one meaning — the rule the global nav's own icon test pins. Inside a firm the
// collisions are different rows, so four subjects take their own: a pension pot is not a
// transaction, a flow definition is not a journey in flight, the firm's own event stream is not
// the tenant's record trail, and a data source is not the WorkOS connection.
import PiggyBankIcon from '@lucide/svelte/icons/piggy-bank';
import RouteIcon from '@lucide/svelte/icons/route';
import TagsIcon from '@lucide/svelte/icons/tags';
import PlugIcon from '@lucide/svelte/icons/plug';
import HistoryIcon from '@lucide/svelte/icons/history';
import ReceiptIcon from '@lucide/svelte/icons/receipt';
import LandmarkIcon from '@lucide/svelte/icons/landmark';
import ContactRoundIcon from '@lucide/svelte/icons/contact-round';
import EyeIcon from '@lucide/svelte/icons/scan-eye';
import CirclePlayIcon from '@lucide/svelte/icons/circle-play';
import MessageCircleIcon from '@lucide/svelte/icons/message-circle';
import { TENANT_CAPABILITY_MANIFEST, tenantCapabilityHref } from '$lib/tenant-capabilities';
import { PLATFORM_NAV_STRUCTURE, type TenantCapabilityId } from '@graphene/ui/nav';

/** The signed-in operator, as the layout has it from `+layout.server.ts`. */
export interface NavPrincipal {
	permissions: readonly string[];
	/** Passed through to `can` for call-site compatibility; `can` deliberately does not trust it. */
	role: string | null;
}

/**
 * The cross-tenant sidebar groups for an operator, in render order: Dashboard, the four subject
 * groups, then Internal operations (pinned).
 *
 * A group with no visible items is dropped entirely — an empty heading reads as a section that
 * failed to load rather than as one the operator cannot see.
 */
export function buildGlobalNav({ permissions, role }: NavPrincipal): NavGroup[] {
	const allowed = (slug: string) => can(permissions, role, slug);

	return (
		[
			// The hub. Standalone and always first, like the platform's: it is where an operator
			// starts, so it is not inside a disclosure that could be shut.
			{
				items: allowed('tenants:read')
					? [{ label: 'Dashboard', href: '/', icon: LayoutDashboardIcon }]
					: []
			},
			{
				label: 'Firms',
				collapsible: true,
				defaultOpen: true,
				items: [
					allowed('tenants:read')
						? { label: 'Tenants', href: '/tenants', icon: BuildingIcon }
						: null,
					// The cross-tenant person directory. It was reachable only from one dashboard tile,
					// so an ops user had to know the URL existed.
					allowed('tenants:read') ? { label: 'Users', href: '/users', icon: UsersIcon } : null,
					// The role + email preset a new firm clones — how a firm gets set up, so it sits with
					// the firms rather than with Graphene's own configuration.
					allowed('model:read') ? { label: 'Model tenant', href: '/model', icon: CopyIcon } : null
				].filter((i) => i !== null)
			},
			{
				label: 'Data',
				collapsible: true,
				defaultOpen: true,
				items: [
					// `exact` because the outbound-call log lives UNDER this route (`/platforms/api`):
					// prefix matching lit both rows at once, and two active rows in one group reads as a
					// broken indicator.
					allowed('platforms:read')
						? { label: 'Platforms', href: '/platforms', icon: NetworkIcon, exact: true }
						: null,
					// Cross-provider, so it sits beside Platforms rather than inside one.
					allowed('platforms:read')
						? { label: 'API log', href: '/platforms/api', icon: WebhookIcon }
						: null,
					allowed('lookthrough:read')
						? { label: 'Look-through', href: '/look-through', icon: FileSpreadsheetIcon }
						: null,
					allowed('platforms:read')
						? { label: 'Illustrations', href: '/illustrations', icon: BookOpenIcon }
						: null,
					// SEI platform approvals, feature toggles and job runs -- not a custodian pipeline.
					allowed('integrations:read')
						? { label: 'Integrations', href: '/integrations', icon: PlugIcon }
						: null
				].filter((i) => i !== null)
			},
			{
				label: 'Money',
				collapsible: true,
				defaultOpen: true,
				items: [
					allowed('contributions:read')
						? { label: 'Contributions', href: '/contributions', icon: BanknoteIcon }
						: null,
					allowed('transfers:read')
						? { label: 'Transfers', href: '/transfers', icon: ArrowLeftRightIcon }
						: null
				].filter((i) => i !== null)
			},
			{
				label: 'Oversight',
				collapsible: true,
				defaultOpen: true,
				items: [
					// AML screening is Graphene's own regulatory responsibility, so the console is
					// cross-firm and lives in the global nav rather than under a tenant.
					allowed('aml:read') ? { label: 'AML', href: '/aml', icon: ShieldAlertIcon } : null,
					allowed('audit:read')
						? { label: 'Activity', href: '/activity', icon: ActivityIcon }
						: null,
					allowed('audit:read')
						? { label: 'Audit log', href: '/audit', icon: ScrollTextIcon }
						: null
				].filter((i) => i !== null)
			},
			// Graphene acting as the platform operator — pinned to the bottom, where it already was.
			//
			// `defaultOpen: true` and NOT the closed posture the platform gives Firm settings: that
			// group is fourteen rows and competes for a short viewport, this one is five. Closing it
			// would also put the estate-wide kill switch behind a disclosure, which is the one row here
			// that has to be reachable without a thought.
			//
			// No `solo` for the same reason — holding four subject groups shut for three rows would
			// cost more than it buys.
			{
				label: 'Internal operations',
				collapsible: true,
				defaultOpen: true,
				pinned: true,
				items: [
					allowed('operations:read')
						? { label: 'Staff', href: '/operations/users', icon: UserCogIcon }
						: null,
					allowed('operations:read')
						? { label: 'Roles', href: '/operations/roles', icon: ShieldIcon }
						: null,
					allowed('platform_terms:read')
						? { label: 'Terms', href: '/operations/terms', icon: FileSignatureIcon }
						: null,
					allowed('mi_reports:read')
						? { label: 'Reports', href: '/operations/reports', icon: ChartColumnIcon }
						: null,
					// The estate-wide automation kill switch. Its own row rather than a dashboard tile:
					// `automations:global_pause` is granted independently of `tenants:read`, so an
					// operator can hold the switch and not the dashboard it would have sat on — which is
					// also why this group can render for somebody who sees nothing else in it.
					allowed('automations:global_pause')
						? { label: 'Automations', href: '/automations', icon: OctagonXIcon }
						: null
				].filter((i) => i !== null)
			}
		] satisfies NavGroup[]
	).filter((g) => g.items.length > 0);
}

/**
 * Build the sidebar for one firm, as data.
 *
 * The operating groups are a projection of the platform portal's IA
 * (`@graphene/ui/nav`) — same labels, order, Firm settings sections — so an
 * operator learning the product sees one sidebar, not two. The firm itself is named by the sidebar's
 * scope header (with the way back to the tenant list); the admin Overview hub sits above the mirror;
 * a Graphene-only Configure group sits below Firm settings for tenant administration that has no
 * platform counterpart.
 *
 * Gates stay admin-native: `tenant_portal:read` for operate rows, `tenants:read` / `model:read` for
 * Overview and configure rows, plus the existing `platforms:read` / `book:read` / `audit:read`
 * overlays. There is no firm `firm_settings:show` on this surface.
 *
 * Paths come from `$lib/tenant-capabilities`. A capability marked unavailable emits no link.
 */
export function buildTenantNav(tenantUid: string, { permissions, role }: NavPrincipal): NavGroup[] {
	const allowed = (slug: string) => can(permissions, role, slug);
	const base = `/tenants/${tenantUid}`;
	const mayConfigure = allowed('tenants:read') || allowed('model:read');
	const mayOperate = allowed('tenant_portal:read');

	const ICONS: Record<TenantCapabilityId, Component> = {
		dashboard: LayoutDashboardIcon,
		clients: UsersIcon,
		accounts: WalletIcon,
		transactions: BanknoteIcon,
		transfers: ArrowLeftRightIcon,
		reports: ChartColumnIcon,
		contributions: PiggyBankIcon,
		journeys: WorkflowIcon,
		reviews: EyeIcon,
		messages: MessageCircleIcon,
		documents: FileTextIcon,
		team: UserCogIcon,
		profiles: ContactRoundIcon,
		roles: ShieldIcon,
		actions: CirclePlayIcon,
		studio: PaletteIcon,
		forms: ListChecksIcon,
		flows: RouteIcon,
		automations: ZapIcon,
		templates: LayoutTemplateIcon,
		content: NewspaperIcon,
		terms: FileSignatureIcon,
		emails: MailIcon,
		'document-types': TagsIcon,
		activity: ActivityIcon,
		'data-sources': PlugIcon
	};

	const capabilityItem = (id: TenantCapabilityId, section?: string): NavItem | null => {
		const capability = TENANT_CAPABILITY_MANIFEST[id];
		if (!capability.available) return null;
		if (capability.kind === 'operate' && !mayOperate) return null;
		if (capability.kind === 'configure' && !mayConfigure) return null;
		// Logs' loader enforces audit:read on top of the workspace permission.
		if (id === 'activity' && !allowed('audit:read')) return null;

		return {
			label: capability.label,
			href: tenantCapabilityHref(tenantUid, capability),
			icon: ICONS[id],
			...(section ? { section } : {}),
			// Studio's Terminology child would otherwise light both rows; Dashboard is a hub above
			// every operate path.
			...(id === 'studio' || id === 'dashboard' ? { exact: true } : {})
		};
	};

	const mirrorGroups: NavGroup[] = PLATFORM_NAV_STRUCTURE.map((group) => {
		const items = group.items
			.map((entry) => capabilityItem(entry.id, entry.section))
			.filter((item): item is NavItem => item !== null);
		// Admin puts Overview beside Dashboard in the hub; structure only carries Dashboard.
		if (group.id === 'hub') {
			const hub: NavItem[] = [
				...(mayConfigure ? [{ label: 'Overview', href: base, icon: HomeIcon, exact: true }] : []),
				...items
			];
			return { items: hub };
		}
		return {
			...(group.label ? { label: group.label } : {}),
			...(group.collapsible ? { collapsible: true } : {}),
			...(group.defaultOpen != null ? { defaultOpen: group.defaultOpen } : {}),
			...(group.pinned ? { pinned: true } : {}),
			...(group.solo ? { solo: true } : {}),
			items
		};
	}).filter((group) => group.items.length > 0);

	// Three things, and only three: the firm's Settings (one page — its record, sign-in, platforms
	// and storage as sections of it, `settings/+layout.svelte`), the Catalogue, and Oversight.
	// Anything the firm can already reach from Firm settings is NOT repeated here: notification
	// rules are configured from Logs, exactly as on the platform.
	const configureItems: NavItem[] = [
		// Storage is the one Settings section a book reader can open without tenants:read, so the
		// row appears for either — the page itself offers only the sections the principal holds.
		...(mayConfigure || allowed('book:read')
			? [{ label: 'Settings', href: `${base}/settings`, icon: SettingsIcon }]
			: []),
		...(allowed('platforms:read')
			? [
					{
						label: 'Products',
						href: `${base}/platform/products`,
						icon: BoxesIcon,
						section: 'Catalogue'
					},
					{
						label: 'Models',
						href: `${base}/platform/models`,
						icon: ChartPieIcon,
						section: 'Catalogue'
					},
					{
						label: 'Instruments',
						href: `${base}/platform/instruments`,
						icon: CoinsIcon,
						section: 'Catalogue'
					},
					{
						label: 'Fee packages',
						href: `${base}/platform/fee-packages`,
						icon: ReceiptIcon,
						section: 'Catalogue'
					},
					{
						label: 'Illustrations',
						href: `${base}/platform/illustrations`,
						icon: BookOpenIcon,
						section: 'Catalogue'
					},
					{
						label: 'Transfer providers',
						href: `${base}/platform/transfer-providers`,
						icon: LandmarkIcon,
						section: 'Catalogue'
					},
					{
						label: 'Third-party providers',
						href: `${base}/platform/third-party-providers`,
						icon: BuildingIcon,
						section: 'Catalogue'
					},
					// The words the catalogue is described in — product and wrapper names as the firm's
					// portal prints them. It reads and writes the catalogue endpoint under the catalogue
					// gates, so it is a Catalogue row, whatever its URL says about branding.
					{
						label: 'Terminology',
						href: `${base}/branding/terminology`,
						icon: TypeIcon,
						section: 'Catalogue'
					}
				]
			: []),
		...(allowed('book:read')
			? [
					{
						label: 'Deleted documents',
						href: `${base}/book/documents`,
						icon: Trash2Icon,
						section: 'Oversight'
					}
				]
			: []),
		...(allowed('audit:read')
			? [
					{
						label: 'Audit log',
						href: `${base}/audit`,
						icon: HistoryIcon,
						section: 'Oversight'
					}
				]
			: [])
	];

	const configureGroup: NavGroup | null =
		configureItems.length > 0
			? {
					label: 'Configure',
					collapsible: true,
					defaultOpen: false,
					// Pinned with Firm settings, and `solo` for the same reason: a group this long takes the
					// column to itself. Opening either closes the other.
					pinned: true,
					solo: true,
					items: configureItems
				}
			: null;

	// No "All tenants" row: the sidebar's scope header names the firm and carries the way back to
	// the tenant list (`$lib/record-focus` `firmContext`), as the platform's client header carries
	// "Dashboard". Two back links in one column read as two different places to go.
	return [...mirrorGroups, ...(configureGroup ? [configureGroup] : [])].filter(
		(group) => group.items.length > 0
	);
}
