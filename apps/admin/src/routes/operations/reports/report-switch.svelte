<script lang="ts">
	import { enhance } from '$app/forms';
	import { Switch } from '@graphene/ui';

	let {
		code,
		enabled,
		label
	}: {
		code?: string;
		enabled: boolean;
		label: string;
	} = $props();

	let formEl: HTMLFormElement | undefined = $state();
</script>

<form
	bind:this={formEl}
	method="POST"
	action="?/toggle"
	class="flex items-center gap-2"
	use:enhance
>
	{#if code}
		<input type="hidden" name="code" value={code} />
	{/if}
	<input type="hidden" name="enabled" value={enabled ? 'false' : 'true'} />
	<Switch
		checked={enabled}
		aria-label={label}
		onCheckedChange={(next) => {
			const input = formEl?.querySelector<HTMLInputElement>('input[name="enabled"]');
			if (input) input.value = String(next);
			formEl?.requestSubmit();
		}}
	/>
</form>
