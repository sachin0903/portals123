<!-- The server load redirects to /documents (files list alongside documents there now); this never
     renders. Kept only so the route resolves as a page — its ?/upload and ?/delete actions are
     still posted to by the merged Documents section. -->
<script lang="ts">
	import { Button, EmptyState } from '@graphene/ui';
	import FolderOpenIcon from '@lucide/svelte/icons/folder-open';
</script>

<EmptyState
	icon={FolderOpenIcon}
	title="Redirecting to documents"
	description="Files now live alongside your documents."
	class="mx-auto mt-16 max-w-md"
>
	{#snippet action()}
		<Button href="/documents" variant="outline">Go to documents</Button>
	{/snippet}
</EmptyState>
