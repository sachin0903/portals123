<script lang="ts">
	import type { BrandingBlock } from '@graphene/auth';
	import CopyIcon from '@lucide/svelte/icons/copy';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import { Button } from '../ui/button/index.js';
	import { Input } from '../ui/input/index.js';
	import { Label } from '../ui/label/index.js';
	import { Textarea } from '../ui/textarea/index.js';
	import { Switch } from '../ui/switch/index.js';
	import Segmented from '../ui/segmented/segmented.svelte';
	import SelectMenu from '../ui/select-menu/select-menu.svelte';
	import { CLIENT_BLOCK_BY_TYPE, type BlockType, type SettingDescriptor } from './client-blocks.js';
	import { resolveVariant } from './variants.js';
	import {
		inspectorValue,
		parseNumberSetting,
		personaVariantContext,
		withSetting
	} from './pages-view.js';
	import type { Persona } from '../../fixtures/personas.js';

	// The per-block settings inspector, rendered FROM the registry's SettingDescriptor[]
	// (client-blocks.ts) — declarative, so a new block setting needs no inspector change. NOT the
	// flows DynamicForm: this is a handful of flat controls, purpose-built. Every edit emits the
	// WHOLE next settings object; the parent replaces the block wholesale.
	let {
		block,
		canManage = true,
		persona,
		pageSlug,
		onchange,
		onduplicate,
		onremove
	}: {
		block: BrandingBlock;
		canManage?: boolean;
		/** Drives the 'auto' variant readout — the resolution shown is THIS persona's. */
		persona: Persona;
		pageSlug: string;
		onchange: (settings: Record<string, unknown>) => void;
		onduplicate: () => void;
		onremove: () => void;
	} = $props();

	const meta = $derived(CLIENT_BLOCK_BY_TYPE[block.type]);
	const ctx = $derived(personaVariantContext(persona, pageSlug));

	function set(d: SettingDescriptor, value: unknown) {
		onchange(withSetting(block.settings, d.key, value));
	}

	// The caption under a 'variant' control while it sits on Auto: what auto would pick for the
	// active persona, so a firm sees the consequence of leaving it alone. Null when a concrete
	// variant is pinned (nothing to explain) or the type has a single look (nothing to pick).
	function autoCaption(d: SettingDescriptor): string | null {
		if (d.key !== 'variant' || !meta) return null;
		const stored = block.settings?.[d.key];
		if (typeof stored === 'string' && stored !== 'auto' && meta.variants.includes(stored))
			return null;
		const resolved = resolveVariant(meta.type as BlockType, block.settings, ctx);
		if (resolved === 'auto') return null;
		const label = d.options?.find((o) => o.value === resolved)?.label ?? resolved;
		return `Auto — currently ‘${label}’ for ${persona.label}`;
	}
</script>

<div class="space-y-4">
	<div>
		<p class="text-sm font-semibold">{meta?.label ?? block.type}</p>
		{#if meta}
			<p class="text-muted-foreground text-xs">{meta.description}</p>
		{/if}
	</div>

	{#if meta && meta.settings.length > 0}
		<div class="space-y-3.5">
			{#each meta.settings as d (d.key)}
				{@const value = inspectorValue(d, block.settings)}
				{@const caption = autoCaption(d)}
				<div class="space-y-1.5">
					{#if d.control === 'switch'}
						<div class="flex items-center justify-between gap-2">
							<Label class="text-xs">{d.label}</Label>
							<Switch
								class="scale-90"
								checked={value === true}
								disabled={!canManage}
								aria-label={d.label}
								onCheckedChange={(v) => set(d, v)}
							/>
						</div>
					{:else if d.control === 'segmented'}
						<Label class="text-xs">{d.label}</Label>
						<Segmented
							value={value as string}
							disabled={!canManage}
							onValueChange={(v) => set(d, v)}
							options={d.options ?? []}
						/>
					{:else if d.control === 'select'}
						<Label class="text-xs" for={`bi-${block.id}-${d.key}`}>{d.label}</Label>
						<SelectMenu
							id={`bi-${block.id}-${d.key}`}
							size="sm"
							value={value as string}
							disabled={!canManage}
							items={d.options ?? []}
							onValueChange={(v) => set(d, v)}
						/>
					{:else if d.control === 'number'}
						<Label class="text-xs" for={`bi-${block.id}-${d.key}`}>{d.label}</Label>
						<Input
							id={`bi-${block.id}-${d.key}`}
							type="number"
							class="h-8"
							min={d.min}
							max={d.max}
							value={value === '' ? '' : String(value)}
							disabled={!canManage}
							oninput={(e) => set(d, parseNumberSetting(e.currentTarget.value, d))}
						/>
					{:else if d.control === 'textarea'}
						<Label class="text-xs" for={`bi-${block.id}-${d.key}`}>{d.label}</Label>
						<Textarea
							id={`bi-${block.id}-${d.key}`}
							rows={3}
							maxlength={d.maxLength}
							value={value as string}
							disabled={!canManage}
							oninput={(e) => set(d, e.currentTarget.value)}
						/>
					{:else}
						<Label class="text-xs" for={`bi-${block.id}-${d.key}`}>{d.label}</Label>
						<Input
							id={`bi-${block.id}-${d.key}`}
							class="h-8"
							maxlength={d.maxLength}
							value={value as string}
							disabled={!canManage}
							oninput={(e) => set(d, e.currentTarget.value)}
						/>
					{/if}
					{#if caption}
						<p class="text-muted-foreground text-[0.7rem] leading-snug">{caption}</p>
					{:else if d.help}
						<p class="text-muted-foreground text-[0.7rem] leading-snug">{d.help}</p>
					{/if}
				</div>
			{/each}
		</div>
	{:else}
		<p class="text-muted-foreground text-xs">This block has no settings.</p>
	{/if}

	{#if canManage}
		<!-- Duplicate lives here (not on the tile): one affordance, next to the block it copies. -->
		<div class="flex items-center gap-2 border-t pt-3">
			<Button variant="outline" size="sm" class="gap-1.5" onclick={onduplicate}>
				<CopyIcon class="size-3.5" />
				Duplicate
			</Button>
			<Button
				variant="ghost"
				size="sm"
				class="text-destructive hover:text-destructive gap-1.5"
				onclick={onremove}
			>
				<Trash2Icon class="size-3.5" />
				Remove
			</Button>
		</div>
	{/if}
</div>
