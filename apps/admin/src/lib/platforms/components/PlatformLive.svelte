<script lang="ts">
	// One EventSource for the platform pages. The daemon publishes /events/stream; the
	// API copies it and this route is the only place the browser connects. Run and model
	// events patch the store immediately. A finished run also reloads the overview, because
	// row counts and the plan are not on the event.
	import { invalidateAll } from '$app/navigation';
	import { onMount } from 'svelte';
	import type { ApiEvent } from '../live';
	import type { PlatformStore } from '../store.svelte';

	let { slug, store }: { slug: string; store: PlatformStore } = $props();

	let state = $state<'connecting' | 'live'>('connecting');

	onMount(() => {
		let source: EventSource | null = null;
		let reconnect: ReturnType<typeof setTimeout> | undefined;
		let reload: ReturnType<typeof setTimeout> | undefined;
		let backoff = 1000;
		let stopped = false;
		let generation = 0;
		let lastSeq = store.data.eventSeq;

		function connect() {
			if (stopped) return;
			const mine = ++generation;
			// The snapshot already applied everything up to eventSeq. A reconnect
			// must not walk backwards over that, or the backlog's old
			// run.succeeded events schedule a full reload.
			lastSeq = Math.max(lastSeq, store.data.eventSeq);
			source?.close();
			source = new EventSource(`/platforms/${slug}/events?after=${lastSeq}`);
			source.onopen = () => {
				if (mine !== generation) return;
				state = 'live';
				backoff = 1000;
			};
			source.onmessage = (message) => {
				if (mine !== generation) return;
				let event: ApiEvent;
				try {
					event = JSON.parse(message.data) as ApiEvent;
				} catch {
					return;
				}
				if (typeof event.seq === 'number') lastSeq = event.seq;
				store.applyDaemonEvent(event);
				if (
					event.type === 'run.enqueued' ||
					event.type === 'run.succeeded' ||
					event.type === 'run.failed' ||
					event.type === 'run.cancelled' ||
					(event.type === 'run.cancel_requested' && event.payload?.state === 'cancelled') ||
					event.type === 'reset.finished'
				) {
					clearTimeout(reload);
					reload = setTimeout(() => void invalidateAll(), 800);
				}
			};
			source.onerror = () => {
				if (mine !== generation || stopped) return;
				state = 'connecting';
				source?.close();
				source = null;
				reconnect = setTimeout(connect, backoff);
				backoff = Math.min(backoff * 2, 30_000);
			};
		}

		connect();
		return () => {
			stopped = true;
			generation += 1;
			clearTimeout(reconnect);
			clearTimeout(reload);
			source?.close();
		};
	});
</script>

<p class="text-muted-foreground text-xs">
	{state === 'live' ? 'Live updates connected' : 'Reconnecting to pipeline events…'}
</p>
