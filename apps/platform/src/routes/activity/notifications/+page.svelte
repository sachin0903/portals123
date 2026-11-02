<script lang="ts">
	import { NotificationRules, PageHeader, toast, type RuleDraft } from '@graphene/ui';
	import { invalidateAll } from '$app/navigation';

	let { data } = $props();
	let saving = $state(false);

	async function saveRule(draft: RuleDraft) {
		saving = true;
		try {
			const path = draft.uid
				? `/activity/notifications/rules/${draft.uid}`
				: '/activity/notifications/rules';
			const res = await fetch(path, {
				method: draft.uid ? 'PATCH' : 'POST',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify(draft)
			});
			if (!res.ok) throw new Error('save failed');
			toast.success('Notification rule saved.');
			await invalidateAll();
		} catch {
			toast.error('Could not save the rule. Please try again.');
		} finally {
			saving = false;
		}
	}

	// Persist a batch of inline toggle edits in one round-trip: each draft is created (no uid) or
	// patched (uid), then a single reload resyncs the list. One toast, not one per row.
	async function saveMany(drafts: RuleDraft[]) {
		if (!drafts.length) return;
		saving = true;
		try {
			for (const draft of drafts) {
				const path = draft.uid
					? `/activity/notifications/rules/${draft.uid}`
					: '/activity/notifications/rules';
				const res = await fetch(path, {
					method: draft.uid ? 'PATCH' : 'POST',
					headers: { 'content-type': 'application/json' },
					body: JSON.stringify(draft)
				});
				if (!res.ok) throw new Error('save failed');
			}
			toast.success(drafts.length === 1 ? 'Notification saved.' : 'Notification changes saved.');
			await invalidateAll();
		} catch {
			toast.error('Could not save your changes. Please try again.');
			// A batch can fail part-way; reload so already-saved rows aren't re-created on retry
			// (a create carries no uid until the list resyncs).
			await invalidateAll();
		} finally {
			saving = false;
		}
	}

	async function deleteRule(uid: string) {
		try {
			const res = await fetch(`/activity/notifications/rules/${uid}`, { method: 'DELETE' });
			if (!res.ok) throw new Error('delete failed');
			toast.success('Notification rule removed.');
			await invalidateAll();
		} catch {
			toast.error('Could not remove the rule. Please try again.');
		}
	}
</script>

<svelte:head><title>Notifications · Activity</title></svelte:head>

<!-- The platform listing shell: a full-height flex column so the CARD scrolls its own rows
     rather than the page growing — the frame accounts/transactions/Activity all use. -->
<div
	class="-m-4 flex min-w-0 flex-col gap-5 p-4 md:-m-6 md:h-[calc(100svh-var(--topbar-h,4rem))] md:overflow-hidden md:p-6"
>
	<PageHeader
		class="mb-0"
		back={{ href: '/activity', label: 'Activity' }}
		title="Notifications"
		description="Choose what each event notifies. Events a recipient can be resolved from are listed; the rest are recorded but can notify nobody, and are hidden until you ask for them."
	/>
	<NotificationRules
		class="min-h-0 flex-1"
		events={data.events}
		rules={data.rules}
		emailTemplates={data.emailTemplates}
		canManage={data.canManage}
		firmMode
		groupBy="event"
		automationsHref="/automations"
		activityHref={(eventTypes) => `/emails?event_type=${encodeURIComponent(eventTypes.join(','))}`}
		{saving}
		onSave={data.canManage ? saveRule : undefined}
		onSaveMany={data.canManage ? saveMany : undefined}
		onDelete={data.canManage ? deleteRule : undefined}
		historyUrl={(uid) => `/activity/notifications/rules/${uid}/versions`}
	/>
</div>
