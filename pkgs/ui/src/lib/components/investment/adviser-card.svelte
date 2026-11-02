<script lang="ts">
	import Building2Icon from '@lucide/svelte/icons/building-2';
	import MailIcon from '@lucide/svelte/icons/mail';
	import MessageCircleIcon from '@lucide/svelte/icons/message-circle';
	import PhoneIcon from '@lucide/svelte/icons/phone';
	import * as Avatar from '../ui/avatar/index.js';
	import { buttonVariants } from '../ui/button/index.js';
	import { cn } from '../../utils.js';
	import { initials as adviserInitials } from '../../initials.js';
	import type { Adviser } from './types.js';

	let { adviser }: { adviser: Adviser } = $props();

	// The FIRM standing in for an unassigned adviser (types.ts `kind`): the same card with no
	// heading — the firm's own logo and name introduce it, and a "Your firm" line over them said
	// it twice — the firm's icon for a face and a building for the fallback: initials of a
	// company name ("GP") read as a person who does not exist. A person keeps "Your adviser":
	// the heading is what tells a client the name under it is their adviser.
	const isFirm = $derived(adviser.kind === 'firm');

	const initials = $derived(adviserInitials(adviser.name, ''));
</script>

<!-- One person, not a form. A wide span (the home card beside Documents) puts the actions on
     the same row as the name — a stacked trio under a left-huddled identity left the rest of
     the panel blank. A narrow span stacks them, which is all a phone has room for. The
     container is the card, so the break follows the span the firm gave the block, not the
     viewport. h-full still fills a shared grid track on the no-JS path. -->
<div class="glass-panel @container h-full rounded-xl p-5">
	<!-- The widget heading register (widget-header.svelte), on a panel rather than a Card. -->
	{#if !isFirm}
		<h2 class="text-[0.8125rem] leading-5 font-semibold">Your adviser</h2>
	{/if}
	<div class={cn('flex flex-col gap-3 @md:flex-row @md:items-center', !isFirm && 'mt-3')}>
		<div class="flex min-w-0 flex-1 items-center gap-3">
			<Avatar.Root class="size-12">
				<!-- The firm's headshot (`auth.users.photo_url`, 0118), else the initials this always
				     drew. alt is empty because the name is read from the line beside it. A firm's
				     icon is a mark, not a face: contained with a little air on the brand wash, so a
				     square logo is not cropped into a circle. -->
				{#if adviser.photoUrl}
					<Avatar.Image
						src={adviser.photoUrl}
						alt=""
						class={isFirm ? 'bg-brand-subtle object-contain p-2' : 'object-cover'}
					/>
				{/if}
				<Avatar.Fallback
					class={isFirm
						? 'bg-brand-subtle text-brand-strong'
						: 'bg-primary text-primary-foreground font-medium'}
				>
					{#if isFirm}
						<Building2Icon class="size-5" aria-hidden="true" />
					{:else}
						{initials}
					{/if}
				</Avatar.Fallback>
			</Avatar.Root>
			<div class="min-w-0">
				<p class="truncate font-medium">{adviser.name}</p>
				<!-- Who, then what they do, then where. The job title takes the line the firm used to
				     have to itself; the firm moves down rather than being dropped, because a client
				     reading an adviser card needs to know which firm they are dealing with. The firm
				     itself gets one line saying what the contacts beside it reach. -->
				{#if isFirm}
					<p class="text-muted-foreground truncate text-sm">Client support</p>
				{:else}
					{#if adviser.jobTitle}
						<p class="text-muted-foreground truncate text-sm">{adviser.jobTitle}</p>
					{/if}
					<p class="text-muted-foreground truncate text-sm">{adviser.firm}</p>
				{/if}
				{#if adviser.qualifications?.length}
					<p
						class="text-muted-foreground/80 truncate text-xs"
						title={adviser.qualifications.join(', ')}
					>
						{adviser.qualifications.join(' · ')}
					</p>
				{/if}
			</div>
		</div>
		<!-- Three ways to reach them, one of them labelled. Email and Call are the icons
		     everyone knows and used to be three words wide each; Message keeps its word because
		     it is the one that stays inside the portal (and the one the firm most wants taken).
		     36px round, which is the size of the shell's own icon buttons; `tap-target` extends
		     the hit area to 44 invisibly, and `gap-2` is exactly the room two such extensions
		     need not to reach each other. Stacked (a narrow span) the row spans the card and
		     Message takes the slack, so it reads as the card's action bar rather than three
		     buttons huddled left of an empty strip. -->
		<div class="flex w-full shrink-0 items-center gap-2 @md:w-auto">
			<!-- An address or number the firm has not given is not a button to nowhere. -->
			{#if adviser.email}
				<a
					href="mailto:{adviser.email}"
					aria-label="Email {adviser.name}"
					title="Email {adviser.name}"
					class={cn(
						buttonVariants({ variant: 'outline', size: 'icon-lg' }),
						'tap-target rounded-full'
					)}
				>
					<MailIcon class="size-4" />
				</a>
			{/if}
			{#if adviser.phone}
				<a
					href="tel:{adviser.phone}"
					aria-label="Call {adviser.name}"
					title="Call {adviser.name}"
					class={cn(
						buttonVariants({ variant: 'outline', size: 'icon-lg' }),
						'tap-target rounded-full'
					)}
				>
					<PhoneIcon class="size-4" />
				</a>
			{/if}
			<a
				href="/messages"
				class={cn(
					buttonVariants({ size: 'lg' }),
					'tap-target flex-1 rounded-full px-4 @md:flex-none'
				)}
			>
				<MessageCircleIcon class="size-4" /> Message
			</a>
		</div>
	</div>
</div>
