// The client's chat types. The message, attachment and request-item shapes are the SAME rows
// the platform reads, so they come from `@graphene/ui/book/messages` rather than being restated
// here — this file used to carry its own copy of all of them, plus its own `formatDayLabel`,
// `formatMessageTime`, `groupMessagesByDay` and `formatFileSize`, drifting from the shared ones
// that the thread and composer actually render with. Only what differs stays: the client's
// conversation row, which `/messaging/thread` returns in a narrower shape than the staff inbox's.
export {
	ATTACHMENT_ACCEPT,
	type ApiAttachment,
	type ApiMessage,
	type ApiRequestItem
} from '@graphene/ui/book/messages';

export type ApiConversation = {
	id: number;
	/** What a `/chats/{uid}` path names (0112_messaging_uids); docs/api.md → "Addressing". */
	uid: string;
	tenant_id: number;
	contact_id: number;
	adviser_user_id: string | null;
	/** The client's own read watermark — where the "New" bar goes on their next visit. */
	client_last_read_at: string | null;
	/** The firm's read watermark — "Seen" under the last of the client's messages at or before it. */
	staff_last_read_at: string | null;
	created_at: string;
	updated_at: string;
	unread: boolean;
};
