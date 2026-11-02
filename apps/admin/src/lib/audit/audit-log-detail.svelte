<script lang="ts">
	// The detail pane of the audit-log master-detail — the Events treatment applied to a DML
	// record-change row: the CHANGES first (an UPDATE's per-column old → new, an INSERT/DELETE's row
	// values), then a Metadata definition list, then the raw `detail` JSON via the shared SAFE
	// highlighter. The `detail` shape comes from the audit.record trigger (0001): UPDATE →
	// `{id, changed:{col:{old,new}}}`, INSERT/DELETE → `{id, row:{…}}`.
	import { highlightJson, previewValue, shortenForJson } from '@graphene/ui';
	import type { AuditEvent } from './types';

	let { event }: { event: AuditEvent } = $props();

	function when(iso: string): string {
		const d = new Date(iso);
		return Number.isNaN(d.getTime()) ? iso : d.toLocaleString('en-GB');
	}

	// The op is the last dotted segment of the action code (`schema.table.op`).
	const op = $derived(event.action.split('.').at(-1) ?? '');
	const opVerb = $derived(
		op === 'insert' ? 'created' : op === 'delete' ? 'deleted' : op === 'update' ? 'changed' : op
	);

	// A changed value for the compact "Changes" line: nullish stays null (so the "was X →" arrow logic
	// still reads a cleared value), otherwise the shared previewValue caps it — a branding `theme`
	// jsonb or a GrapesJS email HTML body is summarised ("{…} 8 fields (4 KB)") instead of dumped as a
	// wall. The full value is still available in the raw "Details" block below.
	function toText(value: unknown): string | null {
		if (value === null || value === undefined) return null;
		return previewValue(value);
	}

	type ChangeRow = { col: string; from: string | null; to: string | null };
	const changed = $derived.by<ChangeRow[]>(() => {
		const c = (
			event.detail as { changed?: Record<string, { old?: unknown; new?: unknown }> } | null
		)?.changed;
		if (!c || typeof c !== 'object') return [];
		return Object.entries(c).map(([col, v]) => ({ col, from: toText(v?.old), to: toText(v?.new) }));
	});
	const rowValues = $derived.by<[string, string | null][]>(() => {
		const r = (event.detail as { row?: Record<string, unknown> } | null)?.row;
		if (!r || typeof r !== 'object') return [];
		return Object.entries(r).map(([k, v]) => [k, toText(v)] as [string, string | null]);
	});

	// The raw before/after evidence, with long strings + data: URIs shortened for display so a
	// GrapesJS body or a data-URI logo doesn't fill the pane (the trail itself keeps the full value).
	const detailLines = $derived(
		event.detail ? highlightJson(JSON.stringify(shortenForJson(event.detail), null, 2)) : []
	);
</script>

<div class="space-y-3 p-5">
	<!-- The change itself, in a sentence: who did what to which record. -->
	<div class="flex flex-wrap items-center gap-2 text-sm">
		<span class="font-medium">{event.actor_name ?? event.actor_email ?? 'System'}</span>
		<span class="text-muted-foreground">{opVerb}</span>
		<code class="font-mono text-xs">{event.entity_type ?? event.action}</code>
		{#if event.entity_id != null}
			<code class="text-muted-foreground font-mono text-xs">#{event.entity_id}</code>
		{/if}
	</div>

	<!-- CHANGES first: an UPDATE's per-column old → new, else a created/removed row's values. -->
	{#if changed.length}
		<div class="space-y-1.5 text-xs">
			<h2 class="text-muted-foreground text-xs font-semibold">Changes</h2>
			{#each changed as c (c.col)}
				<div class="flex flex-wrap items-center gap-x-2 gap-y-0.5">
					<span class="text-muted-foreground">{c.col}</span>
					{#if c.from !== null}
						<span class="font-medium break-all">{c.from}</span>
						<span class="text-muted-foreground" aria-hidden="true">→</span>
					{/if}
					<span class="font-medium break-all">{c.to ?? '∅'}</span>
				</div>
			{/each}
		</div>
	{:else if rowValues.length}
		<div class="space-y-1.5 text-xs">
			<h2 class="text-muted-foreground text-xs font-semibold">
				{op === 'delete' ? 'Removed row' : 'New row'}
			</h2>
			<dl class="space-y-1">
				{#each rowValues as [key, value] (key)}
					<div class="flex gap-4">
						<dt class="text-muted-foreground w-40 shrink-0 truncate">{key}</dt>
						<dd class="font-medium break-all">{value ?? '∅'}</dd>
					</div>
				{/each}
			</dl>
		</div>
	{/if}

	<div class="border-border my-4 border-t"></div>

	<!-- Metadata: the machine facts, definition-list style (the Events-detail treatment). -->
	<h2 class="text-sm font-semibold">Metadata</h2>
	<dl class="mt-3 space-y-2 text-xs">
		<div class="flex gap-4">
			<dt class="text-muted-foreground w-24 shrink-0">When</dt>
			<dd title={event.created_at}>{when(event.created_at)}</dd>
		</div>
		<div class="flex gap-4">
			<dt class="text-muted-foreground w-24 shrink-0">Action</dt>
			<dd><code class="font-mono">{event.action}</code></dd>
		</div>
		{#if event.entity_type}
			<div class="flex gap-4">
				<dt class="text-muted-foreground w-24 shrink-0">Target</dt>
				<dd>
					<code class="font-mono">{event.entity_type}</code>
					{#if event.entity_id != null}
						<code class="text-muted-foreground font-mono">#{event.entity_id}</code>
					{/if}
				</dd>
			</div>
		{/if}
		{#if event.tenant_name}
			<div class="flex gap-4">
				<dt class="text-muted-foreground w-24 shrink-0">Firm</dt>
				<dd>{event.tenant_name}</dd>
			</div>
		{/if}
		<div class="flex gap-4">
			<dt class="text-muted-foreground w-24 shrink-0">Actor</dt>
			<dd>
				{#if event.actor_email}
					{event.actor_name ?? event.actor_email}
					<code class="text-muted-foreground font-mono">{event.actor_email}</code>
				{:else}
					<span class="text-muted-foreground">System (no principal)</span>
				{/if}
			</dd>
		</div>
	</dl>

	<!-- The raw before/after evidence, always shown (the Events-detail "Details" treatment). -->
	{#if detailLines.length}
		<div class="border-border my-4 border-t"></div>
		<h2 class="text-sm font-semibold">Details</h2>
		<div
			class="bg-muted/30 mt-3 overflow-x-auto rounded-lg border p-3 font-mono text-[11px] leading-relaxed"
		>
			{#each detailLines as segs, i (i)}
				<div class="flex">
					<span
						class="text-muted-foreground/50 w-8 shrink-0 pr-3 text-right tabular-nums select-none"
						>{i + 1}</span
					>
					<code class="whitespace-pre"
						>{#each segs as s (s)}<span class={s.cls}>{s.text}</span>{/each}</code
					>
				</div>
			{/each}
		</div>
	{/if}
</div>
