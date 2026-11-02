<script lang="ts">
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import ListFilterIcon from '@lucide/svelte/icons/list-filter';
	import SearchIcon from '@lucide/svelte/icons/search';
	import { Button } from '../ui/button/index.js';
	import * as DropdownMenu from '../ui/dropdown-menu/index.js';
	import { cn } from '../../utils.js';
	import { openSearchPalette } from '../../search.js';

	/**
	 * The one way a client browses a library on the portal — the Insights page and the Docs
	 * page share it, so the two never drift. One bar, three ranks of control, after Material 3's
	 * primary tabs and Apple News's section bar rather than a stack of pills over a free-floating
	 * box:
	 *
	 * - the TABS are how the reader browses (topics, document categories): full-size labels on
	 *   a 44px target, a 3px brand indicator under the current one, a hairline under the row, and
	 *   on a phone the row scrolls sideways under a fade rather than wrapping into a tag cloud;
	 * - an optional PULL-DOWN beside them for a secondary narrowing used rarely (the kind of
	 *   piece, the account) — a label with a chevron, an icon alone on a phone;
	 * - SEARCH as a toolbar button. It does not grow a second search box: it opens the portal's
	 *   one palette (`SpotlightSearch`, via `openSearchPalette`) on this page's category, so a
	 *   client who searches from the Docs page gets documents, in the same place and with the
	 *   same results as searching from the header.
	 *
	 * Presentational: tab and menu state belong to the caller. Interactivity off (the studio
	 * preview) renders everything inert.
	 */
	export type BrowseOption = { value: string; label: string };

	let {
		tabs,
		tab,
		ontab,
		tabsLabel,
		menu = null,
		search = null,
		interactive = true
	}: {
		tabs: BrowseOption[];
		tab: string;
		ontab: (value: string) => void;
		/** Accessible name for the tab row, e.g. "Filter by topic". */
		tabsLabel: string;
		menu?: {
			/** The menu's heading ("Show", "Account"). */
			heading: string;
			options: BrowseOption[];
			value: string;
			onchange: (value: string) => void;
			/** True when the current value is the unnarrowed default — the trigger reads quiet. */
			quiet?: boolean;
		} | null;
		search?: {
			/** Accessible name, e.g. "Search documents". */
			label: string;
			/** The palette category to open on (an API group type), or null for everything. */
			scope: string | null;
		} | null;
		interactive?: boolean;
	} = $props();

	const menuLabel = $derived(
		menu ? (menu.options.find((o) => o.value === menu.value)?.label ?? menu.heading) : ''
	);
</script>

<div data-browse-bar>
	<div class="flex items-end gap-2 border-b">
		{#if tabs.length > 1}
			<div
				class="no-scrollbar -mb-px flex min-w-0 flex-1 snap-x gap-6 overflow-x-auto [mask-image:linear-gradient(to_right,black_calc(100%-1.5rem),transparent)]"
				role="group"
				aria-label={tabsLabel}
			>
				{#each tabs as t (t.value)}
					{@const on = tab === t.value}
					<button
						type="button"
						disabled={!interactive}
						onclick={() => ontab(t.value)}
						aria-pressed={on}
						class={cn(
							'focus-visible:ring-ring relative h-11 shrink-0 snap-start text-[0.9375rem] font-medium whitespace-nowrap transition-colors focus-visible:ring-2 focus-visible:outline-none last:me-6',
							'after:absolute after:inset-x-0 after:bottom-0 after:h-[3px] after:rounded-t-full after:transition-colors',
							on
								? 'text-foreground after:bg-[var(--brand)]'
								: 'text-muted-foreground hover:text-foreground after:bg-transparent'
						)}
					>
						{t.label}
					</button>
				{/each}
			</div>
		{:else}
			<div class="flex-1"></div>
		{/if}
		<div class="flex h-11 shrink-0 items-center gap-0.5">
			{#if menu}
				<DropdownMenu.Root>
					<DropdownMenu.Trigger disabled={!interactive}>
						{#snippet child({ props })}
							<Button
								{...props}
								variant="ghost"
								class={cn(
									'h-9 rounded-full px-3 text-sm',
									menu.quiet ? 'text-muted-foreground' : 'text-foreground'
								)}
								aria-label="{menu.heading}: {menuLabel}"
							>
								<span class="@md:inline hidden">{menuLabel}</span>
								<ListFilterIcon class="@md:hidden size-4" />
								<ChevronDownIcon class="@md:inline hidden size-3.5 opacity-60" />
							</Button>
						{/snippet}
					</DropdownMenu.Trigger>
					<DropdownMenu.Content align="end" class="w-48">
						<DropdownMenu.Label>{menu.heading}</DropdownMenu.Label>
						<DropdownMenu.Separator />
						<DropdownMenu.RadioGroup
							value={menu.value}
							onValueChange={(v) => menu.onchange(v ?? menu.options[0]?.value ?? '')}
						>
							{#each menu.options as option (option.value)}
								<DropdownMenu.RadioItem value={option.value}>{option.label}</DropdownMenu.RadioItem>
							{/each}
						</DropdownMenu.RadioGroup>
					</DropdownMenu.Content>
				</DropdownMenu.Root>
			{/if}
			{#if search}
				<Button
					variant="ghost"
					size="icon-lg"
					class="text-muted-foreground hover:text-foreground rounded-full"
					aria-label={search.label}
					disabled={!interactive}
					onclick={() => openSearchPalette(search.scope)}
				>
					<SearchIcon class="size-[1.125rem]" />
				</Button>
			{/if}
		</div>
	</div>
</div>
