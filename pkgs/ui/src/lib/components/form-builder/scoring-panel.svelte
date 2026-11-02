<script lang="ts">
	import type { FormField, FormScoring, FormScoringBand, FormScoringField } from '@graphene/ui';
	import { Button, Input, Label, SelectMenu, Textarea } from '@graphene/ui';
	import { untrack } from 'svelte';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import { resolveOptions } from '../../forms/editor/options.js';

	let {
		fields = [],
		scoring = null,
		onchange
	}: {
		fields?: FormField[];
		scoring?: FormScoring | null;
		onchange?: (scoring: FormScoring | null) => void;
	} = $props();

	type WeightDraft = { option: string; label: string; points: string };
	type FieldDraft = { field_key: string; weights: WeightDraft[] };
	type BandDraft = { key: string; label: string; min: string; max: string; definition: string };

	const SCORABLE = new Set([
		'select',
		'segmented',
		'combobox',
		'multiSelect',
		'multiPicker',
		'checkbox'
	]);

	function weightsFrom(
		field: FormField | undefined,
		stored?: Record<string, number>
	): WeightDraft[] {
		const options = field ? resolveOptions(field) : [];
		const seen = new Set<string>();
		const rows: WeightDraft[] = options.map((option) => {
			seen.add(option.value);
			const points = stored?.[option.value];
			return {
				option: option.value,
				label: option.label,
				points: points === undefined ? '' : String(points)
			};
		});
		for (const [option, points] of Object.entries(stored ?? {})) {
			if (seen.has(option)) continue;
			rows.push({ option, label: option, points: String(points) });
		}
		if (rows.length === 0) rows.push({ option: '', label: '', points: '' });
		return rows;
	}

	function seedFields(source: FormScoringField[]): FieldDraft[] {
		return source.map((item) => ({
			field_key: item.field_key,
			weights: weightsFrom(
				fields.find((field) => field.fieldKey === item.field_key),
				item.weights
			)
		}));
	}

	function seedBands(source: FormScoringBand[]): BandDraft[] {
		return source.map((band) => ({
			key: band.key,
			label: band.label,
			min: String(band.min),
			max: String(band.max),
			definition: band.definition ?? ''
		}));
	}

	let enabled = $state(false);
	let scored = $state<FieldDraft[]>([]);
	let bands = $state<BandDraft[]>([]);

	// Seed once. The edit route remounts per form, and reseeding on every parent render would
	// wipe a band the author is still typing.
	untrack(() => {
		if (scoring && (scoring.fields.length > 0 || scoring.bands.length > 0)) {
			enabled = true;
			scored = seedFields(scoring.fields);
			bands = seedBands(scoring.bands);
		}
	});

	const questionItems = $derived(
		fields
			.filter((field) => field.fieldKey && SCORABLE.has(field.fieldType))
			.map((field) => ({ value: field.fieldKey, label: field.label || field.fieldKey }))
	);

	function emit(): void {
		if (!enabled) {
			onchange?.(null);
			return;
		}
		const nextFields: FormScoringField[] = [];
		for (const field of scored) {
			if (!field.field_key.trim()) continue;
			const weights: Record<string, number> = {};
			for (const row of field.weights) {
				const option = row.option.trim();
				if (!option || row.points.trim() === '') continue;
				const points = Number(row.points);
				if (!Number.isFinite(points)) continue;
				weights[option] = points;
			}
			nextFields.push({ field_key: field.field_key.trim(), weights });
		}
		const nextBands: FormScoringBand[] = [];
		for (const band of bands) {
			const min = Number(band.min);
			const max = Number(band.max);
			if (
				!band.key.trim() ||
				!band.label.trim() ||
				!Number.isFinite(min) ||
				!Number.isFinite(max)
			) {
				continue;
			}
			nextBands.push({
				key: band.key.trim(),
				label: band.label.trim(),
				min,
				max,
				definition: band.definition
			});
		}
		onchange?.({ fields: nextFields, bands: nextBands });
	}

	function enable(): void {
		enabled = true;
		emit();
	}

	function disable(): void {
		enabled = false;
		scored = [];
		bands = [];
		emit();
	}

	function addQuestion(): void {
		const used = new Set(scored.map((field) => field.field_key));
		const next = questionItems.find((item) => !used.has(item.value));
		const field = fields.find((item) => item.fieldKey === next?.value);
		scored = [...scored, { field_key: next?.value ?? '', weights: weightsFrom(field) }];
		emit();
	}

	function setQuestion(index: number, fieldKey: string): void {
		const field = fields.find((item) => item.fieldKey === fieldKey);
		scored = scored.map((item, i) =>
			i === index ? { field_key: fieldKey, weights: weightsFrom(field, undefined) } : item
		);
		emit();
	}

	function patchWeight(fieldIndex: number, weightIndex: number, patch: Partial<WeightDraft>): void {
		scored = scored.map((field, i) =>
			i === fieldIndex
				? {
						...field,
						weights: field.weights.map((row, j) => (j === weightIndex ? { ...row, ...patch } : row))
					}
				: field
		);
		emit();
	}

	function addWeight(fieldIndex: number): void {
		scored = scored.map((field, i) =>
			i === fieldIndex
				? { ...field, weights: [...field.weights, { option: '', label: '', points: '' }] }
				: field
		);
		emit();
	}

	function removeQuestion(index: number): void {
		scored = scored.filter((_, i) => i !== index);
		emit();
	}

	function addBand(): void {
		bands = [...bands, { key: '', label: '', min: '', max: '', definition: '' }];
		emit();
	}

	function patchBand(index: number, patch: Partial<BandDraft>): void {
		bands = bands.map((band, i) => (i === index ? { ...band, ...patch } : band));
		emit();
	}

	function removeBand(index: number): void {
		bands = bands.filter((_, i) => i !== index);
		emit();
	}

	function bandIncomplete(band: BandDraft): boolean {
		if (!band.key && !band.label && !band.min && !band.max && !band.definition) return false;
		const min = Number(band.min);
		const max = Number(band.max);
		return !band.key.trim() || !band.label.trim() || !Number.isFinite(min) || !Number.isFinite(max);
	}
</script>

<section class="bg-card space-y-4 rounded-xl border p-4">
	<div class="flex items-start justify-between gap-3">
		<div>
			<h2 class="text-lg font-semibold">Scoring</h2>
			<p class="text-muted-foreground text-sm">
				Points per answer, then bands for the total. Computed when the form is submitted — a posted
				band is replaced.
			</p>
		</div>
		{#if enabled}
			<Button variant="ghost" size="sm" onclick={disable}>Remove scoring</Button>
		{/if}
	</div>

	{#if !enabled}
		<Button variant="outline" size="sm" onclick={enable}>
			<PlusIcon class="mr-1.5 size-4" /> Add scoring
		</Button>
	{:else}
		<div class="space-y-3">
			<div class="flex items-center justify-between">
				<h3 class="text-sm font-medium">Questions</h3>
				<Button variant="outline" size="sm" onclick={addQuestion}>
					<PlusIcon class="mr-1 size-4" /> Add question
				</Button>
			</div>
			{#if scored.length === 0}
				<p class="text-muted-foreground text-sm">No questions scored yet.</p>
			{/if}
			{#each scored as field, fieldIndex (fieldIndex)}
				<div class="space-y-3 rounded-lg border p-3">
					<div class="flex items-end gap-2">
						<div class="min-w-0 flex-1 space-y-1.5">
							<Label>Question</Label>
							{#if questionItems.length > 0}
								<SelectMenu
									items={questionItems}
									value={field.field_key || undefined}
									placeholder="Choose a question"
									onValueChange={(value) => setQuestion(fieldIndex, value)}
								/>
							{:else}
								<Input
									class="font-mono text-sm"
									placeholder="field_key"
									value={field.field_key}
									oninput={(event) => {
										scored = scored.map((item, i) =>
											i === fieldIndex ? { ...item, field_key: event.currentTarget.value } : item
										);
										emit();
									}}
								/>
							{/if}
						</div>
						<Button
							variant="ghost"
							size="icon"
							class="size-8"
							aria-label="Remove question"
							onclick={() => removeQuestion(fieldIndex)}
						>
							<Trash2Icon class="size-4" />
						</Button>
					</div>
					<div class="space-y-2">
						<div class="text-muted-foreground grid grid-cols-[1fr_1fr_6rem_auto] gap-2 text-xs">
							<span>Option label</span>
							<span>Stored value</span>
							<span>Points</span>
							<span class="w-8"></span>
						</div>
						{#each field.weights as row, weightIndex (weightIndex)}
							<div class="grid grid-cols-[1fr_1fr_6rem_auto] items-center gap-2">
								<Input
									placeholder="Strongly agree"
									value={row.label}
									oninput={(event) =>
										patchWeight(fieldIndex, weightIndex, { label: event.currentTarget.value })}
								/>
								<Input
									class="font-mono text-sm"
									placeholder="a"
									value={row.option}
									oninput={(event) =>
										patchWeight(fieldIndex, weightIndex, { option: event.currentTarget.value })}
								/>
								<Input
									type="number"
									placeholder="0"
									value={row.points}
									oninput={(event) =>
										patchWeight(fieldIndex, weightIndex, { points: event.currentTarget.value })}
								/>
								<Button
									variant="ghost"
									size="icon"
									class="size-8"
									aria-label="Remove option weight"
									onclick={() => {
										scored = scored.map((item, i) =>
											i === fieldIndex
												? { ...item, weights: item.weights.filter((_, j) => j !== weightIndex) }
												: item
										);
										emit();
									}}
								>
									<Trash2Icon class="size-4" />
								</Button>
							</div>
						{/each}
						<Button variant="ghost" size="sm" onclick={() => addWeight(fieldIndex)}>
							<PlusIcon class="mr-1 size-4" /> Add option
						</Button>
					</div>
				</div>
			{/each}
		</div>

		<div class="space-y-3 border-t pt-4">
			<div class="flex items-center justify-between">
				<h3 class="text-sm font-medium">Bands</h3>
				<Button variant="outline" size="sm" onclick={addBand}>
					<PlusIcon class="mr-1 size-4" /> Add band
				</Button>
			</div>
			{#if bands.length === 0}
				<p class="text-muted-foreground text-sm">No bands yet. The total is still stored.</p>
			{/if}
			{#each bands as band, bandIndex (bandIndex)}
				<div class="space-y-3 rounded-lg border p-3">
					<div class="grid gap-2 sm:grid-cols-[1fr_1fr_5rem_5rem_auto]">
						<div class="space-y-1">
							<Label class="text-muted-foreground text-xs">Label</Label>
							<Input
								placeholder="Cautious"
								value={band.label}
								oninput={(event) => patchBand(bandIndex, { label: event.currentTarget.value })}
							/>
						</div>
						<div class="space-y-1">
							<Label class="text-muted-foreground text-xs">Key</Label>
							<Input
								class="font-mono text-sm"
								placeholder="cautious"
								value={band.key}
								oninput={(event) => patchBand(bandIndex, { key: event.currentTarget.value })}
							/>
						</div>
						<div class="space-y-1">
							<Label class="text-muted-foreground text-xs">Min</Label>
							<Input
								type="number"
								value={band.min}
								oninput={(event) => patchBand(bandIndex, { min: event.currentTarget.value })}
							/>
						</div>
						<div class="space-y-1">
							<Label class="text-muted-foreground text-xs">Max</Label>
							<Input
								type="number"
								value={band.max}
								oninput={(event) => patchBand(bandIndex, { max: event.currentTarget.value })}
							/>
						</div>
						<div class="flex items-end">
							<Button
								variant="ghost"
								size="icon"
								class="size-8"
								aria-label="Remove band"
								onclick={() => removeBand(bandIndex)}
							>
								<Trash2Icon class="size-4" />
							</Button>
						</div>
					</div>
					<Textarea
						rows={2}
						placeholder="What this band means, in a sentence the letter can quote."
						value={band.definition}
						oninput={(event) => patchBand(bandIndex, { definition: event.currentTarget.value })}
					/>
					{#if bandIncomplete(band)}
						<p class="text-muted-foreground text-xs">
							A band is saved once it has a key, a label, and a minimum and maximum.
						</p>
					{/if}
				</div>
			{/each}
		</div>
	{/if}
</section>
