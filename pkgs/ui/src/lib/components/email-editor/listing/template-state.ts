/**
 * `current_version_status` is the NEWEST version's status, not the template's — reading it raw is wrong (months-published-plus-one-draft reads "Draft"). `EmailTemplateOut` has no `current_published_version_id`, only head status/number + count, so: head published → "Published"; head draft + 1 version → "Draft"; head draft + >1 → "Unpublished changes" (an earlier version's status is unknowable). The labels are the VERSION vocabulary the API publishes, so the badge and the version-history dialog read as one thing — "Live" is a second word for Published and is never used. Shared by both mounts so firm and operations agree.
 */
import type { StatusTone } from '../../ui/status-badge/index.js';

export interface EmailTemplateStateInput {
	current_version_status: string | null;
	version_count: number;
}

export interface EmailTemplateState {
	label: string;
	tone: StatusTone;
	/** One sentence, the consequence — shown next to the badge on hover. */
	hint: string;
}

export function emailTemplateState(t: EmailTemplateStateInput): EmailTemplateState {
	const many = t.version_count > 1;
	if (t.current_version_status === 'published') {
		return {
			label: 'Published',
			tone: 'success',
			hint: 'This is the version recipients get.'
		};
	}
	if (t.current_version_status === 'draft') {
		return many
			? {
					label: 'Unpublished changes',
					tone: 'warning',
					hint: 'The newest version is a draft, so it isn’t being sent yet.'
				}
			: {
					label: 'Draft',
					tone: 'neutral',
					hint: 'Never published — Graphene’s default is sent until you publish this.'
				};
	}
	// No versions at all, or a head version that has been retired to `archived`.
	return {
		label: 'Not published',
		tone: 'neutral',
		hint: many
			? 'The newest version isn’t published.'
			: 'Nothing is published, so Graphene’s default is sent.'
	};
}
