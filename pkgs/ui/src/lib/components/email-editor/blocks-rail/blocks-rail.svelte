<script lang="ts">
	// The palette: a floating icon rail whose flyouts hold GrapesJS's OWN block panel and the merge
	// fields. IT DRAWS NO TILES OF ITS OWN — `blockManager: { custom: true }` plus a hand-rolled drag
	// cost two rounds of broken drag-and-drop, because `BlockView` owns the handlers, the drop model,
	// the sorter hand-off and the teardown. Not a Popover either: `Popover.Content` portals to the body.
	import type { Snippet } from 'svelte';
	import { cn } from '../../../utils.js';
	import { matchFields, NOTIFICATION_GROUP, type MergeFieldGroup } from '../merge-fields.js';
	import SearchIcon from '@lucide/svelte/icons/search';
	import CircleQuestionMarkIcon from '@lucide/svelte/icons/circle-question-mark';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import BlocksIcon from '@lucide/svelte/icons/blocks';
	import TypeIcon from '@lucide/svelte/icons/type';
	import MessageSquareIcon from '@lucide/svelte/icons/message-square';
	import ImageIcon from '@lucide/svelte/icons/image';
	import BracesIcon from '@lucide/svelte/icons/braces';
	import CodeXmlIcon from '@lucide/svelte/icons/code-xml';

	let {
		paletteMount,
		categories = [],
		onselectcategory,
		mergeFields = [],
		disabled = false,
		oninsertField,
		codeOpen = false,
		codeDisabled = false,
		oncode,
		onreference,
		open = $bindable(null)
	}: {
		/** The element GrapesJS's BlockManager renders into — mounted once, at init, so always rendered. */
		paletteMount: Snippet;
		/** From GrapesJS (`BlockManager.getCategories()`), not a list here, so a category the palette gains
		 *  gets a segment; an unknown label falls back to the generic icon, or its blocks are unreachable. */
		categories?: readonly string[];
		/** Opens the category in GrapesJS's own collapse state, so rail and panel cannot disagree. */
		onselectcategory?: (label: string) => void;
		mergeFields?: readonly MergeFieldGroup[];
		disabled?: boolean;
		/** Insert a token into the canvas. Owned by the editor, which knows the selection. */
		oninsertField?: (token: string) => void;
		/** Open the context panel — what this email is about, and the details that buys. Offered from
		 *  HERE because that is where an author is when the list raises the question. Absent ⇒ no button. */
		onreference?: () => void;
		/** Which flyout is showing — a category label, `'fields'`, or null. Bindable: a click on the
		 *  canvas backdrop closes it, which the rail cannot see for itself (see the note below). */
		open?: string | null;
		codeOpen?: boolean;
		/** Separate from `disabled` (= read-only OR not loaded): read-only may still read the HTML. */
		codeDisabled?: boolean;
		/** Toggle the HTML code view. Absent ⇒ no button (a mount that has no code pane). */
		oncode?: () => void;
	} = $props();

	/** A press on one of GrapesJS's own `.gjs-block` tiles — nothing else in the panel is a placement. */
	function onpaletteclick(event: MouseEvent): void {
		const target = event.target as HTMLElement | null;
		if (target?.closest?.('.gjs-block')) open = null;
	}

	// BINDABLE, so the workspace can shut a flyout on a click outside it. Dismissing on the rail's own
	// document-level listener was ruled out: a drag that leaves for the canvas fires no click on
	// release, and closing on pointerdown unmounts GrapesJS's own tile mid-drag.
	function toggle(key: string): void {
		open = open === key ? null : key;
		// A stale query would reopen the flyout showing one field of sixteen, which reads as a failed list.
		if (open !== 'fields') fieldQuery = '';
	}

	// Type to filter, through the same `matchFields` that `{{` in the canvas uses, so the two agree.
	let fieldQuery = $state('');
	const shownFields = $derived(matchFields(mergeFields, fieldQuery));
	/** Any category's flyout: GrapesJS renders ONE panel, so a segment only changes what is expanded. */
	const paletteOpen = $derived(open !== null && open !== 'fields');

	const CATEGORY_ICONS: Record<string, typeof BlocksIcon> = {
		Basic: TypeIcon,
		Media: ImageIcon,
		Message: MessageSquareIcon
	};

	const ICON_CLASS =
		'focus-visible:ring-ring/50 flex size-8 items-center justify-center rounded-md transition-colors focus-visible:ring-2 focus-visible:outline-none disabled:opacity-50';
	const ACTIVE = 'bg-brand-subtle text-brand-strong';
	// `text-foreground/70`, not `text-muted-foreground`: a rail icon is the only thing naming its group.
	const IDLE = 'text-foreground/70 hover:bg-muted hover:text-foreground';
</script>

<!-- A floating pill against `.workspace` (email-editor.svelte), not the canvas column, which scrolls.
     `--email-chrome`, fallback `--card` — that is the email SHEET's paint. Also the flyouts' container. -->
<div
	class="ring-foreground/10 absolute top-4 left-3 z-20 flex flex-col items-center gap-1 rounded-xl bg-[var(--email-chrome,var(--card))] p-1.5 shadow-[var(--elev-2)] ring-1"
>
	{#each categories as label (label)}
		{@const Icon = CATEGORY_ICONS[label] ?? BlocksIcon}
		<button
			type="button"
			{disabled}
			aria-label={label}
			aria-expanded={open === label}
			title={label}
			onclick={() => {
				onselectcategory?.(label);
				toggle(label);
			}}
			class={cn(ICON_CLASS, open === label ? ACTIVE : IDLE)}
		>
			<Icon class="size-4" />
		</button>
	{/each}
	{#if categories.length === 0}
		<!-- One inert icon before the editor loads, so the rail does not collapse and then jump to height. -->
		<span class={cn(ICON_CLASS, 'text-muted-foreground/50')} aria-hidden="true">
			<BlocksIcon class="size-4" />
		</span>
	{/if}

	{#if mergeFields.length > 0 && oninsertField}
		<!-- No vertical margin: the rail is a flex column with `gap-1`, and `my-1` put 17px round a 1px rule. -->
		<div class="bg-border h-px w-5"></div>
		<button
			type="button"
			{disabled}
			aria-label="Merge fields"
			aria-expanded={open === 'fields'}
			title="Merge fields"
			onclick={() => toggle('fields')}
			class={cn(ICON_CLASS, open === 'fields' ? ACTIVE : IDLE)}
		>
			<BracesIcon class="size-4" />
		</button>
	{/if}

	{#if oncode}
		<!-- The HTML code view — the braces button above is the field menu, not code. `aria-pressed`, not
		     `aria-expanded`: a mode, not a flyout. The title carries the warning, where the decision is made. -->
		<!-- No vertical margin, for the reason the divider above gives. -->
		<div class="bg-border h-px w-5"></div>
		<button
			type="button"
			disabled={codeDisabled}
			aria-label="HTML code"
			aria-pressed={codeOpen}
			title="HTML code — read-only. Editing it by hand can silently break the conditions, repeats and merge fields the panel edits."
			onclick={() => oncode?.()}
			class={cn(ICON_CLASS, codeOpen ? ACTIVE : IDLE)}
		>
			<CodeXmlIcon class="size-4" />
		</button>
	{/if}

	<!-- Always rendered, merely `hidden`: GrapesJS mounts its panel here once, at init. Dismissed by the
	     icon, never by an outside click — a drag leaving for the canvas would unmount it mid-drag. -->
	<div
		class={cn(
			'bg-popover ring-foreground/10 absolute top-2 left-full z-20 ml-1.5 w-52 rounded-lg p-2 shadow-md ring-1',
			!paletteOpen && 'hidden'
		)}
		role="group"
		aria-label="Blocks"
	>
		<!-- The tiles wear `cursor: grab` and say nothing about the click `appendBlockOnClick` handles. -->
		<p class="text-muted-foreground px-1 pb-1.5 text-[0.65rem] leading-snug">
			Click to add at the end, or drag into place.
		</p>
		<!-- `gjs-email-palette` is what the compact re-theme in create.ts is scoped by. -->
		<!-- Capped against the viewport, not 24rem: a fixed box scrolled sixteen blocks four at a time. -->
		<!-- A tile click closes the flyout, which otherwise covers the block just placed and opened for
		     typing. A CLICK, not a pointerdown: a drag leaving for the canvas fires none on release. -->
		<div
			class="gjs-email-palette max-h-[calc(100svh-var(--topbar-h,4rem)-9rem)] overflow-y-auto"
			onclickcapture={onpaletteclick}
		>
			{@render paletteMount()}
		</div>
	</div>

	{#if open === 'fields'}
		<div
			class="bg-popover ring-foreground/10 absolute top-2 left-full z-20 ml-1.5 w-64 rounded-lg p-2 shadow-md ring-1"
			role="group"
			aria-label="Merge fields"
		>
			{#if onreference}
				<!-- The flyout is a short list scoped to what is inserted here; the panel answers where they
				     come from and what ticking another context would add. DRESSED AS A BUTTON — a bordered
				     row with a trailing chevron. As a bare line of muted text among a list of rows that are
				     themselves clicked, it read as a caption for them. -->
				<button
					type="button"
					onclick={() => {
						open = null;
						onreference?.();
					}}
					class="text-foreground hover:bg-accent hover:border-ring focus-visible:ring-ring/50 mb-1.5 flex w-full cursor-pointer items-center gap-1.5 rounded-md border px-1.5 py-1.5 text-left text-[0.7rem] font-medium transition-colors focus-visible:ring-2 focus-visible:outline-none"
				>
					<CircleQuestionMarkIcon class="text-muted-foreground size-3.5 shrink-0" />
					<span class="min-w-0 flex-1 truncate">What this email is about</span>
					<ChevronRightIcon class="text-muted-foreground size-3 shrink-0" />
				</button>
			{/if}
			<div class="relative mb-1">
				<SearchIcon
					class="text-muted-foreground pointer-events-none absolute top-1/2 left-1.5 size-3 -translate-y-1/2"
				/>
				<input
					type="text"
					bind:value={fieldQuery}
					placeholder="Search fields"
					aria-label="Search fields"
					autocomplete="off"
					spellcheck="false"
					class="placeholder:text-muted-foreground focus-visible:ring-ring/50 h-6 w-full rounded border-none bg-transparent pr-1 pl-6 text-xs outline-none focus-visible:ring-2"
				/>
			</div>
			<div
				class="max-h-[calc(100svh-var(--topbar-h,4rem)-9rem)] overflow-y-auto [scrollbar-gutter:stable]"
			>
				<!-- WHERE THESE COME FROM, before how to use them: an author looking at a list they did
				     not write needs to know who fills it, or a field reads as a promise nobody made.
				     Click only — a field lands at the caret, and a drop resolves to an element. -->
				<p class="text-muted-foreground px-1 pb-1 text-[0.65rem] leading-snug">
					The platform fills these in when the email is sent. Click one to insert it where you are
					typing.
				</p>
				{#each shownFields as group (group.group)}
					<p
						class="text-muted-foreground px-1 pt-1.5 pb-1 text-[0.65rem] font-semibold tracking-wide uppercase"
					>
						{group.group}
					</p>
					{#if group.group === NOTIFICATION_GROUP}
						<p class="text-muted-foreground px-1 pb-1 text-[0.65rem] leading-snug">
							Only when a notification rule is what sends this email.
						</p>
					{/if}
					{#each group.fields as field (field.token)}
						<button
							type="button"
							title={field.token}
							onclick={() => {
								oninsertField?.(field.token);
								open = null;
							}}
							class="hover:bg-accent focus-visible:ring-ring/50 flex w-full min-w-0 items-center gap-1.5 rounded px-1.5 py-1 text-left text-xs transition-colors focus-visible:ring-2 focus-visible:outline-none"
						>
							<span class="truncate">{field.label}</span>
						</button>
					{/each}
				{/each}
				{#if shownFields.length === 0}
					<p class="text-muted-foreground px-1.5 py-1 text-xs">No matching field.</p>
				{/if}
			</div>
		</div>
	{/if}
</div>
