<script lang="ts">
	/**
	 * The panel that hands over a member's set-password link — without ever putting it on screen.
	 *
	 * The URL sets somebody's password, so it is treated as a credential in transit rather than as
	 * page content: it goes to the clipboard on request and this page's copy is dropped the instant
	 * it is copied, or when the countdown runs out. Nothing renders it, so a screenshot, a
	 * screen-share or a passer-by cannot capture it. Minting it is audited server-side with the
	 * actor (`POST /admin/tenants/{id}/members/{uid}/invitation-link`).
	 *
	 * The one case that has to stay workable is a clipboard the browser won't give us (a non-secure
	 * context, or a denied permission). Then — and only then — the operator gets a deliberate
	 * second click to show the link, because otherwise this control would look like it worked and
	 * hand over nothing.
	 */
	import { Alert, AlertDescription, AlertTitle, Button, Input, toast } from '@graphene/ui';
	import KeyRoundIcon from '@lucide/svelte/icons/key-round';
	import { copyToClipboard, countdown, isExpired, remainingMs, type Reveal } from '$lib/invitation';

	let {
		reveal,
		who = null,
		onclear
	}: {
		reveal: Reveal;
		/** Who the link belongs to (an email), so the panel can name them. */
		who?: string | null;
		/** Drop the caller's copy of the url — on copy, on discard, or on expiry. */
		onclear: () => void;
	} = $props();

	let now = $state(Date.now());
	let copying = $state(false);
	// The escape hatch is not offered up-front: an always-available "show it" would just be the old
	// on-screen credential with an extra click. It appears only once a copy has actually failed.
	let copyFailed = $state(false);
	let shown = $state(false);

	// Tick the countdown, and clear the reveal the moment it lapses — the timer is the enforcement,
	// not just the label.
	$effect(() => {
		const timer = setInterval(() => {
			now = Date.now();
			if (isExpired(reveal, now)) onclear();
		}, 1000);
		return () => clearInterval(timer);
	});

	const left = $derived(countdown(remainingMs(reveal, now)));

	async function copy(): Promise<void> {
		copying = true;
		const ok = await copyToClipboard(reveal.url);
		copying = false;
		if (ok) {
			toast.success('Invitation link copied. It is no longer held on this page.');
			onclear();
			return;
		}
		copyFailed = true;
		toast.error('Clipboard access was denied. Show the link and copy it manually.');
	}
</script>

<Alert variant="warning">
	<KeyRoundIcon />
	<AlertTitle>
		Invitation link ready{#if who}
			for {who}{/if}
	</AlertTitle>
	<AlertDescription class="space-y-2">
		<p>
			This link sets the member's password, so it is copied directly to your clipboard and removed
			from this page when the countdown ends. Send it through a trusted channel. It replaces any
			earlier link, and generating it is recorded against your account.
		</p>
		{#if shown}
			<!-- Clipboard-failure escape hatch only (see the component comment). -->
			<Input readonly value={reveal.url} class="w-full" aria-label="Invitation link" />
		{/if}
		<div class="flex flex-wrap items-center gap-2">
			<Button type="button" variant="secondary" size="sm" disabled={copying} onclick={copy}>
				{copying ? 'Copying…' : 'Copy to clipboard'}
			</Button>
			{#if copyFailed && !shown}
				<Button type="button" variant="ghost" size="sm" onclick={() => (shown = true)}>
					Show it instead
				</Button>
			{/if}
			<Button type="button" variant="ghost" size="sm" onclick={onclear}>Discard now</Button>
			<span class="text-muted-foreground ml-auto text-xs tabular-nums">Discarded in {left}</span>
		</div>
	</AlertDescription>
</Alert>
