<script lang="ts">
	import { enhance } from '$app/forms';
	import {
		AuthProfile,
		ConfirmDialog,
		DropdownMenu,
		PageHeader,
		isSuspendedMember,
		toast
	} from '@graphene/ui';
	import MoreVerticalIcon from '@lucide/svelte/icons/ellipsis-vertical';
	import type { ActionResult, SubmitFunction } from '@sveltejs/kit';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	let confirm = $state<'passwordReset' | 'resetMfa' | 'deactivate' | null>(null);
	let confirmOpen = $state(false);
	let forms: Record<string, HTMLFormElement | undefined> = {};
	const ask = (action: 'passwordReset' | 'resetMfa' | 'deactivate') => {
		confirm = action;
		confirmOpen = true;
	};

	const submit =
		(action: string, success: string): SubmitFunction =>
		() =>
		async ({ result, update }: { result: ActionResult; update: () => Promise<void> }) => {
			if (result.type === 'success') {
				const delivered = (result.data as { emailDelivered?: boolean } | undefined)?.emailDelivered;
				if (action === 'passwordReset' && !delivered)
					toast.error('The reset was created, but the email could not be delivered.');
				else toast.success(success);
			} else
				toast.error(
					result.type === 'failure'
						? String(result.data?.error ?? 'Something went wrong.')
						: 'Something went wrong.'
				);
			await update();
		};
</script>

<PageHeader
	eyebrow="Internal operations identity"
	title={data.profile.user.name ?? data.profile.user.email}
	description={data.profile.user.email}
	back={{ href: '/operations/users', label: 'Operations staff' }}
>
	{#snippet actions()}
		{#if data.canManage}
			<DropdownMenu.Root>
				<DropdownMenu.Trigger
					class="border-input bg-background hover:bg-accent inline-flex h-8 items-center gap-2 rounded-lg border px-3 text-sm font-medium"
				>
					<MoreVerticalIcon class="size-4" /> Manage
				</DropdownMenu.Trigger>
				<DropdownMenu.Content align="end">
					<DropdownMenu.Item onclick={() => ask('passwordReset')}>
						Send password reset
					</DropdownMenu.Item>
					<DropdownMenu.Item onclick={() => ask('resetMfa')}>Reset two-factor</DropdownMenu.Item>
					{#if isSuspendedMember(data.profile.memberships)}
						<DropdownMenu.Item onclick={() => forms.reactivate?.requestSubmit()}>
							Reactivate
						</DropdownMenu.Item>
					{:else}
						<DropdownMenu.Item variant="destructive" onclick={() => ask('deactivate')}>
							Deactivate
						</DropdownMenu.Item>
					{/if}
				</DropdownMenu.Content>
			</DropdownMenu.Root>
		{/if}
	{/snippet}
</PageHeader>

<AuthProfile
	profile={data.profile}
	canRevokeSessions={data.canManage}
	clientHref={(uid) => `/tenants/${data.tenantUid}/book/clients/${uid}`}
/>

{#if data.canManage}
	<form
		method="POST"
		action="?/passwordReset"
		use:enhance={submit('passwordReset', 'Password reset sent.')}
		bind:this={forms.passwordReset}
		class="hidden"
	></form>
	<form
		method="POST"
		action="?/resetMfa"
		use:enhance={submit('resetMfa', 'Two-factor reset.')}
		bind:this={forms.resetMfa}
		class="hidden"
	></form>
	<form
		method="POST"
		action="?/deactivate"
		use:enhance={submit('deactivate', 'Staff member deactivated.')}
		bind:this={forms.deactivate}
		class="hidden"
	></form>
	<form
		method="POST"
		action="?/reactivate"
		use:enhance={submit('reactivate', 'Staff member reactivated.')}
		bind:this={forms.reactivate}
		class="hidden"
	></form>
{/if}

<ConfirmDialog
	bind:open={confirmOpen}
	title={confirm === 'passwordReset'
		? 'Send a password reset?'
		: confirm === 'resetMfa'
			? 'Reset two-factor authentication?'
			: 'Deactivate this staff member?'}
	description={confirm === 'passwordReset'
		? 'A fresh reset link will be emailed and every live session will end.'
		: confirm === 'resetMfa'
			? 'Verify their identity first. Every authenticator and live session will be removed.'
			: 'They will lose Internal Operations access and their live sessions will end.'}
	confirmLabel={confirm === 'passwordReset'
		? 'Send reset'
		: confirm === 'resetMfa'
			? 'Reset two-factor'
			: 'Deactivate'}
	destructive
	onconfirm={() => {
		if (confirm) forms[confirm]?.requestSubmit();
		confirm = null;
	}}
/>
