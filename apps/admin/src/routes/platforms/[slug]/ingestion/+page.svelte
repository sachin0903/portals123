<script lang="ts">
	// INGESTION — how this custodian's data arrives, and whether it did.
	//
	// Transport-shaped, because the three custodians do not ingest the same way and pretending they
	// do is what the page it replaces got wrong: it rendered an unconditional notice about SECCL's
	// legacy per-kind webhook API on every provider — on SEI's page, whose entire ingestion is a
	// nightly file drop, and on Morgan Lloyd's, which is poll-only. SEI then showed a nav item
	// labelled "Streams" over "No streams configured for this platform", which is true and useless.
	//
	// Only SEI has a per-file ledger, and that is a FACT ABOUT THE TRANSPORT rather than a gap: its
	// PGP-encrypted SFTP drop is the head of its own DAG, so every file's arrival, trailer check and
	// decryption is a row. The other two say so, rather than showing an empty table that reads as
	// "nothing arrived last night".
	import {
		Alert,
		AlertDescription,
		Badge,
		Card,
		CardContent,
		CardHeader,
		CardTitle,
		DataTable,
		type DataTableColumn,
		type DataTableSort,
		humanise
	} from '@graphene/ui';
	import EventFeed from '$lib/platforms/components/EventFeed.svelte';
	import { usePlatformStore } from '$lib/platforms/context';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	const holder = usePlatformStore();
	const store = $derived(holder.store);
	const streams = $derived(store.data.streams);

	// Every heading sorts. The derived ones say so: a drop's `files` is the length of its batch,
	// its `state` is a badge computed from `failed`, and a stream's DLQ count is what an operator
	// is actually hunting for — so each orders by the number behind the badge rather than by a
	// field of the column's name, which does not exist.
	const dropColumns: DataTableColumn<(typeof drops)[number]>[] = [
		{ key: 'date', header: 'Drop', sortable: true, sortDefault: true, defaultDir: 'desc' },
		{
			key: 'files',
			header: 'Files',
			sortable: true,
			align: 'right',
			defaultDir: 'desc',
			sortValue: (d) => d.batch.length
		},
		{ key: 'rows', header: 'Rows', sortable: true, align: 'right', defaultDir: 'desc' },
		{
			key: 'state',
			header: 'State',
			sortable: true,
			defaultDir: 'desc',
			sortValue: (d) => d.failed ?? 0
		}
	];
	let dropSort = $state<DataTableSort | null>(null);

	const failedColumns: DataTableColumn<(typeof files)[number]>[] = [
		{ key: 'filename', header: 'File', sortable: true, sortDefault: true },
		{ key: 'report', header: 'Report', sortable: true },
		{ key: 'trailer_rows', header: 'Expected', sortable: true, align: 'right', defaultDir: 'desc' },
		{ key: 'rows_read', header: 'Read', sortable: true, align: 'right', defaultDir: 'desc' },
		{ key: 'error', header: 'Error', sortable: true }
	];
	let failedSort = $state<DataTableSort | null>(null);

	const streamColumns: DataTableColumn<(typeof streams)[number]>[] = [
		{ key: 'name', header: 'Stream', sortable: true, sortDefault: true },
		{ key: 'status', header: 'Status', sortable: true },
		{ key: 'target', header: 'Target', sortable: true },
		{ key: 'offset', header: 'Offset', sortable: true, align: 'right', defaultDir: 'desc' },
		{ key: 'lag', header: 'Lag', sortable: true, align: 'right', defaultDir: 'desc' },
		{ key: 'dlq', header: 'DLQ', sortable: true, align: 'right', defaultDir: 'desc' }
	];
	let streamSort = $state<DataTableSort | null>(null);
	const files = $derived(data.ingestion?.files ?? []);

	/** One line per drop, newest first — a night is the unit an operator asks about. */
	const drops = $derived.by(() => {
		const byDate = new Map<string, typeof files>();
		for (const file of files) {
			const key = file.drop_date ?? 'unknown';
			byDate.set(key, [...(byDate.get(key) ?? []), file]);
		}
		return [...byDate.entries()].map(([date, batch]) => ({
			date,
			batch,
			failed: batch.filter((f) => f.status === 'failed').length,
			rows: batch.reduce((total, f) => total + (f.rows_read ?? 0), 0)
		}));
	});

	const fmt = (n: number | null) => (n === null ? '—' : n.toLocaleString('en-GB'));
</script>

<div class="space-y-5">
	{#if data.ingestionError}
		<Alert variant="destructive">
			<AlertDescription>
				Could not read the ingestion ledger ({data.ingestionError}). This is not the same as nothing
				having arrived.
			</AlertDescription>
		</Alert>
	{/if}

	{#if data.ingestion?.note}
		<p class="text-muted-foreground text-sm">{data.ingestion.note}</p>
	{/if}

	{#if data.ingestion?.kind === 'sftp'}
		<Card class="gap-2">
			<CardHeader>
				<CardTitle class="text-sm">Recent drops</CardTitle>
			</CardHeader>
			<CardContent>
				{#if !data.ingestion.available}
					<p class="text-muted-foreground text-sm">
						No file delivery has been processed yet.
						{#if data.ingestion.reason}
							<span class="font-mono text-xs">({data.ingestion.reason})</span>
						{/if}
					</p>
				{:else if drops.length === 0}
					<p class="text-muted-foreground text-sm">
						No files were found in the latest delivery. Check this with the provider.
					</p>
				{:else}
					<DataTable
						columns={dropColumns}
						rows={drops}
						bind:sort={dropSort}
						getKey={(drop) => drop.date}
						label="Drops"
					>
						{#snippet cell(drop, column)}
							{#if column.key === 'date'}
								<span class="font-mono text-xs">{drop.date}</span>
							{:else if column.key === 'files'}
								<span class="tabular-nums">{drop.batch.length}</span>
							{:else if column.key === 'rows'}
								<span class="tabular-nums">{fmt(drop.rows)}</span>
							{:else if column.key === 'state'}
								{#if drop.failed}
									<Badge variant="destructive" class="font-normal">{drop.failed} failed</Badge>
								{:else}
									<Badge variant="secondary" class="font-normal">complete</Badge>
								{/if}
							{/if}
						{/snippet}
					</DataTable>
				{/if}
			</CardContent>
		</Card>

		<!-- A FAILED FILE, NAMED. The trailer count is SEI's own statement of how many records it
		     sent; a mismatch means the drop was truncated, and the file fails rather than landing
		     short — which would otherwise read as a quiet day at that firm. -->
		{#if files.some((f) => f.status === 'failed')}
			<Card class="gap-2">
				<CardHeader>
					<CardTitle class="text-sm">
						Failed files
						<Badge variant="destructive" class="ml-2">
							{files.filter((f) => f.status === 'failed').length}
						</Badge>
					</CardTitle>
				</CardHeader>
				<CardContent>
					<DataTable
						columns={failedColumns}
						rows={files.filter((f) => f.status === 'failed')}
						bind:sort={failedSort}
						getKey={(file) => file.filename}
						label="Failed files"
					>
						{#snippet cell(file, column)}
							{#if column.key === 'filename'}
								<span class="font-mono text-xs">{file.filename}</span>
							{:else if column.key === 'report'}
								<span class="text-xs">{file.report}</span>
							{:else if column.key === 'trailer_rows'}
								<span class="text-xs tabular-nums">{fmt(file.trailer_rows)}</span>
							{:else if column.key === 'rows_read'}
								<span class="text-xs tabular-nums">{fmt(file.rows_read)}</span>
							{:else if column.key === 'error'}
								<span class="text-destructive block max-w-96 truncate text-xs" title={file.error}>
									{file.error}
								</span>
							{/if}
						{/snippet}
					</DataTable>
				</CardContent>
			</Card>
		{/if}
	{:else if data.ingestion?.kind === 'webhooks'}
		<!-- READ-ONLY, and the reason is Seccl-specific, so it belongs on Seccl's page alone.
		     A switch here is a real provider mutation: our gateway is wired to Seccl's legacy
		     per-kind `PATCH /webhook/config/{kind}/{firmId}`, which has NO documented disable, while
		     the v1 API has full CRUD. Flipping it today would be a no-op or would change live
		     delivery for a firm. Showing the real state read-only is honest; a switch that
		     half-works is not. -->
		<Alert>
			<AlertDescription>
				Seccl streams are read-only here. Manage stream settings directly in Seccl.
			</AlertDescription>
		</Alert>
		<Card>
			<CardContent>
				{#if streams.length === 0}
					<p class="text-muted-foreground py-10 text-center text-sm">
						No streams configured for this platform.
					</p>
				{:else}
					<DataTable
						columns={streamColumns}
						rows={streams}
						bind:sort={streamSort}
						getKey={(stream) => stream.name}
						label="Streams"
					>
						{#snippet cell(stream, column)}
							{#if column.key === 'name'}
								<span class="font-mono text-xs font-medium">{stream.name}</span>
							{:else if column.key === 'status'}
								<Badge
									variant={stream.status === 'live' ? 'secondary' : 'outline'}
									class="font-normal"
								>
									{humanise(stream.status)}
								</Badge>
							{:else if column.key === 'target'}
								<span class="text-muted-foreground font-mono text-xs">{stream.target}</span>
							{:else if column.key === 'offset'}
								<span class="font-mono text-xs tabular-nums">{stream.offset}</span>
							{:else if column.key === 'lag'}
								<span class="text-muted-foreground text-xs tabular-nums">{stream.lag}</span>
							{:else if column.key === 'dlq'}
								{#if stream.dlq > 0}
									<span class="text-destructive text-xs tabular-nums">{stream.dlq}</span>
								{:else}
									<span class="text-muted-foreground text-xs tabular-nums">0</span>
								{/if}
							{/if}
						{/snippet}
					</DataTable>
				{/if}
			</CardContent>
		</Card>
	{/if}

	<Card>
		<CardContent><EventFeed script={store.data.feedScript} /></CardContent>
	</Card>
</div>
