<script lang="ts">
	import { Badge, Button, cn, FIELD_WRAPPER_CLASS } from '@graphene/ui';
	import XIcon from '@lucide/svelte/icons/x';
	import {
		allowlistPasteSummary,
		canonicalAllowlistEntry,
		mergeAllowlistPaste
	} from '$lib/allowlist-entries';

	let {
		initial,
		id = 'email-allowlist',
		readonly = false
	}: {
		initial: string[];
		id?: string;
		readonly?: boolean;
	} = $props();

	let entries = $state<string[]>([]);
	let draft = $state('');
	let note = $state<string | null>(null);
	let invalid = $state(false);

	$effect(() => {
		entries = [...initial];
		draft = '';
		note = null;
		invalid = false;
	});

	function commit(raw: string) {
		const entry = canonicalAllowlistEntry(raw);
		if (!raw.trim()) return;
		if (!entry) {
			invalid = true;
			return;
		}
		invalid = false;
		const merged = mergeAllowlistPaste(entries, entry);
		entries = merged.entries;
		draft = '';
		note = merged.duplicate ? 'Already listed.' : null;
	}

	function absorb(raw: string) {
		const merged = mergeAllowlistPaste(entries, raw);
		entries = merged.entries;
		note = allowlistPasteSummary(merged);
		invalid = merged.rejected > 0 && merged.added === 0;
		draft = '';
	}

	function onPaste(event: ClipboardEvent) {
		const text = event.clipboardData?.getData('text') ?? '';
		if (!/[\s,;]/.test(text)) return;
		event.preventDefault();
		absorb(text);
	}

	function onInput(event: Event) {
		const value = (event.currentTarget as HTMLInputElement).value;
		if (/[\s,;]/.test(value)) absorb(value);
	}

	function onKeydown(event: KeyboardEvent) {
		if (event.key === 'Enter' || event.key === ',') {
			event.preventDefault();
			commit(draft);
			return;
		}
		if (event.key === 'Backspace' && !draft && entries.length) {
			entries = entries.slice(0, -1);
			note = null;
		}
	}
</script>

<div class="space-y-2">
	<!-- The posted value. One field, new-line separated, which the action already splits. -->
	<input type="hidden" name="entries" value={entries.join('\n')} />
	<div
		class={cn(
			FIELD_WRAPPER_CLASS,
			'flex h-auto min-h-16 flex-wrap content-start items-center gap-1.5 py-2',
			invalid && 'border-destructive ring-destructive/20 ring-3'
		)}
	>
		{#each entries as entry (entry)}
			<Badge variant="secondary" class="gap-1 pr-0.5 font-normal">
				<span>{entry}</span>
				{#if !readonly}
					<Button
						type="button"
						variant="ghost"
						size="icon-xs"
						class="size-5"
						aria-label="Remove {entry}"
						onclick={() => {
							entries = entries.filter((item) => item !== entry);
							note = null;
						}}
					>
						<XIcon />
					</Button>
				{/if}
			</Badge>
		{/each}
		{#if !readonly}
			<input
				{id}
				class="placeholder:text-muted-foreground min-w-40 flex-1 bg-transparent text-base outline-none md:text-sm"
				placeholder={entries.length ? 'Add or paste more' : 'Paste addresses or domains'}
				bind:value={draft}
				aria-invalid={invalid}
				onpaste={onPaste}
				oninput={onInput}
				onkeydown={onKeydown}
				onblur={() => commit(draft)}
			/>
		{/if}
	</div>
	{#if note}
		<p class="text-muted-foreground text-xs">{note}</p>
	{/if}
	{#if invalid}
		<p class="text-destructive text-xs">Enter an address or a domain, like ada@firm.test.</p>
	{/if}
</div>
