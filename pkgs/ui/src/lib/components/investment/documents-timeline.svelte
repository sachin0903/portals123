<script lang="ts">
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import { cn } from '../../utils.js';
	import { documentIcon } from './documents-icons.js';
	import type { ClientDocument } from './types.js';

	/**
	 * The documents page as a client reads it: newest first, in RECENCY groups, each document one
	 * friendly row — a category mark, the name, "Statements · 15 Jul" and a chevron. No size, no
	 * format badge, no per-category sections: a client looking for "the thing from July" finds it
	 * by WHEN, and the dense register (`DocumentsList`/`DocumentsIndex`) kept answering the
	 * adviser's question ("every illustration") on the client's page. Those looks remain for the
	 * firms that want them; this is the default.
	 *
	 * The groups follow the Files-app convention rather than the calendar: "This month", "Last
	 * month", "Earlier in 2026", then one group per earlier year. Month runs looked right with a
	 * dozen documents and fell apart at thirty — twenty headings over twenty-six rows, most of
	 * them over a single document — where the recency groups stay at four or five however long
	 * the history grows. The row's own stamp carries the day and month, and the year once the
	 * group no longer says it.
	 *
	 * Documents without an ISO date (fixtures, older loaders) file in one unnamed run in their
	 * given order, so nothing is ever dropped for lacking a field.
	 */
	let {
		documents,
		now = new Date()
	}: {
		documents: ClientDocument[];
		/** "Today", for the recency groups — injectable so a render is reproducible. */
		now?: Date;
	} = $props();

	const dayLabel = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short' });
	const dayYearLabel = new Intl.DateTimeFormat('en-GB', {
		day: 'numeric',
		month: 'short',
		year: 'numeric'
	});

	function parsed(d: ClientDocument): Date | null {
		if (!d.isoDate) return null;
		const t = new Date(d.isoDate);
		return Number.isNaN(t.getTime()) ? null : t;
	}

	type Run = { key: string; label: string | null; withYear: boolean; items: ClientDocument[] };

	/** Which group a date files under, relative to `now`. */
	function groupOf(at: Date): { key: string; label: string; withYear: boolean } {
		const months = (now.getFullYear() - at.getFullYear()) * 12 + (now.getMonth() - at.getMonth());
		if (months <= 0) return { key: 'this-month', label: 'This month', withYear: false };
		if (months === 1) return { key: 'last-month', label: 'Last month', withYear: false };
		if (at.getFullYear() === now.getFullYear())
			return { key: 'this-year', label: `Earlier in ${now.getFullYear()}`, withYear: false };
		return { key: `y-${at.getFullYear()}`, label: String(at.getFullYear()), withYear: true };
	}

	const runs = $derived.by((): Run[] => {
		const dated = documents
			.map((doc) => ({ doc, at: parsed(doc) }))
			.filter((x): x is { doc: ClientDocument; at: Date } => x.at !== null)
			.sort((a, b) => b.at.getTime() - a.at.getTime());
		const undated = documents.filter((doc) => parsed(doc) === null);
		const out: Run[] = [];
		for (const { doc, at } of dated) {
			const g = groupOf(at);
			const last = out[out.length - 1];
			if (last && last.key === g.key) last.items.push(doc);
			else out.push({ ...g, items: [doc] });
		}
		if (undated.length > 0)
			out.push({ key: 'undated', label: null, withYear: false, items: undated });
		return out;
	});

	/** "15 Jul" when the ISO date is known ("15 Jul 2025" once the group spans years); the
	 * loader's display date otherwise. */
	function dayOf(doc: ClientDocument, withYear: boolean): string {
		const at = parsed(doc);
		if (!at) return doc.date;
		return (withYear ? dayYearLabel : dayLabel).format(at);
	}
</script>

{#snippet row(doc: ClientDocument, withYear: boolean)}
	{@const Icon = documentIcon(doc.category)}
	<a
		href="/documents/{doc.id}"
		class="hover:bg-accent/40 flex items-center gap-3 px-4 py-3.5 transition-colors first:rounded-t-xl last:rounded-b-xl"
	>
		<span
			class={cn(
				'relative flex size-10 shrink-0 items-center justify-center rounded-lg',
				doc.unread ? 'bg-brand-subtle text-brand-strong' : 'bg-muted text-muted-foreground'
			)}
		>
			<Icon class="size-5" />
			{#if doc.unread}
				<span
					class="ring-background absolute -top-0.5 -right-0.5 size-2.5 rounded-full bg-[var(--brand)] ring-2"
					aria-hidden="true"
				></span>
			{/if}
		</span>
		<div class="min-w-0 flex-1">
			<p class={cn('line-clamp-2 leading-snug', doc.unread ? 'font-semibold' : 'font-medium')}>
				{doc.name}
				{#if doc.unread}<span class="sr-only">(new)</span>{/if}
			</p>
			<p class="text-muted-foreground mt-0.5 text-xs">{doc.category} · {dayOf(doc, withYear)}</p>
		</div>
		<ChevronRightIcon class="text-muted-foreground size-4 shrink-0" />
	</a>
{/snippet}

<div class="space-y-6 pt-1" data-documents-timeline>
	{#each runs as run (run.key)}
		<section class="space-y-2">
			{#if run.label}
				<h2 class="text-muted-foreground px-1 text-xs font-semibold tracking-[0.16em] uppercase">
					{run.label}
				</h2>
			{/if}
			<div class="glass-panel divide-y rounded-xl">
				{#each run.items as doc (doc.id)}
					{@render row(doc, run.withYear)}
				{/each}
			</div>
		</section>
	{/each}
</div>
