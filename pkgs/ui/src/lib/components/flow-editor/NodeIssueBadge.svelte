<!--
	"Something here stops this publishing." The badge is the canvas half of the publish preflight:
	the engine's issues name a node, the builder groups them by node id (`validation.groupIssues`),
	and this marks the ones at fault so an author with a forty-node journey does not have to read a
	list of sentences and hunt for the node each one is about.

	Deliberately not a tooltip: the sentences live in the inspector, against the field at fault. This
	only has to say "look here".
-->
<script lang="ts">
	import AlertTriangleIcon from '@lucide/svelte/icons/alert-triangle';

	let { show, label = 'This node has a problem' }: { show: boolean; label?: string } = $props();
</script>

{#if show}
	<span
		class="border-background absolute -top-1.5 -right-1.5 flex size-5 items-center justify-center
			rounded-full border-2 shadow-sm"
		style="background: var(--destructive); color: var(--destructive-foreground)"
		title={label}
		aria-label={label}
	>
		<AlertTriangleIcon class="size-3" />
	</span>
{/if}
