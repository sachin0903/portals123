import { env } from '$env/dynamic/private';

/** Base URL of the FastAPI backend (server-side admin calls). */
export const apiUrl = (): string => env.API_URL ?? 'http://127.0.0.1:8000';

/** Bearer auth header for the signed-in admin's access token. */
export const authHeaders = (accessToken: string | null): Record<string, string> => ({
	Authorization: `Bearer ${accessToken}`
});

export const jsonHeaders = (accessToken: string | null): Record<string, string> => ({
	...authHeaders(accessToken),
	'Content-Type': 'application/json'
});

/** Pull a human-readable message out of a FastAPI error response. */
export async function detail(res: Response): Promise<string> {
	try {
		const body = await res.json();
		if (typeof body?.detail === 'string') return body.detail;
		if (Array.isArray(body?.detail) && body.detail[0]?.msg) return String(body.detail[0].msg);
		if (typeof body?.detail?.message === 'string') return body.detail.message;
	} catch {
		/* fall through */
	}
	return `API responded ${res.status}`;
}

// There is deliberately no ROLES constant here: roles are per-tenant with arbitrary slugs (no
// global templates), so the assignable set always comes from GET /admin/tenants/{id}/roles.

export type Tenant = {
	id: number;
	uid: string;
	alias: string;
	name: string;
	website: string | null;
	enabled: boolean;
	parent_id: number | null;
	workos_project_id: string | null;
};

/** A firm as returned by the list endpoint — enriched with onboarding status + user count. */
export type TenantSummary = {
	id: number;
	uid: string;
	alias: string;
	name: string;
	website: string | null;
	enabled: boolean;
	workos_project_id: string | null;
	user_count: number;
	has_environment: boolean;
	has_keys: boolean;
	has_orgs: boolean;
};

/** One portal a person holds within a firm. */
export type MemberPortal = {
	portal: Portal;
	role_slug: string | null;
	status: string;
};

/** One custodian that knows a person, NAMED BY THE API (`providers.meta()`) — the portals keep no
 *  provider-name table of their own, because the six that existed had drifted and the one that
 *  mattered named the wrong custodian for anything unlisted. */
export type MemberProvider = {
	provider: string;
	name: string;
};

export type Member = {
	user_id: string;
	user_uid: string;
	email: string;
	name: string | null;
	enabled: boolean;
	portals: MemberPortal[];
	/** The custodians this person is known to — `auth.users.provider_refs` (0118). */
	providers: MemberProvider[];
	/** True when the ROW is a placeholder minted from a custodian's own directory rather than a
	 *  WorkOS login (0123): disabled, holding no portal, and not able to sign in. */
	from_provider: boolean;
	/** Which SIDE of the firm this login is on (`auth.users.type`, 0124) — `client` and/or
	 *  `staff`. Distinct from `portals`, which is what they may DO: this is what they ARE, and it
	 *  is never an authorisation input. Empty means nobody has said. */
	type: string[];
	/** Whether this member of staff may hold a client book (0125). A boolean and not a `type`
	 *  member because advising is a capability WITHIN staff — `{client, adviser}` is not a person.
	 *  No custodian can tell us, so an operator states it. */
	is_adviser: boolean;
	/** The firm's own words about them (0118), as the Edit drawer edits them. */
	first_name: string | null;
	last_name: string | null;
	direct_phone: string | null;
	job_title: string | null;
	qualifications: string[];
	fca_reference: string | null;
	bio: string | null;
	photo_url: string | null;
};

export type Environment = {
	id: number;
	/** The row's address — `/admin/environments/{uid}/secrets` is what a save PUTs. */
	uid: string;
	stage: string;
	workos_client_id: string | null;
	issuer: string | null;
	client_portal_host: string | null;
	platform_host: string | null;
	operations_host: string | null;
	firm_org_id: string | null;
	client_portal_client_id: string | null;
	platform_client_id: string | null;
	operations_client_id: string | null;
	has_secrets: boolean;
	has_client_portal_secret: boolean;
	has_platform_secret: boolean;
	has_operations_secret: boolean;
};

export type Portal = 'client' | 'platform' | 'operations';
export type Permission = {
	slug: string;
	name: string;
	resource: string | null;
	action: string | null;
	portal: Portal | null;
	description: string | null;
};
export type OrgRole = {
	slug: string;
	name: string;
	description: string | null;
	portal: Portal | null;
	permissions: string[];
};

// --- Integrations (SEI custodian/software platform approvals, feature toggles, and job runs) —
// apps/admin/src/routes/integrations/** ---------------------------------------------------------

export type IntegrationStatus = 'pending' | 'approved' | 'rejected';

/** A code/description pair — a feature, as `GET /integrations/features` returns it. */
export type CodeRef = { code: string; description: string | null };

/** A real portals tenant (`TenantOut`) — the edit drawer's Firm dropdown (0210; firm_id is a real
 *  FK to `auth.tenants` now, not an SEI firm code). */
export type TenantRef = { id: number; name: string };

/**
 * One integration, as the list endpoint returns it (`PlatformIntegrationOut`).
 *
 * `uid` is the platform's uid (the nanoid `/integrations/{uid}` addresses) — the API's own field
 * name for it, not the numeric serial the schema also keeps internally.
 */
export type IntegrationSummary = {
	uid: string;
	platform: string | null;
	/** The portals tenant (`auth.tenants.id`) this integration belongs to. `null` until an
	 *  operator picks one. */
	firm_id: number | null;
	firm_description: string | null;
	tenant_id: string;
	tenant_name: string;
	status: IntegrationStatus;
	enabled: boolean;
	installed: boolean;
	feature_ids: string[];
	/** On the base (list) type, not detail-only: the edit sheet opens from either the list's
	 *  row-click or the detail page's header, and needs this to seed its Reason field either
	 *  way — a rejected integration's own stored reason, not a blank the operator must retype
	 *  before any other field can be saved. */
	rejection_reason: string | null;
	created_date: string;
	updated_date: string;
};

/** The detail endpoint's `integration` (`PlatformIntegrationDetailOut`) — every list field plus
 *  the approval trail. */
export type IntegrationDetail = IntegrationSummary & {
	application_data: Record<string, unknown> | null;
	approved_by: string | null;
	updated_by: string | null;
};

export type IntegrationDetailResponse = {
	integration: IntegrationDetail;
	firms: TenantRef[];
	features: CodeRef[];
	/** This integration's single most recent run, independent of the Job Runs tab's own page or
	 *  search — `null` when it has never run. See the Summary card's `lastRun`. */
	last_run: JobRun | null;
};

/** One job run, as `GET /integrations/{uid}/runs` returns it (`JobRunOut`). */
export type JobRun = {
	uid: string;
	run_date: string;
	feature: string;
	status: 'success' | 'failed';
	error_message: string | null;
	record_count: number | null;
	newly_added: number | null;
	failed: number | null;
	data_date: string | null;
};

export type JobRunsPage = { items: JobRun[]; total: number; page: number; limit: number };

// --- Clients and Accounts tabs (`/integrations/[uid]`, mapping a portals client to its Intelliflo
// Office client, and each of its accounts to a plan of that client) — apps/admin/src/routes/
// integrations/[uid]/ClientsMapping.svelte and AccountsMapping.svelte -----------------------------

/** One of the picked client's own platform accounts (`AccountSearchResult`) — the Accounts
 *  dropdown, populated once a Client is chosen. */
export type AccountSearchResult = {
	account_uid: string;
	display_name: string;
};

/** An Intelliflo client search hit (`IoClientOut`) — same shape the platform portal's "Link to
 *  IO" card already uses; `raw` travels through to the Link POST verbatim. */
export type IoClientSearchResult = {
	client_id_io: string;
	display_name: string | null;
	raw: Record<string, unknown>;
};

/** An Intelliflo plan (`IoPlanOut`) — the IO Accounts dropdown. Unlike the platform portal's "IO
 *  Plans" dropdown, a plan with no `policy_number` yet is still offered: pushing one in is this
 *  tab's whole point. */
export type IoPlanSearchResult = {
	plan_id_io: string;
	policy_number: string | null;
	reference: string | null;
	plan_type: string | null;
	portfolio_category: string | null;
	raw: Record<string, unknown>;
};

/** The account/plan half of an already-complete mapping (`LinkedPairOut`) — embedded on a
 *  `ClientsTableRow` whose client already has a full Intelliflo link, so the row restores it on
 *  page load without a second round trip. */
export type LinkedPair = {
	client: IoClientSearchResult;
	account_uid: string;
	account_display_name: string;
	plan: IoPlanSearchResult;
};

/** One client row (`ClientsTableRowOut`) from `GET .../clients-table` — the Clients and Accounts
 *  tab's Client column, a straight paginated listing of `crm.contacts` (cross-tenant, see the
 *  backend's own note on why) rather than a search box. `tenant_name` disambiguates same-named
 *  clients across different firms. */
export type ClientsTableRow = {
	contact_uid: string;
	contact_display_name: string;
	contact_tenant_name: string | null;
	/** The Intelliflo client this client is mapped to, or null. Independent of accounts. */
	io_client: IoClientSearchResult | null;
	/** How many of this client's accounts carry an Intelliflo link. While above zero the client's
	 *  Intelliflo client cannot be changed: the accounts are unlinked first, one at a time. */
	linked_account_count: number;
	existing_link: LinkedPair | null;
};

/** One page of the Clients table (`ClientsTablePageOut`). */
export type ClientsTablePage = {
	items: ClientsTableRow[];
	total: number;
};

/** One ACCOUNT of a client that is already mapped to an Intelliflo client (`AccountsTableRowOut`)
 *  from `GET .../accounts-table`: the Accounts tab has a row per account. */
export type AccountsTableRow = {
	account_uid: string;
	account_display_name: string;
	account_number: string | null;
	contact_uid: string;
	contact_display_name: string;
	/** The Intelliflo client the account's client is mapped to; the plans dropdown is its plans. */
	client_id_io: string;
	/** The account's current plan link, or null when it is not linked yet. */
	plan: IoPlanSearchResult | null;
};

/** One page of the Accounts table (`AccountsTablePageOut`). */
export type AccountsTablePage = {
	items: AccountsTableRow[];
	total: number;
	/** Accounts whose client is not mapped yet: they cannot be linked until it is. */
	accounts_awaiting_client: number;
};

/** What unlinking a client did (`IoClientUnlinkOut`): false when it had no Intelliflo link. */
export type IoClientUnlinkResult = {
	unlinked: boolean;
	client_id_io: string | null;
};

/** What unlinking an account did (`IoUnlinkOut`). */
export type IoUnlinkResult = {
	unlinked: boolean;
	plan_id_io: string | null;
	policy_number_cleared: boolean;
	note: string | null;
};
