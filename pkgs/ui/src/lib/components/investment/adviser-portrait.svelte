<script lang="ts">
	import Building2Icon from '@lucide/svelte/icons/building-2';
	import { cn } from '../../utils.js';
	import { initials as adviserInitials } from '../../initials.js';
	import { formatPhoneNumber, phoneIssue } from '../../forms/editor/masks.js';
	import type { Adviser } from './types.js';

	// The 'portrait' adviser look — the concierge. A centred column with no card: a large
	// initials disc in the brand tint, the name set in the display face, the firm as a
	// small-caps line, then quiet text actions. The email address and phone number are SHOWN
	// as visible text beside the links (a review flagged that adviser-card hides them inside
	// buttons — a client should be able to read the number, not just tap it).
	//
	// NB the brief's `--brand-soft` doesn't exist in theme.css; the toning system is
	// `--brand-subtle` (wash) + `--brand-strong` (ink guaranteed readable on it), so the disc
	// uses those.
	let { adviser }: { adviser: Adviser } = $props();

	// The FIRM standing in for an unassigned adviser (types.ts `kind`): no heading (the logo and
	// name introduce it — adviser-card.svelte), the firm's icon (contained, not cropped) or a
	// building for the disc, no firm line under a firm.
	const isFirm = $derived(adviser.kind === 'firm');

	// Same derivation as adviser-card (lib/initials.ts). Still the fallback, not the default: a
	// firm that has uploaded a headshot (`auth.users.photo_url`, 0118) gets it, and one that has
	// not gets exactly what it got before.
	const initials = $derived(adviserInitials(adviser.name, ''));
</script>

<div class="flex h-full flex-col items-center justify-center py-4 text-center">
	{#if !isFirm}
		<p class="text-muted-foreground text-xs font-medium tracking-[0.18em] uppercase">
			Your adviser
		</p>
	{/if}
	{#if adviser.photoUrl}
		<!-- alt is empty and the name is read from the heading below: a screen reader announcing
		     "photo of Tom Baxter" immediately before "Tom Baxter" says it twice. -->
		<img
			src={adviser.photoUrl}
			alt=""
			class={cn(
				'bg-brand-subtle mt-4 size-16 rounded-full',
				isFirm ? 'object-contain p-3' : 'object-cover'
			)}
		/>
	{:else}
		<div
			class="bg-brand-subtle text-brand-strong mt-4 flex size-16 items-center justify-center rounded-full text-xl font-medium"
			aria-hidden="true"
		>
			{#if isFirm}
				<Building2Icon class="size-7" />
			{:else}
				{initials}
			{/if}
		</div>
	{/if}
	<!-- The display face — the same themable numeral/heading family the ledger header uses;
	     an unthemed tenant falls back to the body face, never the UA serif. -->
	<p
		class="mt-3 text-xl font-medium tracking-tight"
		style="font-family: var(--font-display, var(--font-sans))"
	>
		{adviser.name}
	</p>
	<!-- The job title sits between the name and the firm because that is the order it is read in:
	     who, what they do, where. Rendered only when the firm has said — an empty row under a name
	     reads as missing data. -->
	{#if isFirm}
		<p class="text-muted-foreground mt-1 text-sm">Client support</p>
	{:else}
		{#if adviser.jobTitle}
			<p class="text-muted-foreground mt-1 text-sm">{adviser.jobTitle}</p>
		{/if}
		<p class="text-muted-foreground mt-1 text-xs font-medium tracking-[0.16em] uppercase">
			{adviser.firm}
		</p>
	{/if}
	<!-- Qualifications are the reason a client trusts the person, so they are shown as discrete
	     designations rather than joined into a sentence: "DipPFS" and "Chartered FCSI" are two
	     credentials, and a comma-joined string reads as one. -->
	{#if adviser.qualifications?.length}
		<ul class="mt-2 flex flex-wrap justify-center gap-1.5" aria-label="Qualifications">
			{#each adviser.qualifications as qualification (qualification)}
				<li
					class="border-border text-muted-foreground rounded-full border px-2 py-0.5 text-[11px] font-medium"
				>
					{qualification}
				</li>
			{/each}
		</ul>
	{/if}
	{#if adviser.bio}
		<p class="text-muted-foreground mx-auto mt-3 max-w-[34ch] text-sm leading-relaxed">
			{adviser.bio}
		</p>
	{/if}
	<div class="mt-3 flex flex-col items-center text-sm">
		<!-- Each action row is min-h-11 so the plain-text links keep a ≥44px touch target. -->
		{#if adviser.email}
			<p class="flex min-h-11 flex-wrap items-center justify-center gap-x-2">
				<a href="mailto:{adviser.email}" class="text-brand font-medium hover:underline">Email</a>
				<span class="text-muted-foreground break-all">{adviser.email}</span>
			</p>
		{/if}
		{#if adviser.phone}
			<p class="flex min-h-11 flex-wrap items-center justify-center gap-x-2">
				<a href="tel:{adviser.phone}" class="text-brand font-medium hover:underline">Call</a>
				<!-- Stored canonical (`+442079460000`), read grouped. A value that is not a number
				     the phone field accepts is shown exactly as stored rather than mangled. -->
				<span class="text-muted-foreground"
					>{phoneIssue(adviser.phone) ? adviser.phone : formatPhoneNumber(adviser.phone)}</span
				>
			</p>
		{/if}
		<p class="flex min-h-11 items-center">
			<a href="/messages" class="text-brand font-medium hover:underline">Message</a>
		</p>
	</div>
</div>
