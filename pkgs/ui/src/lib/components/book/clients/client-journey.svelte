<script lang="ts">
	// One journey as this client answered it. Each form is a glass card. Short answers sit in a
	// grid with the label above the value, so a long label never squeezes what was written; a long
	// answer (an address, a note) takes the whole row and wraps. A repeating schedule is the shared
	// table, and a scored questionnaire leads with its band.
	import { formatDate } from '../../../dates.js';
	import { Alert, AlertDescription, AlertTitle } from '../../ui/alert/index.js';
	import { Button } from '../../ui/button/index.js';
	import { Card, CardContent } from '../../ui/card/index.js';
	import { Money } from '../../ui/money/index.js';
	import { DataTable, type DataTableColumn } from '../../ui/data-table/index.js';
	import EmptyState from '../../ui/empty-state/empty-state.svelte';
	import { SectionHeading } from '../../ui/section-heading/index.js';
	import { StatusBadge, type StatusTone } from '../../ui/status-badge/index.js';
	import { TruncatedText } from '../../ui/truncated-text/index.js';
	import PageHeader from '../../shell/page-header.svelte';
	import { stateLabel, stateTone } from '../journeys/applications.js';
	import type { ClientJourneyDetail, ClientJourneyForm } from './client-journey.js';
	import {
		presentAnswers,
		scoredResult,
		type PresentedRow,
		type PresentedTable
	} from './journey-answers.js';

	let {
		journey,
		journeyHref,
		documentHref,
		otherHref
	}: {
		journey: ClientJourneyDetail;
		/** The working journey, where a step can still be filled. */
		journeyHref: string;
		documentHref: (uid: string) => string;
		/** The other person's own Journeys section, on a journey shared by two clients. */
		otherHref: (uid: string) => string;
	} = $props();

	function formState(form: ClientJourneyForm): { label: string; tone: StatusTone } {
		if (form.status === 'fulfilled') return { label: 'Submitted', tone: 'success' };
		if (Object.keys(form.answers).length) return { label: 'Saved', tone: 'info' };
		return { label: stateLabel(form.status), tone: stateTone(form.status) };
	}

	function submittedLine(form: ClientJourneyForm): string | null {
		if (!form.submitted_at) return null;
		const when = formatDate(form.submitted_at);
		return form.submitted_by ? `${when} · ${form.submitted_by}` : when;
	}

	const description = $derived.by(() => {
		const parts = [`Started ${formatDate(journey.created_at)}`];
		if (journey.others.length) parts.push(`with ${journey.others.map((p) => p.name).join(', ')}`);
		return parts.join(' · ');
	});

	const recorded = $derived(journey.forms.length > 0 || journey.documents.length > 0);

	/** A repeating schedule's columns, for the shared table. */
	function tableColumns(table: PresentedTable): DataTableColumn<string[]>[] {
		return table.columns.map((header, i) => ({
			key: String(i),
			header,
			cellClass: 'truncate'
		}));
	}

	const documentColumns: DataTableColumn<ClientJourneyDetail['documents'][number]>[] = [
		{ key: 'title', header: 'Document', headerClass: 'w-[70%]', cellClass: 'truncate' },
		{ key: 'status', header: 'Status', headerClass: 'w-[30%]' }
	];
</script>

{#snippet headerActions()}
	<StatusBadge
		tone={stateTone(journey.status)}
		pulse={journey.status === 'active' || journey.status === 'waiting'}
	>
		{stateLabel(journey.status)}
	</StatusBadge>
	<Button href={journeyHref} variant="outline" size="sm">Open journey</Button>
{/snippet}

{#snippet register(rows: PresentedRow[])}
	<dl class="grid grid-cols-1 gap-x-8 gap-y-4 sm:grid-cols-2 xl:grid-cols-3">
		{#each rows as row, i (`${row.label}-${i}`)}
			<div class="min-w-0 {row.value.length > 48 ? 'sm:col-span-2 xl:col-span-3' : ''}">
				<dt class="text-muted-foreground text-xs">{row.label}</dt>
				<dd class="mt-0.5 text-sm text-pretty whitespace-pre-wrap">
					{#if row.money}
						<Money amount={row.money.amount} currency={row.money.currency} />
					{:else}
						{row.value}
					{/if}
				</dd>
			</div>
		{/each}
	</dl>
{/snippet}

<div class="w-full">
	<PageHeader title={journey.flow_name} {description} actions={headerActions} class="mb-4" />

	{#if journey.others.length}
		<p class="text-muted-foreground mb-5 text-xs">
			Also on this journey:
			{#each journey.others as person, i (person.uid)}
				{#if i > 0},{/if}
				<a href={otherHref(person.uid)} class="text-brand hover:underline">{person.name}</a>
			{/each}
		</p>
	{/if}

	{#if journey.status === 'cancelled'}
		<Alert class="mb-5">
			<AlertTitle>This journey was stopped</AlertTitle>
			<AlertDescription>What was recorded before it stopped is kept below.</AlertDescription>
		</Alert>
	{/if}

	<div class="space-y-5">
		{#if !recorded}
			<EmptyState
				title="Nothing recorded yet"
				description="Answers appear here once a form on this journey is saved."
			>
				{#snippet action()}
					<Button href={journeyHref} size="sm">Open journey</Button>
				{/snippet}
			</EmptyState>
		{/if}

		{#each journey.forms as form (form.step_key)}
			{@const sections = presentAnswers(form.fields, form.answers)}
			{@const result = scoredResult(form.outputs)}
			{@const state = formState(form)}
			{@const line = submittedLine(form)}
			<Card glass>
				<CardContent class="space-y-5">
					<SectionHeading title={form.title} class="mb-0">
						{#snippet actions()}
							{#if line}<span class="text-muted-foreground text-xs">{line}</span>{/if}
							<StatusBadge tone={state.tone}>{state.label}</StatusBadge>
						{/snippet}
					</SectionHeading>

					{#if result}
						<div class="bg-muted/40 rounded-md px-3 py-2">
							<p class="text-sm font-medium">{result.headline}</p>
							{#if result.definition}
								<p class="text-muted-foreground mt-0.5 text-xs text-pretty">{result.definition}</p>
							{/if}
						</div>
					{/if}

					{#each sections as section, si (`${section.heading ?? ''}-${si}`)}
						{@const plain = section.rows.filter((row) => !row.table)}
						{@const tables = section.rows.filter((row) => row.table)}
						<section class="min-w-0 {si > 0 ? 'border-border/60 border-t pt-5' : ''}">
							<!-- A one-section form whose section is named after the form would print the
							     title twice a line apart. -->
							{#if section.heading && !(sections.length === 1 && section.heading === form.title)}
								<p class="text-muted-foreground mb-3 text-xs font-semibold tracking-wide uppercase">
									{section.heading}
								</p>
							{/if}
							{#if plain.length}
								{@render register(plain)}
							{/if}
							{#each tables as row, ri (`${row.label}-${ri}`)}
								{#if row.table}
									<div class="mt-3 min-w-0">
										<p class="text-muted-foreground mb-1.5 text-xs">{row.label}</p>
										<DataTable
											columns={tableColumns(row.table)}
											rows={row.table.rows}
											getKey={(cells) => row.table!.rows.indexOf(cells)}
											dense
											label={row.label}
										>
											{#snippet cell(cells, column)}
												{@const money =
													row.table!.cells[row.table!.rows.indexOf(cells)]?.[Number(column.key)]
														?.money}
												{#if money}
													<Money amount={money.amount} currency={money.currency} />
												{:else}
													<TruncatedText text={cells[Number(column.key)] ?? '—'} />
												{/if}
											{/snippet}
										</DataTable>
									</div>
								{/if}
							{/each}
						</section>
					{/each}
				</CardContent>
			</Card>
		{/each}

		{#if journey.documents.length}
			<Card glass>
				<CardContent class="space-y-3">
					<SectionHeading title="Documents" count={journey.documents.length} class="mb-0" />
					<DataTable
						columns={documentColumns}
						rows={journey.documents}
						getKey={(doc) => doc.uid ?? `${doc.title}-${doc.status}`}
						fixed
						dense
						label="Journey documents"
					>
						{#snippet cell(doc, column)}
							{#if column.key === 'title'}
								{#if doc.uid}
									<TruncatedText text={doc.name ?? doc.title} href={documentHref(doc.uid)} />
								{:else}
									<TruncatedText text={doc.title} />
								{/if}
							{:else}
								<StatusBadge tone={stateTone(doc.status)}>{stateLabel(doc.status)}</StatusBadge>
							{/if}
						{/snippet}
					</DataTable>
				</CardContent>
			</Card>
		{/if}
	</div>
</div>
