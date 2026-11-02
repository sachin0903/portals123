<script lang="ts">
	// "Upload new photos": offered while the API allows an upload; otherwise disabled, with the
	// API's reason beside it.
	import UploadIcon from '@lucide/svelte/icons/upload';
	import { Button, type ButtonVariant } from '../../ui/button/index.js';
	import type { IdentityUpload } from './identity-upload.js';

	let {
		upload,
		onclick,
		variant = 'ghost'
	}: { upload: IdentityUpload; onclick: () => void; variant?: ButtonVariant } = $props();

	const reasonId = $props.id();
	const reason = $derived(!upload.allowed ? upload.blocked : null);
</script>

<Button
	{variant}
	size="sm"
	disabled={!upload.allowed}
	aria-describedby={reason ? reasonId : undefined}
	{onclick}
>
	<UploadIcon class="size-4" /> Upload new photos
</Button>
{#if reason}
	<p id={reasonId} class="text-muted-foreground text-xs">{reason}</p>
{/if}
