<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { can } from '@graphene/api-client/permissions';
	import {
		Button,
		Command,
		Dialog,
		LoadErrorBanner,
		SPOTLIGHT_CHIP_CLASS,
		SPOTLIGHT_OVERLAY_CLASS,
		SPOTLIGHT_PANEL_CLASS,
		SPOTLIGHT_PHONE_CLOSE_CLASS,
		SPOTLIGHT_PANEL_STYLE,
		cn,
		type NavGroup
	} from '@graphene/ui';
	import { onMount } from 'svelte';
	import SearchIcon from '@lucide/svelte/icons/search';
	import UsersIcon from '@lucide/svelte/icons/users';
	import WalletIcon from '@lucide/svelte/icons/wallet';
	import FileTextIcon from '@lucide/svelte/icons/file-text';
	import WorkflowIcon from '@lucide/svelte/icons/workflow';
	import ArrowLeftRightIcon from '@lucide/svelte/icons/arrow-left-right';
	import MessageCircleIcon from '@lucide/svelte/icons/message-circle';
	import ScanEyeIcon from '@lucide/svelte/icons/scan-eye';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import UploadIcon from '@lucide/svelte/icons/upload';
	import CornerDownLeftIcon from '@lucide/svelte/icons/corner-down-left';
	import CircleQuestionMarkIcon from '@lucide/svelte/icons/circle-question-mark';
	import XIcon from '@lucide/svelte/icons/x';

	import {
		categoriesForGroups,
		COMMAND_PREFIX,
		consumeSearchGuide,
		highlightParts,
		hrefFor,
		MIN_QUERY_LENGTH,
		PALETTE_SHORTCUTS,
		paletteKeyboardAction,
		parsePaletteQuery,
		relevanceOrderedHits,
		routableGroups,
		scheduleQuery,
		searchShortcutLabel,
		SCOPE_PREFIX,
		scopedSearchUrl,
		SEARCH_SCOPES,
		SearchClient,
		settle,
		type Hit,
		type Results,
		type SearchScope,
		uploadDocumentHref
	} from '$lib/search';

	let {
		nav = [],
		permissions = [],
		role = null,
		userKey = null
	}: {
		nav?: NavGroup[];
		permissions?: string[];
		role?: string | null;
		userKey?: string | null;
	} = $props();

	const ICONS: Record<string, typeof SearchIcon> = {
		client: UsersIcon,
		account: WalletIcon,
		document: FileTextIcon,
		case: WorkflowIcon,
		transaction: ArrowLeftRightIcon,
		message: MessageCircleIcon,
		message_group: MessageCircleIcon,
		review: ScanEyeIcon
	};
	const TYPE_LABELS: Record<string, string> = {
		client: 'Client',
		account: 'Account',
		document: 'Document',
		case: 'Journey',
		transaction: 'Transaction',
		message: 'Message',
		message_group: 'Message',
		review: 'Review'
	};

	let open = $state(false);
	let query = $state('');
	/** The scope the user pinned from a tile or the scope list. An inline `clients:` token overrides
	 *  it for as long as it is in the box, which is why the two are not the same piece of state. */
	let pinnedScope = $state<SearchScope>(null);
	let results = $state<Results | null>(null);
	let loading = $state(false);
	let failed = $state(false);
	let guideChecked = $state(false);
	let showGuide = $state(false);
	let inputEl = $state<HTMLInputElement | null>(null);
	// Unknown during SSR; withholding the keycap until mount avoids briefly telling a Mac user Ctrl K.
	let shortcutLabel = $state<'⌘ K' | 'Ctrl K' | null>(null);

	onMount(() => {
		shortcutLabel = searchShortcutLabel(navigator);
	});

	const parsed = $derived(parsePaletteQuery(query));
	const term = $derived(parsed.term);
	const termKey = $derived(term.toLocaleLowerCase());
	const tokenScope = $derived(parsed.mode === 'search' ? parsed.scope : undefined);
	const scope = $derived(tokenScope === undefined ? pinnedScope : tokenScope);
	const ready = $derived(parsed.mode === 'search' && term.length >= MIN_QUERY_LENGTH);
	const client = new SearchClient((q: string) => scopedSearchUrl(q, scope));
	const groups = $derived(routableGroups(results));
	const orderedHits = $derived(relevanceOrderedHits(groups));
	const resultCategories = $derived(categoriesForGroups(groups));
	const showResultCategories = $derived(scope === null && resultCategories.length > 1);
	const activeScope = $derived(
		SEARCH_SCOPES.find((item) => item.type === scope) ?? SEARCH_SCOPES[0]
	);
	const pages = $derived(
		nav.flatMap((group) =>
			group.items.map((item) => ({
				label: item.label,
				href: item.href,
				icon: item.icon ?? SearchIcon
			}))
		)
	);
	const actions = $derived.by(() => {
		const allowed = (slug: string) => can(permissions, role, slug);
		const uploadHref = uploadDocumentHref(page.route.id, page.params.uid, allowed);
		return [
			...(allowed('contacts:create')
				? [{ label: 'Create client', href: '/clients?new=1', icon: UsersIcon }]
				: []),
			...(allowed('cases:write')
				? [{ label: 'Start journey', href: '/journeys/new', icon: WorkflowIcon }]
				: []),
			// On a client's record: that client's Documents tab. Elsewhere: the Documents page, which
			// needs documents:show — see `uploadDocumentHref`.
			...(uploadHref ? [{ label: 'Upload document', href: uploadHref, icon: UploadIcon }] : [])
		];
	});
	const visiblePages = $derived(
		ready ? pages.filter((item) => item.label.toLocaleLowerCase().includes(termKey)) : []
	);
	/** Commands do something. They answer to `/` and to nothing else. */
	const visibleActions = $derived(
		parsed.mode === 'commands'
			? actions.filter((item) => item.label.toLocaleLowerCase().includes(termKey))
			: []
	);
	/** Scopes only narrow where a search looks, so they answer to `>` and never appear under `/`. */
	const visibleScopes = $derived(
		parsed.mode === 'scopes'
			? SEARCH_SCOPES.filter(
					(item) =>
						item.label.toLocaleLowerCase().includes(termKey) || item.command.includes(termKey)
				)
			: []
	);
	const hasAnything = $derived(
		groups.length > 0 ||
			visiblePages.length > 0 ||
			visibleActions.length > 0 ||
			visibleScopes.length > 0
	);
	/** Resting, the palette is just the bar: categories and actions are a keystroke or a click away,
	 *  so nothing is listed until the user has actually asked for something. */
	const listOpen = $derived(showGuide || failed || parsed.mode !== 'search' || ready);

	async function run(q: string): Promise<void> {
		loading = true;
		failed = false;
		const next = settle(await client.run(q));
		if (!next) return;
		loading = false;
		results = next.results;
		failed = next.failed;
	}

	function idle(): void {
		client.cancel();
		results = null;
		failed = false;
		loading = false;
		showGuide = false;
	}

	// Reading scope here makes a tile/token change cancel and rerun the same query in its branch.
	$effect(() => {
		const scheduledScope = scope;
		if (parsed.mode !== 'search') {
			idle();
			return;
		}
		return scheduleQuery(term, {
			query: (q) => {
				// A scope change tears this effect down; the comparison also documents why scope is
				// a dependency instead of an incidental read inside SearchClient's URL callback.
				if (scope === scheduledScope) void run(q);
			},
			idle
		});
	});

	function reset(): void {
		client.cancel();
		query = '';
		pinnedScope = null;
		results = null;
		failed = false;
		loading = false;
	}

	function openPalette(prefix = ''): void {
		if (!guideChecked) {
			guideChecked = true;
			try {
				showGuide = consumeSearchGuide(localStorage, userKey);
			} catch {
				// Storage can be blocked by browser policy. Still explain it once for this mount.
				showGuide = true;
			}
		}
		query = prefix;
		open = true;
	}

	function closeAndGo(href: string): void {
		open = false;
		reset();
		void goto(href);
	}

	function select(hit: Hit): void {
		const href = hrefFor(hit);
		if (href) closeAndGo(href);
	}

	function chooseScope(next: SearchScope): void {
		if (pinnedScope === next) return;
		client.cancel();
		results = null;
		failed = false;
		loading = false;
		pinnedScope = next;
	}

	/** Picked from the `>` list: the text that was typed filtered THAT list, so it goes with it. */
	function chooseScopeFromList(next: SearchScope): void {
		chooseScope(next);
		query = '';
	}

	/** Dismissing the guide with the mouse has to hand typing back to the field it explains. */
	function dismissGuide(): void {
		showGuide = false;
		inputEl?.focus();
	}

	/** The two buttons are a mouse route to the `>` and `/` prefixes, so they do exactly what typing
	 *  one does — and hand focus back, or the arrow keys would be left driving the button. */
	function toggleMode(prefix: typeof COMMAND_PREFIX | typeof SCOPE_PREFIX): void {
		query = query.trim().startsWith(prefix) ? '' : prefix;
		showGuide = false;
		inputEl?.focus();
	}

	/** A token scope lives in the text, so clearing the scope has to clear the text that named it. */
	function clearScope(): void {
		if (tokenScope !== undefined) query = term;
		chooseScope(null);
	}

	function isEditableTarget(target: EventTarget | null): boolean {
		if (!(target instanceof HTMLElement)) return false;
		return (
			target.isContentEditable ||
			target.tagName === 'INPUT' ||
			target.tagName === 'TEXTAREA' ||
			target.tagName === 'SELECT'
		);
	}

	function onWindowKeydown(e: KeyboardEvent): void {
		const action = paletteKeyboardAction(
			{
				key: e.key,
				altKey: e.altKey,
				metaKey: e.metaKey,
				ctrlKey: e.ctrlKey,
				editable: isEditableTarget(e.target)
			},
			{ open, query, scope }
		);
		if (!action) return;
		if (action.kind === 'toggle') {
			e.preventDefault();
			if (open) open = false;
			else openPalette();
			return;
		}
		if (action.kind === 'open') {
			e.preventDefault();
			openPalette(action.prefix);
			return;
		}
		e.preventDefault();
		e.stopPropagation();
		clearScope();
	}
</script>

<svelte:window onkeydown={onWindowKeydown} />

{#snippet highlighted(value: string)}
	{#each highlightParts(value, term) as part}
		{#if part.matched}
			<mark class="bg-primary/15 text-inherit rounded-sm font-semibold">{part.text}</mark>
		{:else}
			{part.text}
		{/if}
	{/each}
{/snippet}

<Button
	variant="outline"
	onclick={() => openPalette()}
	aria-label="Search platform"
	title={shortcutLabel ? `Search platform (${shortcutLabel})` : 'Search platform'}
	class="border-border/60 bg-foreground/[0.04] text-muted-foreground hover:border-ring hover:bg-background hover:text-foreground h-9 shrink-0 gap-2 rounded-[var(--radius-chrome)] px-2.5 font-normal"
>
	<SearchIcon class="size-4" />
	<span class="hidden text-sm sm:inline">Search</span>
	{#if shortcutLabel}
		<kbd class="bg-muted hidden rounded px-1.5 py-0.5 font-mono text-[10px] leading-none md:inline">
			{shortcutLabel}
		</kbd>
	{/if}
</Button>

<!-- The one glass surface in a sharp portal: `sharp.css` zeroes the glass knobs because a surface
     an adviser reads all day must not sit on a moving backdrop — a palette is open for seconds, so
     that reasoning does not reach it. The material, geometry and phone behaviour (a full-screen
     search view below `sm`) are the shared SPOTLIGHT_* constants in @graphene/ui's search.ts, so
     this palette and the client's cannot drift — docs/search.md, "The panel". The only local
     addition is room on the right of the field for the `?` guide button beside the close. -->
<Command.Dialog
	bind:open
	title="Platform search"
	description="Find records, open pages or start common actions"
	shouldFilter={false}
	showCloseButton
	overlayClass={SPOTLIGHT_OVERLAY_CLASS}
	contentStyle={SPOTLIGHT_PANEL_STYLE}
	class={cn(SPOTLIGHT_PANEL_CLASS, 'sm:[&_[data-slot=command-input-wrapper]]:pr-24')}
	onOpenChange={(next: boolean) => {
		if (!next) reset();
	}}
>
	<Command.Input
		bind:ref={inputEl}
		bind:value={query}
		oninput={() => (showGuide = false)}
		placeholder={parsed.mode === 'commands'
			? 'Find a quick action…'
			: parsed.mode === 'scopes'
				? 'Choose what to search…'
				: scope
					? `Search ${activeScope.label.toLocaleLowerCase()}…`
					: 'Search by name, email, title or reference…'}
		aria-label="Search clients, accounts, documents, journeys, transactions, messages and reviews"
		class="placeholder:text-muted-foreground/70 text-xl font-light md:text-xl"
	/>
	<!-- Sits on the same line as the dialog's own close button (repositioned on the dialog above), one
     gap to its left, so the two controls read as a pair rather than two separately-placed icons. -->
	<Button
		type="button"
		variant="ghost"
		size="icon-sm"
		onclick={() => (showGuide = true)}
		aria-label="How to use platform search"
		title="How to use search"
		class="text-muted-foreground hover:text-foreground absolute top-7 right-14 z-10 max-sm:hidden"
	>
		<CircleQuestionMarkIcon class="size-4" />
	</Button>
	<!-- The phone's way out: the shared round close beside the pill field (the dialog's own close
	     and the guide button hide below `sm`). -->
	<Dialog.Close
		data-slot="spotlight-cancel"
		aria-label="Close search"
		class={SPOTLIGHT_PHONE_CLOSE_CLASS}
	>
		<XIcon />
	</Dialog.Close>
	<!-- Outside the list on purpose: these are the bar's own controls, so they stay put whether or not
	     anything is listed below. Once a query returns several kinds of record, this becomes a
	     Spotlight-style contextual category strip: only categories PRESENT in the result are offered,
	     and selecting one uses the existing server scope so it can return a full category result
	     rather than merely hiding the other four-row previews in the browser. -->
	<div
		class="border-border/40 flex items-center gap-2 border-t px-3 py-2 max-sm:border-t-0 max-sm:pt-0 max-sm:pb-3"
	>
		{#if showGuide}
			<span class="text-xs font-medium">How search works</span>
		{:else if showResultCategories}
			<div
				role="group"
				aria-label="Filter search results by category"
				class="no-scrollbar flex min-w-0 flex-1 items-center gap-1 overflow-x-auto"
			>
				<Button
					type="button"
					variant="secondary"
					size="sm"
					aria-pressed="true"
					class={SPOTLIGHT_CHIP_CLASS}
				>
					All
				</Button>
				{#each resultCategories as category (category.type)}
					<Button
						type="button"
						variant="ghost"
						size="sm"
						onclick={() => chooseScope(category.type)}
						aria-pressed="false"
						class={cn(
							SPOTLIGHT_CHIP_CLASS,
							'text-muted-foreground hover:text-foreground max-sm:text-foreground'
						)}
					>
						{category.label}
					</Button>
				{/each}
			</div>
		{:else if scope}
			<Button
				type="button"
				variant="secondary"
				size="sm"
				onclick={clearScope}
				aria-label={`Search all categories instead of ${activeScope.label.toLocaleLowerCase()}`}
				class="bg-primary/10 text-primary hover:bg-primary/15 h-7 gap-1.5 px-2 text-xs"
			>
				{activeScope.label}
				<XIcon class="size-3" />
			</Button>
		{:else}
			<span class="text-muted-foreground text-xs">All categories</span>
		{/if}
		{#if !showResultCategories}
			<div class="ml-auto flex items-center gap-1">
				<Button
					type="button"
					variant="ghost"
					size="sm"
					onclick={() => toggleMode(SCOPE_PREFIX)}
					class={[
						'text-muted-foreground hover:text-foreground h-7 gap-1.5 px-2 text-xs',
						parsed.mode === 'scopes' && 'bg-accent text-accent-foreground'
					]}
				>
					Categories
					<kbd class="bg-muted rounded px-1 font-mono text-[10px]">{SCOPE_PREFIX}</kbd>
				</Button>
				<Button
					type="button"
					variant="ghost"
					size="sm"
					onclick={() => toggleMode(COMMAND_PREFIX)}
					class={[
						'text-muted-foreground hover:text-foreground h-7 gap-1.5 px-2 text-xs',
						parsed.mode === 'commands' && 'bg-accent text-accent-foreground'
					]}
				>
					Quick actions
					<kbd class="bg-muted rounded px-1 font-mono text-[10px]">{COMMAND_PREFIX}</kbd>
				</Button>
			</div>
		{/if}
	</div>

	<!-- The panel owns the hard cap and the list gives way inside it. Its max-height is ALSO the
	     reveal track, and it takes `motion-reveal` — the same bouncy spring and duration as the panel
	     above it, so the palette and the results it unfolds read as one quick gesture rather than a
	     fast box containing a slow one. Not `motion-expand`: that is the zero-bounce spring for a
	     surface too large to bounce. The spring overshoots this max-height mid-flight; the panel's own
	     max-h absorbs it, so the bounce is felt at the bottom edge without ever passing the cap. -->
	<Command.List
		class={[
			'no-scrollbar motion-reveal min-h-0 flex-1 scroll-py-2 transition-[max-height,opacity,padding,border-color]',
			listOpen
				? 'border-border/40 max-h-[calc(var(--spotlight-max-h)-8.5rem)] border-t p-2 opacity-100'
				: 'pointer-events-none max-h-0 overflow-hidden border-transparent p-0 opacity-0'
		]}
	>
		{#if showGuide}
			<!-- No heading here: the row above already says "How search works". -->
			<div class="px-2 pt-1 pb-3">
				<div class="grid gap-1.5 sm:grid-cols-3">
					<div class="border-border/60 bg-muted/30 rounded-lg border p-2.5">
						<div
							class="bg-primary/10 text-primary mb-2 flex size-6 items-center justify-center rounded-md"
						>
							<SearchIcon class="size-3.5" />
						</div>
						<p class="text-sm font-medium">Find anything</p>
						<p class="text-muted-foreground mt-0.5 text-xs">
							Type a name, email, title or reference.
						</p>
					</div>
					<div class="border-border/60 bg-muted/30 rounded-lg border p-2.5">
						<div
							class="bg-primary/10 text-primary mb-2 flex size-6 items-center justify-center rounded-md"
						>
							<UsersIcon class="size-3.5" />
						</div>
						<p class="text-sm font-medium">Narrow the results</p>
						<p class="text-muted-foreground mt-0.5 text-xs">
							Pick a category when you know where to look.
						</p>
					</div>
					<div class="border-border/60 bg-muted/30 rounded-lg border p-2.5">
						<div
							class="bg-primary/10 text-primary mb-2 flex size-6 items-center justify-center rounded-md"
						>
							<PlusIcon class="size-3.5" />
						</div>
						<p class="text-sm font-medium">Start something</p>
						<p class="text-muted-foreground mt-0.5 text-xs">Create a client, task or journey.</p>
					</div>
				</div>
				<dl class="mt-3 grid gap-x-6 gap-y-1.5 px-1 sm:grid-cols-2">
					{#each PALETTE_SHORTCUTS as shortcut (shortcut.label)}
						<div class="flex items-center gap-2">
							<dt class="flex shrink-0 items-center gap-1">
								{#each shortcut.keys as key (key)}
									<kbd class="bg-muted rounded px-1.5 py-0.5 font-mono text-[10px]">{key}</kbd>
								{/each}
							</dt>
							<dd class="text-muted-foreground min-w-0 truncate text-xs">{shortcut.label}</dd>
						</div>
					{/each}
				</dl>
				<div class="mt-3 flex justify-end px-1">
					<Button type="button" size="sm" onclick={dismissGuide}>Start searching</Button>
				</div>
			</div>
		{:else if failed}
			<LoadErrorBanner
				class="m-1"
				message="Search is unavailable. Try again."
				onRetry={() => run(term)}
			/>
		{/if}

		{#if !showGuide && visibleScopes.length}
			<Command.Group heading="Search only in" class="motion-item-enter py-1">
				{#each visibleScopes as item (item.command)}
					{@const ScopeIcon = item.type ? ICONS[item.type] : SearchIcon}
					<Command.Item
						value={`scope-pick-${item.command}`}
						onSelect={() => chooseScopeFromList(item.type)}
						class="min-h-9 gap-2.5 px-2.5 py-1.5"
					>
						<div
							class="bg-muted text-muted-foreground flex size-6 shrink-0 items-center justify-center rounded-md"
						>
							<ScopeIcon class="size-3.5" />
						</div>
						<span class="text-sm font-medium">{item.label}</span>
					</Command.Item>
				{/each}
			</Command.Group>
		{/if}

		{#if !showGuide && visibleActions.length}
			<Command.Group heading="Quick actions" class="motion-item-enter py-1">
				{#each visibleActions as action (action.href)}
					<Command.Item
						value={`action-${action.label}`}
						onSelect={() => closeAndGo(action.href)}
						class="min-h-9 gap-2.5 px-2.5 py-1.5"
					>
						<div
							class="bg-primary/10 text-primary flex size-6 shrink-0 items-center justify-center rounded-md"
						>
							<action.icon class="size-3.5" />
						</div>
						<span class="text-sm font-medium">{action.label}</span>
						<Command.Shortcut><PlusIcon class="size-3.5" /></Command.Shortcut>
					</Command.Item>
				{/each}
			</Command.Group>
		{/if}

		{#if !showGuide && visiblePages.length}
			<Command.Group heading="Pages" class="motion-item-enter py-1">
				{#each visiblePages as destination (destination.href)}
					<Command.Item
						value={`page-${destination.label}`}
						onSelect={() => closeAndGo(destination.href)}
						class="min-h-10 gap-3 px-3 py-2"
					>
						<destination.icon class="text-muted-foreground size-4" />
						<span>{destination.label}</span>
						<Command.Shortcut><CornerDownLeftIcon class="size-3.5" /></Command.Shortcut>
					</Command.Item>
				{/each}
			</Command.Group>
		{/if}

		{#if !showGuide && ready && loading && !results}
			<p class="text-muted-foreground px-3 py-6 text-center text-sm">Searching…</p>
		{:else if !showGuide && ready}
			{#if orderedHits.length}
				<!-- One result stream. Type remains explicit on every row, but it no longer dictates
				     position: the backend's cross-entity rank is the order the keyboard and eye walk. -->
				<Command.Group
					heading={`Best matches · ${orderedHits.length}`}
					class="motion-item-enter py-1"
				>
					{#each orderedHits as hit (`${hit.type}:${hit.id}`)}
						{@const Icon = ICONS[hit.type] ?? SearchIcon}
						<Command.Item
							value={`result-${hit.type}-${hit.id}`}
							onSelect={() => select(hit)}
							class="min-h-13 gap-3 px-3 py-2.5"
						>
							<div
								class="bg-muted text-muted-foreground flex size-8 shrink-0 items-center justify-center rounded-lg"
							>
								<Icon class="size-4" />
							</div>
							<div class="min-w-0 flex-1">
								<div class="flex min-w-0 items-center gap-2">
									<span class="truncate font-medium">{@render highlighted(hit.title)}</span>
									<span
										class="border-border/70 text-muted-foreground shrink-0 rounded-md border px-1.5 py-0.5 text-[10px] font-medium"
									>
										{TYPE_LABELS[hit.type] ?? 'Result'}
									</span>
								</div>
								{#if hit.subtitle}
									<p class="text-muted-foreground mt-0.5 truncate text-xs">
										{@render highlighted(hit.subtitle)}
									</p>
								{/if}
							</div>
							{#if hit.meta}
								<span class="text-muted-foreground max-w-36 shrink-0 truncate text-right text-xs">
									{@render highlighted(hit.meta)}
								</span>
							{/if}
						</Command.Item>
					{/each}
				</Command.Group>
			{/if}
			{#if !loading && !hasAnything}
				<Command.Empty forceMount>
					No matches for “{term}”. Check the spelling or search all categories.
				</Command.Empty>
			{/if}
		{:else if !showGuide && parsed.mode === 'commands' && !hasAnything}
			<Command.Empty forceMount>No quick actions match “{term}”.</Command.Empty>
		{:else if !showGuide && parsed.mode === 'scopes' && !hasAnything}
			<Command.Empty forceMount>No categories match “{term}”.</Command.Empty>
		{/if}
	</Command.List>
	<!-- Only while something is listed, and only the keys that act on a LIST: the two prefixes are
	     buttons in the row above now, and repeating them here was the same hint twice. -->
	{#if listOpen && !showGuide}
		<div
			class="border-border/40 text-muted-foreground hidden items-center gap-4 border-t px-4 py-2 text-[10px] sm:flex"
		>
			<span><kbd class="font-mono">↑↓</kbd> Navigate</span>
			<span><kbd class="font-mono">↵</kbd> Open</span>
			<span class="ml-auto"><kbd class="font-mono">esc</kbd> Back / close</span>
		</div>
	{/if}
</Command.Dialog>
