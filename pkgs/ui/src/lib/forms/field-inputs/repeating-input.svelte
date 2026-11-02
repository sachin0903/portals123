<script lang="ts">
	import PlusIcon from '@lucide/svelte/icons/plus';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import { Button } from '../../components/ui/button/index.js';
	import { Checkbox } from '../../components/ui/checkbox/index.js';
	import { Input } from '../../components/ui/input/index.js';
	import { Label } from '../../components/ui/label/index.js';
	import { SelectMenu } from '../../components/ui/select-menu/index.js';
	import { Textarea } from '../../components/ui/textarea/index.js';
	import Segmented from '../../components/ui/segmented/segmented.svelte';
	import { resolveOptions } from '../editor/options';
	import { repeatingColumns } from '../repeating.js';
	import type { FormField } from '../types.js';
	import BankAccountNumberInput from './bank-account-number-input.svelte';
	import ComboboxInput from './combobox-input.svelte';
	import DateInput from './date-input.svelte';
	import EmailInput from './email-input.svelte';
	import MoneyInput from './money-input.svelte';
	import MultiSelectInput from './multi-select-input.svelte';
	import PercentageInput from './percentage-input.svelte';
	import PhoneNumberInput from './phone-number-input.svelte';
	import SortCodeInput from './sort-code-input.svelte';

	let {
		field,
		value,
		onchange,
		invalid = false,
		id
	}: {
		field: FormField;
		value?: unknown;
		onchange: (value: Record<string, unknown>[]) => void;
		invalid?: boolean;
		id?: string;
	} = $props();

	const columns = $derived(repeatingColumns(field.options));

	const rows = $derived.by((): Record<string, unknown>[] => {
		if (!Array.isArray(value)) return [];
		return value.map((row) =>
			row && typeof row === 'object' && !Array.isArray(row)
				? { ...(row as Record<string, unknown>) }
				: {}
		);
	});

	function kind(type: string): string {
		switch (type) {
			case 'textarea':
				return 'textarea';
			case 'number':
				return 'number';
			case 'checkbox':
				return 'checkbox';
			case 'select':
				return 'select';
			case 'segmented':
				return 'segmented';
			case 'combobox':
				return 'combobox';
			case 'multiSelect':
			case 'multiPicker':
				return 'multiselect';
			case 'date':
			case 'dateOfBirth':
				return 'date';
			case 'currency':
			case 'money':
				return 'money';
			case 'percentage':
				return 'percentage';
			case 'phone':
			case 'phoneNumber':
				return 'phone';
			case 'email':
				return 'email';
			case 'bankAccountNumber':
			case 'accountNumber':
				return 'bank';
			case 'sortCode':
				return 'sortcode';
			default:
				return 'text';
		}
	}

	function choices(column: (typeof columns)[number]) {
		return resolveOptions({ options: column.options ?? null });
	}

	function currencyCode(column: (typeof columns)[number]): string {
		const options = column.options;
		if (
			options &&
			typeof options === 'object' &&
			!Array.isArray(options) &&
			'currency' in options
		) {
			return String((options as { currency?: unknown }).currency || 'GBP');
		}
		return 'GBP';
	}

	function add(): void {
		onchange([...rows, {}]);
	}

	function patch(index: number, key: string, next: unknown): void {
		onchange(rows.map((row, i) => (i === index ? { ...row, [key]: next } : row)));
	}

	function remove(index: number): void {
		onchange(rows.filter((_, i) => i !== index));
	}
</script>

<div class="space-y-3" aria-invalid={invalid || undefined}>
	{#each rows as row, index (index)}
		<div class="space-y-3 rounded-lg border p-3">
			<div class="flex items-center justify-between gap-2">
				<p class="text-muted-foreground text-xs tracking-wide uppercase">Row {index + 1}</p>
				<Button
					type="button"
					variant="ghost"
					size="icon"
					class="size-8"
					aria-label="Remove row {index + 1}"
					onclick={() => remove(index)}
				>
					<Trash2Icon class="size-4" aria-hidden="true" />
				</Button>
			</div>
			<div class="grid gap-3 sm:grid-cols-2">
				{#each columns as column (column.key)}
					{@const cellId = `${id ?? field.uid}-${index}-${column.key}`}
					{@const cell = row[column.key]}
					{@const cellKind = kind(column.type)}
					<div class={cellKind === 'textarea' || cellKind === 'checkbox' ? 'sm:col-span-2' : ''}>
						{#if cellKind === 'checkbox'}
							<label class="flex items-center gap-2 text-sm" for={cellId}>
								<Checkbox
									id={cellId}
									checked={cell === true}
									onCheckedChange={(next) => patch(index, column.key, Boolean(next))}
								/>
								<span>{column.label}</span>
							</label>
						{:else}
							<Label for={cellId} class="text-muted-foreground mb-1 text-xs">{column.label}</Label>
							{#if cellKind === 'textarea'}
								<Textarea
									id={cellId}
									value={String(cell ?? '')}
									aria-invalid={invalid || undefined}
									oninput={(event) => patch(index, column.key, event.currentTarget.value)}
								/>
							{:else if cellKind === 'number'}
								<Input
									id={cellId}
									type="number"
									value={(cell ?? '') as string | number}
									autocomplete="off"
									aria-invalid={invalid || undefined}
									oninput={(event) =>
										patch(
											index,
											column.key,
											event.currentTarget.value === '' ? '' : Number(event.currentTarget.value)
										)}
								/>
							{:else if cellKind === 'select'}
								<SelectMenu
									id={cellId}
									items={choices(column)}
									value={cell == null || cell === '' ? undefined : String(cell)}
									placeholder="Select…"
									onValueChange={(next) => patch(index, column.key, next)}
									class={invalid ? 'border-destructive' : undefined}
								/>
							{:else if cellKind === 'segmented'}
								<Segmented
									options={choices(column)}
									value={cell == null || cell === '' ? undefined : String(cell)}
									onValueChange={(next) => patch(index, column.key, next)}
									shape="square"
								/>
							{:else if cellKind === 'combobox'}
								<ComboboxInput
									id={cellId}
									value={cell}
									options={choices(column)}
									{invalid}
									onchange={(next) => patch(index, column.key, next)}
								/>
							{:else if cellKind === 'multiselect'}
								<MultiSelectInput
									id={cellId}
									value={cell}
									options={choices(column)}
									{invalid}
									onchange={(next) => patch(index, column.key, next)}
								/>
							{:else if cellKind === 'money'}
								<MoneyInput
									id={cellId}
									value={cell}
									currencyCode={currencyCode(column)}
									{invalid}
									onchange={(next) => patch(index, column.key, next)}
								/>
							{:else if cellKind === 'percentage'}
								<PercentageInput
									id={cellId}
									value={cell}
									{invalid}
									onchange={(next) => patch(index, column.key, next)}
								/>
							{:else if cellKind === 'date'}
								<DateInput
									id={cellId}
									value={cell}
									{invalid}
									onchange={(next) => patch(index, column.key, next)}
								/>
							{:else if cellKind === 'phone'}
								<PhoneNumberInput
									id={cellId}
									value={cell}
									{invalid}
									onchange={(next) => patch(index, column.key, next)}
								/>
							{:else if cellKind === 'email'}
								<EmailInput
									id={cellId}
									value={cell}
									{invalid}
									onchange={(next) => patch(index, column.key, next)}
								/>
							{:else if cellKind === 'bank'}
								<BankAccountNumberInput
									id={cellId}
									value={cell}
									{invalid}
									onchange={(next) => patch(index, column.key, next)}
								/>
							{:else if cellKind === 'sortcode'}
								<SortCodeInput
									id={cellId}
									value={cell}
									{invalid}
									onchange={(next) => patch(index, column.key, next)}
								/>
							{:else}
								<Input
									id={cellId}
									value={String(cell ?? '')}
									autocomplete="off"
									aria-invalid={invalid || undefined}
									oninput={(event) => patch(index, column.key, event.currentTarget.value)}
								/>
							{/if}
						{/if}
					</div>
				{/each}
			</div>
		</div>
	{/each}

	<Button type="button" variant="outline" size="sm" onclick={add}>
		<PlusIcon class="mr-1.5 size-4" aria-hidden="true" />
		Add row
	</Button>
</div>
