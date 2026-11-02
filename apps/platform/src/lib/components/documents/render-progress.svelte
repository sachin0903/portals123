<script lang="ts">
	/**
	 * The wait while a document renders: where it has got to, how long it has taken, and — when a
	 * phase runs long — what is probably wrong.
	 *
	 * Both halves of the wait are the server's own state (`documents.status`), so this cannot say a
	 * phase is finished when it is not. See `render-progress.ts` for why there is no time-based
	 * progress: a bar that advances on a timer made a render worker that was not running look
	 * exactly like a slow render.
	 */
	import { Button } from '@graphene/ui';
	import CheckIcon from '@lucide/svelte/icons/check';
	import LoaderCircleIcon from '@lucide/svelte/icons/loader-circle';
	import TriangleAlertIcon from '@lucide/svelte/icons/triangle-alert';
	import { elapsedLabel, fraction, stallNote, steps, type RenderStatus } from './render-progress';

	let {
		status,
		elapsedMs,
		/** What is being rendered, for the heading. */
		title,
		subtitle = undefined,
		/**
		 * Stop waiting and stop the render. Absent ⇒ no Cancel is offered, which is how a surface
		 * without the permission (or without a document to cancel yet) stays read-only.
		 */
		oncancel = undefined,
		cancelling = false
	}: {
		status: RenderStatus;
		elapsedMs: number;
		title: string;
		subtitle?: string;
		oncancel?: (() => void) | undefined;
		cancelling?: boolean;
	} = $props();

	const rows = $derived(steps(status));
	const pct = $derived(Math.round(fraction(status) * 100));
	const note = $derived(stallNote(status, elapsedMs));
	const active = $derived(rows.find((r) => r.state === 'active') ?? null);
</script>

<div class="mx-auto flex max-w-md flex-col gap-5 py-20">
	<div class="text-center">
		<p class="font-semibold">{title}</p>
		{#if subtitle}
			<p class="text-muted-foreground mt-1 text-sm">{subtitle}</p>
		{/if}
	</div>

	<div class="space-y-2">
		<!-- `aria-valuenow` carries the same number the bar draws, so the readout and the announced
		     value cannot disagree. -->
		<div
			class="bg-muted h-1.5 w-full overflow-hidden rounded-full"
			role="progressbar"
			aria-valuemin="0"
			aria-valuemax="100"
			aria-valuenow={pct}
			aria-label="Document progress"
		>
			<div
				class="bg-primary h-full rounded-full transition-[width] duration-500 ease-out"
				style:width={`${pct}%`}
			></div>
		</div>
		<div class="text-muted-foreground flex items-center justify-between text-xs">
			<span>{active?.detail ?? ''}</span>
			<!-- A ticking readout is the honest substitute for a moving bar: it always changes, and it
			     never implies work is happening. -->
			<span class="tabular-nums">{elapsedLabel(elapsedMs)}</span>
		</div>
	</div>

	<ol class="space-y-2.5">
		{#each rows as step (step.key)}
			<li class="flex items-center gap-2.5 text-sm">
				<span
					class={[
						'flex size-5 shrink-0 items-center justify-center rounded-full border',
						step.state === 'done'
							? 'border-primary bg-primary text-primary-foreground'
							: step.state === 'active'
								? 'border-primary text-primary'
								: 'border-muted text-muted-foreground'
					].join(' ')}
				>
					{#if step.state === 'done'}
						<CheckIcon class="size-3" />
					{:else if step.state === 'active'}
						<LoaderCircleIcon class="size-3 animate-spin" />
					{/if}
				</span>
				<span class={step.state === 'pending' ? 'text-muted-foreground' : 'font-medium'}>
					{step.label}
				</span>
			</li>
		{/each}
	</ol>

	{#if oncancel && status !== 'generated'}
		<!-- The way out. Without it the only exit from a wait that can last minutes was the browser's
		     back button, which left the job queued to surprise the operator later. Quiet styling: it
		     is an escape hatch, not the expected next step. -->
		<div class="flex justify-center">
			<Button variant="ghost" size="sm" onclick={oncancel} disabled={cancelling}>
				{cancelling ? 'Cancelling…' : 'Cancel'}
			</Button>
		</div>
	{/if}

	{#if note}
		<!-- Amber, not destructive: nothing has failed. The document will still finish on its own, so
		     this explains the wait rather than announcing an error. -->
		<div class="border-warning/40 bg-warning/10 flex gap-2.5 rounded-lg border p-3">
			<TriangleAlertIcon class="text-warning mt-0.5 size-4 shrink-0" />
			<p class="text-xs">{note}</p>
		</div>
	{/if}
</div>
