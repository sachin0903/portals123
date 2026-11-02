import type { PlatformNavConcept, PlatformNavConceptId } from '@graphene/ui/nav';

type CapabilityBase = { label: string };

export type CapabilityRoute = CapabilityBase & {
	kind: 'configure' | 'operate';
	/** Route relative to `/tenants/[uid]`. */
	path: `/${string}` | '';
	/** False means the route is the declared destination for a later rollout slice. */
	available: boolean;
};

type CapabilityExclusion = CapabilityBase & {
	kind: 'personal';
	reason: string;
};

export type TenantCapabilityDisposition = CapabilityRoute | CapabilityExclusion;

export function isCapabilityRoute(
	disposition: TenantCapabilityDisposition
): disposition is CapabilityRoute {
	return disposition.kind !== 'personal';
}

/**
 * One explicit disposition for every platform capability. This is a product/security manifest,
 * not a route menu: unavailable destinations document the rollout without emitting dead links.
 */
export const TENANT_CAPABILITY_MANIFEST = {
	dashboard: { label: 'Dashboard', kind: 'operate', path: '/operate', available: true },
	clients: { label: 'Clients', kind: 'operate', path: '/operate/clients', available: true },
	accounts: { label: 'Accounts', kind: 'operate', path: '/operate/accounts', available: true },
	transactions: {
		label: 'Transactions',
		kind: 'operate',
		path: '/operate/transactions',
		available: true
	},
	transfers: { label: 'Transfers', kind: 'operate', path: '/operate/transfers', available: true },
	// Firms run these on the platform, under their own row-level security. An operations
	// session bypasses that, so there is no operate page here yet.
	reports: { label: 'Reports', kind: 'operate', path: '/operate/reports', available: false },
	contributions: {
		label: 'Contributions',
		kind: 'operate',
		path: '/operate/contributions',
		available: true
	},
	journeys: { label: 'Journeys', kind: 'operate', path: '/operate/journeys', available: true },
	// NO `tasks` ENTRY, and that is not an omission. Standalone tasks were withdrawn as a
	// user-facing platform feature (Requests and Reviews cover what they were reached for), so
	// `tasks` left the platform's concept inventory — and this manifest mirrors that inventory by
	// construction. The `/operate/tasks` routes stay on disk, unlinked, exactly as the platform's
	// `/tasks` routes do: the task engine still backs journey steps.
	reviews: { label: 'Reviews', kind: 'operate', path: '/operate/reviews', available: true },
	actions: {
		label: 'Actions',
		kind: 'operate',
		path: '/operate/actions',
		available: true
	},
	messages: { label: 'Messages', kind: 'operate', path: '/operate/messages', available: true },
	documents: { label: 'Documents', kind: 'operate', path: '/operate/documents', available: true },
	team: { label: 'Team', kind: 'configure', path: '/members', available: true },
	profiles: { label: 'Profiles', kind: 'configure', path: '/profiles', available: true },
	roles: { label: 'Roles', kind: 'configure', path: '/roles', available: true },
	studio: { label: 'Studio', kind: 'configure', path: '/branding', available: true },
	forms: { label: 'Forms', kind: 'operate', path: '/operate/forms', available: true },
	flows: { label: 'Flows', kind: 'operate', path: '/operate/flows', available: true },
	automations: {
		label: 'Automations',
		kind: 'operate',
		path: '/operate/automations',
		available: true
	},
	// ONE entry for both template kinds, and none for datasets: the platform folded emails into
	// `/templates` and moved datasets beneath it, so the concepts they mirrored are gone. This
	// manifest is keyed by concept id, which is what keeps the two from drifting apart.
	templates: { label: 'Templates', kind: 'operate', path: '/operate/templates', available: true },
	content: { label: 'Content', kind: 'operate', path: '/operate/content', available: true },
	terms: { label: 'Terms', kind: 'operate', path: '/operate/terms', available: true },
	emails: {
		label: 'Emails',
		kind: 'operate',
		path: '/operate/emails',
		available: true
	},
	'document-types': {
		label: 'Types',
		kind: 'operate',
		path: '/operate/documents/types',
		available: true
	},
	activity: {
		label: 'Activity',
		kind: 'operate',
		path: '/operate/activity',
		available: true
	},
	'data-sources': {
		label: 'Data sources',
		kind: 'operate',
		path: '/operate/data-sources',
		available: true
	},
	account: {
		label: 'Account',
		kind: 'personal',
		reason: 'The operator keeps their own Graphene account; they never assume a firm user identity.'
	},
	security: {
		label: 'Security',
		kind: 'personal',
		reason: 'Login, password and MFA settings belong only to the authenticated person.'
	},
	'notification-preferences': {
		label: 'Notification preferences',
		kind: 'personal',
		reason: 'Notification preferences belong to the authenticated person, not to a target firm.'
	},
	'storage-credentials': {
		label: 'Storage credentials',
		kind: 'personal',
		reason: 'Personal S3 credentials cannot be issued or viewed on another user’s behalf.'
	}
} satisfies Record<PlatformNavConceptId, TenantCapabilityDisposition>;

export function tenantCapabilityHref(tenantUid: string, disposition: CapabilityRoute): string {
	return `/tenants/${tenantUid}${disposition.path}`;
}

export function capabilityRows(
	concepts: readonly PlatformNavConcept[]
): Array<PlatformNavConcept & { disposition: TenantCapabilityDisposition }> {
	return concepts.map((concept) => ({
		...concept,
		disposition: TENANT_CAPABILITY_MANIFEST[concept.id]
	}));
}
