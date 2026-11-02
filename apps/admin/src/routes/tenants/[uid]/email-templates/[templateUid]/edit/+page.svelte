<script lang="ts">
	import { enhance } from '$app/forms';
	import { invalidateAll, beforeNavigate, goto } from '$app/navigation';
	import { page } from '$app/state';
	import {
		EmailTemplateEditorPage,
		emailBrandColours,
		emailMastheadPreview,
		tenantRelayPath
	} from '@graphene/ui/email-editor';
	import type { PageData, ActionData } from './$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();

	// Name what is being edited. This surface serves ANY tenant, so the chrome cannot say "Model
	// email templates" — an operator editing a firm's copy needs to see WHOSE clients receive it.
	// `tenant` comes from the [uid] layout.
	const sectionLabel = $derived(
		data.isModel ? 'Model email templates' : `${data.tenant?.name ?? 'Firm'} — emails`
	);

	// The From name in the preview's mock inbox, and the masthead wordmark. The firm portal reads its
	// own `appName`, which the API resolves straight off `auth.tenants.name`
	// (`environments.env_config_by_host`) — so the equivalent here is the target tenant's name,
	// already resolved by the [uid] layout at no extra round trip. Pass nothing and every operator
	// previews against the component's 'Your firm' placeholder, which is the one thing the preview
	// exists to get right.
	//
	// THE MODEL PRESET NAMES ITSELF TOO. It used to show 'Your firm' on the argument that no client
	// ever receives mail from a tenant with no logins, so a real-looking From would be a fabrication.
	// That reads well and is wrong about what an operator is looking at: they are editing a ROW, and
	// the masthead is the one place the canvas says whose row it is. "Model Tenant" is the honest
	// answer and it is the row's own name — a placeholder in its place is the surface declining to
	// say what it is showing. The placeholder survives for the case it was written for: a tenant
	// whose name is genuinely unknown or blank.
	const senderName = $derived((data.tenant?.name ?? '').trim() || 'Your firm');

	// THE MASTHEAD WORDMARK TAKES THE SAME VALUE. `brand.name` is the firm's own name at send, so the
	// From name and the wordmark answer one question — whose name is on this email — and must not
	// disagree. It was `page.data.appName`, which the PLATFORM layout sets and this portal does not: the
	// masthead therefore had no name to fall back to, and a firm with no logo (every firm until branding
	// storage lands) previewed its masthead as an empty dashed slot.

	// Brand-colour swatches for the TARGET tenant, never this portal's own: the admin surface wears the
	// Graphene product look and is not tenant-themed, so `page.data.branding` is Graphene's and would
	// put OUR colours into a firm's email. The loader fetches the firm's stored branding (the [uid]
	// layout carries the tenant's identity, not its theme) and returns null for the model preset, which
	// gets no swatches — a literal from any palette is wrong in a document every firm clones.
	const brandColours = $derived(emailBrandColours(data.branding));

	// WHOSE LOGO, said in the URL. A stored logo is a bare `/_assets/{name}` and the relay resolves
	// the tenant from the HOST it is called on — which on this portal is the operations tenant, so
	// every firm's object 404'd and the canvas showed a broken slot with no wordmark behind it (a
	// present URL suppresses the fallback). `tenantRelayPath` re-points it at the tenant-qualified
	// relay route, which existed for cross-firm audit rows and had no caller until now.
	const brandLogoUrl = $derived(
		tenantRelayPath(emailMastheadPreview(data.branding).src, page.params.uid)
	);
</script>

<!-- The posture the LISTING already states, said in the editor too: this is where a firm's
     client-facing copy is actually changed and published, and every write is stamped
     operations-authored. Declared out here and passed as a value — a snippet inside an `{#if}` in a
     component's body turns the rest of that body into implicit children. -->
{#snippet actingNotice()}
	Acting for {data.tenant?.name ?? 'this firm'}
{/snippet}

<EmailTemplateEditorPage
	template={data.template}
	version={data.version}
	defaultContent={data.defaultContent}
	canManage={data.canManage}
	documentTitle={`${data.template.name} · ${sectionLabel} · Internal operations`}
	backHref={`/tenants/${page.params.uid}/email-templates`}
	backLabel={sectionLabel}
	previewUrl={`/tenants/${page.params.uid}/email-templates/${data.template.uid}/preview`}
	sendTestUrl={`/tenants/${page.params.uid}/email-templates/${data.template.uid}/send-test`}
	{senderName}
	{brandLogoUrl}
	brandName={senderName}
	{brandColours}
	{form}
	{enhance}
	{beforeNavigate}
	{goto}
	onPublished={() => invalidateAll()}
	notice={!data.isModel && data.canManage ? actingNotice : undefined}
>
	{#snippet noPermission()}
		You can view this template but not change it. Editing
		{data.isModel ? 'the model preset' : "a firm's emails"} needs the
		<code>{data.isModel ? 'model:write' : 'tenants:write'}</code> permission.
	{/snippet}
</EmailTemplateEditorPage>
