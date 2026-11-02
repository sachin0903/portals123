<script lang="ts" module>
	export type StepState = 'done' | 'open' | 'waiting' | 'advisory' | 'none';
	export type Step = {
		key: string;
		label: string;
		state: StepState;
		/** Shown as a badge on an `open` step when above zero. */
		count?: number | null;
		/** Becomes the step's tooltip text. */
		hint?: string | null;
	};
</script>

<script lang="ts">
	import CheckIcon from '@lucide/svelte/icons/check';
	import ClockIcon from '@lucide/svelte/icons/clock';
	import MinusIcon from '@lucide/svelte/icons/minus';
	import { cn } from '../../../utils.js';

	// A horizontal step bar with the same node/line grammar as OnboardingStages, but each step
	// carries a compliance state rather than a position. With `onselect` the steps are buttons;
	// without it they are plain items.
	let {
		steps,
		active = null,
		onselect,
		class: className
	}: {
		steps: Step[];
		/** Key of the step the page has expanded: gets the selected look and aria-current. */
		active?: string | null;
		onselect?: (key: string) => void;
		class?: string;
	} = $props();

	const NODE: Record<StepState, string> = {
		done: 'border-success bg-success text-success-foreground',
		open: 'border-primary bg-primary text-primary-foreground',
		waiting: 'border-border text-muted-foreground',
		advisory: 'border-warning-line bg-warning-subtle text-warning-strong',
		none: 'border-border text-muted-foreground'
	};

	function srText(s: Step): string {
		switch (s.state) {
			case 'done':
				return 'done';
			case 'open':
				return s.count && s.count > 0 ? `needs review, ${s.count}` : 'needs review';
			case 'waiting':
				return 'waiting';
			case 'advisory':
				return 'advisory';
			default:
				return 'not started';
		}
	}
</script>

{#if steps.length}
	<!-- The list scrolls sideways on a narrow screen, and a scroll box clips on every side: the
	     padding gives the active step's ring and the count badge room inside it, and the negative
	     margin keeps the bar where it would sit without it. -->
	<ol data-slot="stepper" class={cn('-m-2 flex items-start overflow-x-auto p-2', className)}>
		{#each steps as s, i (s.key)}
			{@const isActive = active === s.key}
			{@const last = i === steps.length - 1}
			<li class={cn('flex items-center', last ? 'flex-none' : 'min-w-[5.5rem] flex-1')}>
				{#snippet body()}
					<span class="relative flex shrink-0">
						<span
							class={cn(
								'flex size-6 items-center justify-center rounded-full border-2 text-[11px] font-semibold',
								NODE[s.state],
								isActive && 'ring-ring/40 ring-offset-background ring-2 ring-offset-2'
							)}
						>
							{#if s.state === 'done'}
								<CheckIcon class="size-3.5" aria-hidden="true" />
							{:else if s.state === 'waiting'}
								<ClockIcon class="size-3.5" aria-hidden="true" />
							{:else if s.state === 'advisory'}
								<span class="bg-warning size-2 rounded-full" aria-hidden="true"></span>
							{:else if s.state === 'none'}
								<MinusIcon class="size-3.5" aria-hidden="true" />
							{:else}
								<span class="bg-primary-foreground size-1.5 rounded-full" aria-hidden="true"></span>
							{/if}
						</span>
						{#if s.state === 'open' && s.count && s.count > 0}
							<span
								data-slot="stepper-count"
								class="bg-destructive text-destructive-foreground absolute -top-1.5 -right-2.5 min-w-4 rounded-full px-1 text-center text-[10px] leading-4 font-semibold"
								aria-hidden="true">{s.count}</span
							>
						{/if}
					</span>
					<span
						class={cn(
							'max-w-[5.5rem] text-center text-[10px] leading-tight font-medium sm:max-w-none sm:whitespace-nowrap',
							s.state === 'none' || s.state === 'waiting'
								? 'text-muted-foreground'
								: 'text-foreground',
							isActive && 'font-semibold'
						)}>{s.label}</span
					>
					<span class="sr-only">: {srText(s)}</span>
				{/snippet}
				{#if onselect}
					<button
						type="button"
						title={s.hint ?? undefined}
						aria-current={isActive ? 'step' : undefined}
						class="hover:bg-muted focus-visible:ring-ring flex flex-col items-center gap-1 rounded-md px-2 py-1 focus-visible:ring-2 focus-visible:outline-none"
						onclick={() => onselect(s.key)}
					>
						{@render body()}
					</button>
				{:else}
					<div
						title={s.hint ?? undefined}
						aria-current={isActive ? 'step' : undefined}
						class="flex flex-col items-center gap-1 px-2 py-1"
					>
						{@render body()}
					</div>
				{/if}
				{#if !last}
					<div
						class={cn(
							'-mt-4 h-0.5 min-w-2 flex-1',
							s.state === 'done' ? 'bg-success' : 'bg-border'
						)}
					></div>
				{/if}
			</li>
		{/each}
	</ol>
{/if}
