<script lang="ts">
	// The export affordance every book listing shares: one labelled button at the foot of the
	// table's card, opening a short menu of formats.
	//
	// WHY A MENU AND NOT TWO BUTTONS. Exporting is one intention with a choice of container. Two
	// buttons put that choice permanently on screen, competing with the actions that actually differ
	// from each other, and they scale badly the moment a third format appears.
	//
	// WHY IT IS LABELLED AND NOT AN ICON. It was icon-only for a while, and that was a straight
	// regression on what it replaced: the per-page control this supersedes read "Export CSV", and a
	// download glyph alone does not. `title` and `aria-label` covered hover and assistive tech, which
	// is the accessibility question and not the discoverability one — someone scanning a toolbar they
	// have not used before has to RECOGNISE a glyph where before they could read a word. The word
	// costs about sixty pixels at the quietest end of the card. It says "Export" rather than
	// "Export CSV" because CSV is one of two formats, and naming one of them on the trigger
	// would hide the other.
	//
	// WHY IT LIVES AT THE CARD'S FOOT, not in `ListingToolbar` beside the filters. The toolbar's
	// controls all NARROW the list; this one acts on whatever they have left. Putting it among them
	// reads as another filter, and on a stacked toolbar it ends up on the second row, in a different
	// place on every page. Beside the pager it is the last thing in the card, which is where you are
	// once you have finished reading the rows.
	//
	// WHY LINKS, FETCHED. They stay links, so "open in a new tab" and "copy link" behave. A plain
	// click fetches the file in place instead of navigating (`export-download.ts`): a large file can
	// take many seconds to build, and as a navigation the reader saw nothing happen until it arrived —
	// and a failure replaced the listing with a bare error page. Now the trigger says it is working,
	// and a failure is a toast over the listing they were on.
	//
	// What links here do NOT buy is a no-JS fallback, and it is worth being exact about that: the
	// menu is closed on the server, so SSR emits the trigger and no items at all. The plain button
	// this replaced did work without JS. That is a real regression in the abstract and not one in
	// this product — the staff portal is a client-rendered SvelteKit app whose editors, drawers and
	// filters all need JS — but the reason is "this app requires JS", not "links are progressive".
	import DownloadIcon from '@lucide/svelte/icons/download';
	import FileSpreadsheetIcon from '@lucide/svelte/icons/file-spreadsheet';
	import TableIcon from '@lucide/svelte/icons/table';
	import LoaderCircleIcon from '@lucide/svelte/icons/loader-circle';
	import { mergeProps } from 'bits-ui';
	import { toast } from 'svelte-sonner';
	import * as DropdownMenu from '../../ui/dropdown-menu/index.js';
	import { Button } from '../../ui/button/index.js';
	import { fetchExport, saveFile } from './export-download.js';

	let {
		href,
		label = 'Export'
	}: {
		/**
		 * The export endpoint for THIS listing, already carrying the current filters. The format is
		 * appended as `format=`, so the route answers both from one place and the two files can never
		 * disagree about which rows they contain.
		 */
		href: string;
		/**
		 * The word on the button, and its accessible name. Names the ACTION rather than the resource
		 * ("Export", not "Export transactions") — one component serves every listing, and the card it
		 * sits in has already said which rows these are.
		 */
		label?: string;
	} = $props();

	/** `href` already has a query string more often than not, so pick the joiner rather than assume. */
	const withFormat = (format: string) => `${href}${href.includes('?') ? '&' : '?'}format=${format}`;

	// Excel first: it is what these files are opened in, and the only one that arrives formatted —
	// frozen header, real dates, money to the penny. CSV stays for feeding another system. PDF
	// still generates (`format=pdf` on the same routes) but is not offered here.
	const FORMATS = [
		{ format: 'xlsx', name: 'Excel', icon: FileSpreadsheetIcon },
		{ format: 'csv', name: 'CSV', icon: TableIcon }
	] as const;

	/** The format being built, while one is. One at a time: the trigger is disabled meanwhile. */
	let busy = $state<(typeof FORMATS)[number] | null>(null);
	const shown = $derived(busy ? `Exporting ${busy.name}…` : label);

	async function start(event: MouseEvent, item: (typeof FORMATS)[number]) {
		// A modified click (new tab, save link as) stays the browser's own.
		if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
			return;
		}
		event.preventDefault();
		if (busy) return;
		busy = item;
		const url = withFormat(item.format);
		// A toast as well as the trigger: the trigger sits at the card's foot, where a spinner is
		// easy to miss, and a large file can take many seconds to build.
		const pending = toast.loading(`Preparing ${item.name}…`);
		try {
			const outcome = await fetchExport(url, saveFile);
			if (outcome.kind === 'failed') toast.error(outcome.message, { id: pending });
			else if (outcome.kind === 'saved') toast.success(`${item.name} downloaded`, { id: pending });
			else {
				toast.dismiss(pending);
				window.location.assign(url);
			}
		} catch {
			toast.error('The export failed. Try again, or choose another format.', { id: pending });
		} finally {
			busy = null;
		}
	}
</script>

<DropdownMenu.Root>
	<DropdownMenu.Trigger>
		{#snippet child({ props })}
			<!-- `aria-label` as well as the visible word, and they are the same string by
			     construction — one value feeds both, so they cannot drift into the mismatch that breaks
			     speech input ("click Export" must hit the thing that reads Export). No `title`: a
			     tooltip that repeats visible text is noise. -->
			<Button
				{...props}
				variant="outline"
				size="sm"
				class="h-8"
				aria-label={shown}
				aria-busy={busy ? 'true' : undefined}
				disabled={busy !== null}
			>
				{#if busy}
					<LoaderCircleIcon class="size-3.5 animate-spin" />
				{:else}
					<DownloadIcon class="size-3.5" />
				{/if}
				{shown}
			</Button>
		{/snippet}
	</DropdownMenu.Trigger>
	<DropdownMenu.Content align="end" class="w-44">
		<!-- `data-sveltekit-reload` for the clicks the browser still handles (a new tab): without it
		     SvelteKit's client router intercepts, finds no page to render, and the file never
		     arrives. -->
		{#each FORMATS as item (item.format)}
			<DropdownMenu.Item>
				{#snippet child({ props })}
					<a
						{...mergeProps(props, { onclick: (e: MouseEvent) => start(e, item) })}
						href={withFormat(item.format)}
						data-sveltekit-reload
					>
						<item.icon class="size-4" />
						{item.name}
					</a>
				{/snippet}
			</DropdownMenu.Item>
		{/each}
	</DropdownMenu.Content>
</DropdownMenu.Root>
