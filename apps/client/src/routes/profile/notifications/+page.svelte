<script lang="ts">
	import {
		ClientNotificationPreferences,
		LoadErrorBanner,
		toast,
		type NotificationPreference
	} from '@graphene/ui';
	import { invalidate, invalidateAll } from '$app/navigation';

	let { data } = $props();
	let saving = $state(false);

	async function save(prefs: NotificationPreference[]) {
		saving = true;
		try {
			const res = await fetch('/profile/notifications/save', {
				method: 'PUT',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({
					preferences: prefs.map((p) => ({
						category: p.category,
						trigger_key: p.trigger_key,
						in_app: p.in_app,
						email: p.email
					}))
				})
			});
			if (!res.ok) throw new Error('save failed');
			toast.success('Your notification preferences have been saved.');
			await invalidateAll();
		} catch {
			toast.error('Could not save your preferences. Please try again.');
		} finally {
			saving = false;
		}
	}
</script>

<div class="space-y-4">
	<h2 class="text-xl font-semibold tracking-tight">Notifications</h2>
	{#if data.loadError}
		<LoadErrorBanner
			message="We couldn’t load your notification preferences. Please try again."
			onRetry={() => invalidate('app:notification-preferences')}
		/>
	{:else}
		<ClientNotificationPreferences preferences={data.preferences} {saving} onSave={save} />
	{/if}
</div>
