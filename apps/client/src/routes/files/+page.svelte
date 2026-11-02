<script lang="ts">
	import { Button, EmptyState } from '@graphene/ui';
	import FileTextIcon from '@lucide/svelte/icons/file-text';
</script>

<!-- The server load redirects to /documents (files list there as "Files on record"); this never
     renders. Kept only so the route resolves as a page — /files/[uid]/download is unchanged. -->
<EmptyState
	icon={FileTextIcon}
	title="Your documents"
	description="View and download your files from Documents."
>
	{#snippet action()}
		<Button href="/documents">View documents</Button>
	{/snippet}
</EmptyState>
