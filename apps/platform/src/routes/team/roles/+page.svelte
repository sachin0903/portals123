<script lang="ts">
	import { enhance } from '$app/forms';
	import { invalidateAll } from '$app/navigation';
	import {
		Alert,
		AlertDescription,
		ListingSkeleton,
		LoadErrorBanner,
		PageHeader,
		RolesEditor,
		Streamed
	} from '@graphene/ui';
	import type { PageData, ActionData } from './$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();
</script>

{#snippet notAuthorised()}
	<Alert variant="destructive">
		<AlertDescription>
			You do not have permission to view your firm's roles. Ask a firm administrator for access.
		</AlertDescription>
	</Alert>
{/snippet}

<PageHeader
	title="Roles &amp; permissions"
	description="Configure what each role can do in the Platform."
/>

{#if !data.authorised || !data.catalogue}
	{@render notAuthorised()}
{:else}
	<!-- `keep`: a saved permission set re-runs the load, and the editor holds its place while the
	     fresh catalogue lands rather than dropping back to the placeholder. -->
	<Streamed data={data.catalogue} onretry={() => invalidateAll()} keep>
		{#snippet pending()}
			<ListingSkeleton fill={false} header={false} />
		{/snippet}
		{#snippet children(catalogue)}
			{#if catalogue.forbidden}
				{@render notAuthorised()}
			{:else if catalogue.loadError}
				<LoadErrorBanner onRetry={() => invalidateAll()} />
			{:else}
				<RolesEditor
					roles={catalogue.roles}
					permissions={catalogue.permissions}
					{form}
					{enhance}
					canManage={data.canManage}
				/>
			{/if}
		{/snippet}
	</Streamed>
{/if}
