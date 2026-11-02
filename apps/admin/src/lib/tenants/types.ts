// Shapes returned by GET /admin/tenants/{id}/overview — the readiness panel's contract.
// Defined here rather than in the route's server module so the component that renders them
// does not have to import from a +page.server file.

export type SectionState = 'ok' | 'pending' | 'attention';

export type TenantOverview = {
	tenant_id: number;
	sections: {
		platforms: {
			state: SectionState;
			connected: number;
			configured: number;
			never_synced: string[];
			providers: {
				provider: string;
				/** The provider's display name, from the API's registry. */
				name: string;
				enabled: boolean;
				last_fanout_at: string | null;
				slices: number;
			}[];
		};
		storage: {
			state: SectionState;
			bucket: string | null;
			// Null until the first scan. NOT zero — see app/storage_usage.py.
			usage: {
				datalake_bytes: number;
				datalake_objects: number;
				files_bytes: number;
				files_objects: number;
				objects_truncated: boolean;
				scan_ms: number | null;
				error: string | null;
				computed_at: string;
			} | null;
		};
		workos: {
			state: SectionState;
			project_id: string | null;
			members: number;
			staff_members: number;
			client_members: number;
			pending: number;
			roles: number;
		};
		branding: {
			state: SectionState;
			configured: boolean;
			custom_pages: boolean;
			tokens: number;
		};
	};
};
