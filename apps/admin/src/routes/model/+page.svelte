<script lang="ts">
	import { enhance } from '$app/forms';
	import { Alert, AlertDescription, Button, PageHeader, RolesEditor } from '@graphene/ui';
	import TriangleAlertIcon from '@lucide/svelte/icons/triangle-alert';
	import MailIcon from '@lucide/svelte/icons/mail';
	import BellIcon from '@lucide/svelte/icons/bell';
	import type { PageData, ActionData } from './$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();
</script>

<PageHeader
	eyebrow="Internal operations"
	title="Model tenant"
	description="The role + email preset every new firm clones. Configure roles + permissions per portal here."
>
	{#snippet actions()}
		<Button href={`/tenants/${data.modelTenantUid}/email-templates`} variant="outline">
			<MailIcon class="size-4" /> Email templates
		</Button>
		<Button href={`/tenants/${data.modelTenantUid}/notifications`} variant="outline">
			<BellIcon class="size-4" /> Notifications
		</Button>
	{/snippet}
</PageHeader>

<Alert variant="warning" class="mb-6">
	<TriangleAlertIcon class="size-4" />
	<AlertDescription>
		Changes here only affect firms created afterwards — never existing ones.
	</AlertDescription>
</Alert>

<RolesEditor
	roles={data.roles}
	permissions={data.permissions}
	{form}
	{enhance}
	canManage={data.canManage}
/>
