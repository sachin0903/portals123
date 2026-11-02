<script lang="ts">
	// Event stream. Entries carrying a real timestamp (`t`, live datasets) render as
	// the actual log, newest first. Without one (concept fixtures) the feed simulates
	// a live stream — cycling the script, prepending an entry every few seconds
	// against a fake clock (static list under reduced motion).
	import { onMount } from 'svelte';
	import { prefersReducedMotion } from 'svelte/motion';
	import type { FeedEvent, FeedEventKind } from '../types';

	let { script }: { script: FeedEvent[] } = $props();

	const live = $derived(script.some((e) => e.t));

	const ICON: Record<FeedEventKind, string> = {
		succeeded: '✓',
		started: '▸',
		enqueued: '+',
		sink: '⇥',
		failed: '✕',
		flush: '≈',
		file: '⇣'
	};
	const ICON_CLASS: Record<FeedEventKind, string> = {
		succeeded: 'text-positive',
		started: 'text-brand',
		enqueued: 'text-muted-foreground',
		sink: 'text-primary',
		failed: 'text-destructive',
		flush: 'text-brand',
		file: 'text-primary'
	};

	interface Entry extends FeedEvent {
		id: number;
		time: string;
	}

	let entries = $state<Entry[]>([]);
	let scriptIndex = 0;
	let clock = 12 * 3600 + 3 * 60 + 3;
	let nextId = 0;

	const pad = (x: number) => String(x).padStart(2, '0');
	const ts = (s: number) =>
		`${pad(((s / 3600) | 0) % 24)}:${pad(((s / 60) | 0) % 60)}:${pad(s % 60)}`;

	function push() {
		const event = script[scriptIndex % script.length]!;
		scriptIndex++;
		clock += 7 + ((scriptIndex * 13) % 40);
		entries = [{ ...event, id: nextId++, time: ts(clock) }, ...entries].slice(0, 16);
	}

	// Live feeds follow the dataset: an SSE event prepends a row and this list must
	// show it without waiting for the page to reload.
	$effect(() => {
		if (!live) return;
		entries = script.slice(0, 16).map((e, i) => ({ ...e, id: i, time: e.t ?? '' }));
	});

	onMount(() => {
		if (!script.length || live) return;
		for (let i = 0; i < 8; i++) push();
		if (prefersReducedMotion.current) return;
		const interval = setInterval(push, 3500);
		return () => clearInterval(interval);
	});
</script>

<div class="flex min-h-0 flex-col">
	<div
		class="text-muted-foreground flex items-center gap-2 border-b px-1 pb-2 text-[11px] font-medium"
	>
		<span class="relative flex size-2">
			<span
				class="bg-positive absolute inline-flex h-full w-full animate-ping rounded-full opacity-60 motion-reduce:animate-none"
			></span>
			<span class="bg-positive relative inline-flex size-2 rounded-full"></span>
		</span>
		Event stream
	</div>
	<div class="min-h-0 flex-1 space-y-1 overflow-y-auto pt-2">
		{#each entries as entry (entry.id)}
			<div class="flex items-baseline gap-2 px-1 font-mono text-[11px]">
				<span class="text-muted-foreground/70 shrink-0 tabular-nums">{entry.time}</span>
				<span class="{ICON_CLASS[entry.kind]} shrink-0">{ICON[entry.kind]}</span>
				<span class="text-muted-foreground min-w-0 truncate">
					<span class="text-foreground font-medium">{entry.model}</span>
					{entry.message}
				</span>
			</div>
		{/each}
	</div>
</div>
