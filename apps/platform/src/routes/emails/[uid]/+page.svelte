<script lang="ts">
	import { MessageDetail } from '@graphene/ui/comms';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
</script>

<svelte:head><title>{data.message.subject}</title></svelte:head>

<!-- The page scrolls: the preview is a fixed pane and must not be clipped to the viewport the way
     the listing shell is. Negative margin still cancels the shell padding so the edges line up. -->
<div class="-m-4 flex flex-col gap-3 p-4 md:-m-6 md:p-6">
	<a
		class="text-muted-foreground shrink-0 text-sm underline-offset-2 hover:underline"
		href="/emails"
	>
		← Activity
	</a>
	<MessageDetail message={data.message} clientHref={(uid) => `/clients/${uid}`} />
</div>
