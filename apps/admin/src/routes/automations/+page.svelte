<script lang="ts">
	// The estate-wide automation kill switch — `automations:global_pause`, which until now was a
	// permission with nothing behind it. Its own page rather than a tile on the dashboard: the
	// dashboard is a `tenants:read` surface, and this control is granted independently of it.
	import { enhance } from '$app/forms';
	import { invalidateAll } from '$app/navigation';
	import {
		Alert,
		AlertDescription,
		AlertTitle,
		Button,
		Card,
		CardContent,
		CardDescription,
		CardHeader,
		CardTitle,
		ConfirmDialog,
		Label,
		LoadErrorBanner,
		PageHeader,
		StatusBadge,
		Textarea,
		toast
	} from '@graphene/ui';
	import OctagonXIcon from '@lucide/svelte/icons/octagon-x';

	let { data, form } = $props();

	const pause = $derived(data.state);
	const paused = $derived(pause?.paused === true);
	const f = $derived((form ?? {}) as { error?: string; reason?: string });

	let reason = $state('');
	let busy = $state(false);
	let confirmOpen = $state(false);
	let pauseForm = $state<HTMLFormElement | null>(null);

	function when(value: string | null | undefined): string {
		if (!value) return '—';
		const parsed = new Date(value);
		return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('en-GB');
	}
</script>

<svelte:head><title>Automations · Operations</title></svelte:head>

<PageHeader
	eyebrow="Internal operations"
	title="Automations"
	description="The estate-wide kill switch. Stops every firm's automations at once."
/>

<div class="space-y-6">
	{#if data.loadError}
		<LoadErrorBanner message={data.loadError} onRetry={() => invalidateAll()} />
	{/if}

	{#if paused}
		<Alert variant="warning">
			<OctagonXIcon class="size-4" />
			<AlertTitle>Automations are stopped across the platform</AlertTitle>
			<AlertDescription>
				No firm's rules are firing and no journey is advancing unattended. Anything that would have
				fired while this is on is dropped, not queued — resuming does not release a backlog.
			</AlertDescription>
		</Alert>
	{/if}

	{#if f.error}
		<Alert variant="destructive"><AlertDescription>{f.error}</AlertDescription></Alert>
	{/if}

	<Card glass>
		<CardHeader>
			<CardTitle class="flex flex-wrap items-center gap-2">
				Platform switch
				<StatusBadge tone={paused ? 'warning' : 'success'}>
					{paused ? 'Stopped' : 'Running'}
				</StatusBadge>
			</CardTitle>
			<CardDescription>
				Separate from a firm's own pause: a firm that stopped its own automations stays stopped when
				this is released.
			</CardDescription>
		</CardHeader>
		<CardContent class="space-y-6">
			{#if paused}
				<div class="grid gap-x-8 gap-y-3 text-sm sm:grid-cols-3">
					<div>
						<p class="text-muted-foreground text-xs">Stopped by</p>
						<p>{pause?.paused_by_name ?? '—'}</p>
					</div>
					<div>
						<p class="text-muted-foreground text-xs">Stopped at</p>
						<p>{when(pause?.paused_at)}</p>
					</div>
					<div class="sm:col-span-3">
						<p class="text-muted-foreground text-xs">Reason</p>
						<p>{pause?.reason ?? '—'}</p>
					</div>
				</div>
			{/if}

			<form
				method="POST"
				action="?/setPause"
				bind:this={pauseForm}
				use:enhance={() => {
					busy = true;
					return async ({ result, update }) => {
						busy = false;
						if (result.type === 'success') {
							toast.success(paused ? 'Automations restarted.' : 'Automations stopped.');
							reason = '';
						} else if (result.type === 'failure')
							toast.error(String(result.data?.error ?? 'Something went wrong.'));
						await update();
					};
				}}
				class="space-y-4"
			>
				<input type="hidden" name="paused" value={paused ? 'false' : 'true'} />
				{#if !paused}
					<div class="space-y-2">
						<Label for="reason">Why are you stopping automations?</Label>
						<Textarea
							id="reason"
							name="reason"
							rows={2}
							bind:value={reason}
							placeholder="Which rule, which firm, what it is doing"
						/>
						<p class="text-muted-foreground text-xs">
							Shown to every operator who opens this page, and kept in the audit trail.
						</p>
					</div>
				{/if}
				<Button
					type="button"
					variant={paused ? 'default' : 'destructive'}
					disabled={busy}
					onclick={() => (confirmOpen = true)}
				>
					{#if busy}
						Working…
					{:else if paused}
						Restart automations
					{:else}
						Stop all automations
					{/if}
				</Button>
			</form>
		</CardContent>
	</Card>
</div>

<ConfirmDialog
	bind:open={confirmOpen}
	title={paused ? 'Restart automations?' : 'Stop every automation?'}
	description={paused
		? 'Rules start firing again for every firm; nothing held during the stop is released.'
		: 'Every firm’s rules stop immediately, and anything that would have fired meanwhile is dropped.'}
	confirmLabel={paused ? 'Restart' : 'Stop everything'}
	destructive={!paused}
	onconfirm={() => pauseForm?.requestSubmit()}
/>
