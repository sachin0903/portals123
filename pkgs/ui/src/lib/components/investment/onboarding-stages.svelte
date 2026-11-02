<script lang="ts">
	import CheckIcon from '@lucide/svelte/icons/check';
	import type { PortalOnboardingStage } from './types.js';

	// The bare numbered-circle stepper — no card, no link, no self-hide condition. Shared between
	// the onboardingProgress block's 'milestones' look (onboarding-milestones.svelte, which adds
	// the clickable card chrome) and the client's dedicated /onboarding page (which is already the
	// destination, so it needs the stepper with none of that wrapping). Same node/line grammar as
	// the staff-facing journey-detail.svelte stepper.
	let { stages }: { stages: PortalOnboardingStage[] } = $props();
</script>

{#if stages.length}
	<ol class="flex items-start">
		<!-- Position keeps two journeys of one flow from sharing a key. A repeat throws and blanks
		     the page; the key alone is the stage, which is the same word on every onboarding. -->
		{#each stages as st, i (`${st.key}:${i}`)}
			<li class="flex flex-1 items-center {i === stages.length - 1 ? 'flex-none' : ''}">
				<div class="flex flex-col items-center gap-1">
					<span
						class="flex size-6 shrink-0 items-center justify-center rounded-full border-2 text-[11px] font-semibold
						{st.status === 'done'
							? 'border-brand bg-brand text-brand-foreground'
							: st.status === 'current'
								? 'border-brand text-brand'
								: 'border-border text-muted-foreground'}"
					>
						{#if st.status === 'done'}<CheckIcon class="size-3.5" />{:else}{i + 1}{/if}
					</span>
					<!-- The label WRAPS on a phone and only goes single-line from `sm` up: five stages of
					     "Getting your details" length across a 360px viewport overflowed the row, so the
					     later milestones were clipped off the edge on the device most clients read this
					     on. The width cap is what gives it something to wrap within. -->
					<span
						class="max-w-[5.5rem] text-center text-[10px] leading-tight font-medium sm:max-w-none sm:whitespace-nowrap {st.status ===
						'upcoming'
							? 'text-muted-foreground'
							: 'text-foreground'}">{st.label}</span
					>
				</div>
				{#if i < stages.length - 1}
					<div
						class="mx-1 h-0.5 flex-1 {st.status === 'done' ? 'bg-brand' : 'bg-border'} -mt-4"
					></div>
				{/if}
			</li>
		{/each}
	</ol>
{/if}
