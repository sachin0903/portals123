<script lang="ts">
	// One transfer, beside the queue. Identity and the amount, the custodian's own status, the
	// rail for this route, the chain of custody, the in-specie instruments, reconciliation,
	// then the note. An absent callback hides the control, so a read-only mount stays read-only.
	//
	// The workbench mounts it only while a row is picked, so the header carries the way back out:
	// an X (and the workbench's Esc).
	import LandmarkIcon from '@lucide/svelte/icons/landmark';
	import XIcon from '@lucide/svelte/icons/x';
	import { Badge } from '../../ui/badge/index.js';
	import { StatusBadge } from '../../ui/status-badge/index.js';
	import { Button } from '../../ui/button/index.js';
	import { Card, CardContent } from '../../ui/card/index.js';
	import { Money } from '../../ui/money/index.js';
	import NoteComposer from '../notes/composer.svelte';
	import { RecordId } from '../../ui/record-id/index.js';
	import SagaRail from './saga-rail.svelte';
	import PersonAvatar from '../../data/person-avatar.svelte';
	import { LINK_RECORD } from '../../../text-shell.js';
	import { formatDate } from '../../../dates.js';
	import {
		ageLabel,
		basisLabel,
		crystallisationLabel,
		pensionTypeLabel,
		transferValue,
		type ApiTransfer,
		type TransferInstrument
	} from './api.js';
	import {
		cancelStep,
		nextStep,
		reconciliationColumns,
		statusLabel,
		statusTone,
		type TransferStepId
	} from './stages.js';

	const PARTY: Record<string, string> = {
		'morgan-lloyd': 'Morgan Lloyd',
		seccl: 'Seccl',
		sei: 'SEI'
	};

	let {
		transfer = null,
		busy = false,
		links,
		onstep,
		onnote,
		onclose
	}: {
		transfer?: ApiTransfer | null;
		busy?: boolean;
		links?: {
			client?: (uid: string) => string;
			account?: (uid: string) => string;
		};
		/** Absent ⇒ the footer draws no buttons. A detected or finished row has no step either. */
		onstep?: (uid: string, step: TransferStepId) => void;
		/** Absent ⇒ the note is read-only. Stored on our record; not sent to Seccl or SEI. */
		onnote?: (uid: string, note: string) => void;
		/** Closes the panel. Absent ⇒ no X. */
		onclose?: () => void;
	} = $props();

	let draft = $state('');
	$effect(() => {
		draft = transfer?.note ?? '';
	});

	const step = $derived(transfer ? nextStep(transfer) : null);
	const cancel = $derived(transfer ? cancelStep(transfer) : null);
	const columns = $derived(transfer ? reconciliationColumns(transfer) : []);
	const custodianRef = $derived(transfer ? custodianReference(transfer) : null);
	const reference = $derived(
		!transfer
			? null
			: transfer.transaction_reference
				? { label: 'Transaction', value: transfer.transaction_reference }
				: { label: 'Transfer', value: transfer.uid }
	);

	/** How long it has waited, in the queue's own words ("5d ago"), for the Raised line. */
	const waited = $derived.by(() => {
		if (!transfer) return null;
		const label = ageLabel(transfer);
		return label === '—' ? null : label === 'today' ? label : `${label} ago`;
	});

	const sectionHeading = 'text-muted-foreground mb-2 text-xs font-semibold tracking-wide uppercase';

	const instruments = $derived(transfer?.basis === 'in_specie' ? (transfer.instruments ?? []) : []);

	function unitsLabel(quantity: number | null): string | null {
		if (quantity == null) return null;
		return new Intl.NumberFormat('en-GB', { maximumFractionDigits: 4 }).format(quantity);
	}

	function instrumentStatus(status: string | null): string | null {
		const folded = (status ?? '').trim().toLowerCase();
		if (!folded) return null;
		if (folded === 'completed' || folded === 'complete' || folded === 'settled') return 'Completed';
		if (folded === 'pending') return 'Pending';
		return status!.trim();
	}

	function instrumentTitle(instrument: TransferInstrument): string {
		return instrument.name || instrument.code || 'Unnamed instrument';
	}

	function party(code: string | null | undefined): string {
		if (!code) return '—';
		return PARTY[code] ?? code;
	}

	/** The custodian's own transfer reference, distinct from the transaction id above. */
	function custodianReference(row: ApiTransfer): string | null {
		const route = row.route ?? '';
		const custodian = row.custodian ?? '';
		const seccl = row.seccl_transfer_id?.trim() || null;
		const sei = row.sei_activity_id?.trim() || null;
		if (custodian === 'seccl' || route === 'seccl') return seccl;
		if (custodian === 'sei' || route === 'sei' || route === 'ml_sei') return sei;
		return seccl ?? sei;
	}
</script>

<Card glass class="flex h-full min-h-0 flex-col">
	<CardContent class="flex min-h-0 flex-1 flex-col p-0">
		{#if !transfer}
			<div class="text-muted-foreground flex flex-1 items-center justify-center p-6 text-sm">
				Select a transfer to see where it is and what happens next.
			</div>
		{:else}
			<div class="min-h-0 flex-1 space-y-5 overflow-y-auto p-4">
				<div class="space-y-2">
					<div class="flex items-center justify-between gap-3">
						{#if reference}
							<div class="min-w-0">
								<RecordId label={reference.label} value={reference.value} class="max-w-full" />
							</div>
						{/if}
						<div class="flex shrink-0 items-center gap-1.5">
							<StatusBadge tone={statusTone(transfer)} class="shrink-0">
								{statusLabel(transfer)}
							</StatusBadge>
							{#if onclose}
								<Button
									variant="ghost"
									size="icon"
									class="text-muted-foreground hover:text-foreground -my-1 -mr-1.5 size-8"
									aria-label="Close panel"
									title="Close (Esc)"
									onclick={onclose}
								>
									<XIcon class="size-4" />
								</Button>
							{/if}
						</div>
					</div>

					<div class="flex items-start justify-between gap-3">
						<p class="min-w-0 text-sm leading-snug">
							{#if links?.client && transfer.contact_uid}
								<a href={links.client(transfer.contact_uid)} class="font-medium {LINK_RECORD}">
									{transfer.client_name ?? 'Unknown client'}
								</a>
							{:else}
								<span class="font-medium">{transfer.client_name ?? 'Unknown client'}</span>
							{/if}
							<span class="text-muted-foreground"> · </span>
							{#if links?.account && transfer.account_uid && transfer.account_name}
								<a href={links.account(transfer.account_uid)} class="font-medium {LINK_RECORD}">
									{transfer.account_name}
								</a>
							{:else}
								<span class="font-medium">{transfer.account_name ?? '—'}</span>
							{/if}
							{#if transfer.product_type}
								<span class="text-muted-foreground"> · {transfer.product_type}</span>
							{/if}
						</p>
						<div class="shrink-0 text-right">
							{#if transferValue(transfer) === null}
								<p class="text-muted-foreground text-xs">Not yet advised</p>
							{:else}
								<p class="text-lg leading-none font-semibold">
									<Money amount={transferValue(transfer) ?? 0} currency={transfer.currency} />
								</p>
							{/if}
							<p class="text-muted-foreground mt-1 text-xs">{basisLabel(transfer)}</p>
						</div>
					</div>

					<div class="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
						<span class="inline-flex items-center gap-1.5">
							<LandmarkIcon class="size-3 shrink-0" />
							{party(transfer.custodian)}
						</span>
						{#if custodianRef}
							<span>
								Ref:
								<span class="text-muted-foreground font-mono">{custodianRef}</span>
							</span>
						{/if}
						{#if transfer.ceding_status}
							<Badge variant="outline" class="text-muted-foreground">{transfer.ceding_status}</Badge
							>
						{/if}
					</div>
				</div>

				<!-- What the queue's row says that the lines above do not, so opening a transfer never
				     loses a fact the table had: the adviser (a column that folds away as soon as this panel
				     opens beside the queue) and when it was raised, with how long it has waited. -->
				<dl class="divide-border divide-y text-xs">
					<div class="flex items-baseline justify-between gap-4 py-1.5">
						<dt class="text-muted-foreground">Adviser</dt>
						<dd class="min-w-0 text-right">
							{#if transfer.adviser}
								<!-- The face the queue's Adviser column gives them, so one colleague reads the
								     same here as in the table. -->
								<span class="inline-flex max-w-full items-center gap-1.5"
									><PersonAvatar name={transfer.adviser} size="xs" /><span class="truncate"
										>{transfer.adviser}</span
									></span
								>
							{:else}
								Unassigned
							{/if}
						</dd>
					</div>
					<div class="flex items-baseline justify-between gap-4 py-1.5">
						<dt class="text-muted-foreground">Raised</dt>
						<dd class="text-right tabular-nums">
							{formatDate(transfer.created_at)}{waited ? ` · ${waited}` : ''}
						</dd>
					</div>
				</dl>

				<SagaRail {transfer} />

				<div>
					<h4 class={sectionHeading}>Chain of custody</h4>
					<dl class="divide-border divide-y text-xs">
						<div class="flex items-baseline justify-between gap-4 py-1.5">
							<dt class="text-muted-foreground">Ceding scheme</dt>
							<dd class="text-right">{transfer.ceding_provider}</dd>
						</div>
						<div class="flex items-baseline justify-between gap-4 py-1.5">
							<dt class="text-muted-foreground">Policy</dt>
							<dd class="text-right">{transfer.policy_number ?? '—'}</dd>
						</div>
						<div class="flex items-baseline justify-between gap-4 py-1.5">
							<dt class="text-muted-foreground">Scheme administrator</dt>
							<dd class="text-right">
								{party(transfer.scheme_administrator)}
								{#if transfer.ml_status_label}
									<span class="text-muted-foreground block">
										{transfer.ml_status_label}{transfer.ml_status_at
											? ` · ${transfer.ml_status_at}`
											: ''}
									</span>
								{/if}
							</dd>
						</div>
						<div class="flex items-baseline justify-between gap-4 py-1.5">
							<dt class="text-muted-foreground">Custodian</dt>
							<dd class="text-right">{party(transfer.custodian)}</dd>
						</div>
						{#if transfer.crystallisation}
							<div class="flex items-baseline justify-between gap-4 py-1.5">
								<dt class="text-muted-foreground">Drawdown status</dt>
								<dd class="text-right">{crystallisationLabel(transfer.crystallisation)}</dd>
							</div>
						{/if}
						{#if transfer.pension_type != null}
							<div class="flex items-baseline justify-between gap-4 py-1.5">
								<dt class="text-muted-foreground">Pension type</dt>
								<dd class="text-right">{pensionTypeLabel(transfer.pension_type)}</dd>
							</div>
						{/if}
					</dl>
				</div>

				{#if transfer.basis === 'in_specie'}
					<div>
						<h4 class={sectionHeading}>Instruments</h4>
						{#if instruments.length === 0}
							<p class="text-muted-foreground text-xs">No instruments reported yet.</p>
						{:else}
							<ul class="divide-border divide-y">
								{#each instruments as instrument, i (`${instrument.code ?? instrument.name ?? 'instrument'}-${i}`)}
									{@const instrumentLabel = instrumentStatus(instrument.status)}
									{@const units = unitsLabel(instrument.quantity)}
									<li class="flex items-baseline justify-between gap-3 py-1.5">
										<div class="min-w-0">
											<p class="truncate text-xs">{instrumentTitle(instrument)}</p>
											{#if instrument.code && instrument.code !== instrument.name}
												<p class="text-muted-foreground font-mono text-[10.5px]">
													{instrument.code}
												</p>
											{/if}
										</div>
										<div class="flex shrink-0 items-baseline gap-2">
											{#if units}
												<span class="text-muted-foreground text-xs tabular-nums">{units}</span>
											{/if}
											{#if instrumentLabel}
												<Badge
													variant="outline"
													class={instrumentLabel === 'Completed'
														? 'text-success-strong'
														: 'text-muted-foreground'}
												>
													{instrumentLabel}
												</Badge>
											{/if}
										</div>
									</li>
								{/each}
							</ul>
						{/if}
					</div>
				{/if}

				<div>
					<h4 class={sectionHeading}>Reconciliation</h4>
					<div class="grid gap-3 {columns.length === 3 ? 'grid-cols-3' : 'grid-cols-2'}">
						{#each columns as column (column.heading)}
							<div class="min-w-0">
								<p class="text-muted-foreground text-xs">{column.heading}</p>
								<p class="text-lg leading-tight font-semibold">
									{#if column.amount === null}
										<span class="text-muted-foreground">—</span>
									{:else}
										<Money amount={column.amount} currency={transfer.currency} />
									{/if}
								</p>
								<p class="text-muted-foreground text-xs">{column.party}</p>
							</div>
						{/each}
					</div>
					{#if transfer.reconciliation.variance !== null && transfer.reconciliation.variance !== 0}
						<p class="text-destructive-strong mt-2 text-xs font-medium">
							Variance
							<Money amount={transfer.reconciliation.variance} currency={transfer.currency} />
						</p>
					{/if}
					{#if transfer.reconciliation.reason}
						<p class="text-muted-foreground mt-2 text-xs">{transfer.reconciliation.reason}</p>
					{/if}
				</div>

				<div>
					<h4 class={sectionHeading}>Notes</h4>
					{#if onnote}
						<NoteComposer
							bind:value={draft}
							placeholder="Add a note…"
							disabled={busy}
							pending={busy}
							onkeydown={(e) => {
								if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
									e.preventDefault();
									if (draft.trim() !== (transfer.note ?? '').trim())
										onnote?.(transfer.uid, draft.trim());
								}
							}}
							onsubmit={() => {
								if (draft.trim() !== (transfer.note ?? '').trim())
									onnote?.(transfer.uid, draft.trim());
							}}
						/>
					{:else if transfer.note}
						<p class="text-xs whitespace-pre-wrap break-words">{transfer.note}</p>
					{/if}
				</div>
			</div>

			{#if transfer.provider_error}
				<p class="text-destructive px-4 pt-3 text-xs">{transfer.provider_error}</p>
			{/if}
			<div
				class="border-border flex shrink-0 items-center gap-2 border-t px-4 py-3"
				data-slot="card-footer"
			>
				{#if step && onstep}
					<Button
						class="min-w-0 flex-1"
						disabled={busy}
						onclick={() => onstep?.(transfer.uid, step.id)}
					>
						{step.label}
					</Button>
				{/if}
				{#if cancel && onstep}
					<Button
						variant="outline"
						class="shrink-0"
						disabled={busy}
						onclick={() => onstep?.(transfer.uid, cancel.id)}
					>
						{cancel.label}
					</Button>
				{/if}
				{#if !step && !cancel}
					<p class="text-muted-foreground flex min-h-8 w-full items-center justify-center text-xs">
						No actions available
					</p>
				{/if}
			</div>
		{/if}
	</CardContent>
</Card>
