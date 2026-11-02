<script lang="ts">
	import { deserialize } from '$app/forms';
	import { invalidateAll } from '$app/navigation';
	import { Card } from '@graphene/ui/identity-capture-ui';
	import IdentityVerification from '$lib/components/onboarding/identity-verification.svelte';
	import {
		IDENTITY_CAPTURE_KEYS,
		SUBMIT_FAILED,
		submitError,
		type IdentityCaptureKey,
		type IdentityDocumentType
	} from '$lib/identity-capture';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	/** What was last told to the computer, so the same answer is not sent twice. */
	let lastTaken = '';

	/** Tell the computer waiting on this phone what has been taken. Best effort: a miss only
	 *  delays its list, and the photos themselves go together when they are sent. */
	function ontaken(keys: IdentityCaptureKey[], documentType: IdentityDocumentType | null): void {
		const said = JSON.stringify([keys, documentType]);
		if (said === lastTaken) return;
		lastTaken = said;
		const body = new FormData();
		for (const key of keys) body.append('key', key);
		if (documentType) body.append('document_type', documentType);
		void fetch('/i?/taken', { method: 'POST', body }).catch(() => {
			lastTaken = '';
		});
	}

	/** Name the document and send the photos, in one request, as the signed-in page does. */
	async function send(
		documentType: IdentityDocumentType,
		photos: Partial<Record<IdentityCaptureKey, File>>
	): Promise<string | null> {
		const body = new FormData();
		body.append('document_type', documentType);
		for (const key of IDENTITY_CAPTURE_KEYS) {
			const file = photos[key];
			if (file) body.append(key, file, file.name);
		}
		let error: string | null;
		try {
			const res = await fetch('/i?/submit', { method: 'POST', body });
			error = submitError(deserialize(await res.text()));
		} catch {
			// The request never landed: the photos are still here to send again.
			error = SUBMIT_FAILED;
		}
		if (!error) await invalidateAll();
		return error;
	}
</script>

<svelte:head><title>Verify your identity</title></svelte:head>

<!-- The same card the portal puts its own pages on, so the phone reads as the portal it came from
     rather than as a page floating on the sign-in backdrop. -->
<Card class="w-full gap-4 p-5">
	<div class="space-y-1">
		<h1 class="text-xl font-semibold">Verify your identity</h1>
		<p class="text-muted-foreground text-sm">
			Take the photos on this phone. No sign-in is necessary.
		</p>
	</div>
	{#if data.capture}
		<IdentityVerification capture={data.capture} {send} {ontaken} />
	{:else if data.unavailable}
		<p class="text-muted-foreground text-sm" role="alert">
			The photo page could not be opened. Check the connection and try the link again.
		</p>
	{:else}
		<p class="text-muted-foreground text-sm">
			This link has already been used or has expired. Go back to the other device and ask for a new
			one.
		</p>
	{/if}
</Card>
