<!--
	The fallback inspector: a node's title and its config as JSON. Every kind gets a real inspector
	in Phase C2; until then this keeps a node editable rather than unreachable, and it stays
	afterwards as the honest last resort for a config key no inspector has grown a field for yet.

	The JSON box only commits on a parse — an author mid-keystroke has invalid JSON almost all of
	the time, and writing that through would blank the node's config on every character.
-->
<script lang="ts">
	import { Input, Label } from '@graphene/ui';
	import type { InspectorProps } from './types';

	let { node, canManage, onchange }: InspectorProps = $props();

	let text = $state('');
	let invalid = $state(false);
	// Re-seed the box when the selection moves to a different node (and on first render, which is
	// why `shownFor` starts empty rather than at the node's own id).
	let shownFor = $state('');
	$effect(() => {
		if (shownFor !== node.id) {
			shownFor = node.id;
			text = JSON.stringify(node.config ?? {}, null, 2);
			invalid = false;
		}
	});

	function commit() {
		try {
			const parsed = JSON.parse(text || '{}');
			if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('object');
			node.config = parsed as Record<string, unknown>;
			invalid = false;
			onchange();
		} catch {
			invalid = true;
		}
	}
</script>

<div class="space-y-4">
	<div class="space-y-1.5">
		<Label for="node-title">Title</Label>
		<Input
			id="node-title"
			bind:value={node.title}
			disabled={!canManage}
			oninput={onchange}
			placeholder="What this does"
		/>
	</div>

	<div class="space-y-1.5">
		<Label for="node-config">Settings</Label>
		<textarea
			id="node-config"
			class="border-input bg-background focus-visible:ring-ring min-h-40 w-full rounded-md border px-3 py-2 font-mono text-xs focus-visible:ring-1 focus-visible:outline-none"
			bind:value={text}
			disabled={!canManage}
			oninput={commit}
			spellcheck="false"></textarea>
		<p class="text-muted-foreground text-xs">
			{#if invalid}
				<span class="text-destructive"
					>These settings are not valid. The last valid version is saved.</span
				>
			{:else}
				Raw settings for this node.
			{/if}
		</p>
	</div>
</div>
