<script lang="ts">
	import { enhance } from '$app/forms';
	import { PageHeader, RolesEditor } from '@graphene/ui';
	import type { PageData, ActionData } from './$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();
</script>

<PageHeader
	eyebrow="Internal operations"
	title="Operations roles"
	description="Roles &amp; permissions for Graphene internal-operations staff. A role carrying the System administration permission grants full cross-tenant admin access."
/>

<RolesEditor
	roles={data.roles}
	permissions={data.permissions}
	{form}
	{enhance}
	portals={['operations']}
	canManage={data.canManage}
/>
