<script lang="ts">
	import type { Snippet } from 'svelte';
	import Logo from '../brand/logo.svelte';
	import type { Brand } from './types.js';

	let {
		brand,
		tagline,
		children
	}: {
		/** Per-tenant white-label brand (logo, name, legal). Unset ⇒ Graphene default. */
		brand?: Brand;
		/** Understated descriptive line shown on the brand side (overrides the brand's tagline). */
		tagline?: string;
		children?: Snippet;
	} = $props();

	const heroTagline = $derived(
		tagline ??
			brand?.tagline ??
			'Your accounts, holdings and documents — together in one considered place.'
	);
	const legal = $derived(
		brand?.legal ??
			'Graphene Platforms Ltd is authorised and regulated by the Financial Conduct Authority.'
	);
</script>

<!-- Liquid-glass scene that follows the theme: the ambient brand canvas behind,
     with the form floating in a frosted glass card that refracts it. -->
<div class="glass-canvas relative flex min-h-svh items-center justify-center overflow-hidden p-6">
	<!-- A hint of the brand for the glass to refract — a hint, not a field. The washes used to
	     be 34–60% of the brand colour, so the page ground became a similar colour to the buttons
	     and the brand looked overridden by its own background. Plain radial gradients (no CSS
	     blur filter) so liquidGL's html2canvas snapshot captures them faithfully.
	     Decorative + behind everything; the frosted card softens them further.

	     Every wash is mixed from --brand / --primary, the same two tokens the `glass-canvas`
	     utility composes, so login re-themes with the firm instead of showing Graphene's teal and
	     indigo behind someone else's logo — this is the most brand-visible screen in the product.
	     They were four hardcoded rgba stops; the comment justifying that claimed theme colours
	     could not be gated by --glass-canvas-strength, which was never the case (the gate is the
	     `opacity` below, and it still applies). A portal on the sharp surface preset
	     (@graphene/ui/sharp.css sets the strength to 0) still gets a flat canvas here.

	     No --positive/--negative: the fourth wash was green, but a decorative field carries no
	     gain/loss meaning, so it takes --primary rather than borrowing a semantic token. -->
	<div
		class="pointer-events-none absolute inset-0"
		aria-hidden="true"
		style="opacity: var(--glass-canvas-strength, 1); background:
			radial-gradient(38% 34% at 16% 20%, color-mix(in oklch, var(--brand) 16%, transparent), transparent 70%),
			radial-gradient(44% 40% at 88% 26%, color-mix(in oklch, var(--primary) 12%, transparent), transparent 72%),
			radial-gradient(50% 46% at 42% 102%, color-mix(in oklch, var(--brand) 12%, transparent), transparent 72%),
			radial-gradient(36% 32% at 70% 78%, color-mix(in oklch, var(--primary) 10%, transparent), transparent 70%);"
	></div>

	<div class="relative grid w-full max-w-5xl items-center gap-y-10 lg:grid-cols-2 lg:gap-x-16">
		<!-- brand side — sits on the canvas (desktop) -->
		<div class="hidden max-w-md lg:block">
			<Logo
				class="text-foreground"
				markClass="h-9"
				src={brand?.logoUrl}
				darkSrc={brand?.logoDarkUrl}
				name={brand?.name}
			/>
			<p
				class="text-foreground/90 mt-10 text-2xl leading-snug font-medium tracking-tight text-balance"
			>
				{heroTagline}
			</p>
			<div class="text-muted-foreground mt-10 space-y-1.5 border-t pt-6 text-xs leading-relaxed">
				<p>{legal}</p>
			</div>
		</div>

		<!-- the page (login form / MFA card) supplies its own surface -->
		<div class="mx-auto w-full max-w-sm">
			<div class="mb-8 flex justify-center lg:hidden">
				<Logo
					class="text-foreground"
					markClass="h-8"
					src={brand?.logoUrl}
					darkSrc={brand?.logoDarkUrl}
					name={brand?.name}
				/>
			</div>
			{@render children?.()}
		</div>
	</div>
</div>
