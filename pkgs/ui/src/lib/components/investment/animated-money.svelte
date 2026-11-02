<script lang="ts">
	// The headline figure with a one-time settle-in: on first mount it rises from 90% of the
	// value into place (~280ms, cubicOut) — see count-up.ts for the maths and the WHY of 90%.
	//
	// SSR-safe by construction: the server (and the very first client frame) renders the FINAL
	// amount — `running` only flips inside $effect, which never runs during SSR — so there is no
	// hydration flash and a crawler/print view sees the true figure. Under prefers-reduced-motion,
	// or when the surface is inert (`animate === false`, e.g. the studio preview), the effect
	// bails and the final state simply stands.
	import { Tween, prefersReducedMotion } from 'svelte/motion';
	import { cubicOut } from 'svelte/easing';
	import { Money } from '../ui/money/index.js';
	import { COUNT_UP_MS, countUpAt } from './count-up.js';

	let {
		amount,
		animate = true,
		class: className,
		minorClass
	}: {
		amount: number;
		/** False renders the final figure statically (previews, reduced-capability surfaces). */
		animate?: boolean;
		class?: string;
		/** Forwarded to Money — the pence styling differs between hero ink and page ground. */
		minorClass?: string;
	} = $props();

	const t = new Tween(0, { easing: cubicOut });
	let running = $state(false);
	$effect(() => {
		if (!animate || prefersReducedMotion.current) return;
		running = true;
		t.set(1, { duration: COUNT_UP_MS });
	});

	const shown = $derived(running ? countUpAt(amount, t.current) : amount);
</script>

<Money amount={shown} class={className} {minorClass} />
