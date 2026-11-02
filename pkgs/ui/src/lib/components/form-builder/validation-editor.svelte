<script lang="ts">
	import type { FieldValidation } from '@graphene/ui';
	import { Tabs, Input, Label, Segmented, Textarea, PATTERN_PRESETS } from '@graphene/ui';
	import { untrack } from 'svelte';

	let {
		value = $bindable(null),
		fieldType = 'text'
	}: {
		value?: FieldValidation | null;
		fieldType?: string;
	} = $props();

	const showLength = $derived(['text', 'textarea', 'email'].includes(fieldType));
	const showRows = $derived(fieldType === 'textarea');
	const showBounds = $derived(['number', 'currency', 'percentage', 'money'].includes(fieldType));

	const PATTERN_OPTIONS = PATTERN_PRESETS.map((p) => ({ value: p.pattern, label: p.label }));

	// Working copy as strings (inputs); numbers are coerced on emit.
	const init = untrack(() => value ?? {});
	let placeholder = $state(init.placeholder ?? '');
	let errorMessage = $state(init.errorMessage ?? '');
	let minLength = $state(init.minLength?.toString() ?? '');
	let maxLength = $state(init.maxLength?.toString() ?? '');
	let rows = $state(init.rows?.toString() ?? '');
	let min = $state(init.min?.toString() ?? '');
	let max = $state(init.max?.toString() ?? '');
	let pattern = $state(init.pattern ?? '');
	let patternMessage = $state(init.patternMessage ?? '');

	let tab = $state('builder');
	let jsonText = $state(JSON.stringify(value ?? null, null, 2));
	let jsonError = $state('');

	function num(s: string): number | undefined {
		const n = Number(s);
		return s.trim() !== '' && Number.isFinite(n) ? n : undefined;
	}

	function emit() {
		const out: FieldValidation = {};
		// Preserve any legacy `conditional` block already on the value (visibility
		// lives in Conditions, but we must not drop it if imported data carries it).
		if (value && value.conditional) out.conditional = value.conditional;
		if (placeholder.trim()) out.placeholder = placeholder.trim();
		if (errorMessage.trim()) out.errorMessage = errorMessage.trim();
		if (showLength) {
			if (num(minLength) !== undefined) out.minLength = num(minLength);
			if (num(maxLength) !== undefined) out.maxLength = num(maxLength);
			if (pattern.trim()) out.pattern = pattern.trim();
			if (patternMessage.trim()) out.patternMessage = patternMessage.trim();
		}
		if (showRows && num(rows) !== undefined) out.rows = num(rows);
		if (showBounds) {
			if (num(min) !== undefined) out.min = num(min);
			if (num(max) !== undefined) out.max = num(max);
		}
		const next = Object.keys(out).length ? out : null;
		value = next;
		jsonText = JSON.stringify(next ?? null, null, 2);
		jsonError = '';
	}

	function applyPreset(preset: { pattern: string; message: string }) {
		pattern = preset.pattern;
		// Fill a message only if the author hasn't written their own.
		if (!patternMessage.trim()) patternMessage = preset.message;
		emit();
	}

	function applyJson() {
		try {
			const parsed = jsonText.trim() ? JSON.parse(jsonText) : null;
			value = parsed as FieldValidation | null;
			const v = parsed ?? {};
			placeholder = v.placeholder ?? '';
			errorMessage = v.errorMessage ?? '';
			minLength = v.minLength?.toString() ?? '';
			maxLength = v.maxLength?.toString() ?? '';
			rows = v.rows?.toString() ?? '';
			min = v.min?.toString() ?? '';
			max = v.max?.toString() ?? '';
			pattern = v.pattern ?? '';
			patternMessage = v.patternMessage ?? '';
			jsonError = '';
		} catch {
			jsonError = 'Invalid JSON.';
		}
	}
</script>

<Tabs.Root bind:value={tab}>
	<Tabs.List class="grid w-full grid-cols-2">
		<Tabs.Trigger value="builder">Builder</Tabs.Trigger>
		<Tabs.Trigger value="json">JSON</Tabs.Trigger>
	</Tabs.List>

	<Tabs.Content value="builder" class="space-y-3 pt-3">
		<div class="space-y-1.5">
			<Label>Placeholder</Label>
			<Input bind:value={placeholder} oninput={emit} placeholder="Shown when empty" />
		</div>

		{#if showLength}
			<div class="grid grid-cols-2 gap-3">
				<div class="space-y-1.5">
					<Label>Min length</Label>
					<Input type="number" bind:value={minLength} oninput={emit} />
				</div>
				<div class="space-y-1.5">
					<Label>Max length</Label>
					<Input type="number" bind:value={maxLength} oninput={emit} />
				</div>
			</div>
			<div class="space-y-1.5">
				<Label>Pattern (regex)</Label>
				<Segmented
					options={PATTERN_OPTIONS}
					value={pattern}
					onValueChange={(v) => {
						const preset = PATTERN_PRESETS.find((p) => p.pattern === v);
						if (preset) applyPreset(preset);
					}}
				/>
				<Input
					class="font-mono text-sm"
					bind:value={pattern}
					oninput={emit}
					placeholder="^[A-Z]+$"
				/>
			</div>
			<div class="space-y-1.5">
				<Label>Pattern message</Label>
				<Input
					bind:value={patternMessage}
					oninput={emit}
					placeholder="Shown when the pattern fails"
				/>
			</div>
		{/if}

		{#if showRows}
			<div class="space-y-1.5">
				<Label>Rows</Label>
				<Input type="number" bind:value={rows} oninput={emit} />
			</div>
		{/if}

		{#if showBounds}
			<div class="grid grid-cols-2 gap-3">
				<div class="space-y-1.5">
					<Label>Minimum value</Label>
					<Input type="number" bind:value={min} oninput={emit} />
				</div>
				<div class="space-y-1.5">
					<Label>Maximum value</Label>
					<Input type="number" bind:value={max} oninput={emit} />
				</div>
			</div>
		{/if}

		<div class="space-y-1.5">
			<Label>Error message</Label>
			<Input bind:value={errorMessage} oninput={emit} placeholder="Overrides the default message" />
		</div>
	</Tabs.Content>

	<Tabs.Content value="json" class="space-y-2 pt-3">
		<Textarea
			bind:value={jsonText}
			oninput={applyJson}
			rows={6}
			spellcheck={false}
			class="p-2 font-mono text-xs"
			aria-invalid={Boolean(jsonError)}
		/>
		{#if jsonError}<p class="text-destructive text-xs">{jsonError}</p>{/if}
		<p class="text-muted-foreground text-xs">
			Input constraints only — visibility belongs in Conditions.
		</p>
	</Tabs.Content>
</Tabs.Root>
