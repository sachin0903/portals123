<script lang="ts">
	import { NotificationRules, PageHeader, toast, type RuleDraft } from '@graphene/ui';
	import { invalidateAll } from '$app/navigation';

	let { data } = $props();
	let savingRule = $state(false);

	async function saveRule(draft: RuleDraft) {
		savingRule = true;
		try {
			const path = draft.uid
				? `/tenants/${data.tenant.uid}/notifications/rules/${draft.uid}`
				: `/tenants/${data.tenant.uid}/notifications/rules`;
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
			savingRule = false;
		}
	}

	// Persist a batch of inline toggle edits in one round-trip: each draft is created (no uid) or
	// patched (uid), then a single reload resyncs the list. One toast, not one per row.
	async function saveMany(drafts: RuleDraft[]) {
		if (!drafts.length) return;
		savingRule = true;
		try {
			for (const draft of drafts) {
				const path = draft.uid
					? `/tenants/${data.tenant.uid}/notifications/rules/${draft.uid}`
					: `/tenants/${data.tenant.uid}/notifications/rules`;
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
			savingRule = false;
		}
	}

	async function deleteRule(uid: string) {
		try {
			const res = await fetch(`/tenants/${data.tenant.uid}/notifications/rules/${uid}`, {
				method: 'DELETE'
			});
			if (!res.ok) throw new Error('delete failed');
			toast.success('Notification rule removed.');
			await invalidateAll();
		} catch {
			toast.error('Could not remove the rule. Please try again.');
		}
	}

	// Seed the shipped built-in defaults as real, editable rules (idempotent + additive).
	async function loadDefaults() {
		try {
			const res = await fetch(`/tenants/${data.tenant.uid}/notifications/load-defaults`, {
				method: 'POST'
			});
			if (!res.ok) throw new Error('load failed');
			const { created } = (await res.json()) as { created: number };
			toast.success(
				created === 0
					? 'Already has every default notification.'
					: `Added ${created} default notification${created === 1 ? '' : 's'}.`
			);
			await invalidateAll();
		} catch {
			toast.error('Could not load the defaults. Please try again.');
		}
	}
</script>

<svelte:head><title>Notifications · {data.tenant.name}</title></svelte:head>

<div class="space-y-6">
	<!-- Reached from Logs, as on the platform — there is no sidebar row for this page. -->
	<PageHeader
		title="Notifications"
		description="This firm's notifications, managed by operations."
		back={{ href: `/tenants/${data.tenant.uid}/operate/activity`, label: 'Activity' }}
	/>

	<NotificationRules
		rules={data.rules}
		catalogue={data.catalogue}
		emailTemplates={data.emailTemplates}
		canManage={data.canManage}
		saving={savingRule}
		onSave={data.canManage ? saveRule : undefined}
		onSaveMany={data.canManage ? saveMany : undefined}
		onDelete={data.canManage ? deleteRule : undefined}
		onLoadDefaults={data.canManage ? loadDefaults : undefined}
		historyUrl={(uid) => `/tenants/${data.tenant.uid}/notifications/rules/${uid}/versions`}
	/>
</div>
