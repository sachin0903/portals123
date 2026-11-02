<script lang="ts">
	import {
		CLIENT_HEADER_BUTTON_ACTIVE_CLASS,
		CLIENT_HEADER_BUTTON_CLASS,
		CLIENT_HEADER_ICON_CLASS,
		cn
	} from '@graphene/ui';
	import MessageCircleIcon from '@lucide/svelte/icons/message-circle';
	import { page } from '$app/state';

	// The header's shared liquid-glass round (search and the bell wear the same one); lit like
	// the tab bar's active pill only while messages IS the open page.
	const active = $derived(page.url.pathname.startsWith('/messages'));
</script>

<a
	href="/messages"
	aria-label="Messages"
	title="Messages"
	aria-current={active ? 'page' : undefined}
	class={cn(CLIENT_HEADER_BUTTON_CLASS, active && CLIENT_HEADER_BUTTON_ACTIVE_CLASS)}
>
	<MessageCircleIcon class={CLIENT_HEADER_ICON_CLASS} />
</a>
