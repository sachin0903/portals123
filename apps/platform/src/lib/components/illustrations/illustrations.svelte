<script lang="ts">
	// Illustrations filed for a client, on the same listing frame as the rest of the book:
	// a glass card, the shared toolbar, and the shared table. A new one is chosen from that
	// toolbar, which asks which product it is for before the form opens.
	import { deserialize } from '$app/forms';
	import { invalidateAll } from '$app/navigation';
	import {
		Badge,
		Button,
		REFRESH_WHILE,
		RefreshWhile,
		Card,
		CardContent,
		ConfirmDialog,
		DataTable,
		DropdownMenu,
		EmptyState,
		formatDate,
		formatDateTime,
		LoadErrorBanner,
		Money,
		StatusBadge,
		toast,
		type DataTableColumn,
		type DataTableSort,
		type StatusTone
	} from '@graphene/ui';
	import { ListingToolbar, humanise, search } from '@graphene/ui/book/listing';
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import FileTextIcon from '@lucide/svelte/icons/file-text';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import ScrollTextIcon from '@lucide/svelte/icons/scroll-text';
	import { postShare } from './share-action';

	export interface IllustrationDoc {
		uid: string;
		document_uid?: string | null;
		name: string | null;
		created_at: string | null;
		product_type?: string | null;
		status?: string | null;
		/** The client can already read the PDF. */
		shared_with_client?: boolean;
		inputs?: {
			product_type?: string | null;
			quotation_type?: string | null;
			existing_fund_value?: number | null;
			term_years?: number | null;
			account_id?: number | null;
			output_reference?: string | null;
			model?: { id?: number; name?: string | null } | null;
			/** True when the filed PDF is a Graphene stand-in, not a quotation from the engine. */
			placeholder?: boolean | null;
			placeholder_reason?: string | null;
		} | null;
	}

	const TYPES = [
		{ id: 'SIPP', label: 'SIPP' },
		{ id: 'GIA', label: 'GIA' },
		{ id: 'ISA', label: 'ISA' }
	] as const;

	let {
		contactUid,
		illustrations = [],
		canIllustrate = false,
		canShare = false,
		clientName = 'this client',
		loadError = false,
		onretry
	}: {
		/** The client's uid — every href here is into this app's own `/clients/[uid]` routes. */
		contactUid: string;
		illustrations?: IllustrationDoc[];
		/** `illustrations:write`. Gates the New illustration menu; the listing itself stays readable. */
		canIllustrate?: boolean;
		/** `documents:send`. Gates Share — putting the PDF in the client portal. */
		canShare?: boolean;
		/** Named in the share confirmation. */
		clientName?: string;
		loadError?: boolean;
		onretry?: () => void;
	} = $props();

	let shareOpen = $state(false);
	let shareUid = $state<string | null>(null);

	function askShare(uid: string): void {
		shareUid = uid;
		shareOpen = true;
	}

	async function confirmShare(): Promise<void> {
		const uid = shareUid;
		if (!uid) return;
		const res = await postShare(uid);
		const result = deserialize(await res.text());
		if (result.type !== 'success') {
			const message =
				result.type === 'failure'
					? String(result.data?.error ?? 'Could not share the illustration.')
					: 'Could not share the illustration.';
			toast.error(message);
			throw new Error(message);
		}
		toast.success('Shared with the client');
		shareUid = null;
		await invalidateAll();
	}

	let sort = $state<DataTableSort | null>({ key: 'created', dir: 'desc' });
	let query = $state('');

	const wrapperOf = (d: IllustrationDoc) => d.product_type ?? d.inputs?.product_type ?? null;
	const newHref = (type: string) => `/clients/${contactUid}/illustrations/new?type=${type}`;

	const statusOf = (d: IllustrationDoc): { label: string; tone: StatusTone } => {
		if (d.status === 'generating') return { label: 'Generating', tone: 'warning' };
		if (d.status === 'failed') return { label: 'Failed', tone: 'danger' };
		if (d.status === 'placeholder' || d.inputs?.placeholder)
			return { label: 'Indicative', tone: 'warning' };
		return { label: 'Ready', tone: 'success' };
	};

	const rows = $derived(
		illustrations.filter((d) =>
			search(
				query,
				d.name,
				wrapperOf(d),
				d.inputs?.quotation_type,
				humanise(d.inputs?.quotation_type),
				d.inputs?.output_reference,
				d.inputs?.model?.name,
				statusOf(d).label
			)
		)
	);

	const columns: DataTableColumn<IllustrationDoc>[] = [
		{
			key: 'created',
			header: 'Created',
			sortable: true,
			defaultDir: 'desc',
			sortValue: (d) => (d.created_at ? new Date(d.created_at) : null),
			headerClass: 'w-[18%]'
		},
		{
			key: 'product',
			header: 'Type',
			sortable: true,
			sortValue: (d) => wrapperOf(d),
			headerClass: 'w-[16%]'
		},
		{
			key: 'fund',
			header: 'Fund illustrated',
			sortable: true,
			align: 'right',
			defaultDir: 'desc',
			sortValue: (d) => d.inputs?.existing_fund_value,
			headerClass: 'w-[18%]'
		},
		{
			key: 'reference',
			header: 'Reference',
			sortable: true,
			hideBelowWidth: 'lg',
			sortValue: (d) => d.inputs?.output_reference,
			headerClass: 'w-[20%]'
		},
		{
			key: 'status',
			header: 'Status',
			sortable: true,
			sortValue: (d) => statusOf(d).label,
			headerClass: 'w-[14%]'
		},
		{ key: 'actions', header: '', align: 'right', headerClass: 'w-[14%]' }
	];

	const generating = $derived(illustrations.some((d) => d.status === 'generating'));
</script>

<RefreshWhile
	active={generating && !!onretry}
	interval={REFRESH_WHILE.inFlightList}
	onpoll={() => onretry?.()}
/>

{#snippet newIllustration()}
	<DropdownMenu.Root>
		<DropdownMenu.Trigger>
			{#snippet child({ props })}
				<Button {...props}>
					<PlusIcon class="size-4" /> New illustration
					<ChevronDownIcon class="size-3.5 opacity-70" />
				</Button>
			{/snippet}
		</DropdownMenu.Trigger>
		<DropdownMenu.Content align="end" class="min-w-40">
			{#each TYPES as type (type.id)}
				<DropdownMenu.Item>
					{#snippet child({ props })}
						<a {...props} href={newHref(type.id)}>{type.label}</a>
					{/snippet}
				</DropdownMenu.Item>
			{/each}
		</DropdownMenu.Content>
	</DropdownMenu.Root>
{/snippet}

<div
	class="-m-4 flex min-w-0 flex-col gap-5 p-4 md:-m-6 md:h-[calc(100svh-var(--topbar-h,4rem))] md:overflow-hidden md:p-6"
>
	{#if loadError}
		<LoadErrorBanner
			message="This client’s previous illustrations couldn’t be loaded. You can still produce a new one."
			onRetry={onretry}
		/>
	{/if}
	{#if illustrations.length === 0 && !loadError}
		<EmptyState
			icon={ScrollTextIcon}
			title="No illustrations yet"
			description="A projection filed against this client."
			action={canIllustrate ? newIllustration : undefined}
		/>
	{:else}
		<Card glass class="flex min-w-0 flex-col md:min-h-0 md:flex-1">
			<CardContent class="flex min-w-0 flex-col gap-4 md:min-h-0 md:flex-1 md:overflow-hidden">
				<ListingToolbar
					class="shrink-0"
					bind:query
					placeholder="Search illustrations…"
					dirty={query.trim() !== ''}
					onclear={() => (query = '')}
					actions={canIllustrate ? newIllustration : undefined}
				/>
				<DataTable
					{columns}
					{rows}
					bind:sort
					scroll
					getKey={(d) => d.uid}
					fixed
					label="Illustrations"
					empty={loadError
						? 'Illustrations couldn’t be loaded.'
						: 'No illustrations match your search.'}
				>
					{#snippet cell(d, column)}
						{#if column.key === 'created'}
							<span class="whitespace-nowrap tabular-nums" title={formatDateTime(d.created_at)}
								>{formatDate(d.created_at)}</span
							>
						{:else if column.key === 'product'}
							{#if wrapperOf(d)}
								<Badge variant="outline" class="font-normal">{wrapperOf(d)}</Badge>
							{:else}
								<span class="text-muted-foreground">—</span>
							{/if}
						{:else if column.key === 'fund'}
							<Money amount={d.inputs?.existing_fund_value} />
						{:else if column.key === 'reference'}
							<span class="text-muted-foreground font-mono text-xs"
								>{d.inputs?.output_reference ?? '—'}</span
							>
						{:else if column.key === 'status'}
							{@const status = statusOf(d)}
							<StatusBadge tone={status.tone}>{status.label}</StatusBadge>
						{:else if column.key === 'actions'}
							<div class="flex items-center justify-end gap-1">
								{#if d.shared_with_client}
									<span class="text-muted-foreground px-2 text-xs">Shared</span>
								{:else if canShare && d.document_uid && d.status === 'ready'}
									<Button type="button" variant="ghost" size="sm" onclick={() => askShare(d.uid)}>
										Share
									</Button>
								{/if}
								{#if d.document_uid && d.status !== 'generating' && d.status !== 'failed'}
									<Button
										variant="ghost"
										size="sm"
										href="/clients/{contactUid}/documents/{d.document_uid}"
									>
										<FileTextIcon class="mr-1.5 size-3.5" /> Open
									</Button>
								{/if}
							</div>
						{/if}
					{/snippet}
				</DataTable>
			</CardContent>
		</Card>
	{/if}
</div>

{#if canShare}
	<ConfirmDialog
		bind:open={shareOpen}
		title={`Share this illustration with ${clientName}?`}
		description="It will appear in their portal."
		confirmLabel="Share"
		pendingLabel="Sharing…"
		onconfirm={confirmShare}
	/>
{/if}
