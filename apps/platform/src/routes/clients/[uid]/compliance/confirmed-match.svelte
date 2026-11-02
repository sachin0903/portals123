<script lang="ts">
	// One AML match Graphene confirmed and let the onboarding continue past, sized to sit level with
	// the identity check: a photo, who they are, how strongly they matched, on which lists, their
	// latest roles and the start of the notes. **Full profile** opens everything the AML console shows
	// of the match — every photo, name, role, tie, note and article — in a side panel. Never
	// Graphene's decision or its comment.
	import {
		AmlMatchPhotos,
		AmlMatchProfile,
		Badge,
		bornLine,
		Button,
		countryReasons,
		matchScore,
		matchSubtitle,
		partialDate,
		roleDates,
		Sheet,
		StatusBadge
	} from '@graphene/ui';
	import type { ConfirmedAmlMatch } from './+page.server';

	let { match }: { match: ConfirmedAmlMatch } = $props();

	const score = $derived(matchScore(match.score));
	const subtitle = $derived(matchSubtitle(match));
	const born = $derived(bornLine(match));
	/** The lists the match was found on, each with its date: ComplyCube's references, else the
	 *  names we stored. */
	const sources = $derived(
		match.references.length
			? match.references
					.filter((reference) => !!reference.source)
					.map((reference) =>
						reference.date
							? `${reference.source} · ${partialDate(reference.date)}`
							: String(reference.source)
					)
			: match.sources
	);
	const countries = $derived(countryReasons(match.country_details).map((entry) => entry.country));
	/** The roles shown here: the current first, then the latest. The rest are in the full profile. */
	const ROLES_SHOWN = 3;
	const roles = $derived(match.roles.slice(0, ROLES_SHOWN));

	let open = $state(false);
</script>

<div class="space-y-3">
	<!-- The photos, as the AML console shows them, beside who they are and the facts that
	     identify them. -->
	<div class="flex items-start gap-4">
		<AmlMatchPhotos images={match.images} name={match.name} />
		<div class="min-w-0 flex-1 space-y-2">
			<div class="flex items-start justify-between gap-3">
				<div class="min-w-0">
					<p class="font-semibold">{match.name}</p>
					{#if subtitle}
						<p class="text-muted-foreground text-xs">{subtitle}</p>
					{/if}
				</div>
				{#if score}
					<span
						class="text-warning-strong shrink-0 text-lg leading-tight font-semibold tabular-nums"
					>
						{score}
					</span>
				{/if}
			</div>
			{#if match.active !== null || match.types.length}
				<div class="flex flex-wrap gap-1">
					{#if match.active !== null}
						<StatusBadge tone={match.active ? 'warning' : 'neutral'}>
							{match.active ? 'Currently listed' : 'No longer listed'}
						</StatusBadge>
					{/if}
					{#each match.types as type, i (i)}
						<Badge variant="outline" title={type}>{type}</Badge>
					{/each}
				</div>
			{/if}
			<dl class="grid grid-cols-[auto_minmax(0,1fr)] items-baseline gap-x-3 gap-y-1 text-xs">
				{#if born}
					<dt class="text-muted-foreground">Born</dt>
					<dd>{born}</dd>
				{/if}
				{#if match.aliases.length}
					<dt class="text-muted-foreground">Also known as</dt>
					<dd class="line-clamp-1" title={match.aliases.join(', ')}>{match.aliases.join(', ')}</dd>
				{/if}
				{#if match.categories.length}
					<dt class="text-muted-foreground">Categories</dt>
					<dd>{match.categories.join(', ')}</dd>
				{/if}
				{#if countries.length}
					<dt class="text-muted-foreground">Countries</dt>
					<dd>{countries.join(', ')}</dd>
				{/if}
				{#if sources.length}
					<dt class="text-muted-foreground">Sources</dt>
					<dd>{sources.join('; ')}</dd>
				{/if}
			</dl>
		</div>
	</div>

	{#if roles.length}
		<div class="space-y-1 text-xs">
			<p class="text-muted-foreground text-[0.7rem] font-semibold tracking-wide uppercase">
				Roles <span class="tabular-nums">({match.roles.length})</span>
			</p>
			<ul class="space-y-1">
				{#each roles as role, i (i)}
					{@const when = roleDates(role)}
					<li class="flex flex-wrap items-baseline justify-between gap-x-3">
						<span class="font-medium">{role.title}</span>
						{#if when}<span class="text-muted-foreground tabular-nums">{when}</span>{/if}
					</li>
				{/each}
			</ul>
		</div>
	{/if}

	{#if match.notes}
		<div class="space-y-1 text-xs">
			<p class="text-muted-foreground text-[0.7rem] font-semibold tracking-wide uppercase">Notes</p>
			<p class="line-clamp-3 leading-relaxed whitespace-pre-line">{match.notes}</p>
		</div>
	{/if}

	<Button variant="outline" size="sm" onclick={() => (open = true)}>Full profile</Button>
</div>

<!-- The header stays put; the profile scrolls under it. -->
<Sheet.Root bind:open>
	<Sheet.Content class="flex w-full flex-col gap-0 overflow-hidden p-0">
		<Sheet.Header class="border-b px-6 pt-6 pb-4">
			<Sheet.Title>{match.name}</Sheet.Title>
			<Sheet.Description>A confirmed AML match, as Graphene screened it.</Sheet.Description>
		</Sheet.Header>
		<div class="min-h-0 flex-1 overflow-y-auto px-6 py-4">
			<AmlMatchProfile {match} />
		</div>
	</Sheet.Content>
</Sheet.Root>
