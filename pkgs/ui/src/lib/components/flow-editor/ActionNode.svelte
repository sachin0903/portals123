<!--
	An ACTION node — work the engine does, unattended.

	It reads as a work node (a filled card, same family as a step) because it IS planned work: it
	becomes a step row, it has a status, and a failure lives on it. What differs is that nobody is
	assigned to it, so it wears the hue-less ink surface rather than an actor's colour, and its chip
	says "Automatic" instead of naming a person. Colour alone never carries that — the bolt icon and
	the word are both there, same rule as the actor fills in graph.ts.
-->
<script lang="ts">
	import { Handle, Position, type NodeProps } from '@xyflow/svelte';
	import ZapIcon from '@lucide/svelte/icons/zap';
	import TagIcon from '@lucide/svelte/icons/tag';
	import TagsIcon from '@lucide/svelte/icons/tags';
	import ListTodoIcon from '@lucide/svelte/icons/list-todo';
	import MailIcon from '@lucide/svelte/icons/mail';
	import UserCheckIcon from '@lucide/svelte/icons/user-check';
	import FlagIcon from '@lucide/svelte/icons/flag';
	import FileInputIcon from '@lucide/svelte/icons/file-input';
	import FileOutputIcon from '@lucide/svelte/icons/file-output';
	import SendIcon from '@lucide/svelte/icons/send';
	import WebhookIcon from '@lucide/svelte/icons/webhook';
	import { OUT_HANDLE } from '@graphene/ui/book/journeys';
	import { ACTOR_STYLE, detailForNode, type FlowNodeData } from './graph';
	import NodeIssueBadge from './NodeIssueBadge.svelte';

	let { data, selected }: NodeProps = $props();
	const d = $derived(data as FlowNodeData);

	const ICONS: Record<string, typeof ZapIcon> = {
		apply_tag: TagIcon,
		remove_tag: TagsIcon,
		raise_task: ListTodoIcon,
		send_email: MailIcon,
		assign_adviser: UserCheckIcon,
		set_case_status: FlagIcon,
		request_document: FileInputIcon,
		generate_document: FileOutputIcon,
		send_document: SendIcon,
		webhook_out: WebhookIcon
	};

	const style = ACTOR_STYLE.system;
	const Icon = $derived(ICONS[d.node.type ?? ''] ?? ZapIcon);
	const detail = $derived(detailForNode(d.node, d.lookups));
</script>

<div
	class="relative w-60 overflow-hidden rounded-xl shadow-md transition-shadow {selected
		? 'ring-background/80 ring-2 ring-offset-2 ring-offset-transparent'
		: 'hover:shadow-lg'}"
	style="background:{style.bg}; color: var(--hero-foreground)"
>
	<Handle
		type="target"
		position={Position.Top}
		class="!size-2.5 !border-2 !border-current/70 !bg-current"
	/>

	<div class="flex items-center gap-2 px-2.5 py-1.5" style="background:{style.head}">
		<span class="flex size-6 shrink-0 items-center justify-center rounded-md bg-current/15">
			<Icon class="size-3.5" />
		</span>
		<span class="text-[11px] font-semibold">Action</span>
		<span
			class="ml-auto flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-semibold"
			style="background:color-mix(in oklab, var(--hero-foreground) 18%, transparent)"
		>
			<ZapIcon class="size-3" /> Automatic
		</span>
	</div>

	<div class="space-y-1 px-2.5 py-2">
		<p class="text-sm leading-tight font-semibold">{d.node.title || d.node.id}</p>
		<p class="truncate text-xs" style="color:{style.sub}" title={detail}>{detail}</p>
	</div>

	<Handle
		id={OUT_HANDLE}
		type="source"
		position={Position.Bottom}
		class="!size-2.5 !border-2 !border-current/70 !bg-current"
	/>
	<NodeIssueBadge show={d.hasIssues} />
</div>
