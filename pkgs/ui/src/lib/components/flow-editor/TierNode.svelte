<!--
	A scope band — a dotted outline around a RUN of neighbouring work at the same scope, with the
	words for how often it happens ("For each client", "For each account").

	CHROME, and descriptive only. It is derived from the `scope` every work node already carries and
	sized from wherever the layout put those nodes, so it can never move one. That is the difference
	from the tier containers it replaces: those grouped by scope and then laid each group out, which
	made a tier-crossing spine zigzag between boxes. A band forms only where the flow ALREADY runs
	several same-scope steps back to back (`graph.ts::scopeBands`), and where it does not, there is
	no band.

	Deliberately NOT a parent node: `parentId` + `extent: 'parent'` would clamp its members inside
	it, which is precisely the power something purely descriptive must not have.

	Quiet enough to read as ground rather than as another thing competing with the cards — a dashed
	hairline, no fill, and the label as a small pill sitting on the top edge the way a fieldset
	legend does, so the band needs no header strip and therefore no vertical room of its own.
-->
<script lang="ts">
	import Repeat2Icon from '@lucide/svelte/icons/repeat-2';
	import type { TierNodeData } from './graph';

	let { data }: { data: TierNodeData } = $props();
</script>

<div
	class="border-muted-foreground/40 pointer-events-none relative h-full w-full rounded-xl
		border-2 border-dashed bg-background/80"
>
	<!-- The wash is `--background` and NOT `--muted`: the canvas itself is `--muted`, so a band the
	     colour of the canvas is a band nobody can see. It shows in the gutters between the cards,
	     which is where a band has room to be read at all.
	     The outline is `--muted-foreground` at low alpha rather than `--border`: that token is a
	     hairline sized for the edge of a card sitting ON the canvas, and at the zoom a whole journey
	     is read at, a hairline dashed line disappears entirely. -->
	<span
		class="bg-background border-muted-foreground/40 text-muted-foreground absolute -top-3 left-4
			inline-flex
			items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-semibold"
	>
		{#if data.repeats}
			<Repeat2Icon class="size-3.5" />
		{/if}
		{data.label}
	</span>
</div>
