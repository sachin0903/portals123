<script lang="ts">
	import ChevronLeftIcon from '@lucide/svelte/icons/chevron-left';
	import * as Sidebar from '../ui/sidebar/index.js';
	import { personInitials } from './nav-groups.js';
	import type { NavContext } from './types.js';

	let {
		context,
		interactive = true
	}: {
		context: NavContext;
		/** `false` ⇒ rendered inside the branding preview: no navigable hrefs (see saas-shell). */
		interactive?: boolean;
	} = $props();

	// "Mr Max Oakden" ⇒ MO — the honorific is dropped so this agrees with the avatar the record's own
	// page draws. Falls back to a dot rather than a letter: a stray "G" here would read as the
	// Graphene mark, and this tile is the RECORD, not the brand.
	const monogram = $derived(personInitials(context.title, '·'));

	// One accessible label for the rail tile — a screen reader gets the full context that the
	// expanded header prints in three lines.
	const railLabel = $derived(
		[context.eyebrow, context.title, context.subtitle].filter(Boolean).join(' — ')
	);

	// A firm (or anything else with a mark) gets its tile drawn in the expanded header too, so the
	// scope reads at a glance. A person does not: the record page owns their avatar, and a second
	// copy of it in the chrome would be a second thing to keep in step.
	const hasMark = $derived(Boolean(context.image || context.icon));
</script>

<!--
  The focused header. When the sidebar is scoped to one record (a client, a tenant) the WHOLE
  sidebar should read as focused on it: this band sits between the brand lockup and the nav, and the
  nav below it is that record's sections.

  It is bounded top and bottom by `--glass-border` so it reads as its own band rather than as a
  first nav group, and the hairline continues the sidebar-header divider's line weight.
-->
{#snippet tile(labelled: boolean)}
	<!--
	  The one tile, drawn twice (expanded and rail). Image first — a firm's own mark, `object-contain`
	  so a non-square icon is letterboxed rather than cropped — then the glyph, then the monogram.
	  Tinted with the tonal brand ramp so it is legible against `--sidebar` but distinct from the
	  header's brand mark (which uses `--sidebar-primary`).

	  `role="img"` + `aria-label`, not a button: there is nothing to click here, and a control that
	  does nothing is worse than an indicator that looks like one. In the expanded header the three
	  text lines already say it, so the tile is decorative there (`labelled` false).
	-->
	<span
		role="img"
		aria-label={labelled ? railLabel : undefined}
		aria-hidden={labelled ? undefined : true}
		class="bg-brand-subtle text-brand-strong border-brand-line grid size-8 shrink-0 place-items-center overflow-hidden rounded-[var(--radius-chrome)] border text-[0.65rem] font-semibold tracking-[0.02em]"
	>
		{#if context.image}
			<img src={context.image} alt="" class="size-full object-contain p-0.5" />
		{:else if context.icon}
			{@const Icon = context.icon}
			<Icon class="size-4" />
		{:else}
			{monogram}
		{/if}
	</span>
{/snippet}

<div class="border-b [border-bottom-color:var(--glass-border)]">
	<Sidebar.Group class="gap-1 py-2">
		{#if context.back}
			<!-- Captured as a `{@const}`: `context.back` is optional, and the narrowing from the `{#if}`
			     does not survive into the child snippet's closure. -->
			{@const back = context.back}
			<Sidebar.Menu class="gap-0.5">
				<Sidebar.MenuItem>
					<!-- The escape hatch, and the only interactive control in the band. It is a real nav row
					     (same height, radius and hover as the nav below) so the rail gets its icon + tooltip
					     for free — which is what keeps the focused sidebar escapable at 56px wide. -->
					<Sidebar.MenuButton
						tooltipContent={back.label}
						class="text-muted-foreground h-8 gap-2.5 rounded-[var(--radius-chrome)] px-2.5 text-xs font-medium"
					>
						{#snippet child({ props })}
							<a href={interactive ? back.href : undefined} {...props}>
								<ChevronLeftIcon />
								<span class="group-data-[collapsible=icon]:sr-only">{back.label}</span>
							</a>
						{/snippet}
					</Sidebar.MenuButton>
				</Sidebar.MenuItem>
			</Sidebar.Menu>
		{/if}

		<!-- Expanded: [tile] eyebrow / title / subtitle. -->
		<div
			class="flex min-w-0 items-center gap-2.5 px-2.5 pt-0.5 group-data-[collapsible=icon]:hidden"
		>
			{#if hasMark}
				{@render tile(false)}
			{/if}
			<div class="flex min-w-0 flex-col gap-0.5">
				{#if context.eyebrow}
					<span class="text-muted-foreground/80 text-[0.7rem] leading-none font-medium">
						{context.eyebrow}
					</span>
				{/if}
				<span class="text-sidebar-foreground truncate text-sm leading-tight font-semibold">
					{context.title}
				</span>
				{#if context.subtitle}
					<span class="text-muted-foreground truncate text-xs leading-tight"
						>{context.subtitle}</span
					>
				{/if}
			</div>
		</div>

		<!--
		  Collapsed rail: a 56px column cannot hold a name, an email and an eyebrow, and shrinking them
		  produces three lines of ellipsis. It gets the tile instead — the same size and corner as the
		  account row's avatar — carrying the full label for a screen reader.
		-->
		<div class="hidden justify-center pt-0.5 group-data-[collapsible=icon]:flex">
			{@render tile(true)}
		</div>
	</Sidebar.Group>
</div>
