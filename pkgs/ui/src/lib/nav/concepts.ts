/**
 * Closed inventory of the platform portal concepts a tenant workspace must account for.
 *
 * `tenant` entries are firm capabilities. `personal` entries are tied to the signed-in person's
 * identity or credentials and must never be proxied by an operations user.
 */
export const PLATFORM_NAV_CONCEPTS = [
	{ id: 'dashboard', label: 'Dashboard', scope: 'tenant' },
	{ id: 'clients', label: 'Clients', scope: 'tenant' },
	{ id: 'accounts', label: 'Accounts', scope: 'tenant' },
	{ id: 'transactions', label: 'Transactions', scope: 'tenant' },
	{ id: 'transfers', label: 'Transfers', scope: 'tenant' },
	{ id: 'reports', label: 'Reports', scope: 'tenant' },
	{ id: 'contributions', label: 'Contributions', scope: 'tenant' },
	{ id: 'journeys', label: 'Journeys', scope: 'tenant' },
	{ id: 'reviews', label: 'Reviews', scope: 'tenant' },
	{ id: 'messages', label: 'Messages', scope: 'tenant' },
	{ id: 'documents', label: 'Documents', scope: 'tenant' },
	{ id: 'team', label: 'Team', scope: 'tenant' },
	{ id: 'profiles', label: 'Profiles', scope: 'tenant' },
	{ id: 'roles', label: 'Roles', scope: 'tenant' },
	{ id: 'actions', label: 'Actions', scope: 'tenant' },
	{ id: 'studio', label: 'Studio', scope: 'tenant' },
	{ id: 'forms', label: 'Forms', scope: 'tenant' },
	{ id: 'flows', label: 'Flows', scope: 'tenant' },
	{ id: 'automations', label: 'Automations', scope: 'tenant' },
	// ONE concept for both template kinds, and no separate Datasets: `/templates` covers documents
	// and emails, and a dataset is an input a document template draws on rather than a thing of its
	// own — it lives at `/templates/datasets`. The sidebar has one row for each, so this inventory
	// has one entry for each (the test below holds the two in step).
	{ id: 'templates', label: 'Templates', scope: 'tenant' },
	{ id: 'content', label: 'Content', scope: 'tenant' },
	{ id: 'terms', label: 'Terms', scope: 'tenant' },
	{ id: 'emails', label: 'Emails', scope: 'tenant' },
	{ id: 'document-types', label: 'Types', scope: 'tenant' },
	{ id: 'activity', label: 'Activity', scope: 'tenant' },
	{ id: 'data-sources', label: 'Data sources', scope: 'tenant' },
	{ id: 'account', label: 'Account', scope: 'personal' },
	{ id: 'security', label: 'Security', scope: 'personal' },
	{ id: 'notification-preferences', label: 'Notification preferences', scope: 'personal' },
	{ id: 'storage-credentials', label: 'Storage credentials', scope: 'personal' }
] as const;

export type PlatformNavConcept = (typeof PLATFORM_NAV_CONCEPTS)[number];
export type PlatformNavConceptId = PlatformNavConcept['id'];
export type TenantCapabilityId = Extract<PlatformNavConcept, { scope: 'tenant' }>['id'];
export type PersonalCapabilityId = Extract<PlatformNavConcept, { scope: 'personal' }>['id'];
