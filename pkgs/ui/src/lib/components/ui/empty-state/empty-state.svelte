<script lang="ts">
	import type { Component, Snippet } from 'svelte';
	import { cn } from '../../../utils.js';

	/**
	 * THE empty state — every "nothing here" in the product renders this one treatment
	 * (dashed border, centred, optional icon + call to action) instead of a bespoke div.
	 * Use `compact` inside cards/panels where the full-height version would dominate.
	 *
	 * WHAT AN EMPTY STATE IS FOR (docs/design-language.md): it says what is missing and the one
	 * action that fixes it. It is not an explanation of the feature. Carbon's pattern puts the same
	 * rule the other way round — "don't cover multiple options; pick the most important action", and
	 * "don't lead the user into a dead end" — which is why a bare title with neither a description
	 * nor an action is the one shape to avoid.
	 *
	 * AND IT MUST SAY WHICH KIND OF NOTHING IT IS. Four read alike and mean opposite things:
	 *
	 *   nothing yet     the firm has created none of these        → the action that creates one
	 *   nothing matches a filter or search is applied             → "Clear filters", NEVER "create"
	 *   not permitted   the caller may not read them              → `tone="warning"`, say so
	 *   unavailable     a read failed, or a pipeline has not run  → `tone="warning"`, say so
	 *
	 * The last two are why `tone` exists. A failed read dressed as "you have nothing yet" is a
	 * FALSE STATEMENT ABOUT THE READER'S OWN BOOK, and this estate has paid for it repeatedly in
	 * other guises — a merge reject that "reads identically to 'this tenant has no data here'", a
	 * picker that raises rather than returning `[]` "because an empty dropdown that means
	 * 'unreadable' looks exactly like one that means 'this firm has no staff there'". Words alone
	 * do not carry it: a reader matches the SHAPE before reading the sentence, so the two failure
	 * kinds wear `LoadErrorBanner`'s own tokens and are recognisable at a glance.
	 *
	 * WHERE THE BANNER ENDS AND THIS BEGINS: a failed PAGE load gets `LoadErrorBanner` at the top —
	 * it is one statement about the whole screen, and it carries the retry. A failed SLICE inside an
	 * otherwise-working page gets `tone="warning"` in place, because the reader needs it where the
	 * missing thing would have been. They are not two spellings of one idea; they are two scopes.
	 *
	 * There is deliberately NO `kind` prop. Four of the kinds differ only in their words, and an
	 * enum would invite a component-supplied default sentence — which is exactly how a generic
	 * "Nothing to show." comes to be shipped in place of the one sentence that helps.
	 */
	let {
		icon: Icon,
		title,
		description,
		action,
		compact = false,
		tone = 'neutral',
		chromeless = false,
		class: className
	}: {
		icon?: Component<{ class?: string }>;
		title: string;
		description?: string;
		/** Optional call to action (a Button, a link) rendered under the text. ONE — not a choice. */
		action?: Snippet;
		compact?: boolean;
		/**
		 * `warning` for the two kinds that must never read as "you have nothing": a read that
		 * FAILED, and one the caller is not permitted to make. Shares `LoadErrorBanner`'s tokens, so
		 * a failure looks the same wherever in the page it is stated.
		 */
		tone?: 'neutral' | 'warning';
		/**
		 * Drop the border, fill and padding, keeping the centred stack and the words.
		 *
		 * For a surface whose whole design is the absence of chrome — the client portal's float,
		 * ink, orbit, spectrum, sheet and bars looks — where a dashed card contradicts the look it
		 * sits in. That deviation is legitimate; six hand-rolled copies of it were not, and their
		 * sentences had already drifted apart.
		 */
		chromeless?: boolean;
		class?: string;
	} = $props();
</script>

<div
	data-slot="empty-state"
	class={cn(
		'text-center',
		!chromeless && 'rounded-xl border',
		!chromeless && (tone === 'warning' ? 'border-warning-line bg-warning-subtle' : 'border-dashed'),
		!chromeless && (compact ? 'px-4 py-6' : 'p-10'),
		chromeless && (compact ? 'py-2' : 'py-6'),
		className
	)}
>
	{#if Icon}
		<!-- Decorative: `@lucide/svelte` stamps `aria-hidden="true"` on an icon given no a11y prop,
		     which is what Carbon asks of an empty state's image. The title carries the meaning. -->
		<Icon
			class={cn(
				'mx-auto',
				tone === 'warning' ? 'text-warning-strong' : 'text-muted-foreground',
				compact ? 'mb-2 size-6' : 'mb-3 size-8'
			)}
		/>
	{/if}
	<p class={cn('font-medium', tone === 'warning' && 'text-warning-strong', compact && 'text-sm')}>
		{title}
	</p>
	{#if description}
		<p class="text-muted-foreground mt-1 text-sm">{description}</p>
	{/if}
	{#if action}
		<div class="mt-4 flex justify-center">{@render action()}</div>
	{/if}
</div>
