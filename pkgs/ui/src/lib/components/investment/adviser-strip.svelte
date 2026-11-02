<script lang="ts">
	import Building2Icon from '@lucide/svelte/icons/building-2';
	import MailIcon from '@lucide/svelte/icons/mail';
	import MessageCircleIcon from '@lucide/svelte/icons/message-circle';
	import PhoneIcon from '@lucide/svelte/icons/phone';
	import { cn } from '../../utils.js';
	import { initials as adviserInitials } from '../../initials.js';
	import type { Adviser } from './types.js';

	// The 'strip' adviser look — one slim horizontal band (~72px) for narrow columns and page
	// footers. Hairline rules top and bottom, transparent fill (it borrows whichever page
	// ground it sits on), initials disc + name/firm stacked tight on the left, actions
	// right-aligned. The action labels shed by CONTAINER width — icon-only in a narrow span —
	// so each link carries a permanent aria-label.
	let { adviser }: { adviser: Adviser } = $props();

	// The FIRM standing in for an unassigned adviser (types.ts `kind`): its icon or a building
	// for the disc, "Client support" where the job title and firm would go.
	const isFirm = $derived(adviser.kind === 'firm');

	// Same derivation as adviser-card. See lib/initials.ts.
	const initials = $derived(adviserInitials(adviser.name, ''));
</script>

<div class="@container">
	<div class="flex min-h-[72px] items-center gap-3 border-y py-3">
		{#if adviser.photoUrl}
			<img
				src={adviser.photoUrl}
				alt=""
				class={cn(
					'bg-brand-subtle size-10 shrink-0 rounded-full',
					isFirm ? 'object-contain p-1.5' : 'object-cover'
				)}
			/>
		{:else}
			<div
				class="bg-brand-subtle text-brand-strong flex size-10 shrink-0 items-center justify-center rounded-full text-sm font-medium"
				aria-hidden="true"
			>
				{#if isFirm}
					<Building2Icon class="size-4" />
				{:else}
					{initials}
				{/if}
			</div>
		{/if}
		<div class="min-w-0 flex-1">
			<p class="truncate text-sm leading-tight font-medium">{adviser.name}</p>
			<!-- The compact look takes the job title and NOT the qualifications: this is one 72px
			     row beside two actions, and a third line of designations would push the firm out.
			     The portrait look is where the full credentials belong. -->
			{#if isFirm}
				<p class="text-muted-foreground truncate text-xs leading-tight">Client support</p>
			{:else}
				{#if adviser.jobTitle}
					<p class="text-muted-foreground truncate text-xs leading-tight">{adviser.jobTitle}</p>
				{/if}
				<p class="text-muted-foreground truncate text-xs leading-tight">{adviser.firm}</p>
			{/if}
		</div>
		<div class="flex shrink-0 items-center">
			<!-- min-h/w-11 keeps every action a ≥44px touch target even when it collapses to a
			     bare icon in a narrow column. An address or number the firm has not given draws no
			     action at all. -->
			{#if adviser.email}
				<a
					href="mailto:{adviser.email}"
					aria-label="Email {adviser.name}"
					class="text-brand hover:text-brand-strong inline-flex min-h-11 min-w-11 items-center justify-center gap-1.5 px-2 text-sm font-medium"
				>
					<MailIcon class="size-4" aria-hidden="true" />
					<span class="@md:inline hidden">Email</span>
				</a>
			{/if}
			{#if adviser.phone}
				<a
					href="tel:{adviser.phone}"
					aria-label="Call {adviser.name}"
					class="text-brand hover:text-brand-strong inline-flex min-h-11 min-w-11 items-center justify-center gap-1.5 px-2 text-sm font-medium"
				>
					<PhoneIcon class="size-4" aria-hidden="true" />
					<span class="@md:inline hidden">Call</span>
				</a>
			{/if}
			<a
				href="/messages"
				aria-label="Message {adviser.name}"
				class="text-brand hover:text-brand-strong inline-flex min-h-11 min-w-11 items-center justify-center gap-1.5 px-2 text-sm font-medium"
			>
				<MessageCircleIcon class="size-4" aria-hidden="true" />
				<span class="@md:inline hidden">Message</span>
			</a>
		</div>
	</div>
</div>
