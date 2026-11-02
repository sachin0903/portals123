<script lang="ts">
	/**
	 * The bell, for a layout that STREAMS its seed.
	 *
	 * A root layout used to await `GET /notifications` before it could return, which put the bell's
	 * rows on the critical path to the first byte of every page. Returning them as a promise lets the
	 * HTML go out at once — but the bell cannot simply be handed a promise, because it records the
	 * rows it is MOUNTED with as already announced (`announced`, seeded under `untrack`) so that
	 * signing in does not replay the backlog as a wall of pop-up cards. Mounted empty and handed the
	 * rows a moment later, it would read every unread one as an arrival.
	 *
	 * So the live bell is mounted only once the seed has LANDED, and a static bell of the same box
	 * (`size-8`, the trigger's own) holds the place until then, so the topbar does not shift when the
	 * live one takes over. Later answers — each navigation streams a fresh one — flow through as prop
	 * updates to the already-mounted bell, exactly as the synchronous values did.
	 *
	 * Plain values are rendered immediately, INCLUDING during SSR: a layout that does not stream (the
	 * client portal's) gets the bell it had, with no placeholder flash. The same rule `Streamed` keeps.
	 */
	import { untrack, type ComponentProps } from 'svelte';
	import BellIcon from '@lucide/svelte/icons/bell';
	import NotificationBell from './notification-bell.svelte';
	import type { NotificationItem } from './describe.js';

	type Seed = { items: NotificationItem[]; unread: number };
	type BellProps = ComponentProps<typeof NotificationBell>;

	let {
		items = [],
		unreadCount = 0,
		...rest
	}: Omit<BellProps, 'items' | 'unreadCount'> & {
		/** The loader's rows — streamed, or already in hand. */
		items?: NotificationItem[] | Promise<NotificationItem[]>;
		/** The loader's unread count — streamed, or already in hand. */
		unreadCount?: number | Promise<number>;
	} = $props();

	const settled = (list: typeof items, count: typeof unreadCount): Seed | null =>
		list instanceof Promise || count instanceof Promise
			? null
			: { items: list ?? [], unread: count ?? 0 };

	// Seeded synchronously for plain values — the only case SSR can render — else null until the
	// promises resolve. `untrack`: a one-time read; the effect below is what tracks the props.
	let seed = $state<Seed | null>(untrack(() => settled(items, unreadCount)));

	$effect(() => {
		const list = items;
		const count = unreadCount;
		const now = settled(list, count);
		if (now) {
			seed = now;
			return;
		}
		let live = true;
		void Promise.all([list, count]).then(
			([resolvedItems, resolvedCount]) => {
				if (live) seed = { items: resolvedItems ?? [], unread: resolvedCount ?? 0 };
			},
			() => {
				// The portals' loaders never reject these (they fail open to an empty bell), but a bell
				// that never mounts would be worse than an empty one: mount it empty rather than leave
				// the placeholder up for the whole session.
				if (live && !seed) seed = { items: [], unread: 0 };
			}
		);
		// A navigation that supersedes this one must not have its answer overwritten by the older
		// promise settling late.
		return () => {
			live = false;
		};
	});
</script>

{#if seed}
	<NotificationBell items={seed.items} unreadCount={seed.unread} {...rest} />
{:else}
	<!-- Inert on purpose: a popover that opened onto "No notifications" for a moment would be a lie. -->
	<span
		class="text-muted-foreground flex size-8 items-center justify-center"
		aria-hidden="true"
		data-notification-bell-placeholder
	>
		<BellIcon class={rest.iconClass ?? 'size-4'} />
	</span>
{/if}
