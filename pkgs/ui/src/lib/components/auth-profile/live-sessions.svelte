<script lang="ts">
	import { enhance } from '$app/forms';
	import { Button } from '../ui/button/index.js';
	import ConfirmDialog from '../ui/confirm-dialog/confirm-dialog.svelte';
	import StatusBadge from '../ui/status-badge/status-badge.svelte';
	import type { StatusTone } from '../ui/status-badge/status-badge.svelte';

	/** One live WorkOS session, as the API and the security pages both hand it over. */
	export type LiveSessionRow = {
		id: string;
		ip_address: string | null;
		user_agent: string | null;
		device: string | null;
		created_at: string | null;
		expires_at: string | null;
		current: boolean;
	};

	let {
		sessions = [],
		listed = true,
		canRevoke = false
	}: {
		sessions?: LiveSessionRow[];
		listed?: boolean;
		canRevoke?: boolean;
	} = $props();

	const when = (value: string | null) =>
		value
			? new Intl.DateTimeFormat('en-GB', { dateStyle: 'medium', timeStyle: 'short' }).format(
					new Date(value)
				)
			: null;

	// The expiry clock matches the sign-in clock (a year later, same minute), so a second
	// "2 Oct, 15:13" reads as the start repeated. The date is the part that differs.
	const expiryDate = (value: string) =>
		new Intl.DateTimeFormat('en-GB', { dateStyle: 'medium' }).format(new Date(value));

	const expiryTone = (value: string): StatusTone => {
		const days = (new Date(value).getTime() - Date.now()) / 86_400_000;
		if (days < 2) return 'danger';
		if (days < 14) return 'warning';
		return 'info';
	};

	let confirmOpen = $state(false);
	let pendingId = $state('');
	let formEl = $state<HTMLFormElement | undefined>(undefined);

	const ask = (id: string) => {
		pendingId = id;
		confirmOpen = true;
	};
</script>

{#if !listed}
	<p class="text-muted-foreground text-sm">Sessions could not be loaded.</p>
{:else if sessions.length === 0}
	<p class="text-muted-foreground text-sm">No browsers are signed in.</p>
{:else}
	<div class="divide-y">
		{#each sessions as session (session.id)}
			{@const device = session.device}
			<div class="flex flex-wrap items-start justify-between gap-3 py-3 first:pt-0 last:pb-0">
				<div class="min-w-0 flex-1">
					<div class="flex min-w-0 items-baseline gap-x-2">
						<span class="shrink-0 font-medium">{device ?? 'Unknown device'}</span>
						{#if session.user_agent}
							<span
								class="text-muted-foreground min-w-0 truncate text-xs font-normal"
								title={session.user_agent}
							>
								{session.user_agent}
							</span>
						{/if}
						{#if session.current}
							<span class="shrink-0"><StatusBadge tone="brand">This browser</StatusBadge></span>
						{/if}
					</div>
					<p class="text-muted-foreground flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
						<span>
							{session.ip_address ?? 'No IP recorded'}
							{#if session.created_at}
								· since {when(session.created_at)}
							{/if}
						</span>
						{#if session.expires_at}
							<StatusBadge tone={expiryTone(session.expires_at)}>
								Expires {expiryDate(session.expires_at)}
							</StatusBadge>
						{/if}
					</p>
				</div>
				{#if canRevoke && !session.current}
					<Button type="button" variant="outline" size="sm" onclick={() => ask(session.id)}>
						Sign out
					</Button>
				{/if}
			</div>
		{/each}
	</div>
{/if}

<form method="POST" action="?/revokeSession" class="hidden" bind:this={formEl} use:enhance>
	<input type="hidden" name="session_id" value={pendingId} />
</form>

<ConfirmDialog
	bind:open={confirmOpen}
	title="Sign out this session?"
	description="That browser will need to sign in again. This one stays signed in."
	confirmLabel="Sign out"
	destructive
	onconfirm={() => formEl?.requestSubmit()}
/>
