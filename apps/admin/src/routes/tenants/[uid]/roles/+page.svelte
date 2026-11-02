<script lang="ts">
	import { enhance } from '$app/forms';
	import { Button, PageHeader, RolesEditor, toast } from '@graphene/ui';
	import type { PageData, ActionData } from './$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();

	let syncing = $state(false);
</script>

<PageHeader
	eyebrow={data.tenant.name}
	title="Roles &amp; permissions"
	description="This firm's roles, separated by portal. Toggle permissions per role; changes write through to the firm's WorkOS project."
>
	{#snippet actions()}
		{#if data.canManage}
			<!-- The recovery action for the mirror and WorkOS disagreeing about roles (a role created
			     in WorkOS, or a write that landed there but not here, which then refuses to be
			     re-created as "already in use"). -->
			<form
				method="POST"
				action="?/syncRoles"
				use:enhance={() => {
					syncing = true;
					return async ({ result, update }) => {
						syncing = false;
						if (result.type === 'success') {
							toast.success(`Reconciled ${result.data?.synced ?? 0} role(s) from WorkOS.`);
							await update({ reset: false });
						} else {
							toast.error(
								result.type === 'failure'
									? String(result.data?.error ?? 'Something went wrong.')
									: 'Something went wrong.'
							);
							await update();
						}
					};
				}}
			>
				<Button type="submit" variant="secondary" size="sm" disabled={syncing}>
					{syncing ? 'Reconciling…' : 'Reconcile from WorkOS'}
				</Button>
			</form>
		{/if}
	{/snippet}
</PageHeader>

<RolesEditor
	roles={data.roles}
	permissions={data.permissions}
	{form}
	{enhance}
	canManage={data.canManage}
/>
