import { error, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, detailFromShell, type ContactShell } from '$lib/api';
import { can } from '@graphene/api-client/permissions';
import { clientName } from '@graphene/ui';
import { clientSidebarFocus } from '@graphene/ui/nav';
import type { LayoutServerLoad } from './$types';

/**
 * The client-record chrome, loaded once for every section of it.
 *
 * This is the layer that makes the record read as "the dashboard, focused on this client": it
 * publishes `sidebarFocus`, which the root layout turns into the sidebar's context header + section
 * strip (see `$lib/nav`), so the tabs that used to sit on the page are the chrome now — the same
 * pattern the admin portal uses for a tenant (`apps/admin/src/routes/tenants/[id]`).
 *
 * The URL names the client by their `uid`. The layout asks only for the SHELL
 * (`GET /crm/contacts/{uid}/shell`) — identity, accounts, team, KYC kind/status, and this client's
 * unread flag. Overview and Details fetch the fat record themselves. Other tabs work from
 * id / uid / name / accounts. The shell carries `chats_unread`, so this load never calls
 * `/messaging/unread`.
 *
 * THE SHELL IS THE ONLY AWAIT. It is the sidebar's context header and section strip, so the first
 * byte cannot beat it without a shell-less paint that jumps when the chrome lands. Nothing else
 * is awaited here: the client-book pin (the firm's books + this client's) used to follow the shell
 * as a second stretch, charged to every section of the record before its first byte, and it now
 * rides inside the streamed record of the two sections that draw it (`$lib/classification`).
 */
export const load: LayoutServerLoad = async ({ locals, params, fetch }) => {
	if (!locals.user) redirect(303, '/login');

	const res = await fetch(`${apiUrl()}/crm/contacts/${params.uid}/shell`, {
		headers: authHeaders(locals.accessToken)
	});
	if (res.status === 404) error(404, 'Client not found');
	if (!res.ok) error(res.status, 'Could not load client');
	const shell: ContactShell = await res.json();
	const detail = detailFromShell(shell);

	// Editing the client themselves (details, status, notes) — contacts:write.
	const canManage = can(locals.permissions, locals.role, 'contacts:write');
	const canInvitePortal =
		canManage &&
		(can(locals.permissions, locals.role, 'members:write') ||
			can(locals.permissions, locals.role, 'teams:write') ||
			can(locals.permissions, locals.role, 'profiles:write'));
	// The money-movement split (docs/api.md "six splits"): reading the instruction history is
	// instructions:read; raising/cancelling instructions, bank details, mandates and drawdown
	// are instructions:write. contacts:write no longer implies either.
	const canReadInstructions = can(locals.permissions, locals.role, 'instructions:read');
	const canMoveMoney = can(locals.permissions, locals.role, 'instructions:write');
	// Generating illustrations is its own slug, split out of documents:write. The section is visible
	// with this or with documents:read. The SIPP drawer stays the pension shortcut.
	const canIllustrate = can(locals.permissions, locals.role, 'illustrations:write');
	// Opening the client's own portal read-only as them. Reading the record is the permission;
	// the API additionally requires the client to be in the caller's book (or read_all).
	const canViewAs = can(locals.permissions, locals.role, 'contacts:read');
	// Gates the Documents section + its loader.
	const canReadDocuments = can(locals.permissions, locals.role, 'documents:read');
	// Gates the Events section — this client's slice of the firm's action-event stream. Its own
	// permission now (the tab reads /firm/audit/actions, not the documents feed it used to).
	const canReadActivity = can(locals.permissions, locals.role, 'activity:read');
	// Gates the Compliance section: a firm's read-only view of Graphene's AML/identity review.
	const canReadCompliance = can(locals.permissions, locals.role, 'screening:read');
	// Gates the Journeys section: this client's journeys and the forms they answered.
	const canReadJourneys = can(locals.permissions, locals.role, 'cases:read');
	// Gates the Messages section — the same client<->adviser conversation the platform inbox's Direct
	// Messages shows, viewed from this record instead.
	const canReadMessages = can(locals.permissions, locals.role, 'messages:read');
	// THIS client's own unread dot (never the tenant-wide one the global sidebar's Messages item
	// carries) — best-effort, same discipline as the bell/global dot: a failure or no permission
	// reads as "no dot", not a broken layout. The shell carries the first-paint value; the root
	// layout keeps it live afterwards by polling `GET /chats/unread?contact_id=` itself (see
	// `sidebarFocus.liveUnreadContactId` below and `routes/+layout.svelte`).
	const chatsUnread = canReadMessages ? Boolean(shell.chats_unread) : false;
	const name = clientName(detail.contact);

	return {
		detail,
		canManage,
		canInvitePortal,
		canReadInstructions,
		canMoveMoney,
		canIllustrate,
		canViewAs,
		canReadDocuments,
		canReadActivity,
		canReadCompliance,
		canReadJourneys,
		canReadMessages,
		clientName: name,
		sidebarFocus: clientSidebarFocus({
			uid: detail.contact.uid,
			name,
			email: detail.contact.email,
			canReadDocuments,
			canSeeIllustrations: canReadDocuments || canIllustrate,
			canReadActivity,
			canReadCompliance,
			canReadJourneys,
			canReadMessages,
			chatsUnread,
			contactId: String(detail.contact.id)
		})
	};
};
