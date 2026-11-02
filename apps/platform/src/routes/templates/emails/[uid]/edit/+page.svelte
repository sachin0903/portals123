<script lang="ts">
	import { enhance } from '$app/forms';
	import { invalidateAll, beforeNavigate, goto } from '$app/navigation';
	import { page } from '$app/state';
	import {
		EmailTemplateEditorPage,
		emailBrandColours,
		emailMastheadPreview
	} from '@graphene/ui/email-editor';
	import type { PageData, ActionData } from './$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();

	// The firm's own colours as one-click swatches in the editor's colour pickers. `data.branding` is
	// this firm's PLATFORM-portal branding, resolved per host by the API and put on the root layout —
	// so it costs no round trip, and the studio writes a firm's palette to both portals together.
	// A swatch writes the colour as a literal; it does not follow a later rebrand.
	const brandColours = $derived(emailBrandColours(data.branding));

	// The From name and the masthead wordmark answer one question — whose name is on this email — so
	// they take ONE value. `appName` is the firm's own name, resolved per host by the API off
	// `auth.tenants.name`; an unresolved host previews the placeholder on both.
	const senderName = $derived(String(page.data.appName ?? '').trim() || 'Your firm');
</script>

<EmailTemplateEditorPage
	template={data.template}
	version={data.version}
	defaultContent={data.defaultContent}
	canManage={data.canManage}
	documentTitle={`${data.template.name} · Emails`}
	backHref="/templates?kind=emails"
	backLabel="Emails"
	previewUrl={`/templates/emails/${data.template.uid}/preview`}
	sendTestUrl={`/templates/emails/${data.template.uid}/send-test`}
	{senderName}
	brandLogoUrl={emailMastheadPreview(data.branding).src}
	brandName={senderName}
	{brandColours}
	{form}
	{enhance}
	{beforeNavigate}
	{goto}
	onPublished={() => invalidateAll()}
>
	{#snippet noPermission()}
		You can view this template but not change it. Ask a firm administrator for editing access.
	{/snippet}
</EmailTemplateEditorPage>
