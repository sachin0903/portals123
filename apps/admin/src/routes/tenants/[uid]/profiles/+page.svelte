<script lang="ts">
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import {
		EmptyState,
		Input,
		LoadErrorBanner,
		PageHeader,
		StatusBadge,
		humanise
	} from '@graphene/ui';
	import ContactRoundIcon from '@lucide/svelte/icons/contact-round';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	let search = $state('');
	$effect(() => {
		search = data.query;
	});

	function applySearch(): void {
		const params = new URLSearchParams(page.url.searchParams);
		const term = search.trim();
		if (term) params.set('q', term);
		else params.delete('q');
		const qs = params.toString();
		goto(qs ? `${page.url.pathname}?${qs}` : page.url.pathname, {
			keepFocus: true,
			noScroll: true,
			replaceState: true
		});
	}

	function statusOf(profile: PageData['profiles'][number]): string {
		const portal = profile.portals?.[0];
		if (portal?.status) return portal.status;
		return profile.enabled ? 'active' : 'inactive';
	}
</script>

<svelte:head><title>Profiles · {data.tenant.name}</title></svelte:head>

<div class="space-y-5">
	<PageHeader
		title="Profiles"
		description="Client-portal logins for this firm — the same directory advisers manage under Firm settings."
	/>

	{#if data.forbidden}
		<p class="text-muted-foreground text-sm">
			Operating this firm’s portal requires <code class="text-xs">tenant_portal:read</code> to load profiles.
		</p>
	{:else if data.loadError}
		<LoadErrorBanner message="Couldn’t load profiles." onRetry={() => invalidateAll()} />
	{:else}
		<form
			class="max-w-md"
			onsubmit={(e) => {
				e.preventDefault();
				applySearch();
			}}
		>
			<Input placeholder="Search profiles…" bind:value={search} />
		</form>

		{#if data.profiles.length === 0}
			<EmptyState
				icon={ContactRoundIcon}
				title={data.query ? 'No profiles match that search' : 'No profiles yet'}
				description={data.query
					? 'Try another name or email, or clear the search.'
					: 'A profile is a client-portal login linked to a contact in this firm’s book.'}
			/>
		{:else}
			<div class="divide-border divide-y rounded-md border">
				{#each data.profiles as profile (profile.user_uid)}
					{@const status = statusOf(profile)}
					<div class="flex items-center gap-3 px-4 py-3">
						<div class="min-w-0 flex-1">
							<p class="truncate text-sm font-medium">{profile.name || profile.email}</p>
							<p class="text-muted-foreground truncate text-xs">{profile.email}</p>
						</div>
						<StatusBadge
							tone={status === 'active' ? 'success' : status === 'pending' ? 'warning' : 'neutral'}
							>{humanise(status)}</StatusBadge
						>
					</div>
				{/each}
			</div>
			{#if data.total > data.profiles.length}
				<p class="text-muted-foreground text-xs">
					Showing {data.profiles.length} of {data.total} profiles.
				</p>
			{/if}
		{/if}
	{/if}
</div>
