<script lang="ts">
	import { NotificationPreferences, toast, type NotificationPreference } from '@graphene/ui';

	let { data } = $props();
	let saving = $state(false);

	async function save(prefs: NotificationPreference[]) {
		saving = true;
		try {
			const res = await fetch('/account/notifications/save', {
				method: 'POST',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({
					preferences: prefs.map((p) => ({
						category: p.category,
						// Preferences are per NOTIFICATION (the catalogue trigger), so the trigger_key MUST
						// be sent — without it the API can't match a row to write and silently skips it.
						trigger_key: p.trigger_key,
						in_app: p.in_app,
						email: p.email
					}))
				})
			});
			if (!res.ok) throw new Error('save failed');
			toast.success('Notification preferences saved.');
		} catch {
			toast.error('Could not save your preferences. Please try again.');
		} finally {
			saving = false;
		}
	}
</script>

<svelte:head><title>Notification preferences</title></svelte:head>

<div class="p-1 sm:p-2">
	<NotificationPreferences preferences={data.preferences} {saving} onSave={save} />
</div>
