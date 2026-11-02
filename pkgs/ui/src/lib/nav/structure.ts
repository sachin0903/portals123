/**
 * The platform portal's sidebar information architecture — group order, concept ids, and Firm
 * settings section labels. Hrefs and permission gates stay in each portal's `buildNav`; this file
 * is the product IA both portals project from so admin cannot invent a second structure.
 *
 * Admin's per-tenant sidebar mirrors this exactly, then appends a Graphene-only Configure group.
 * See `docs/design-language.md` (sidebar / Firm settings) and `AGENTS.md`.
 */

import type { TenantCapabilityId } from './concepts';

export type NavStructureItem = {
	id: TenantCapabilityId;
	/** Firm settings subsection label; omitted for daily-work rows. */
	section?: string;
};

export type NavStructureGroup = {
	/** Stable id for tests; not rendered. */
	id: 'hub' | 'investments' | 'pensions' | 'work' | 'firm-settings';
	label?: string;
	collapsible?: boolean;
	defaultOpen?: boolean;
	pinned?: boolean;
	solo?: boolean;
	items: readonly NavStructureItem[];
};

/** Ordered groups of the platform sidebar. Dashboard is the ungated hub; Firm settings is pinned. */
export const PLATFORM_NAV_STRUCTURE: readonly NavStructureGroup[] = [
	{
		id: 'hub',
		items: [{ id: 'dashboard' }]
	},
	{
		id: 'investments',
		label: 'Investments',
		collapsible: true,
		defaultOpen: true,
		items: [
			{ id: 'clients' },
			{ id: 'accounts' },
			{ id: 'transactions' },
			{ id: 'transfers' },
			{ id: 'reports' }
		]
	},
	{
		id: 'pensions',
		label: 'Pensions',
		collapsible: true,
		defaultOpen: true,
		items: [{ id: 'contributions' }]
	},
	{
		id: 'work',
		label: 'Work',
		collapsible: true,
		defaultOpen: true,
		items: [{ id: 'journeys' }, { id: 'reviews' }, { id: 'messages' }, { id: 'documents' }]
	},
	{
		id: 'firm-settings',
		label: 'Firm settings',
		collapsible: true,
		defaultOpen: false,
		pinned: true,
		solo: true,
		items: [
			{ id: 'team', section: 'People & Access' },
			{ id: 'profiles', section: 'People & Access' },
			{ id: 'roles', section: 'People & Access' },
			{ id: 'actions', section: 'People & Access' },
			{ id: 'studio', section: 'Platform Builder' },
			{ id: 'forms', section: 'Platform Builder' },
			{ id: 'flows', section: 'Platform Builder' },
			{ id: 'automations', section: 'Platform Builder' },
			{ id: 'templates', section: 'Communications' },
			{ id: 'content', section: 'Communications' },
			{ id: 'terms', section: 'Communications' },
			{ id: 'emails', section: 'Communications' },
			{ id: 'document-types', section: 'Communications' },
			{ id: 'activity', section: 'Data' },
			{ id: 'data-sources', section: 'Data' }
		]
	}
];

/** Concept ids that appear in the shared structure (excludes personal-only concepts). */
export const PLATFORM_NAV_STRUCTURE_IDS: readonly TenantCapabilityId[] =
	PLATFORM_NAV_STRUCTURE.flatMap((group) => group.items.map((item) => item.id));
