<script lang="ts">
	// Every book page renders under this banner: ops are proxying a firm's own view, and nobody
	// working three firms in one afternoon should have to remember whose data is on screen — or
	// whether their clicks can change it. The banner names the firm and states the mode:
	//  · read-only (book:read) — brand-tinted, an eye: nothing here can be changed.
	//  · acting on behalf of (book:write) — warning-tinted, a pen: the middle-office actions on a
	//    client record are live, so the same screen is now a hand in the firm's book.
	// The surfaces below enforce the difference (actions only mount under book:write, and the API
	// re-gates every call); the banner is the statement of intent the hidden buttons can't make.
	import { Alert, AlertDescription, AlertTitle } from '@graphene/ui';
	import EyeIcon from '@lucide/svelte/icons/eye';
	import PencilIcon from '@lucide/svelte/icons/pencil';
	import type { LayoutData } from './$types';

	let { data, children }: { data: LayoutData; children?: import('svelte').Snippet } = $props();
</script>

{#if data.writing}
	<Alert class="border-warning/60 bg-warning/10 mb-6">
		<PencilIcon class="size-4" />
		<AlertTitle>Acting on behalf of {data.tenant.name}</AlertTitle>
		<AlertDescription>
			You can make authorised changes to this firm's client records. Every change is recorded under
			your name.
		</AlertDescription>
	</Alert>
{:else}
	<Alert class="border-brand/40 bg-brand/5 mb-6">
		<EyeIcon class="size-4" />
		<AlertTitle>Viewing {data.tenant.name}'s book (read-only)</AlertTitle>
		<AlertDescription>
			You can view this firm's clients, accounts and transactions, but cannot change them. Access is
			recorded under your name.
		</AlertDescription>
	</Alert>
{/if}

{@render children?.()}
