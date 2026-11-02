<script lang="ts">
	/**
	 * The top-edge navigation progress bar.
	 *
	 * SvelteKit renders the OLD page for the whole of the new page's `load` — so on a book surface
	 * whose loader talks to the API and the lake, a sidebar click produced no visible change for the
	 * best part of a second and read as a dropped click. This is the acknowledgement: it mounts on
	 * the click and unmounts when the navigation settles, and it is deliberately the only chrome that
	 * fires for EVERY navigation, including a same-page filter change (see saas-shell — the content
	 * dim is reserved for a real page change, or typing in a listing search box would strobe it).
	 *
	 * Indeterminate on purpose: nothing here knows how far along a `load` is. The curve is front-
	 * loaded and asymptotic — most of the width is spent in the first 300ms, then it creeps and never
	 * reaches the end, so a slow load still shows movement without ever promising it is nearly done.
	 */
	let { active = false }: { active?: boolean } = $props();
</script>

{#if active}
	<div
		class="pointer-events-none fixed inset-x-0 top-0 z-50 h-[3px]"
		role="progressbar"
		aria-label="Loading the page"
	>
		<!-- The class is a LITERAL, not composed through `cn()`: Svelte prunes a scoped style whose
		     selector it cannot see in the markup, and a pruned animation leaves a static bar that
		     every other test still passes (the `risk-badge` lesson). -->
		<div class="nav-progress bg-primary h-full w-full origin-left"></div>
	</div>
{/if}

<style>
	.nav-progress {
		animation: nav-progress 6s linear forwards;
		box-shadow: 0 0 6px 0 var(--primary);
	}

	/* The stops carry the easing (hence `linear` above): 0.4 of the bar in the first 200ms, 0.7 by
	   700ms, then a slow crawl to 0.97 that never completes. */
	@keyframes nav-progress {
		0% {
			transform: scaleX(0.04);
		}
		3.3% {
			transform: scaleX(0.4);
		}
		11.6% {
			transform: scaleX(0.7);
		}
		100% {
			transform: scaleX(0.97);
		}
	}

	/* Reduced motion still needs the signal — it is the answer to "did my click land" — so the bar
	   is present and static rather than absent. */
	@media (prefers-reduced-motion: reduce) {
		.nav-progress {
			animation: none;
			transform: scaleX(0.35);
		}
	}
</style>
