<script lang="ts">
	import { page } from '$app/state';
	import type { NavGroup } from '@graphene/ui';
	import FirmBookSearchBar from '$lib/FirmBookSearchBar.svelte';
	import FirmsSearchBar from '$lib/FirmsSearchBar.svelte';
	import { canSearch, canSearchFirmBook } from '$lib/search';

	/**
	 * Dual-mode header search: firms-only globally; platform-parity book search when drilled into
	 * `/tenants/[uid]/…`. Layout passes the principal as props (not `page.data.permissions`) so
	 * role-editor pages that return a grantable-slug catalogue do not hide the bar.
	 */
	let {
		permissions = [],
		role = null,
		nav = [],
		userKey = null
	}: {
		permissions?: string[];
		role?: string | null;
		nav?: NavGroup[];
		userKey?: string | null;
	} = $props();

	const tenantUid = $derived(
		page.url.pathname.startsWith('/tenants/') && page.params.uid ? page.params.uid : null
	);
	const firmBook = $derived(tenantUid !== null && canSearchFirmBook(permissions, role));
	const firms = $derived(tenantUid === null && canSearch(permissions, role));
</script>

{#if firmBook && tenantUid}
	<FirmBookSearchBar {tenantUid} {nav} {permissions} {role} {userKey} />
{:else if firms}
	<FirmsSearchBar {permissions} {role} />
{/if}
