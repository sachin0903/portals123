<script lang="ts">
	// Who ComplyCube matched on an AML screen: their photo, names, roles, ties and the articles behind
	// the match, how strongly, and on which lists. The AML console shows it to the operator deciding
	// the match, with the decision under it (`children`); a firm's compliance tab shows a match once
	// Graphene confirmed it and let the onboarding continue past it, with no decision at all. The top
	// stays short for an operator deciding many in a row; the roles show their first few and the rest
	// folds away until asked for. Laid out by its own width (a container query): wide, who they are
	// sits beside their roles and notes, and the folded detail runs in columns; narrow, it is one
	// column. One photo shows at a time, with arrows for the rest, and a click opens it large in a
	// pop-up over the page. `foldDetails` keeps only the top (a smaller photo, who they are, the
	// score and the chips) and folds the rest behind one Details disclosure, closed until opened.
	import type { Snippet } from 'svelte';
	import { SvelteSet } from 'svelte/reactivity';
	import Building2Icon from '@lucide/svelte/icons/building-2';
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import ExternalLinkIcon from '@lucide/svelte/icons/external-link';
	import UserRoundIcon from '@lucide/svelte/icons/user-round';
	import { Badge } from '../../ui/badge/index.js';
	import * as Collapsible from '../../ui/collapsible/index.js';
	import { ImageViewer, type ViewerImage } from '../../ui/image-viewer/index.js';
	import { StatusBadge } from '../../ui/status-badge/index.js';
	import { humanise } from '../../../sentence-case.js';
	import { isWebUrl } from '../../../urls.js';
	import {
		addressLine,
		bornLine,
		countryReasons,
		groupByType,
		linkHost,
		matchScore,
		matchSubtitle,
		partialDate,
		roleDates,
		type AmlMatchProfileData,
		type MatchReference
	} from './aml-match.js';
	import AmlMatchPhotos from './aml-match-photos.svelte';

	let {
		match,
		nameReviewer = false,
		foldDetails = false,
		children
	}: {
		match: AmlMatchProfileData;
		/** The console names who last reviewed the listing. A firm's compliance tab does not:
		 * firm-facing copy stays vendor-free. */
		nameReviewer?: boolean;
		/** Only the top shows, the rest behind one Details disclosure: for a page that works many
		 *  matches one above the other (the AML console's workbench). */
		foldDetails?: boolean;
		/** What follows the profile: the console's decision on the match. */
		children?: Snippet;
	} = $props();

	const score = $derived(matchScore(match.score));
	const subtitle = $derived(
		[match.entity_type ? humanise(match.entity_type) : null, matchSubtitle(match)]
			.filter(Boolean)
			.join(' · ')
	);
	const born = $derived(bornLine(match));

	/** The lists the match was found on: ComplyCube's references, else the names we stored. */
	const references = $derived<MatchReference[]>(
		match.references.length
			? match.references
			: match.sources.map((source) => ({ source, date: null, to_date: null, url: null }))
	);

	// An associate's third-party photo breaks often: one that fails to load drops out. (The match's
	// own photos are `AmlMatchPhotos`'.)
	const failed = new SvelteSet<string>();

	/** The pop-up over an associate's photo. */
	let viewer = $state<{ images: ViewerImage[]; index: number } | null>(null);

	/** An associate's photo while it still loads. */
	function livePhoto(url: string | null): string | null {
		return isWebUrl(url) && !failed.has(url) ? url : null;
	}

	function isPerson(entityType: string | null): boolean {
		return !entityType || entityType.toLowerCase() === 'person';
	}

	const chips = $derived(
		match.active !== null || match.deceased !== null || match.types.length > 0
	);

	/** The roles shown before "Show all": the current ones come first. */
	const ROLES_SHOWN = 3;
	let allRoles = $state(false);
	const roles = $derived(allRoles ? match.roles : match.roles.slice(0, ROLES_SHOWN));

	/** Past this length a note is clamped to four lines, with a way to read the rest. A shorter note
	 *  is never clamped, so nothing is cut off with no way to see it. */
	const NOTES_CLAMP = 320;
	let allNotes = $state(false);
	const longNotes = $derived((match.notes?.length ?? 0) > NOTES_CLAMP);

	/** Roles or notes to sit beside who the match is, when the card is wide. */
	const aside = $derived(match.roles.length > 0 || !!match.notes);

	const names = $derived(groupByType(match.names));
	const countries = $derived(countryReasons(match.country_details));
	const links = $derived(match.source_links.filter(isWebUrl));
	const folded = $derived(
		match.names.length +
			countries.length +
			match.dates.length +
			match.associations.length +
			links.length >
			0
	);

	/** A field's name: a small section label, so it never runs into the value under it. */
	const FIELD_LABEL =
		'text-muted-foreground mb-1 text-[0.7rem] font-semibold tracking-wide uppercase';
	const PHOTO_BUTTON =
		'focus-visible:ring-ring/50 block shrink-0 cursor-zoom-in outline-none focus-visible:ring-[3px]';
	const TEXT_BUTTON =
		'text-muted-foreground hover:text-foreground focus-visible:ring-ring/50 mt-1 cursor-pointer rounded-sm text-xs font-medium outline-none focus-visible:ring-2';
</script>

<!-- A detail section, closed until the operator opens it. -->
{#snippet section(title: string, count: number, body: Snippet)}
	<Collapsible.Root class="group/section">
		<Collapsible.Trigger
			class="text-muted-foreground hover:text-foreground focus-visible:ring-ring/50 flex cursor-pointer items-center gap-1 rounded-md text-xs font-medium outline-none focus-visible:ring-2"
		>
			{title} <span class="tabular-nums">({count})</span>
			<ChevronDownIcon
				class="motion-toggle size-3.5 transition-transform group-data-[state=closed]/section:-rotate-90"
			/>
		</Collapsible.Trigger>
		<Collapsible.Content class="mt-2">
			{@render body()}
		</Collapsible.Content>
	</Collapsible.Root>
{/snippet}

{#snippet namesBody()}
	<dl class="grid grid-cols-[minmax(0,9rem)_minmax(0,1fr)] items-baseline gap-x-3 gap-y-1">
		{#each names as group (group.type)}
			<dt class="text-muted-foreground text-xs font-medium">{group.type}</dt>
			<dd>{group.items.map((n) => n.name).join(', ')}</dd>
		{/each}
	</dl>
{/snippet}

{#snippet countriesBody()}
	<ul class="space-y-1">
		{#each countries as entry (entry.country)}
			<li>
				{entry.country}
				{#if entry.reasons.length}
					<span class="text-muted-foreground">· {entry.reasons.join(', ')}</span>
				{/if}
			</li>
		{/each}
	</ul>
{/snippet}

{#snippet datesBody()}
	<dl class="grid grid-cols-[minmax(0,10rem)_minmax(0,1fr)] items-baseline gap-x-3 gap-y-1">
		{#each match.dates as fact, i (i)}
			<dt class="text-muted-foreground text-xs font-medium">{fact.type}</dt>
			<dd class="tabular-nums">{partialDate(fact.date)}</dd>
		{/each}
	</dl>
{/snippet}

{#snippet associatesBody()}
	<ul class="space-y-2">
		{#each match.associations as person, i (i)}
			{@const image = livePhoto(person.image)}
			{@const about = [
				person.relationship,
				person.current === true ? 'Current' : person.current === false ? 'Former' : null,
				isPerson(person.entity_type) ? null : humanise(person.entity_type)
			]
				.filter(Boolean)
				.join(' · ')}
			<li class="flex items-start gap-2">
				{#if image}
					<button
						type="button"
						aria-label={`View the photo of ${person.name}`}
						class="{PHOTO_BUTTON} rounded-full"
						onclick={() => (viewer = { images: [{ url: image, label: person.name }], index: 0 })}
					>
						<img
							src={image}
							alt=""
							referrerpolicy="no-referrer"
							loading="lazy"
							decoding="async"
							class="border-border size-6 rounded-full border object-cover"
							onerror={() => failed.add(image)}
						/>
					</button>
				{:else}
					<span
						class="bg-muted text-muted-foreground flex size-6 shrink-0 items-center justify-center rounded-full"
					>
						{#if isPerson(person.entity_type)}
							<UserRoundIcon class="size-3.5" />
						{:else}
							<Building2Icon class="size-3.5" />
						{/if}
					</span>
				{/if}
				<div class="min-w-0">
					<p>
						<span class="font-medium">{person.name}</span>
						{#if about}<span class="text-muted-foreground">· {about}</span>{/if}
					</p>
					{#if person.categories.length}
						<p class="text-muted-foreground text-xs">{person.categories.join(', ')}</p>
					{/if}
				</div>
			</li>
		{/each}
	</ul>
{/snippet}

{#snippet linksBody()}
	<ul class="flex flex-wrap gap-x-4 gap-y-1">
		{#each links as url, i (i)}
			<li class="min-w-0">
				<!-- A third-party page: a new tab that learns nothing about this one. -->
				<a
					href={url}
					target="_blank"
					rel="noopener noreferrer"
					referrerpolicy="no-referrer"
					title={url}
					class="text-primary inline-flex max-w-full items-center gap-0.5 text-xs font-medium hover:underline"
				>
					<span class="truncate">{linkHost(url)}</span>
					<ExternalLinkIcon class="size-3 shrink-0" />
				</a>
			</li>
		{/each}
	</ul>
{/snippet}

<!-- Who the match is: their photos, the facts that identify them, and the lists they are on. -->
{#snippet whoTheyAre()}
	{@render top()}
	{@render facts()}
{/snippet}

<!-- The top: their photos, who they are, how strongly they matched, and the kinds of list. -->
{#snippet top()}
	<div class="flex flex-col gap-3 @sm/who:flex-row @sm/who:items-start">
		<AmlMatchPhotos
			images={match.images}
			name={match.name}
			class={foldDetails ? 'max-w-28!' : ''}
		/>
		<div class="min-w-0 flex-1 space-y-1.5">
			<div class="flex items-start justify-between gap-4">
				<div class="min-w-0">
					<p class="font-semibold">{match.name}</p>
					{#if subtitle}
						<p class="text-muted-foreground text-xs">{subtitle}</p>
					{/if}
				</div>
				{#if score}
					<div class="text-warning-strong shrink-0 text-right leading-tight">
						<span class="block text-lg font-semibold tabular-nums">{score}</span>
						<span class="block text-xs">match</span>
					</div>
				{/if}
			</div>
			{#if chips}
				<div class="flex flex-wrap items-center gap-1">
					{#if match.active !== null}
						<!-- ComplyCube's `active`: whether any listing on the person is still in force. -->
						<span
							title={match.active
								? 'At least one listing on this person is still in force.'
								: 'Every listing on this person has ended.'}
						>
							<StatusBadge tone={match.active ? 'warning' : 'neutral'}>
								{match.active ? 'Currently listed' : 'No longer listed'}
							</StatusBadge>
						</span>
					{/if}
					{#if match.deceased !== null}
						<StatusBadge tone={match.deceased ? 'info' : 'neutral'}>
							Deceased: {match.deceased ? 'Yes' : 'No'}
						</StatusBadge>
					{/if}
					{#each match.types as type, i (i)}
						<Badge variant="outline" title={type}>{type}</Badge>
					{/each}
				</div>
			{/if}
		</div>
	</div>
{/snippet}

<!-- The facts that identify them, and the lists they are on. -->
{#snippet facts()}
	<dl class="space-y-4">
		{#if born}
			<div>
				<dt class={FIELD_LABEL}>Born</dt>
				<dd>{born}</dd>
			</div>
		{/if}
		{#if match.aliases.length}
			<div>
				<dt class={FIELD_LABEL}>Also known as</dt>
				<dd class="line-clamp-2" title={match.aliases.join(', ')}>{match.aliases.join(', ')}</dd>
			</div>
		{/if}
		<div>
			<dt class={FIELD_LABEL}>Categories</dt>
			<dd>
				{#if match.categories.length}
					{match.categories.join(', ')}
				{:else}
					<span class="text-muted-foreground">No category detail returned</span>
				{/if}
			</dd>
		</div>
		{#if match.addresses.length}
			<div>
				<dt class={FIELD_LABEL}>Addresses</dt>
				<dd>{match.addresses.map(addressLine).join('; ')}</dd>
			</div>
		{/if}
		{#if aside}{@render sources()}{/if}
	</dl>
{/snippet}

<!-- The lists the match was found on, each with the article behind it when there is one. -->
{#snippet sources()}
	<div>
		<dt class={FIELD_LABEL}>
			Sources <span class="tabular-nums">({references.length})</span>
		</dt>
		<dd>
			{#if references.length === 0}
				<span class="text-muted-foreground">None returned</span>
			{:else}
				<ul class="space-y-1">
					{#each references as reference, i (i)}
						<li class="flex flex-wrap items-baseline gap-x-2">
							<span>{reference.source ?? 'Unnamed source'}</span>
							{#if reference.date}
								<span class="text-muted-foreground text-xs tabular-nums"
									>{partialDate(reference.date)}</span
								>
							{/if}
							{#if isWebUrl(reference.url)}
								<!-- A third-party page: a new tab that learns nothing about this one. -->
								<a
									href={reference.url}
									target="_blank"
									rel="noopener noreferrer"
									referrerpolicy="no-referrer"
									class="text-primary inline-flex items-center gap-0.5 text-xs font-medium hover:underline"
								>
									View source <ExternalLinkIcon class="size-3" />
								</a>
							{/if}
						</li>
					{/each}
				</ul>
			{/if}
		</dd>
	</div>
{/snippet}

<!-- What the match did and what is said of them: beside who they are when the card is wide. With
     neither roles nor notes, the sources take this side instead. -->
{#snippet whatTheyDid()}
	<dl class="space-y-4">
		{#if !aside}{@render sources()}{/if}
		{#if match.roles.length}
			<div>
				<dt class={FIELD_LABEL}>
					Roles <span class="tabular-nums">({match.roles.length})</span>
				</dt>
				<dd>
					<ul class="divide-border divide-y">
						{#each roles as role, i (i)}
							{@const when = roleDates(role)}
							{@const kind = [role.category, role.type].filter(Boolean).join(' · ')}
							<li class="py-2 first:pt-0">
								<div class="flex flex-wrap items-baseline justify-between gap-x-3">
									<span class="font-medium">{role.title}</span>
									{#if when}
										<span class="text-muted-foreground text-xs tabular-nums">{when}</span>
									{/if}
								</div>
								{#if kind}<p class="text-muted-foreground mt-0.5 text-xs">{kind}</p>{/if}
							</li>
						{/each}
					</ul>
					{#if match.roles.length > ROLES_SHOWN}
						<button type="button" class={TEXT_BUTTON} onclick={() => (allRoles = !allRoles)}>
							{allRoles ? 'Show fewer' : `Show all ${match.roles.length}`}
						</button>
					{/if}
				</dd>
			</div>
		{/if}
		{#if match.notes}
			<div>
				<dt class={FIELD_LABEL}>Notes</dt>
				<dd>
					<p
						class={[
							'text-sm leading-relaxed whitespace-pre-line',
							longNotes && !allNotes && 'line-clamp-4'
						]}
					>
						{match.notes}
					</p>
					{#if longNotes}
						<button type="button" class={TEXT_BUTTON} onclick={() => (allNotes = !allNotes)}>
							{allNotes ? 'Show less' : 'Show more'}
						</button>
					{/if}
				</dd>
			</div>
		{/if}
	</dl>
{/snippet}

<!-- The detail sections, closed until opened, and when ComplyCube last reviewed the match. -->
{#snippet rest()}
	{#if folded}
		<div
			class="border-border grid items-start gap-x-8 gap-y-2 border-t pt-3 @2xl:grid-cols-2 @4xl:grid-cols-3"
		>
			{#if match.names.length}{@render section('Names', match.names.length, namesBody)}{/if}
			{#if countries.length}{@render section('Countries', countries.length, countriesBody)}{/if}
			{#if match.dates.length}{@render section('Dates', match.dates.length, datesBody)}{/if}
			{#if match.associations.length}
				{@render section('Associates', match.associations.length, associatesBody)}
			{/if}
			{#if links.length}{@render section('Source links', links.length, linksBody)}{/if}
		</div>
	{/if}

	{#if match.last_reviewed}
		<p class="text-muted-foreground text-xs">
			Last reviewed{nameReviewer ? ' by ComplyCube' : ''}: {match.last_reviewed}
		</p>
	{/if}
{/snippet}

<div class="@container space-y-3 text-sm">
	{#if foldDetails}
		<div class="@container/who min-w-0">{@render top()}</div>
		<Collapsible.Root class="group/details">
			<Collapsible.Trigger
				class="text-muted-foreground hover:text-foreground focus-visible:ring-ring/50 flex cursor-pointer items-center gap-1 rounded-md text-xs font-medium outline-none focus-visible:ring-2"
			>
				Details
				<ChevronDownIcon
					class="motion-toggle size-3.5 transition-transform group-data-[state=closed]/details:-rotate-90"
				/>
			</Collapsible.Trigger>
			<Collapsible.Content class="mt-3 space-y-3">
				<div class="grid gap-x-8 gap-y-3 @2xl:grid-cols-2">
					<div class="min-w-0">{@render facts()}</div>
					<div class="border-border min-w-0 @2xl:border-l @2xl:pl-8">{@render whatTheyDid()}</div>
				</div>
				{@render rest()}
			</Collapsible.Content>
		</Collapsible.Root>
	{:else}
		<div class="grid gap-x-8 gap-y-3 @2xl:grid-cols-2">
			<div class="@container/who min-w-0 space-y-3">{@render whoTheyAre()}</div>
			<div class="border-border min-w-0 @2xl:border-l @2xl:pl-8">{@render whatTheyDid()}</div>
		</div>
		{@render rest()}
	{/if}

	{@render children?.()}
</div>

{#if viewer}
	<ImageViewer images={viewer.images} index={viewer.index} onclose={() => (viewer = null)} />
{/if}
