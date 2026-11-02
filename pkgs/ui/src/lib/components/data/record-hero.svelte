<script lang="ts">
	/**
	 * The head of a RECORD: the disc, the name with its badges, the facts under it, and whatever can
	 * be done to the thing at the trailing edge.
	 *
	 * ONE component because there are two records — a client and an account — and they had two
	 * copies of this row. The copies drifted exactly where copies do: one bolded the name of the
	 * client it was about and the other did not, the two facts rows used different gaps, and the
	 * account's actions ended up in a ruled strip under the facts while the client's sat in the
	 * row. An adviser moving between the two records was reading two designs for one idea.
	 *
	 * The CARD stays with the caller. A client record wraps this in a card that may carry a
	 * vulnerability banner; an account's is tinted for the same reason. What is shared is the row,
	 * which is the part that has to look identical.
	 */
	import type { Snippet } from 'svelte';
	import ClientAvatar from './client-avatar.svelte';
	import type { AvatarKind } from './client-kind.js';
	import { WRAP_NAME } from '../../text-shell.js';

	let {
		name,
		kind = 'individual',
		badges,
		facts,
		actions
	}: {
		/** What the record is called, and what the disc stands for. */
		name: string;
		/** Which glyph the disc carries — a person, a building for a trust or a company, several
		 *  people for a group, a wallet for an account. An ACCOUNT's head states the account rather
		 *  than its owner, so it passes `account` instead of borrowing the client's kind: the default
		 *  person disc above "Oakden SIPP" read as a record about the owner. */
		kind?: AvatarKind;
		/** The pills beside the name — type, service, status. */
		badges?: Snippet;
		/** The row under it: reference, who owns it, how to reach them. Each clause is the caller's,
		 *  because what a record states about itself is the one thing that differs. */
		facts?: Snippet;
		/** What can be DONE to this record, at the trailing edge of the name's row. */
		actions?: Snippet;
	} = $props();
</script>

<div class="flex min-w-0 items-start gap-4">
	<ClientAvatar {name} {kind} size="lg" />
	<div class="min-w-0 flex-1">
		<div class="flex flex-wrap items-center gap-2">
			<!-- `min-w-0` + `WRAP_NAME`: the heading is a flex item beside up to three badges, so
			     without the first it refuses to shrink and pushes them out of the card, and without the
			     second a long name — an organisation, or a double-barrelled surname — has nowhere to
			     break. -->
			<h1 class="min-w-0 text-2xl font-semibold tracking-tight {WRAP_NAME}">{name}</h1>
			{@render badges?.()}
		</div>
		{#if facts}
			<!-- ONE WEIGHT, one gap. Every clause here is a fact of the same rank, so none of them is
			     bolded and none of them is a heading: the moment one was `font-medium` the row read as
			     a title with footnotes. -->
			<div
				class="text-muted-foreground mt-1.5 flex min-w-0 flex-wrap items-center gap-x-3 gap-y-0.5 text-xs"
			>
				{@render facts()}
			</div>
		{/if}
	</div>
	{#if actions}
		<!-- IN THE ROW, at its end — not absolutely positioned into the card's corner, which is where
		     a vulnerability banner's own button already lives. In the row it simply takes its own
		     space. -->
		<div class="ms-auto flex shrink-0 flex-wrap items-center justify-end gap-2">
			{@render actions()}
		</div>
	{/if}
</div>
