<script lang="ts">
	import type { Snippet } from 'svelte';
	import ChevronLeftIcon from '@lucide/svelte/icons/chevron-left';
	import { WRAP_NAME } from '../../text-shell.js';
	import { cn } from '../../utils.js';
	import { getPageBack } from './page-back.js';
	import type { BackLink } from './types.js';

	// The page heading for every staff-facing screen (platform + admin). One type scale lives here
	// so no screen can invent a bigger title: editorial size, tight tracking, muted standfirst.
	//
	// `title` is usually a NAME, not a label: this component is handed `{tenant.name}`, a free-text
	// task title, a template name, a flow name, a dataset name and — on the admin user page —
	// `{user.name ?? user.email}`, i.e. an email address at 28px. So the h1 takes `WRAP_NAME`
	// (`wrap-anywhere`): an email or an underscored filename has no break opportunity, and without
	// it a single long token runs off the page on whichever of the thirteen screens supplies one.
	// Wrap rather than truncate, because a page's own title is the one string that must never be
	// clipped.
	let {
		title,
		description,
		eyebrow,
		back,
		actions,
		class: className
	}: {
		title: string;
		description?: string;
		/** Small label above the title — the section this screen belongs to. */
		eyebrow?: string;
		/**
		 * Optional back link shown above the title.
		 *
		 * In an app that publishes a back target (see `page-back.ts`) this is the FALLBACK, not the
		 * destination: the live target wins. What a page declares here is the section it belongs to,
		 * which is the right answer only when there is no history to consult — a hard reload, a
		 * pasted link, a new tab — and the wrong one the rest of the time.
		 */
		back?: BackLink;
		/** Optional right-aligned actions (buttons, etc.). */
		actions?: Snippet;
		/**
		 * Overrides on the wrapper — in practice `mb-0`.
		 *
		 * The default `mb-5` is right for a page that stacks its own sections and wrong for one whose
		 * parent already spaces its children: the full-height listings are `flex flex-col gap-5`, so
		 * the gap and this margin BOTH applied and the heading sat 40px off the card instead of 20.
		 * A prop rather than dropping the default, because a dozen other screens rely on it and this
		 * is the exception, not the rule.
		 */
		class?: string;
	} = $props();

	// The app's live back target OVERRIDES `back` — but only where a back link already exists.
	//
	// Both halves matter. Overriding is the whole fix: the declared parent is a guess about how you
	// arrived (`/tasks` for a task opened from the dashboard), and the live target knows. Requiring
	// `back` to be set first is what keeps the change to the ~27 screens that have this control:
	// without the guard, the other ~200 PageHeaders — every listing, every settings screen — would
	// grow a back link they never had, and the two screens that already show the topbar control
	// would show it twice. The topbar's control is the canonical one; this is the page-level echo of
	// it, and an echo needs something to echo.
	const dynamic = getPageBack();
	const resolved = $derived(back ? (dynamic?.current ?? back) : undefined);
</script>

<div class={cn('mb-5 space-y-2', className)}>
	{#if resolved}
		<a
			href={resolved.href}
			class="text-muted-foreground hover:text-foreground focus-visible:ring-ring/50 -mx-1 inline-flex items-center gap-1 rounded px-1 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-[3px]"
		>
			<ChevronLeftIcon class="size-4" />
			{resolved.label}
		</a>
	{/if}
	<div class="flex flex-wrap items-start justify-between gap-x-4 gap-y-3">
		<div class="min-w-0 space-y-1">
			{#if eyebrow}
				<p class="text-muted-foreground text-xs font-medium">
					{eyebrow}
				</p>
			{/if}
			<h1 class="text-[1.75rem] leading-[1.15] font-semibold tracking-[-0.015em] {WRAP_NAME}">
				{title}
			</h1>
			{#if description}
				<p class="text-muted-foreground max-w-2xl text-sm">{description}</p>
			{/if}
		</div>
		{#if actions}
			<div class="flex shrink-0 items-center gap-2">{@render actions()}</div>
		{/if}
	</div>
</div>
