<script lang="ts">
	import { Textarea } from '../../components/ui/textarea/index.js';
	import { cn } from '../../utils.js';

	let {
		value = '',
		onchange,
		minCount = 0,
		maxLength,
		rows = 4,
		placeholder,
		invalid = false,
		id
	}: {
		value?: unknown;
		onchange: (v: string) => void;
		minCount?: number;
		maxLength?: number;
		rows?: number;
		placeholder?: string;
		invalid?: boolean;
		id?: string;
	} = $props();

	const text = $derived(String(value ?? ''));
	const remaining = $derived(Math.max(0, minCount - text.length));
</script>

<div class="space-y-1.5">
	{#if (maxLength && maxLength > 0) || minCount > 0}
		<div class="flex items-center justify-end gap-2">
			{#if maxLength && maxLength > 0}
				<span class="text-muted-foreground text-xs">{text.length}/{maxLength}</span>
			{/if}
			{#if minCount > 0}
				<span class={cn('text-muted-foreground text-xs', remaining === 0 && 'text-foreground')}>
					{remaining === 0 ? 'Minimum met' : `${remaining} characters to go`}
				</span>
			{/if}
		</div>
	{/if}
	<Textarea
		{id}
		{rows}
		{placeholder}
		maxlength={maxLength}
		value={text}
		aria-invalid={invalid || undefined}
		oninput={(e) => onchange(e.currentTarget.value)}
	/>
</div>
