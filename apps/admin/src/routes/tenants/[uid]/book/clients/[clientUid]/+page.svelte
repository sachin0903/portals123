<script lang="ts">
	// Thin mount of the SHARED client record (@graphene/ui book/clients), in one of two postures:
	//  · `book:read` — `actions` is null, so the money/mandate/archive affordances never render and
	//    the operator sees the record as the firm's staff do, changing it as nobody.
	//  · `book:write` — the approved middle-office actions are live (edit details, bank details,
	//    mandate, the four money-movement instructions + cancel), each posting to this route's own
	//    form actions, which target the firm via `?tenant=` on every call. The unapproved
	//    affordances the shared contract forces (deactivate/archive/notes) submit to server-side
	//    refusals.
	// Deep links stay inside the book (`links.account` → this section's account detail).
	// `links.illustrations` is omitted: there is no ops illustrations surface, and the link is the
	// generate entry point (illustrations:write, a firm-side permission) — absent, the shared
	// record hides the button.
	import { enhance } from '$app/forms';
	import { invalidateAll } from '$app/navigation';
	import { ClientRecord, clientName, isPensionProduct } from '@graphene/ui';
	import { dueLabel, isOpen, isOverdue } from '@graphene/ui/book/tasks';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	const c = $derived(data.detail.contact);
	const name = $derived(clientName(c));
	const base = $derived(`/tenants/${data.tenant.uid}/book`);

	// Same labelling rule as the platform: name the wrapper the client holds when they hold one.
	const pensionLabel = $derived(
		data.detail.accounts
			.filter((a) => isPensionProduct(a.product_type))[0]
			?.product_type?.toUpperCase() ?? 'Pension'
	);

	// Open action items, presentation-ready: the due/overdue phrasing is applied app-side with
	// the shared display rules (@graphene/ui/book/tasks — one implementation, structurally typed).
	const openTasks = $derived(
		data.tasks.filter(isOpen).map((t) => ({
			uid: t.uid,
			title: t.title,
			overdue: isOverdue(t),
			dueLabel: t.due_date ? dueLabel(t) : null
		}))
	);
</script>

<svelte:head><title>{name} · {data.tenant.name} · Operations</title></svelte:head>

<ClientRecord
	detail={data.detail}
	screeningChecks={data.detail.kyc}
	allocations={data.allocations}
	cash={data.cash}
	bookCost={data.bookCost}
	unrealisedGain={data.unrealisedGain}
	holdingsError={data.holdingsError}
	instructions={data.instructions}
	funding={data.funding}
	bankAccount={data.bankAccount}
	justificationRequired={data.justificationRequired ?? []}
	tasks={openTasks}
	{pensionLabel}
	links={{
		account: (uid) => `${base}/accounts/${uid}`,
		client: (uid) => `${base}/clients/${uid}`
	}}
	actions={data.canWrite
		? {
				enhance,
				updateClient: '?/updateClient',
				updateBankAccount: '?/updateBankAccount',
				setAccountModel: '?/setAccountModel',
				createInstruction: '?/createInstruction',
				cancelInstruction: '?/cancelInstruction',
				addNote: '?/addNote'
			}
		: null}
	onretry={() => invalidateAll()}
/>
