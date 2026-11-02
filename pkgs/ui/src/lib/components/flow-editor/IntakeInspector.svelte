<script lang="ts">
	import { Button, Checkbox, FORM_FIELD_TYPES, Input, Label, SelectMenu } from '@graphene/ui';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import XIcon from '@lucide/svelte/icons/x';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import PlayIcon from '@lucide/svelte/icons/play';
	import type { FlowIntake, IntakeField } from '@graphene/ui/book/journeys';

	let {
		intake,
		canManage,
		onchange,
		onclose
	}: {
		intake: FlowIntake;
		canManage: boolean;
		onchange: () => void;
		onclose: () => void;
	} = $props();

	const FIELD_TYPES: IntakeField['type'][] = [
		'text',
		'textarea',
		'select',
		'date',
		'email',
		'number',
		'checkbox'
	];
	// The form builder's names for the same types, so the picker reads "Text area", not `textarea`.
	const fieldTypeItems = FIELD_TYPES.map((t) => ({
		value: t,
		label: FORM_FIELD_TYPES.find((type) => type.value === t)?.label ?? t
	}));

	function toggle(
		key: 'collect_platform' | 'collect_custody' | 'collect_product' | 'allow_new' | 'review',
		v: boolean
	) {
		intake[key] = v;
		onchange();
	}
	function slug(s: string) {
		return s
			.toLowerCase()
			.replace(/[^a-z0-9]+/g, '_')
			.replace(/^_+|_+$/g, '');
	}
	function addField() {
		intake.fields = [
			...intake.fields,
			{
				key: `field_${intake.fields.length + 1}`,
				label: '',
				type: 'text',
				required: false,
				options: []
			}
		];
		onchange();
	}
	function removeField(i: number) {
		intake.fields = intake.fields.filter((_, ix) => ix !== i);
		onchange();
	}
	function setLabel(i: number, label: string) {
		const field = intake.fields[i];
		if (!field) return;
		field.label = label;
		if (!field.key || /^field_\d+$/.test(field.key)) {
			field.key = slug(label) || `field_${i + 1}`;
		}
		onchange();
	}
</script>

<div class="bg-background flex h-full w-80 shrink-0 flex-col border-l">
	<div class="flex items-center justify-between border-b px-4 py-3">
		<h2 class="flex items-center gap-2 text-sm font-semibold">
			<PlayIcon class="size-4" /> Intake
		</h2>
		<Button
			variant="ghost"
			size="icon-sm"
			class="text-muted-foreground"
			aria-label="Close"
			onclick={onclose}
		>
			<XIcon class="size-4" />
		</Button>
	</div>

	<div class="flex-1 space-y-5 overflow-y-auto p-4">
		<p class="text-muted-foreground text-xs">
			What “New journey” collects to start this flow. Everything else is gathered by the steps.
		</p>

		<div class="space-y-2">
			<div class="flex items-center gap-2">
				<Checkbox
					id="intake-review"
					checked={intake.review !== false}
					disabled={!canManage}
					onCheckedChange={(v) => toggle('review', v === true)}
				/>
				<Label for="intake-review" class="font-normal">Review before starting</Label>
			</div>
			<p class="text-muted-foreground text-xs">
				A last page that restates the choices. Leave this off when kickoff is only choosing who the
				journey is for.
			</p>
		</div>

		<div class="space-y-2">
			<Label class="text-xs">Collect at kickoff</Label>
			{#each [['collect_platform', 'Custodian'], ['collect_custody', 'Custody (onshore / offshore)'], ['collect_product', 'Product per account'], ['allow_new', 'Allow creating new clients inline']] as [key, label] (key)}
				<div class="flex items-center gap-2">
					<Checkbox
						id={`intake-${key}`}
						checked={intake[key as 'collect_platform']}
						disabled={!canManage}
						onCheckedChange={(v) => toggle(key as 'collect_platform', v === true)}
					/>
					<Label for={`intake-${key}`} class="font-normal">{label}</Label>
				</div>
			{/each}
		</div>

		<div class="space-y-2 border-t pt-4">
			<div class="flex items-center justify-between">
				<Label class="text-xs">Custom fields</Label>
				{#if canManage}
					<Button variant="link" size="xs" class="text-brand px-0" onclick={addField}>
						<PlusIcon class="size-3" />Add field
					</Button>
				{/if}
			</div>
			{#if intake.fields.length === 0}
				<p class="text-muted-foreground/70 text-xs">
					No custom fields — add referral source, notes, etc.
				</p>
			{/if}
			{#each intake.fields as f, i (i)}
				<div class="space-y-2 rounded-lg border p-2.5">
					<div class="flex items-center gap-2">
						<Input
							class="flex-1"
							placeholder="Field label"
							value={f.label}
							disabled={!canManage}
							oninput={(e) => setLabel(i, e.currentTarget.value)}
						/>
						{#if canManage}
							<Button
								variant="ghost"
								size="icon-xs"
								class="text-muted-foreground hover:text-destructive shrink-0"
								aria-label="Remove field"
								onclick={() => removeField(i)}
							>
								<Trash2Icon class="size-4" />
							</Button>
						{/if}
					</div>
					<div class="flex items-center gap-2">
						<SelectMenu
							size="sm"
							class="flex-1"
							items={fieldTypeItems}
							value={f.type}
							disabled={!canManage}
							onValueChange={(v) => {
								f.type = v as IntakeField['type'];
								onchange();
							}}
						/>
						<div class="flex items-center gap-1.5">
							<Checkbox
								id={`intake-field-${i}-required`}
								class="size-3.5"
								checked={f.required}
								disabled={!canManage}
								onCheckedChange={(v) => {
									f.required = v === true;
									onchange();
								}}
							/>
							<Label for={`intake-field-${i}-required`} class="text-xs font-normal">Required</Label>
						</div>
					</div>
					{#if f.type === 'select'}
						<Input
							class="text-xs"
							placeholder="Options, comma-separated"
							value={f.options.join(', ')}
							disabled={!canManage}
							oninput={(e) => {
								f.options = e.currentTarget.value
									.split(',')
									.map((s) => s.trim())
									.filter(Boolean);
								onchange();
							}}
						/>
					{/if}
				</div>
			{/each}
		</div>
	</div>
</div>
