<script lang="ts">
	// WHERE THIS FIRM'S DATA COMES FROM — the firm's own view, read-only.
	//
	// It had none. Whether a firm's book was current was answerable only from Graphene's operations
	// console, so a firm's advisers read a book with no way to tell "this client has no
	// transactions" from "nothing has landed since Tuesday" — which is the ambiguity this whole
	// pipeline keeps producing, seen from the other side.
	//
	// What it deliberately does not show: the slice keys (the custodian's own identifiers, which
	// belong to an enablement only Graphene can set), the model graph, the run history, the SQL
	// console. What is left is "is our data current, and did any of it not make it".
	import { Badge } from '../../ui/badge/index.js';
	import {
		Card,
		CardContent,
		CardDescription,
		CardHeader,
		CardTitle
	} from '../../ui/card/index.js';
	import { DATA_SOURCE_LABEL, dataSourceState, type DataSource } from './types.js';

	let { sources, now = new Date() }: { sources: DataSource[]; now?: Date } = $props();

	const TONE: Record<string, 'default' | 'secondary' | 'destructive' | 'outline'> = {
		current: 'secondary',
		stale: 'outline',
		partial: 'destructive',
		never: 'destructive'
	};

	const when = (iso: string | null): string =>
		iso ? new Date(iso).toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' }) : '—';
</script>

{#if sources.length === 0}
	<Card>
		<CardContent>
			<p class="text-muted-foreground py-10 text-center text-sm">
				No custodians are connected to this firm yet. Your Graphene contact sets these up.
			</p>
		</CardContent>
	</Card>
{:else}
	<div class="grid gap-4 md:grid-cols-2">
		{#each sources as source (source.provider)}
			{@const state = dataSourceState(source, now)}
			<Card>
				<CardHeader class="gap-1">
					<div class="flex items-start justify-between gap-2">
						<CardTitle class="text-lg">{source.name}</CardTitle>
						<Badge variant={TONE[state]} class="font-normal">{DATA_SOURCE_LABEL[state]}</Badge>
					</div>
					<CardDescription>{source.description}</CardDescription>
				</CardHeader>
				<CardContent class="space-y-2 text-xs">
					<div class="flex justify-between gap-4">
						<span class="text-muted-foreground">Last updated</span>
						<span>{when(source.last_merged_at)}</span>
					</div>
					{#if source.last_run}
						<div class="flex justify-between gap-4">
							<span class="text-muted-foreground">Records added or changed</span>
							<span class="tabular-nums">
								{(source.last_run.created + source.last_run.updated).toLocaleString('en-GB')}
							</span>
						</div>
						{#if source.last_run.rejected}
							<!-- A refused row is client data that did NOT reach the book, and the firm is
							     the party who notices its absence. Named without the reason, which is a
							     mapping detail in the custodian's own vocabulary — the actionable part is
							     that the book is short and Graphene should be asked why. -->
							<div class="flex justify-between gap-4">
								<span class="text-muted-foreground">Records not loaded</span>
								<span class="text-destructive tabular-nums">
									{source.last_run.rejected.toLocaleString('en-GB')}
								</span>
							</div>
						{/if}
					{/if}
					{#if state === 'never'}
						<p class="text-muted-foreground text-xs">
							This connection is switched on but nothing has arrived yet. If that is unexpected,
							contact Graphene support.
						</p>
					{:else if state === 'partial'}
						<p class="text-muted-foreground text-xs">
							Some of this custodian's data did not load on the last update, so parts of your book
							may be out of date.
						</p>
					{/if}
				</CardContent>
			</Card>
		{/each}
	</div>
{/if}
