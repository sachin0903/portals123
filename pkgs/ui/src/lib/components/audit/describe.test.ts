import { describe, expect, it } from 'vitest';
import {
	actorLabel,
	actorLedSentence,
	describeActionEvent,
	deslug,
	entityKindLabel,
	eventCategory,
	methodLabel,
	methodRoute,
	permissionsDiff,
	statusLabel,
	statusTone,
	timestampParts,
	type ActionEntity,
	type ActionEvent
} from './describe.js';

describe('describeActionEvent', () => {
	// Table-style: the curated verbs, the vocabulary aliases, the fallbacks. Each row is
	// (event_type, path_params, expected) — the whole contract in one place.
	const cases: Array<[string, Record<string, unknown>, string]> = [
		// The headline case: curated verb + product vocabulary (contact → client).
		['client.created', {}, 'Created a client'],
		['client.updated', { contact_id: '42' }, 'Updated a client'], // no entities -> the type
		['action.firm-delete-contact', {}, 'Deleted a client'],
		// tenant → firm, and "an" before a vowel.
		['firm.updated', { tenant_id: 7 }, 'Updated a firm'],
		['action.admin-export-tenant', {}, 'Exported a firm'],
		['illustration.created', {}, 'Created an illustration'],
		['illustration.shared', {}, 'Shared an illustration'],
		// Plural noun ⇒ no article.
		['form.fields_updated', { uid: 'f_1' }, 'Updated form fields'],
		['action.admin-push-roles', {}, 'Pushed roles'], // no curated "push": de-slugged fallback
		// Mass-ish nouns the crude plural test happens to catch — they must read without "a".
		['action.admin-grant-portal-access', {}, 'Granted portal access'],
		// The portal path param names WHICH portal.
		['action.admin-grant-portal-access', { portal: 'client' }, 'Granted client portal access'],
		['action.admin-revoke-portal-access', { portal: 'platform' }, 'Revoked platform portal access'],
		// Bare verb (nothing after it).
		['action.admin-sync', {}, 'Synced'],
		// Auth events (record_auth_action): a fixed readable label, not a de-slug.
		['authentication.account_activated', {}, 'Activated their account'],
		['authentication.succeeded', {}, 'Signed in'],
		['authentication.failed', {}, 'Failed sign-in'],
		['authentication.mfa_removed', {}, 'Removed multi-factor authentication'],
		// The message-send family reads cleanly (was a bad de-slug "Messaging thread send"). The
		// thread/group channels carry no recipient id, so a clean verb.
		['message.sent', {}, 'Sent a message'],
		['message.attachment_sent', {}, 'Sent an attachment'],
		['message_group.created', {}, 'Created a message group'],
		['message_group.message_sent', {}, 'Sent a message to a group'],
		['message_group.document_requested', {}, 'Requested documents in a group'],
		// Document activity folded into the stream (record_document_action): view / render / access.
		['document.viewed', { uid: 'doc_1' }, 'Viewed a document'],
		['document.rendered', { uid: 'doc_1' }, 'Rendered a document'],
		['document_share.accessed', {}, 'Accessed a document share'],
		// An override, because the derivation ("Updated a member provider ref") reads as machinery
		// where the act is naming a person to a custodian. The provider is a path-param chip.
		['member.provider_ref_updated', { provider: 'sei' }, 'Set a member’s custodian id'],
		['member.profile_updated', {}, 'Updated what a firm says about a member'],
		// Unknown verb ⇒ readable de-slug, capitalised — never empty, never a throw.
		['action.flows-transition-step', {}, 'Transition step'],
		// A one-word operation id has no tag to drop.
		['action.logout', {}, 'Logout'],
		// THE ROUTER TAG IS STRIPPED WHOLE, including a copy the route name repeats. These three shapes
		// are a third of the estate and each produced machine text: a doubled tag ("Admin create email
		// template"), a multi-word tag ("Templates create email template"), and both again for the
		// messaging family. The two routes below are DIFFERENT operations for the same act — the ops one
		// and the firm one — and they must read alike, because the difference is the caller, not the act.
		['email_template.created', {}, 'Created an email template'],
		['content.created', {}, 'Created a content item'],
		['content.published', {}, 'Published a content item'],
		['content_category.created', {}, 'Created a content category'],
		['email_template.created', {}, 'Created an email template'],
		['content.created', {}, 'Created a content item'],
		['content.published', {}, 'Published a content item'],
		['content_category.created', {}, 'Created a content category'],
		['notification_rule.updated', {}, 'Updated a notification rule'],
		['message.sent', {}, 'Sent a message'],
		// Acronyms and proper nouns as a reader writes them, and the article chosen on the SOUND of the
		// cased word ("an AML screen", not "a AML screen").
		['aml.screen_ran', {}, 'Ran an AML screen'],
		['contribution.recorded_at_morgan_lloyd', {}, 'Recorded a contribution at Morgan Lloyd'],
		// A determiner already fills the article's slot ("Provisioned an all firm storage" was the bug).
		['firm.storage_provisioned_for_all', {}, 'Provisioned storage for every firm'],
		// A verb-LAST name is written out: no grammar reaches "Conversation read".
		['conversation.read', {}, 'Read a conversation'],
		// A RETIRED event type — from a route that no longer exists, so it is in no rename map and keeps
		// its pre-rename name for ever (`audit.actions` is append-only). It must still read as English:
		// the legacy path strips the router tag and de-slugs the rest. Rendering the raw id here would
		// put a machine string in a list of sentences, on the rows a reader is least able to explain.
		['action.tags-apply-tag', {}, 'Apply tag'],
		['action.tags-remove-tag', {}, 'Removed a tag'],
		// Data egress. The derived sentence names the machinery ("Rendered an export PDF"); the act a
		// reader is scanning for is that a listing left the platform, so this one is written out.
		['export.pdf_rendered', {}, 'Exported a listing as PDF'],
		['export.listing_exported', {}, 'Exported a listing'],
		[
			'account.regular_movements_updated',
			{},
			'Updated an account’s regular payments and withdrawals'
		],
		['account.bank_accounts_updated', {}, 'Updated an account’s bank details'],
		// Un-prefixed input degrades gracefully (defensive: the helper renders evidence).
		['firm-cancel-request', {}, 'Cancelled a request'],
		['', {}, '']
	];

	it.each(cases)('%s → %s', (eventType, params, expected) => {
		expect(describeActionEvent(eventType, params)).toBe(expected);
	});
});

describe('describeActionEvent — entity enrichment', () => {
	const ent = (kind: string, id: string, label: string | null): ActionEntity => ({
		kind,
		id,
		label
	});

	// Table-style: (event_type, entities, expected) — the curated entity-rich sentences, and the
	// fallbacks when labels are missing (enrichment may only ever improve on the generic line).
	const cases: Array<[string, ActionEntity[], string]> = [
		// The headline case the feature exists for: WHICH login, WHICH client.
		[
			'client.portal_user_linked',
			[ent('contact', '96', 'Margaret Cole'), ent('user', 'u-1', 'm.cole@example.com')],
			'Linked m.cole@example.com to Margaret Cole'
		],
		[
			'client.portal_user_unlinked',
			[ent('contact', '96', 'Margaret Cole'), ent('user', 'u-1', 'm.cole@example.com')],
			'Unlinked m.cole@example.com from Margaret Cole'
		],
		// A deleted user (null label) degrades to the partial form — never a bare id in prose.
		[
			'client.portal_user_linked',
			[ent('contact', '96', 'Margaret Cole'), ent('user', 'u-1', null)],
			'Linked a user to Margaret Cole'
		],
		// No usable labels at all ⇒ today's generic sentence, untouched.
		[
			'client.portal_user_linked',
			[ent('contact', '96', null), ent('user', 'u-1', null)],
			'Linked a client portal user'
		],
		[
			'client.adviser_assigned',
			[ent('contact', '7', 'Ivy Trust'), ent('user', 'u-9', 'adviser@firm.test')],
			'Assigned adviser@firm.test as adviser to Ivy Trust'
		],
		[
			'instruction.created',
			[ent('contact', '7', 'Margaret Cole'), ent('account', '12', 'General Account')],
			'Created an instruction on General Account for Margaret Cole'
		],
		[
			'account.model_updated',
			[ent('account', '12', 'SIPP'), ent('model', '3', 'Balanced 60/40')],
			'Set SIPP to model Balanced 60/40'
		],
		[
			'document.generated',
			[ent('template', 'tpl_1', 'Suitability report'), ent('contact', '96', 'Margaret Cole')],
			'Generated Suitability report for Margaret Cole'
		],
		[
			'document.sent',
			[ent('contact', '96', 'Margaret Cole'), ent('contact', '97', 'Bill Cole')],
			'Sent a document to Margaret Cole and 1 more'
		],
		['aml.screen_ran', [ent('contact', '96', 'Margaret Cole')], 'Screened Margaret Cole'],
		// Reading a document is the most repeated event in the stream, so the row is only worth
		// anything if it names WHICH document — "viewed a document" fifteen times says nothing.
		// An upload NAMES what it created. The uids are lifted off the 2xx response (the row does not
		// exist when the request arrives), so before this every upload read "Uploaded a document".
		['document.uploaded', [ent('document', 'doc_1', 'Passport.pdf')], 'Uploaded Passport.pdf'],
		// One drop may carry several files, so the count is in the sentence rather than a lie about
		// there being one.
		[
			'document.uploaded',
			[ent('document', 'doc_1', 'Passport.pdf'), ent('document', 'doc_2', 'Statement.pdf')],
			'Uploaded Passport.pdf and 1 more'
		],
		// …and the client it was filed under, when the upload declared one.
		[
			'document.uploaded',
			[ent('document', 'doc_1', 'Passport.pdf'), ent('contact', '96', 'Margaret Cole')],
			'Uploaded Passport.pdf for Margaret Cole'
		],
		[
			'document.viewed',
			[ent('document', 'doc_1', 'marlborough-2026-Q2.pdf')],
			'Viewed marlborough-2026-Q2.pdf'
		],
		[
			'document.rendered',
			[ent('document', 'doc_1', 'Suitability report.pdf')],
			'Rendered Suitability report.pdf'
		],
		[
			'document.archived',
			[ent('document', 'doc_1', 'Old ISA form.pdf')],
			'Archived Old ISA form.pdf'
		],
		[
			'document.unarchived',
			[ent('document', 'doc_1', 'Old ISA form.pdf')],
			'Restored Old ISA form.pdf'
		],
		[
			'document.updated',
			[ent('document', 'doc_1', 'Report.pdf'), ent('contact', '96', 'Margaret Cole')],
			'Filed Report.pdf under Margaret Cole'
		],
		// A notify says WHO was told and ABOUT WHAT — and never says "sent": the client could
		// already open it, so a notice is a nudge, not a delivery.
		[
			'document.notified',
			[ent('document', 'doc_1', 'Report.pdf'), ent('contact', '96', 'Margaret Cole')],
			'Notified Margaret Cole about Report.pdf'
		],
		[
			'document.notified',
			[
				ent('document', 'doc_1', 'Report.pdf'),
				ent('contact', '96', 'Margaret Cole'),
				ent('contact', '97', 'Bill Cole')
			],
			'Notified Margaret Cole and 1 more about Report.pdf'
		],
		// A send now names the document too, when its label resolved.
		[
			'document.sent',
			[ent('document', 'doc_1', 'Report.pdf'), ent('contact', '96', 'Margaret Cole')],
			'Sent Report.pdf to Margaret Cole'
		],
		// An event family with no curated builder keeps the generic sentence even WITH entities.
		// THE SUBJECT IS NAMED when the row carries it: the client's name is the fact a reader is
		// scanning for, and what makes two adjacent rows different from each other.
		['client.updated', [ent('contact', '96', 'Margaret Cole')], 'Updated Margaret Cole']
	];

	it.each(cases)('%s (enriched) → %s', (eventType, entities, expected) => {
		expect(describeActionEvent(eventType, {}, entities)).toBe(expected);
	});

	it('is unchanged by an empty or absent entities list', () => {
		expect(describeActionEvent('client.portal_user_linked', {}, [])).toBe(
			'Linked a client portal user'
		);
		expect(describeActionEvent('client.portal_user_linked', {}, null)).toBe(
			'Linked a client portal user'
		);
	});
});

describe('describeActionEvent — from/to enrichment (metadata.subject + metadata.previous)', () => {
	const ent = (kind: string, id: string, label: string | null): ActionEntity => ({
		kind,
		id,
		label
	});
	const member = ent('user', 'u-1', 'm.cole@example.com');

	it('link capacity: "in what capacity" comes from the subject', () => {
		expect(
			describeActionEvent(
				'client.portal_user_linked',
				{},
				[ent('contact', '96', 'Margaret Cole'), member],
				{ subject: { user_id: 'u-1', capacity: 'authorised' } }
			)
		).toBe('Linked m.cole@example.com to Margaret Cole as authorised');
	});

	it('invite: names the invited login (response user_id) and the role (request role_slug)', () => {
		expect(
			describeActionEvent('member.invited', {}, [ent('user', 'u-9', 'zara@firm.test')], {
				subject: { user_id: 'u-9', role_slug: 'adviser' }
			})
		).toBe('Invited zara@firm.test as Adviser');
		// The admin twin shares the builder.
		expect(
			describeActionEvent('member.created', {}, [ent('user', 'u-9', 'zara@firm.test')], {
				subject: { user_id: 'u-9', role_slug: 'adviser' }
			})
		).toBe('Invited zara@firm.test as Adviser');
		// Until the user id resolves, the generic verb-led fallback stands.
		expect(describeActionEvent('member.invited', {}, [], {})).toBe('Invited a member');
	});

	it('an export names the listing and the format it left in', () => {
		expect(
			describeActionEvent('export.listing_exported', {}, [], {
				subject: { listing: 'clients', format: 'xlsx', rows: 12 }
			})
		).toBe('Exported the clients list as Excel');
	});

	it('auth sign-in names the method (WorkOS-aligned), else the plain verb', () => {
		expect(
			describeActionEvent('authentication.succeeded', {}, [], { subject: { method: 'password' } })
		).toBe('Signed in with a password');
		expect(
			describeActionEvent('authentication.succeeded', {}, [], { subject: { method: 'mfa' } })
		).toBe('Signed in with MFA');
		expect(
			describeActionEvent('authentication.succeeded', {}, [], {
				subject: { method: 'password', device: 'Chrome on macOS' }
			})
		).toBe('Signed in with a password from Chrome on macOS');
		expect(
			describeActionEvent('authentication.failed', {}, [], { subject: { method: 'password' } })
		).toBe('Failed to sign in with a password');
		// No method captured ⇒ the plain verb (already pinned in the basic table too).
		expect(describeActionEvent('authentication.succeeded', {}, [], {})).toBe('Signed in');
	});

	it('message send: names the recipient (conversation → client), never the text', () => {
		expect(
			describeActionEvent('message.sent', { conversation_id: '1' }, [
				ent('conversation', '1', 'Margaret Cole')
			])
		).toBe('Sent a message to Margaret Cole');
		// A conversation attachment reuses the same resolver; falls back to a clean verb, not a de-slug.
		expect(
			describeActionEvent('message.attachment_sent', { conversation_id: '1' }, [
				ent('conversation', '1', 'Margaret Cole')
			])
		).toBe('Sent an attachment to Margaret Cole');
		expect(describeActionEvent('message.attachment_sent', {}, [])).toBe('Sent an attachment');
	});

	// The role-change family (firm + admin twins share the builder).
	const roleCases: Array<
		[string, Record<string, unknown>, ActionEntity[], ActionEvent['metadata'], string]
	> = [
		[
			'member.portal_role_updated',
			{ user_id: 'u-1', portal: 'platform' },
			[member],
			{ subject: { role_slug: 'compliance' }, previous: { role_slug: 'adviser' } },
			"m.cole@example.com's platform role: Adviser → Compliance"
		],
		[
			'member.portal_role_updated',
			{ tenant_id: 7, user_id: 'u-1', portal: 'client' },
			[member],
			{ subject: { role_slug: 'read-only' }, previous: { role_slug: 'full-access' } },
			"m.cole@example.com's client role: Full access → Read only"
		],
		// No previous (pre-enrichment row): degrade to "set to", never guess the old role.
		[
			'member.portal_role_updated',
			{ user_id: 'u-1', portal: 'platform' },
			[member],
			{ subject: { role_slug: 'compliance' } },
			"Set m.cole@example.com's platform role to Compliance"
		],
		// A deleted member (no label) still reads as a change when the previous exists.
		[
			'member.portal_role_updated',
			{ user_id: 'u-1', portal: 'platform' },
			[ent('user', 'u-1', null)],
			{ subject: { role_slug: 'compliance' }, previous: { role_slug: 'adviser' } },
			"a member's platform role: Adviser → Compliance"
		]
	];

	it.each(roleCases)('%s → %s', (eventType, pathParams, entities, metadata, expected) => {
		const got = describeActionEvent(eventType, pathParams, entities, metadata);
		expect(got).toContain(expected);
		if (expected.includes('→')) expect(got.startsWith('Changed ')).toBe(true);
	});

	it('role change without member label AND without previous falls back to the generic line', () => {
		expect(
			describeActionEvent(
				'member.portal_role_updated',
				{ user_id: 'u-1', portal: 'platform' },
				[ent('user', 'u-1', null)],
				{ subject: { role_slug: 'compliance' } }
			)
		).toBe('Updated a member portal role'); // the generic line, portal-sharpened as before
	});

	it('permissions set: the summary counts the diff; the chips live in the expanded grid', () => {
		const meta = {
			subject: { permissions: ['a:read', 'b:read', 'c:read'] },
			previous: { permissions: ['b:read', 'z:write'] }
		};
		expect(describeActionEvent('role.permissions_updated', { slug: 'director' }, [], meta)).toBe(
			'Updated role Director: +2 permissions, −1'
		);
		expect(
			describeActionEvent('role.permissions_updated', { tenant_id: 7, slug: 'director' }, [], meta)
		).toBe('Updated role Director: +2 permissions, −1');
	});

	it('permissions set: single-sided and no-op diffs keep honest phrasing', () => {
		const one = (previous: string[], next: string[]) =>
			describeActionEvent('role.permissions_updated', { slug: 'director' }, [], {
				subject: { permissions: next },
				previous: { permissions: previous }
			});
		expect(one(['a:read'], ['a:read', 'b:read'])).toBe('Updated role Director: +1 permission');
		expect(one(['a:read', 'b:read'], ['a:read'])).toBe('Updated role Director: −1 permission');
		expect(one(['a:read'], ['a:read'])).toBe('Updated role Director: no permission changes');
		// No previous captured (legacy row): name the role, never fabricate a diff.
		expect(
			describeActionEvent('role.permissions_updated', { slug: 'director' }, [], {
				subject: { permissions: ['a:read'] }
			})
		).toBe("Updated role Director's permissions");
	});

	it('model change: old → new, resolved by SPECIFIC id (never positional)', () => {
		// previous deliberately resolves to the FIRST model entity — positional lookup would
		// render the change backwards.
		const entities = [
			ent('account', '12', 'SIPP'),
			ent('model', '3', 'Cautious'),
			ent('model', '9', 'Balanced 60/40')
		];
		expect(
			describeActionEvent('account.model_updated', { account_id: '12' }, entities, {
				subject: { model_id: 9 },
				previous: { model_id: 3 }
			})
		).toBe("Changed SIPP's model: Cautious → Balanced 60/40");
		// Without a previous, the existing "set to" sentence survives untouched.
		expect(
			describeActionEvent('account.model_updated', { account_id: '12' }, entities, {
				subject: { model_id: 9 }
			})
		).toBe('Set SIPP to model Balanced 60/40');
	});
});

describe('deslug', () => {
	it('reads slugs and field names as words, speaking the product vocabulary', () => {
		expect(deslug('compliance-officer')).toBe('Compliance officer');
		expect(deslug('role_slug')).toBe('Role slug');
		expect(deslug('contact')).toBe('Client');
		expect(deslug('adviser')).toBe('Adviser');
	});
});

describe('permissionsDiff', () => {
	it('diffs two captured lists, sorted', () => {
		expect(permissionsDiff(['b', 'a'], ['b', 'c', 'd'])).toEqual({
			added: ['c', 'd'],
			removed: ['a']
		});
		expect(permissionsDiff(['a'], ['a'])).toEqual({ added: [], removed: [] });
	});

	it('never guesses from one side or a mis-shaped capture', () => {
		expect(permissionsDiff(undefined, ['a'])).toBeNull();
		expect(permissionsDiff(['a'], undefined)).toBeNull();
		expect(permissionsDiff('a', ['a'])).toBeNull();
		expect(permissionsDiff([1, 2], ['a'])).toBeNull();
	});
});

describe('a chat message names both people', () => {
	// The row renders the actor and this sentence as ONE line, so what a reader sees is the whole
	// thing: "Max Oakden sent a message to Craig Currie". Asserted through `actorLedSentence`
	// because that composition is the point — the description alone is only half the sentence, and
	// a verb this table does not know falls back to the "Actor · Sentence" separator form, which
	// reads as a fragment rather than prose.
	it('names the client when a staff member sends', () => {
		const sentence = describeActionEvent('message.sent', { conversation_id: 5 }, [
			{ kind: 'conversation', id: '5', label: 'Craig Currie' }
		]);
		expect(actorLedSentence('Max Oakden', sentence)).toBe(
			'Max Oakden sent a message to Craig Currie'
		);
	});

	it('names the adviser when the client sends', () => {
		// A client's thread route carries no conversation, so the id is lifted off the response and
		// resolved to the adviser. Before that this read "Craig Currie sent a message" — no
		// recipient at all, on half of every conversation in the trail.
		const sentence = describeActionEvent('message.sent', {}, [
			{ kind: 'conversation_adviser', id: '5', label: 'Max Oakden' }
		]);
		expect(actorLedSentence('Craig Currie', sentence)).toBe(
			'Craig Currie sent a message to Max Oakden'
		);
	});

	it('prefers the path-named client when a staff send carries every entity', () => {
		// A staff send now lifts BOTH response ids as well, so all three entities are present and the
		// client appears twice over (as the conversation and as the contact). The path-named
		// conversation is the specific answer; the others are fallbacks for the direction that has
		// no path param, and picking one here would name the sender or repeat the recipient.
		const sentence = describeActionEvent('message.sent', { conversation_id: 5 }, [
			{ kind: 'conversation_adviser', id: '5', label: 'Max Oakden' },
			{ kind: 'contact', id: '8', label: 'Craig Currie' },
			{ kind: 'conversation', id: '5', label: 'Craig Currie' }
		]);
		expect(actorLedSentence('Max Oakden', sentence)).toBe(
			'Max Oakden sent a message to Craig Currie'
		);
	});

	it('does the same for an attachment', () => {
		const sentence = describeActionEvent('message.attachment_sent', {}, [
			{ kind: 'conversation_adviser', id: '5', label: 'Max Oakden' }
		]);
		expect(actorLedSentence('Craig Currie', sentence)).toBe(
			'Craig Currie sent an attachment to Max Oakden'
		);
	});
});

describe('actorLedSentence', () => {
	it('folds verb-led sentences into actor-first prose', () => {
		expect(actorLedSentence('Alice Feed', 'Created a client')).toBe('Alice Feed created a client');
		expect(actorLedSentence('Alice Feed', "Changed X's platform role: A → B")).toBe(
			"Alice Feed changed X's platform role: A → B"
		);
	});

	it('keeps non-verb fallbacks behind a separator instead of faking grammar', () => {
		expect(actorLedSentence('Alice Feed', 'Transition step')).toBe('Alice Feed · Transition step');
		expect(actorLedSentence('Alice Feed', '')).toBe('Alice Feed');
	});

	// Verbs that ONLY the enriched builders mint are absent from the de-slug VERBS table, so each
	// has to be listed in VERB_LED by hand. Miss one and its sentence quietly degrades to the
	// separator form — grammatical-looking prose is the whole point of the actor-led row, and
	// nothing else in the suite would catch it.
	it.each([
		['Viewed marlborough-2026-Q2.pdf', 'Alice Feed viewed marlborough-2026-Q2.pdf'],
		['Filed Report.pdf under Margaret Cole', 'Alice Feed filed Report.pdf under Margaret Cole'],
		['Restored Old ISA form.pdf', 'Alice Feed restored Old ISA form.pdf'],
		['Booked a contribution on SIPP', 'Alice Feed booked a contribution on SIPP'],
		['Opened a conversation with Max Oakden', 'Alice Feed opened a conversation with Max Oakden']
	])('folds the enriched-only verb in %s', (sentence, expected) => {
		expect(actorLedSentence('Alice Feed', sentence)).toBe(expected);
	});
});

describe('eventCategory', () => {
	const cases: Array<[string, string]> = [
		['member.portal_role_updated', 'roles'], // role beats portal: it IS a role change
		['role.permissions_updated', 'roles'],
		['member.portal_granted', 'people'], // portal access is a people change
		['client.portal_user_linked', 'people'],
		['client.created', 'people'],
		['document.sent', 'documents'],
		['document.viewed', 'documents'],
		['document.rendered', 'documents'],
		['document_share.accessed', 'documents'], // tag `shares` lands in documents
		['template.version_published', 'documents'],
		['illustration.created', 'documents'],
		['export.pdf_rendered', 'documents'], // a listing rendered to a file is paperwork
		['instruction.created', 'money'],
		['account.model_updated', 'money'],
		['task.created', 'tasks'],
		['journey.step_actioned', 'flows'],
		['aml.screen_ran', 'screening'],
		['authentication.account_activated', 'security'], // auth events are their own family
		['authentication.succeeded', 'security'],
		['message.sent', 'people'], // a chat is a people action
		['firm.updated', 'settings'], // the quiet default
		['branding.updated', 'settings']
	];
	it.each(cases)('%s → %s', (eventType, expected) => {
		expect(eventCategory(eventType)).toBe(expected);
	});
});

describe('entityKindLabel', () => {
	it('speaks the product vocabulary and degrades unknown kinds gracefully', () => {
		expect(entityKindLabel('contact')).toBe('Client');
		expect(entityKindLabel('tenant')).toBe('Firm');
		expect(entityKindLabel('user')).toBe('User');
		expect(entityKindLabel('account')).toBe('Account');
		expect(entityKindLabel('widget')).toBe('Widget'); // future kind: capitalised, not thrown
	});
});

describe('actorLabel', () => {
	it('prefers name, then email, then the raw subject', () => {
		expect(
			actorLabel({ actor_name: 'Alice Feed', actor_email: 'a@x.test', actor_sub: 'wos_1' })
		).toBe('Alice Feed');
		expect(actorLabel({ actor_name: null, actor_email: 'a@x.test', actor_sub: 'wos_1' })).toBe(
			'a@x.test'
		);
		expect(actorLabel({ actor_name: null, actor_email: null, actor_sub: 'wos_1' })).toBe('wos_1');
	});
});

describe('methodRoute', () => {
	it('reads as the engineer’s line', () => {
		expect(methodRoute({ method: 'PATCH', route: '/firm/contacts/{contact_id}' })).toBe(
			'PATCH /firm/contacts/{contact_id}'
		);
	});
});

describe('methodLabel', () => {
	it('says what happened, not how it travelled', () => {
		expect(methodLabel('POST')).toBe('Created');
		expect(methodLabel('PUT')).toBe('Replaced');
		expect(methodLabel('PATCH')).toBe('Updated');
		expect(methodLabel('DELETE')).toBe('Deleted');
		expect(methodLabel('GET')).toBe('Read');
	});

	it('is case-insensitive, and falls back to the verb it was given', () => {
		expect(methodLabel('patch')).toBe('Updated');
		// An unknown verb reads as itself rather than vanishing — the log must not silently
		// mislabel a method nobody anticipated.
		expect(methodLabel('OPTIONS')).toBe('OPTIONS');
	});
});

describe('statusLabel', () => {
	it('names the outcome in English, not a code', () => {
		expect(statusLabel(200)).toBe('Succeeded');
		expect(statusLabel(201)).toBe('Succeeded');
		expect(statusLabel(500)).toBe('Failed');
	});

	it('separates the refusals a reader of an audit log actually distinguishes', () => {
		// "Refused" and "Not found" are different findings in a compliance review; a flat
		// "Rejected" for the whole 4xx family would lose that.
		expect(statusLabel(403)).toBe('Refused');
		expect(statusLabel(404)).toBe('Not found');
		expect(statusLabel(409)).toBe('Conflicted');
		expect(statusLabel(422)).toBe('Rejected');
	});

	it('does not claim success for a status it was not built for', () => {
		expect(statusLabel(102)).toBe('Pending');
	});
});

describe('statusTone', () => {
	it('maps status families to the canonical tones', () => {
		expect(statusTone(200)).toBe('success');
		expect(statusTone(201)).toBe('success');
		expect(statusTone(404)).toBe('warning');
		expect(statusTone(500)).toBe('danger');
		expect(statusTone(102)).toBe('neutral');
	});
});

describe('timestampParts', () => {
	const now = new Date('2026-08-03T14:34:00');

	it('same day: time only, with a relative hint', () => {
		expect(timestampParts('2026-08-03T14:32:00', now)).toEqual({
			absolute: '14:32',
			relative: '2 min ago'
		});
	});

	it('graduates the relative hint sensibly', () => {
		expect(timestampParts('2026-08-03T14:33:50', now).relative).toBe('just now');
		expect(timestampParts('2026-08-03T13:30:00', now).relative).toBe('1 hr ago');
		expect(timestampParts('2026-08-02T14:00:00', now).relative).toBe('yesterday');
		expect(timestampParts('2026-07-28T09:15:00', now)).toEqual({
			absolute: '28 Jul, 09:15',
			relative: '6 days ago'
		});
	});

	it('drops the hint when it stops being one, and keeps the year across years', () => {
		expect(timestampParts('2026-06-01T10:00:00', now).relative).toBeNull();
		expect(timestampParts('2025-12-31T23:59:00', now).absolute).toBe('31 Dec 2025, 23:59');
	});

	it('never throws on rubbish — it is rendering evidence', () => {
		expect(timestampParts('not-a-date', now)).toEqual({ absolute: 'not-a-date', relative: null });
	});
});
