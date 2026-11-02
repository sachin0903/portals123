<script lang="ts">
	import { Handle, Position, type NodeProps } from '@xyflow/svelte';
	import ClipboardListIcon from '@lucide/svelte/icons/clipboard-list';
	import FileCheckIcon from '@lucide/svelte/icons/file-check';
	import UploadIcon from '@lucide/svelte/icons/upload';
	import FileOutputIcon from '@lucide/svelte/icons/file-output';
	import ScanFaceIcon from '@lucide/svelte/icons/scan-face';
	import ShieldAlertIcon from '@lucide/svelte/icons/shield-alert';
	import GavelIcon from '@lucide/svelte/icons/gavel';
	import WalletIcon from '@lucide/svelte/icons/wallet';
	import ListTodoIcon from '@lucide/svelte/icons/list-todo';
	import RepeatIcon from '@lucide/svelte/icons/repeat';
	import ZapIcon from '@lucide/svelte/icons/zap';
	import UserIcon from '@lucide/svelte/icons/user';
	import BriefcaseIcon from '@lucide/svelte/icons/briefcase';
	import ShieldCheckIcon from '@lucide/svelte/icons/shield-check';
	import { OUT_HANDLE, type FlowStep, type FlowStepType } from '@graphene/ui/book/journeys';
	import {
		actorOf,
		ACTOR_STYLE,
		ACTOR_LABEL,
		STEP_TYPE_LABEL,
		SCOPE_SHORT,
		detailForNode,
		type FlowNodeData,
		type Actor
	} from './graph';
	import NodeIssueBadge from './NodeIssueBadge.svelte';

	let { data, selected }: NodeProps = $props();
	const d = $derived(data as FlowNodeData);
	const step = $derived(d.node as FlowStep);

	const ICONS: Record<FlowStepType, typeof ClipboardListIcon> = {
		fill_form: ClipboardListIcon,
		accept_terms: FileCheckIcon,
		upload_document: UploadIcon,
		generate_document: FileOutputIcon,
		verify_identity: ScanFaceIcon,
		aml_check: ShieldAlertIcon,
		review: GavelIcon,
		setup_account: WalletIcon,
		task: ListTodoIcon
	};
	const ACTOR_ICON: Record<Actor, typeof UserIcon> = {
		client: UserIcon,
		adviser: BriefcaseIcon,
		compliance: ShieldCheckIcon,
		system: ZapIcon
	};

	const actor = $derived(actorOf(d.node));
	const style = $derived(ACTOR_STYLE[actor]);
	const Icon = $derived(ICONS[step.type] ?? ListTodoIcon);
	const ActorIcon = $derived(ACTOR_ICON[actor]);
	const detail = $derived(detailForNode(d.node, d.lookups));
	// Plan-time visibility stays a PROPERTY of the step in v3 (it answers a different question from a
	// branch: plan this in or out per person/account, rather than which way the case goes). So it is
	// shown as a chip on the node it belongs to — never again as a pseudo-node in the graph.
</script>

<div
	class="relative w-60 overflow-hidden rounded-xl shadow-md transition-shadow {selected
		? 'ring-background/80 ring-2 ring-offset-2 ring-offset-transparent'
		: 'hover:shadow-lg'}"
	style="background:{style.bg}; color: var(--hero-foreground)"
>
	<!-- Handles + on-node chips take `current`, which is the node's own ink (`color` above) — so a
	     firm re-branding the node hue keeps its edge dots and chips in the matching ink instead of a
	     literal white that would clash. -->
	<Handle
		type="target"
		position={Position.Top}
		class="!size-2.5 !border-2 !border-current/70 !bg-current"
	/>

	<div class="flex items-center gap-2 px-2.5 py-2" style="background:{style.head}">
		<span class="flex size-6 shrink-0 items-center justify-center rounded-md bg-current/15">
			<Icon class="size-3.5" />
		</span>
		<span class="text-[11px] font-semibold">{STEP_TYPE_LABEL[step.type]}</span>
		<span
			class="ml-auto flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-semibold"
			style="background:color-mix(in oklab, var(--hero-foreground) 18%, transparent)"
		>
			<ActorIcon class="size-3" />
			{ACTOR_LABEL[actor]}
		</span>
	</div>

	<div class="space-y-1.5 px-2.5 py-2">
		<p class="text-sm leading-tight font-semibold">{step.title || step.id}</p>
		<p class="truncate text-xs" style="color:{style.sub}" title={detail}>{detail}</p>

		<div class="flex items-center gap-1.5 pt-0.5 text-[10px]" style="color:{style.sub}">
			<span>{SCOPE_SHORT[step.scope ?? 'application']}</span>
			{#if !step.required}<span>· optional</span>{/if}
			{#if step.per_participant}
				<span
					class="ml-auto flex items-center gap-1 rounded px-1.5 py-0.5 font-semibold"
					style="background:color-mix(in oklab, var(--hero-foreground) 16%, transparent)"
				>
					<RepeatIcon class="size-2.5" /> Each owner
				</span>
			{/if}
			{#if step.tasks?.length}
				<span
					class="{step.per_participant ? '' : 'ml-auto'} flex items-center gap-1 rounded px-1.5
						py-0.5 font-semibold"
					style="background:color-mix(in oklab, var(--hero-foreground) 16%, transparent)"
					title="Raises {step.tasks.length} task{step.tasks.length === 1 ? '' : 's'}"
				>
					<ZapIcon class="size-2.5" />
					{step.tasks.length}
				</span>
			{/if}
		</div>
	</div>

	<Handle
		id={OUT_HANDLE}
		type="source"
		position={Position.Bottom}
		class="!size-2.5 !border-2 !border-current/70 !bg-current"
	/>
	<NodeIssueBadge show={d.hasIssues} />
</div>
