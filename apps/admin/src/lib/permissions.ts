/**
 * Frontend capability gating for the Internal Operations portal.
 *
 * Re-exports the single canonical permission module generated from the API's
 * registry. The API enforces authorisation regardless — `can` only shapes what
 * the UI offers (`system`/`admin` bypass, and `:write` implies `:read`).
 */
export {
	can,
	satisfies,
	PERMISSIONS,
	PERMISSION_SLUGS,
	type PermissionSlug
} from '@graphene/api-client/permissions';
