<script lang="ts">
	// Right-click canvas menu (rationale in `canvas-menu.ts`). The click happens inside a GrapesJS iframe,
	// so the caller forwards it as a SYNTHETIC `contextmenu` with translated `clientX`/`clientY`; bits-ui
	// anchors content there (zero-size virtual element). Trigger is a zero-size span, not a canvas wrapper — it only receives the dispatched event, so its own geometry is never read.
	import * as ContextMenu from '../ui/context-menu/index.js';
	import {
		canvasMenuGroups,
		type CanvasMenuAction,
		type CanvasMenuSubject
	} from './canvas-menu.js';
	import type { MergeFieldGroup } from './merge-fields.js';
	import ChevronsUpIcon from '@lucide/svelte/icons/chevrons-up';
	import ArrowUpIcon from '@lucide/svelte/icons/arrow-up';
	import ArrowDownIcon from '@lucide/svelte/icons/arrow-down';
	import ChevronsDownIcon from '@lucide/svelte/icons/chevrons-down';
	import CopyIcon from '@lucide/svelte/icons/copy';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import EyeIcon from '@lucide/svelte/icons/eye';
	import EyeOffIcon from '@lucide/svelte/icons/eye-off';
	import BracesIcon from '@lucide/svelte/icons/braces';
	import FileTextIcon from '@lucide/svelte/icons/file-text';

	let {
		open = $bindable(false),
		subject,
		mergeFields = [],
		triggerRef = $bindable(null),
		onaction,
		oninsertField
	}: {
		open?: boolean;
		/** What was right-clicked; null until the menu has been opened once. */
		subject: CanvasMenuSubject | null;
		mergeFields?: readonly MergeFieldGroup[];
		triggerRef?: HTMLElement | null;
		onaction: (action: CanvasMenuAction) => void;
		oninsertField: (token: string) => void;
	} = $props();

	const groups = $derived(subject ? canvasMenuGroups(subject) : []);

	// One glyph per action so the menu is scannable; the four moves reuse the rail header's own icons.
	const ICONS: Record<CanvasMenuAction, typeof ArrowUpIcon> = {
		condition: EyeIcon,
		'condition-clear': EyeOffIcon,
		'move-top': ChevronsUpIcon,
		'move-up': ArrowUpIcon,
		'move-down': ArrowDownIcon,
		'move-bottom': ChevronsDownIcon,
		duplicate: CopyIcon,
		delete: Trash2Icon,
		'page-style': FileTextIcon
	};

	const fieldCount = $derived(mergeFields.reduce((n, group) => n + group.fields.length, 0));
</script>

<ContextMenu.Root bind:open>
	<ContextMenu.Trigger
		bind:ref={triggerRef}
		class="pointer-events-none absolute top-0 left-0 size-0"
		aria-hidden="true"
		tabindex={-1}
	/>
	<ContextMenu.Content class="min-w-48">
		{#if fieldCount > 0}
			<!-- First: the only item that puts something NEW in the email. Submenu, not a flat list — sixteen tokens in six groups would be the whole menu. -->
			<ContextMenu.Sub>
				<ContextMenu.SubTrigger>
					<BracesIcon />
					Insert field
				</ContextMenu.SubTrigger>
				<ContextMenu.SubContent class="max-h-80 overflow-y-auto">
					{#each mergeFields as group (group.group)}
						<ContextMenu.Group>
							<ContextMenu.GroupHeading>{group.group}</ContextMenu.GroupHeading>
							{#each group.fields as field (field.token)}
								<ContextMenu.Item onSelect={() => oninsertField(field.token)}>
									{field.label}
								</ContextMenu.Item>
							{/each}
						</ContextMenu.Group>
					{/each}
				</ContextMenu.SubContent>
			</ContextMenu.Sub>
		{/if}
		{#each groups as group, index (index)}
			<!-- Not before the first group — the field submenu above IS that group's first item. -->
			{#if index > 0}
				<ContextMenu.Separator />
			{/if}
			{#each group as item (item.action)}
				{@const Icon = ICONS[item.action]}
				<ContextMenu.Item variant={item.variant} onSelect={() => onaction(item.action)}>
					<Icon />
					{item.label}
					{#if item.shortcut}
						<ContextMenu.Shortcut>{item.shortcut}</ContextMenu.Shortcut>
					{/if}
				</ContextMenu.Item>
			{/each}
		{/each}
	</ContextMenu.Content>
</ContextMenu.Root>
