/**
 * Portal information architecture shared by the platform and admin portals.
 *
 * Group order, concept ids, the focused-record sidebar, and the client/account section builders
 * live here so admin does not import `apps/platform` by relative path. Href and permission gates
 * stay in each app's own `nav.ts`.
 */

export {
	PLATFORM_NAV_CONCEPTS,
	type PersonalCapabilityId,
	type PlatformNavConcept,
	type PlatformNavConceptId,
	type TenantCapabilityId
} from './concepts';
export {
	PLATFORM_NAV_STRUCTURE,
	PLATFORM_NAV_STRUCTURE_IDS,
	type NavStructureGroup,
	type NavStructureItem
} from './structure';
export {
	FOCUS_ICONS,
	focusContext,
	focusNav,
	focusedNavWithPinned,
	parseFocus,
	type FocusSection,
	type SidebarFocus
} from './sidebar-focus';
export {
	CLIENT_TAB_ANCHORS,
	CLIENT_TAB_ROUTES,
	clientSections,
	clientSidebarFocus,
	clientTabTarget,
	type ClientFocusInput,
	type TabTarget
} from './client-sections';
export {
	ACCOUNT_TAB_ROUTES,
	accountSections,
	accountSidebarFocus,
	accountTabTarget,
	type AccountFocusInput
} from './account-sections';
