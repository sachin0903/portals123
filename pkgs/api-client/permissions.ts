// AUTO-GENERATED from apps/api/app/permissions.py by scripts/generate_permissions_ts.py.
// Do not edit by hand — run `moon run api:generate-permissions` (CI fails on drift).

export type PermissionSlug =
  | 'firm_settings:show'
  | 'members:read'
  | 'members:write'
  | 'notifications:read'
  | 'notifications:write'
  | 'teams:read'
  | 'teams:write'
  | 'profiles:read'
  | 'profiles:write'
  | 'roles:read'
  | 'roles:write'
  | 'branding:read'
  | 'branding:write'
  | 'pages:write'
  | 'documents:read'
  | 'documents:write'
  | 'documents:send'
  | 'documents:show'
  | 'storage:read'
  | 'storage:read_all'
  | 'storage:shared'
  | 'storage:credentials_own'
  | 'storage:credentials'
  | 'document_types:read'
  | 'document_types:write'
  | 'illustrations:write'
  | 'forms:read'
  | 'forms:write'
  | 'forms:submit'
  | 'templates:read'
  | 'templates:write'
  | 'email_templates:read'
  | 'email_templates:write'
  | 'content:read'
  | 'content:write'
  | 'terms:read'
  | 'terms:write'
  | 'platform_terms:read'
  | 'platform_terms:write'
  | 'mi_reports:read'
  | 'mi_reports:write'
  | 'datasets:read'
  | 'datasets:write'
  | 'flows:read'
  | 'flows:write'
  | 'cases:read'
  | 'cases:write'
  | 'automations:read'
  | 'automations:write'
  | 'automations:pause'
  | 'tasks:read'
  | 'tasks:write'
  | 'actions:read'
  | 'actions:write'
  | 'messages:read'
  | 'messages:write'
  | 'contacts:read'
  | 'contacts:write'
  | 'contacts:create'
  | 'contacts:read_all'
  | 'contacts:sync_morgan_lloyd'
  | 'instructions:read'
  | 'instructions:write'
  | 'contributions:read'
  | 'contributions:write'
  | 'transfers:read'
  | 'transfers:write'
  | 'screening:read'
  | 'activity:read'
  | 'data_sources:read'
  | 'listings:export'
  | 'reports:read'
  | 'reports:export'
  | 'tenants:read'
  | 'tenants:write'
  | 'operations:read'
  | 'operations:write'
  | 'model:read'
  | 'model:write'
  | 'platforms:read'
  | 'platforms:write'
  | 'platforms:query'
  | 'lookthrough:read'
  | 'lookthrough:write'
  | 'integrations:read'
  | 'integrations:write'
  | 'book:read'
  | 'book:write'
  | 'tenant_portal:read'
  | 'tenant_portal:write'
  | 'aml:read'
  | 'aml:write'
  | 'aml:adjudicate'
  | 'automations:global_pause'
  | 'audit:read'
  | 'system';

export type Portal = 'client' | 'platform' | 'operations';

export interface PermissionMeta {
	slug: PermissionSlug;
	name: string;
	resource: string;
	action: string;
	portal: Portal;
	description: string;
}

export const PERMISSIONS: Record<PermissionSlug, PermissionMeta> = {
  'firm_settings:show': { slug: 'firm_settings:show', name: 'Show Firm settings', resource: 'firm_settings', action: 'show', portal: 'platform', description: 'Show the Firm settings area of the sidebar at all, on top of each page\'s own permission.' },
  'members:read': { slug: 'members:read', name: 'View team', resource: 'members', action: 'read', portal: 'platform', description: 'View the firm\'s people and their portal access.' },
  'members:write': { slug: 'members:write', name: 'Manage team', resource: 'members', action: 'write', portal: 'platform', description: 'Invite people, toggle Client/Platform access, assign roles, deactivate/reactivate.' },
  'notifications:read': { slug: 'notifications:read', name: 'View notification rules', resource: 'notifications', action: 'read', portal: 'platform', description: 'View the firm\'s notification rules.' },
  'notifications:write': { slug: 'notifications:write', name: 'Manage notification rules', resource: 'notifications', action: 'write', portal: 'platform', description: 'Edit the firm\'s notification rules that Graphene allows the firm to change.' },
  'teams:read': { slug: 'teams:read', name: 'View team', resource: 'teams', action: 'read', portal: 'platform', description: 'View the firm\'s staff and their portal access.' },
  'teams:write': { slug: 'teams:write', name: 'Manage team', resource: 'teams', action: 'write', portal: 'platform', description: 'Invite staff, toggle Client/Platform access, assign roles, deactivate/reactivate.' },
  'profiles:read': { slug: 'profiles:read', name: 'View profiles', resource: 'profiles', action: 'read', portal: 'platform', description: 'View the firm\'s client-linked login profiles.' },
  'profiles:write': { slug: 'profiles:write', name: 'Manage profiles', resource: 'profiles', action: 'write', portal: 'platform', description: 'Invite, edit and deactivate the firm\'s client-linked login profiles.' },
  'roles:read': { slug: 'roles:read', name: 'View roles', resource: 'roles', action: 'read', portal: 'platform', description: 'View the firm\'s roles and their permissions.' },
  'roles:write': { slug: 'roles:write', name: 'Manage roles', resource: 'roles', action: 'write', portal: 'platform', description: 'Create and edit the firm\'s roles and their permissions.' },
  'branding:read': { slug: 'branding:read', name: 'View branding', resource: 'branding', action: 'read', portal: 'platform', description: 'View the firm\'s white-label branding.' },
  'branding:write': { slug: 'branding:write', name: 'Manage branding', resource: 'branding', action: 'write', portal: 'platform', description: 'Edit the firm\'s branding — colours, logo, favicon, name and login copy.' },
  'pages:write': { slug: 'pages:write', name: 'Compose portal pages', resource: 'pages', action: 'write', portal: 'platform', description: 'Compose the client portal\'s pages and blocks (the studio\'s Pages view).' },
  'documents:read': { slug: 'documents:read', name: 'View documents', resource: 'documents', action: 'read', portal: 'platform', description: 'View and download documents, and the document audit log.' },
  'documents:write': { slug: 'documents:write', name: 'Manage documents', resource: 'documents', action: 'write', portal: 'platform', description: 'Upload, classify, update, share and archive documents.' },
  'documents:send': { slug: 'documents:send', name: 'Send documents to clients', resource: 'documents', action: 'send', portal: 'platform', description: 'Release documents to clients and raise/re-point client action requests.' },
  'documents:show': { slug: 'documents:show', name: 'Show Documents page', resource: 'documents', action: 'show', portal: 'platform', description: 'Open the firm-wide Documents page and its sidebar link, on top of View documents.' },
  'storage:read': { slug: 'storage:read', name: 'View reports & exports', resource: 'storage', action: 'read', portal: 'platform', description: 'Read the firm\'s generated reports and exports in object storage.' },
  'storage:read_all': { slug: 'storage:read_all', name: 'View all files', resource: 'storage', action: 'read_all', portal: 'platform', description: 'Read all the firm\'s files and exports in object storage (never the datalake).' },
  'storage:shared': { slug: 'storage:shared', name: 'Use shared documents', resource: 'storage', action: 'shared', portal: 'platform', description: 'Read and write the firm\'s shared documents area (files/shared).' },
  'storage:credentials_own': { slug: 'storage:credentials_own', name: 'Issue own storage credentials', resource: 'storage', action: 'credentials_own', portal: 'platform', description: 'Issue and revoke S3 credentials for storage access — own credentials only.' },
  'storage:credentials': { slug: 'storage:credentials', name: 'Manage storage credentials', resource: 'storage', action: 'credentials', portal: 'platform', description: 'Issue, list and revoke storage credentials for any member of the firm.' },
  'document_types:read': { slug: 'document_types:read', name: 'View document types', resource: 'document_types', action: 'read', portal: 'platform', description: 'View the firm\'s document-type taxonomy.' },
  'document_types:write': { slug: 'document_types:write', name: 'Manage document types', resource: 'document_types', action: 'write', portal: 'platform', description: 'Create, rename, reorder and archive document types.' },
  'illustrations:write': { slug: 'illustrations:write', name: 'Generate illustrations', resource: 'illustrations', action: 'write', portal: 'platform', description: 'Generate pension and investment illustrations and file the PDFs against clients.' },
  'forms:read': { slug: 'forms:read', name: 'View forms', resource: 'forms', action: 'read', portal: 'platform', description: 'View the firm\'s forms and their fields.' },
  'forms:write': { slug: 'forms:write', name: 'Manage forms', resource: 'forms', action: 'write', portal: 'platform', description: 'Create, edit, publish and delete the firm\'s forms and their fields.' },
  'forms:submit': { slug: 'forms:submit', name: 'Submit and read form responses', resource: 'forms', action: 'submit', portal: 'platform', description: 'Record standalone form submissions and read the responses a form has received.' },
  'templates:read': { slug: 'templates:read', name: 'View templates', resource: 'templates', action: 'read', portal: 'platform', description: 'View the firm\'s document templates and their versions.' },
  'templates:write': { slug: 'templates:write', name: 'Manage templates', resource: 'templates', action: 'write', portal: 'platform', description: 'Create, edit, version, publish and archive the firm\'s document templates.' },
  'email_templates:read': { slug: 'email_templates:read', name: 'View email templates', resource: 'email_templates', action: 'read', portal: 'platform', description: 'View the firm\'s email templates and their versions.' },
  'email_templates:write': { slug: 'email_templates:write', name: 'Manage email templates', resource: 'email_templates', action: 'write', portal: 'platform', description: 'Create, edit, version, publish and archive the firm\'s email templates.' },
  'content:read': { slug: 'content:read', name: 'View content', resource: 'content', action: 'read', portal: 'platform', description: 'View the firm\'s published news, articles and client resources.' },
  'content:write': { slug: 'content:write', name: 'Manage content', resource: 'content', action: 'write', portal: 'platform', description: 'Create, edit, publish and archive the firm\'s news, articles and client resources.' },
  'terms:read': { slug: 'terms:read', name: 'View terms', resource: 'terms', action: 'read', portal: 'platform', description: 'View the firm\'s legal terms library and who has accepted them.' },
  'terms:write': { slug: 'terms:write', name: 'Manage terms', resource: 'terms', action: 'write', portal: 'platform', description: 'Create, edit, publish and archive the firm\'s legal terms.' },
  'platform_terms:read': { slug: 'platform_terms:read', name: 'View platform terms', resource: 'platform_terms', action: 'read', portal: 'operations', description: 'View Graphene\'s platform-wide legal terms.' },
  'platform_terms:write': { slug: 'platform_terms:write', name: 'Manage platform terms', resource: 'platform_terms', action: 'write', portal: 'operations', description: 'Create, edit, publish and archive Graphene\'s platform-wide legal terms.' },
  'mi_reports:read': { slug: 'mi_reports:read', name: 'View management reports', resource: 'mi_reports', action: 'read', portal: 'operations', description: 'View the management reports and which of them are switched on.' },
  'mi_reports:write': { slug: 'mi_reports:write', name: 'Manage management reports', resource: 'mi_reports', action: 'write', portal: 'operations', description: 'Switch a management report on or off for every firm.' },
  'datasets:read': { slug: 'datasets:read', name: 'View datasets', resource: 'datasets', action: 'read', portal: 'platform', description: 'View the firm\'s datasets and their sample data.' },
  'datasets:write': { slug: 'datasets:write', name: 'Manage datasets', resource: 'datasets', action: 'write', portal: 'platform', description: 'Create, edit, version and publish the firm\'s datasets.' },
  'flows:read': { slug: 'flows:read', name: 'View flows', resource: 'flows', action: 'read', portal: 'platform', description: 'View the firm\'s flow definitions (e.g. onboarding) and their versions.' },
  'flows:write': { slug: 'flows:write', name: 'Manage flows', resource: 'flows', action: 'write', portal: 'platform', description: 'Create, edit, version and publish the firm\'s flow definitions.' },
  'cases:read': { slug: 'cases:read', name: 'View cases', resource: 'cases', action: 'read', portal: 'platform', description: 'View running flow cases (e.g. onboardings in progress) and their step status.' },
  'cases:write': { slug: 'cases:write', name: 'Manage cases', resource: 'cases', action: 'write', portal: 'platform', description: 'Start, invite a client to, configure and cancel flow cases.' },
  'automations:read': { slug: 'automations:read', name: 'View automations', resource: 'automations', action: 'read', portal: 'platform', description: 'View the firm\'s automations, their triggers and their firing history.' },
  'automations:write': { slug: 'automations:write', name: 'Manage automations', resource: 'automations', action: 'write', portal: 'platform', description: 'Create, edit, arm and disarm the firm\'s automations.' },
  'automations:pause': { slug: 'automations:pause', name: 'Pause the firm\'s automations', resource: 'automations', action: 'pause', portal: 'platform', description: 'Pause and resume every automation in the firm (the kill switch).' },
  'tasks:read': { slug: 'tasks:read', name: 'View tasks', resource: 'tasks', action: 'read', portal: 'platform', description: 'View the firm\'s tasks (action items) and their status.' },
  'tasks:write': { slug: 'tasks:write', name: 'Manage tasks', resource: 'tasks', action: 'write', portal: 'platform', description: 'Create, edit, reassign, complete and cancel tasks.' },
  'actions:read': { slug: 'actions:read', name: 'View actions', resource: 'actions', action: 'read', portal: 'platform', description: 'View the firm\'s provider actions and the runs they produce.' },
  'actions:write': { slug: 'actions:write', name: 'Manage actions', resource: 'actions', action: 'write', portal: 'platform', description: 'Enable provider actions and choose who may run them and which role approves them.' },
  'messages:read': { slug: 'messages:read', name: 'View client messages', resource: 'messages', action: 'read', portal: 'platform', description: 'View client message threads for the firm.' },
  'messages:write': { slug: 'messages:write', name: 'Manage client messages', resource: 'messages', action: 'write', portal: 'platform', description: 'Send messages to clients and mark threads read.' },
  'contacts:read': { slug: 'contacts:read', name: 'View clients', resource: 'contacts', action: 'read', portal: 'platform', description: 'View the firm\'s clients (scoped to the caller\'s own book unless read-all).' },
  'contacts:write': { slug: 'contacts:write', name: 'Manage clients', resource: 'contacts', action: 'write', portal: 'platform', description: 'Edit clients, assign advisers, and link client logins.' },
  'contacts:create': { slug: 'contacts:create', name: 'Create clients', resource: 'contacts', action: 'create', portal: 'platform', description: 'Add a new client to the firm\'s book.' },
  'contacts:read_all': { slug: 'contacts:read_all', name: 'Read all clients', resource: 'contacts', action: 'read_all', portal: 'platform', description: 'See every client in the firm (vs only the own assigned book) -- the "see all" toggle.' },
  'contacts:sync_morgan_lloyd': { slug: 'contacts:sync_morgan_lloyd', name: 'Sync to Morgan Lloyd', resource: 'contacts', action: 'sync_morgan_lloyd', portal: 'platform', description: 'Push clients and SIPP accounts to Morgan Lloyd — creates the contact, scheme, policy and beneficiaries.' },
  'instructions:read': { slug: 'instructions:read', name: 'View money movement', resource: 'instructions', action: 'read', portal: 'platform', description: 'View clients\' money-movement instructions (expected-in, withdrawals, model moves).' },
  'instructions:write': { slug: 'instructions:write', name: 'Manage money movement', resource: 'instructions', action: 'write', portal: 'platform', description: 'Raise and cancel money-movement instructions, set clients\' bank details, change an account\'s model mandate and start a drawdown.' },
  'contributions:read': { slug: 'contributions:read', name: 'View contributions', resource: 'contributions', action: 'read', portal: 'platform', description: 'View the firm\'s pension contributions and the payment expectations they raised.' },
  'contributions:write': { slug: 'contributions:write', name: 'Manage contributions', resource: 'contributions', action: 'write', portal: 'platform', description: 'Book, categorise and cancel contributions, and record them to the back office.' },
  'transfers:read': { slug: 'transfers:read', name: 'View transfers', resource: 'transfers', action: 'read', portal: 'platform', description: 'View transfers in from ceding schemes and their progress.' },
  'transfers:write': { slug: 'transfers:write', name: 'Manage transfers', resource: 'transfers', action: 'write', portal: 'platform', description: 'Raise, update and cancel transfers, and record them to the back office.' },
  'screening:read': { slug: 'screening:read', name: 'View client compliance status', resource: 'compliance', action: 'read', portal: 'platform', description: 'View clients\' identity/AML compliance status and completed reports.' },
  'activity:read': { slug: 'activity:read', name: 'View firm activity', resource: 'activity', action: 'read', portal: 'platform', description: 'View the firm\'s action trail — its staff\'s actions and support actions taken on it.' },
  'data_sources:read': { slug: 'data_sources:read', name: 'View data sources', resource: 'data_sources', action: 'read', portal: 'platform', description: 'See which custodians feed this firm\'s book and when they last did.' },
  'listings:export': { slug: 'listings:export', name: 'Export listings', resource: 'listings', action: 'export', portal: 'platform', description: 'Download CSV, Excel and PDF of the firm\'s book listings (clients, accounts, transactions and holdings).' },
  'reports:read': { slug: 'reports:read', name: 'View reports', resource: 'reports', action: 'read', portal: 'platform', description: 'See the management reports switched on for this firm, and preview their rows.' },
  'reports:export': { slug: 'reports:export', name: 'Export reports', resource: 'reports', action: 'export', portal: 'platform', description: 'Download a management report as CSV or Excel.' },
  'tenants:read': { slug: 'tenants:read', name: 'View firms', resource: 'tenants', action: 'read', portal: 'operations', description: 'View firms and their members, roles, configuration and the user directory.' },
  'tenants:write': { slug: 'tenants:write', name: 'Manage firms', resource: 'tenants', action: 'write', portal: 'operations', description: 'Create/edit firms and manage their members, roles and WorkOS environments.' },
  'operations:read': { slug: 'operations:read', name: 'View operations staff', resource: 'operations', action: 'read', portal: 'operations', description: 'View Graphene\'s internal-operations staff and roles.' },
  'operations:write': { slug: 'operations:write', name: 'Manage operations staff', resource: 'operations', action: 'write', portal: 'operations', description: 'Manage Graphene\'s internal-operations staff and their roles.' },
  'model:read': { slug: 'model:read', name: 'View model tenant', resource: 'model', action: 'read', portal: 'operations', description: 'View the model tenant.' },
  'model:write': { slug: 'model:write', name: 'Manage model tenant', resource: 'model', action: 'write', portal: 'operations', description: 'Configure the model tenant (the role preset new firms clone).' },
  'platforms:read': { slug: 'platforms:read', name: 'View platform integrations', resource: 'platforms', action: 'read', portal: 'operations', description: 'View platform integrations — pipeline models, runs, warehouse data and firm enablements.' },
  'platforms:write': { slug: 'platforms:write', name: 'Manage platform integrations', resource: 'platforms', action: 'write', portal: 'operations', description: 'Manage platform integrations — register sources, trigger syncs and fan-outs.' },
  'platforms:query': { slug: 'platforms:query', name: 'Query platform warehouses', resource: 'platforms', action: 'query', portal: 'operations', description: 'Run read-only SQL against a provider\'s core warehouse (the platform Query console).' },
  'lookthrough:read': { slug: 'lookthrough:read', name: 'View fund look-through data', resource: 'lookthrough', action: 'read', portal: 'operations', description: 'View imported fund compositions and their snapshot history.' },
  'lookthrough:write': { slug: 'lookthrough:write', name: 'Manage fund look-through data', resource: 'lookthrough', action: 'write', portal: 'operations', description: 'Import and correct IFSL fund-composition workbooks.' },
  'integrations:read': { slug: 'integrations:read', name: 'View platform integrations', resource: 'integrations', action: 'read', portal: 'operations', description: 'View SEI custodian/software platform integrations — approvals, DSR grants, feature toggles and job-run history.' },
  'integrations:write': { slug: 'integrations:write', name: 'Manage platform integrations', resource: 'integrations', action: 'write', portal: 'operations', description: 'Approve, reject and configure platform integrations, and manage DSR/feature grants.' },
  'book:read': { slug: 'book:read', name: 'View tenant books', resource: 'book', action: 'read', portal: 'operations', description: 'Read any firm\'s book — clients, accounts, documents, tasks and journeys — as that firm\'s own read-all staff would see it.' },
  'book:write': { slug: 'book:write', name: 'Manage tenant books', resource: 'book', action: 'write', portal: 'operations', description: 'Act on a firm\'s book on its behalf — edit client details/bank accounts, set model mandates, and raise or cancel money-movement instructions.' },
  'tenant_portal:read': { slug: 'tenant_portal:read', name: 'View tenant portal workspaces', resource: 'tenant_portal', action: 'read', portal: 'operations', description: 'Enter a firm\'s operational workspace and use its authorised read-only capabilities.' },
  'tenant_portal:write': { slug: 'tenant_portal:write', name: 'Manage tenant portal workspaces', resource: 'tenant_portal', action: 'write', portal: 'operations', description: 'Act through a firm\'s operational workspace on its behalf, while retaining the Graphene operator as the audited actor.' },
  'aml:read': { slug: 'aml:read', name: 'View AML screening', resource: 'aml', action: 'read', portal: 'operations', description: 'View AML and identity checks across all firms — checks, matches and decisions.' },
  'aml:write': { slug: 'aml:write', name: 'Manage AML screening', resource: 'aml', action: 'write', portal: 'operations', description: 'Run screens, run a failed screen again and turn ongoing monitoring on or off.' },
  'aml:adjudicate': { slug: 'aml:adjudicate', name: 'Adjudicate AML matches', resource: 'aml', action: 'adjudicate', portal: 'operations', description: 'Confirm or dismiss a screening match, and clear, reject or ask new photos for an identity check.' },
  'automations:global_pause': { slug: 'automations:global_pause', name: 'Pause all automations', resource: 'automations', action: 'global_pause', portal: 'operations', description: 'Pause and resume automations across every firm on the platform (the global kill switch).' },
  'audit:read': { slug: 'audit:read', name: 'View audit log', resource: 'audit', action: 'read', portal: 'operations', description: 'View the append-only audit trail.' },
  'system': { slug: 'system', name: 'System administration', resource: 'platform', action: 'admin', portal: 'operations', description: 'Full Graphene internal-operations access (cross-tenant superuser).' },
};

export const PERMISSION_SLUGS: PermissionSlug[] = ['firm_settings:show', 'members:read', 'members:write', 'notifications:read', 'notifications:write', 'teams:read', 'teams:write', 'profiles:read', 'profiles:write', 'roles:read', 'roles:write', 'branding:read', 'branding:write', 'pages:write', 'documents:read', 'documents:write', 'documents:send', 'documents:show', 'storage:read', 'storage:read_all', 'storage:shared', 'storage:credentials_own', 'storage:credentials', 'document_types:read', 'document_types:write', 'illustrations:write', 'forms:read', 'forms:write', 'forms:submit', 'templates:read', 'templates:write', 'email_templates:read', 'email_templates:write', 'content:read', 'content:write', 'terms:read', 'terms:write', 'platform_terms:read', 'platform_terms:write', 'mi_reports:read', 'mi_reports:write', 'datasets:read', 'datasets:write', 'flows:read', 'flows:write', 'cases:read', 'cases:write', 'automations:read', 'automations:write', 'automations:pause', 'tasks:read', 'tasks:write', 'actions:read', 'actions:write', 'messages:read', 'messages:write', 'contacts:read', 'contacts:write', 'contacts:create', 'contacts:read_all', 'contacts:sync_morgan_lloyd', 'instructions:read', 'instructions:write', 'contributions:read', 'contributions:write', 'transfers:read', 'transfers:write', 'screening:read', 'activity:read', 'data_sources:read', 'listings:export', 'reports:read', 'reports:export', 'tenants:read', 'tenants:write', 'operations:read', 'operations:write', 'model:read', 'model:write', 'platforms:read', 'platforms:write', 'platforms:query', 'lookthrough:read', 'lookthrough:write', 'integrations:read', 'integrations:write', 'book:read', 'book:write', 'tenant_portal:read', 'tenant_portal:write', 'aml:read', 'aml:write', 'aml:adjudicate', 'automations:global_pause', 'audit:read', 'system'];

/** Does a set of held slugs satisfy a required one? `system` satisfies everything; a `:write`
 * satisfies the matching `:read` (write implies read). Mirrors app.permissions.satisfies. */
export function satisfies(held: Iterable<string>, required: string): boolean {
	const set = held instanceof Set ? held : new Set(held);
	if (set.has('system') || set.has(required)) return true;
	if (required.endsWith(':read')) return set.has(required.slice(0, -':read'.length) + ':write');
	return false;
}

/** Authorise in the UI: the `system` permission bypasses; otherwise the held permissions must
 * satisfy `required`. Mirrors the API's require_permission.
 *
 * `role` is accepted for call-site compatibility and deliberately NOT trusted. It comes straight
 * from the tenant's own WorkOS environment, so a firm that named a role "admin" used to get a
 * cross-tenant superuser bypass here — the same defect app/auth.py::is_system was fixed for. The
 * only superuser signal is the `system` permission, which a tenant cannot grant itself. */
export function can(
	permissions: Iterable<string>,
	_role: string | null,
	required: PermissionSlug | string
): boolean {
	return satisfies(permissions, required);
}
