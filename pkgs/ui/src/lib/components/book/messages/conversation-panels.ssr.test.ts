/**
 * SSR coverage for the conversation pane's header row — Chat and Files, and the search box.
 *
 * Two tabs, because only two things here are views of the CONVERSATION: what was said and what
 * was sent. Everything about the CLIENT — their accounts, notes, the people on the thread and the
 * work outstanding — is in the rail, and is covered by `message-rail.ssr.test.ts`.
 *
 * The Chat tab is the caller's own snippet, so it is covered where each caller is.
 */
import { describe, expect, it } from 'vitest';
import { createRawSnippet } from 'svelte';
import { render } from 'svelte/server';
import ConversationPanels from './conversation-panels.svelte';
import type { ApiAttachment, ApiMessage, ConversationTab } from './messages.js';

const message = (over: Partial<ApiMessage> = {}): ApiMessage => ({
	id: 1,
	conversation_id: 1,
	conversation_uid: 'cnv_9b2e',
	sender_kind: 'client',
	sender_user_id: null,
	sender_name: null,
	sender_role: null,
	body: 'hello',
	created_at: '2026-08-24T08:57:00Z',
	attachments: [],
	request_item: null,
	...over
});

const attachment = (over: Partial<ApiAttachment> = {}): ApiAttachment => ({
	id: 1,
	uid: 'att_1',
	filename: 'suitability.pdf',
	content_type: 'application/pdf',
	size_bytes: 2048,
	...over
});

const download = (conversationUid: string, attachmentUid: string) =>
	`/chats/${conversationUid}/attachments/${attachmentUid}`;

/** Renders with a tab already selected — SSR runs no click. */
const html = (tab: ConversationTab, props: Record<string, unknown> = {}) =>
	render(ConversationPanels, {
		props: {
			tab,
			messages: [],
			download,
			children: createRawSnippet(() => ({ render: () => '<p>thread</p>' })),
			...props
		}
	}).body;

describe('the conversation tabs', () => {
	it('collects every file on the thread, newest first, both directions', () => {
		const body = html('files', {
			messages: [
				message({ id: 1, attachments: [attachment({ uid: 'att_1', filename: 'first.pdf' })] }),
				message({
					id: 2,
					sender_kind: 'staff',
					sender_user_id: 'u-1',
					sender_name: 'Craig Currie',
					sent_by_me: true,
					attachments: [attachment({ id: 2, uid: 'att_2', filename: 'second.pdf' })]
				})
			]
		});
		expect(body.indexOf('second.pdf')).toBeLessThan(body.indexOf('first.pdf'));
		expect(body).toContain('Sent');
		expect(body).toContain('Received');
		expect(body).toContain('/chats/cnv_9b2e/attachments/att_2');
	});

	it('names FILES in the files panel’s empty state, not the conversation', () => {
		// "Nothing has been sent in this conversation yet" is a sentence about the CONVERSATION, and
		// this panel sits beside a thread that usually has plenty in it — so it read as a statement
		// that the whole thread was empty. An empty state names its own panel's kind.
		const body = html('files');
		expect(body).toContain('No files yet');
		expect(body).toContain('Anything sent or received on this conversation is collected here.');
		// `EmptyState`, not a bare muted paragraph — the one component the design language names for
		// this, and the hand-rolled `<p>` is exactly what it lists as the thing it replaced.
		expect(body).toContain('data-slot="empty-state"');
		expect(body).not.toContain('Nothing has been sent in this conversation yet.');
	});

	it('offers search as a button, not a box sitting open on every thread', () => {
		// A permanent input in this row was a field competing with the two tabs for the reader's eye
		// on every conversation, most of which nobody searches. It is a control the same shape as the
		// tabs beside it, and it opens into the box when it is pressed.
		const body = html('files');
		expect(body).toContain('aria-label="Search this conversation"');
		// LABELLED, like the two tabs beside it: a lone magnifier in a row of labelled controls
		// reads as decoration rather than as the third thing you can press.
		expect(body).toContain('Search');
		expect(body).not.toContain('placeholder="Search this conversation..."');
	});

	it('reserves its own row, so a long thread cannot slide under the tabs', () => {
		// A header a conversation can push off the top is a header you cannot use at the moment you
		// want it.
		const body = html('files');
		const at = body.indexOf('role="tablist"');
		const row = body.slice(Math.max(0, at - 400), at);
		expect(row).toContain('shrink-0');
	});
});
