<script lang="ts">
	import { Alert, AlertDescription, Button, Card, CardContent } from '@graphene/ui';
	import PlayIcon from '@lucide/svelte/icons/play';
	import { QueryError, type QueryResult } from '../rows';
	import { MAT_GLYPH } from '../status';
	import type { PlatformStore } from '../store.svelte';
	import ResultGrid from './ResultGrid.svelte';

	let { store }: { store: PlatformStore } = $props();

	// Live datasets query the promoted relation in the real warehouse; the concept
	// fixtures query by model name against the mock engine.
	const relationOf = (m: { name: string; relation?: string }) =>
		store.data.live ? (m.relation ?? m.name) : m.name;

	// Deliberate initial-value capture: seeds the console once; the user's text owns it after.
	// svelte-ignore state_referenced_locally
	const seed = store.selectedModel ? relationOf(store.selectedModel) : '';
	let sql = $state(seed ? `select * from ${seed} limit 20` : '');
	let editor = $state<HTMLTextAreaElement | null>(null);
	let result = $state<QueryResult | null>(null);
	let error = $state<string | null>(null);
	let history = $state<string[]>([]);
	let running = $state(false);
	let relationQuery = $state('');
	const relations = $derived(
		store.data.models.filter((m) =>
			m.name.toLowerCase().includes(relationQuery.trim().toLowerCase())
		)
	);

	function insertRelation(model: { name: string; relation?: string }) {
		const ref = relationOf(model);
		const el = editor;
		if (!el || sql.trim() === '') {
			sql = `select * from ${ref} limit 20`;
			return;
		}
		const start = el.selectionStart ?? sql.length;
		const end = el.selectionEnd ?? sql.length;
		sql = sql.slice(0, start) + ref + sql.slice(end);
		const pos = start + ref.length;
		queueMicrotask(() => {
			el.focus();
			el.setSelectionRange(pos, pos);
		});
	}

	async function runLive(query: string) {
		running = true;
		try {
			// `/query/run`, not `/query`: the latter is now the page this component renders on, and a
			// route can't be both a page and an endpoint.
			const res = await fetch(`/platforms/${store.data.meta.provider}/query/run`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ sql: query })
			});
			const body = await res.json();
			if (!res.ok) throw new QueryError(String(body?.detail ?? `query failed (${res.status})`));
			const relation = query.match(/\bfrom\s+([\w.]+)/i)?.[1] ?? '';
			result = { ...body, relation } as QueryResult;
			error = null;
		} catch (e) {
			result = null;
			error = e instanceof QueryError ? e.message : 'Query failed.';
		} finally {
			running = false;
		}
	}

	function run() {
		const query = sql.trim();
		if (!query || running) return;
		history = [query, ...history.filter((h) => h !== query)].slice(0, 8);
		// Always the warehouse. There used to be a second path here — a toy SQL engine in `rows.ts`
		// that understood `select <cols|*> from <model> [limit n]` over the fixture datasets — and
		// the only thing distinguishing the two on screen was the word "concept" in the footer.
		void runLive(query);
	}

	function onKeydown(e: KeyboardEvent) {
		if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
			e.preventDefault();
			run();
		}
	}
</script>

<div class="grid gap-4 xl:grid-cols-[minmax(0,1fr)_280px]">
	<div class="min-w-0 space-y-3">
		<div class="space-y-2">
			<textarea
				bind:this={editor}
				bind:value={sql}
				onkeydown={onKeydown}
				rows="6"
				spellcheck="false"
				placeholder="select * from …"
				class="bg-card focus-visible:ring-ring/50 w-full resize-y rounded-xl border p-3 font-mono text-sm outline-none focus-visible:ring-[3px]"
			></textarea>
			<div class="flex items-center gap-3">
				<Button size="sm" onclick={run} disabled={running}>
					<PlayIcon class="size-4" />
					{running ? 'Running…' : 'Run'}
				</Button>
				<span class="text-muted-foreground text-xs"> ⌘↵ to run · read-only data </span>
				{#if result}
					<span class="text-muted-foreground ml-auto font-mono text-xs">
						{result.rowCount} rows · {result.elapsedMs}ms · {result.relation}
					</span>
				{/if}
			</div>
		</div>

		{#if error}
			<Alert variant="destructive">
				<AlertDescription class="font-mono text-xs break-all">{error}</AlertDescription>
			</Alert>
		{:else if result}
			<ResultGrid columns={result.columns} rows={result.rows} />
		{:else}
			<div class="text-muted-foreground rounded-xl border py-16 text-center text-sm">
				Run a read-only query to see results.
			</div>
		{/if}
	</div>

	<div class="space-y-4">
		<Card>
			<CardContent class="space-y-1.5">
				<div class="text-muted-foreground text-[11px] font-medium">Relations</div>
				<input
					bind:value={relationQuery}
					placeholder="Filter…"
					aria-label="Filter relations"
					class="bg-card focus-visible:ring-ring/50 h-7 w-full rounded-md border px-2 font-mono text-xs outline-none focus-visible:ring-[3px]"
				/>
				<div class="max-h-64 space-y-0.5 overflow-y-auto">
					{#each relations as model (model.name)}
						<button
							type="button"
							class="hover:bg-accent/60 flex w-full items-center gap-2 rounded px-1.5 py-0.5 text-left font-mono text-xs transition-colors"
							title="Insert {relationOf(model)}"
							onclick={() => insertRelation(model)}
						>
							<span class="text-muted-foreground">{MAT_GLYPH[model.mat]}</span>
							<span class="truncate">{model.name}</span>
						</button>
					{/each}
				</div>
			</CardContent>
		</Card>

		{#if history.length}
			<Card>
				<CardContent class="space-y-1.5">
					<div class="text-muted-foreground text-[11px] font-medium">History</div>
					<div class="space-y-0.5">
						{#each history as entry (entry)}
							<button
								type="button"
								class="hover:bg-accent/60 w-full truncate rounded px-1.5 py-0.5 text-left font-mono text-xs transition-colors"
								onclick={() => (sql = entry)}
							>
								{entry}
							</button>
						{/each}
					</div>
				</CardContent>
			</Card>
		{/if}
	</div>
</div>
