<script lang="ts">
	import PlusIcon from '@lucide/svelte/icons/plus';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import { Button } from '../../components/ui/button/index.js';
	import { Input } from '../../components/ui/input/index.js';
	import PercentageInput from './percentage-input.svelte';

	type Beneficiary = { first_name: string; surname: string; portion: number | null };

	let {
		value,
		onchange,
		invalid = false
	}: {
		value?: unknown;
		onchange: (value: Beneficiary[]) => void;
		invalid?: boolean;
	} = $props();

	const rows = $derived.by((): Beneficiary[] => {
		if (!Array.isArray(value)) return [];
		return value.map((row) => {
			const item = row && typeof row === 'object' ? (row as Record<string, unknown>) : {};
			return {
				first_name: String(item.first_name ?? ''),
				surname: String(item.surname ?? ''),
				portion:
					typeof item.portion === 'number'
						? item.portion
						: item.portion == null || item.portion === ''
							? null
							: Number(item.portion)
			};
		});
	});
	const total = $derived(rows.reduce((sum, row) => sum + (Number(row.portion) || 0), 0));

	function add(): void {
		onchange([...rows, { first_name: '', surname: '', portion: null }]);
	}

	function patch(index: number, change: Partial<Beneficiary>): void {
		onchange(rows.map((row, i) => (i === index ? { ...row, ...change } : row)));
	}

	function remove(index: number): void {
		onchange(rows.filter((_, i) => i !== index));
	}
</script>

<div class="space-y-3" aria-invalid={invalid || undefined}>
	{#each rows as row, index (index)}
		<div class="grid items-end gap-2 rounded-lg border p-3 sm:grid-cols-[1fr_1fr_9rem_auto]">
			<label class="space-y-1 text-sm">
				<span class="text-muted-foreground text-xs">First name</span>
				<Input
					value={row.first_name}
					oninput={(event) => patch(index, { first_name: event.currentTarget.value })}
				/>
			</label>
			<label class="space-y-1 text-sm">
				<span class="text-muted-foreground text-xs">Surname</span>
				<Input
					value={row.surname}
					oninput={(event) => patch(index, { surname: event.currentTarget.value })}
				/>
			</label>
			<label class="space-y-1 text-sm">
				<span class="text-muted-foreground text-xs">Portion</span>
				<PercentageInput
					value={row.portion}
					onchange={(portion) => patch(index, { portion: Number(portion) })}
				/>
			</label>
			<Button
				type="button"
				variant="ghost"
				size="icon"
				class="size-8"
				aria-label="Remove beneficiary"
				onclick={() => remove(index)}
			>
				<Trash2Icon class="size-4" aria-hidden="true" />
			</Button>
		</div>
	{/each}

	<div class="flex flex-wrap items-center justify-between gap-3">
		<Button type="button" variant="outline" size="sm" onclick={add}>
			<PlusIcon class="mr-1.5 size-4" aria-hidden="true" />
			Add beneficiary
		</Button>
		{#if rows.length}
			<p class="text-sm {Math.abs(total - 100) < 0.001 ? 'text-success' : 'text-muted-foreground'}">
				Total: <span class="font-medium tabular-nums">{total}%</span>
				{#if Math.abs(total - 100) >= 0.001}
					· must equal 100%{/if}
			</p>
		{/if}
	</div>
</div>
