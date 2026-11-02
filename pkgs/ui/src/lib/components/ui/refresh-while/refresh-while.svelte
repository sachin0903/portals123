<script lang="ts">
	// Asks for the page again while `active`, and only while the tab is visible. The load that
	// mounted this already has a fresh answer, so the first tick waits out the interval; coming
	// back to a hidden tab asks at once. Renders nothing.
	import { untrack } from 'svelte';
	import { pollWhileVisible } from '../../notifications/poll-controller.js';

	let {
		active,
		interval,
		onpoll
	}: {
		/** Still waiting on a result that arrives out of band. False stops the timer. */
		active: boolean;
		interval: number;
		onpoll: () => void;
	} = $props();

	$effect(() => {
		if (!active || typeof document === 'undefined') return;
		const ms = interval;
		// `onpoll` is read when the tick fires, not while this effect runs, so a fresh arrow
		// from the parent does not restart the timer on every render.
		return pollWhileVisible(ms, () => untrack(onpoll));
	});
</script>
