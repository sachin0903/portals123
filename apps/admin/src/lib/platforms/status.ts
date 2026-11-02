// Labels, badge variants and canvas colour tokens shared across the Platforms
// concept UI, so the DAG, legend and detail rail always agree.

import type { StatusTone } from '@graphene/ui';
import type {
	ChangeState,
	ConnectionStatus,
	FileStatus,
	Materialisation,
	PlatformRole,
	RunStatus,
	RunTrigger,
	StreamStatus,
	Transport
} from './types';

// pkgs/ui doesn't re-export BadgeVariant from its root index; mirror the union here.
export type BadgeVariant = 'default' | 'secondary' | 'destructive' | 'outline' | 'ghost' | 'link';

export const STATE_LABEL: Record<ChangeState, string> = {
	unchanged: 'Unchanged',
	added: 'Added',
	nonbreaking: 'Refresh',
	breaking: 'Rebuild'
};

export const MAT_GLYPH: Record<Materialisation, string> = {
	stream: '≈',
	table: '▦',
	view: '◇',
	ephemeral: '◌',
	sink: '⇥'
};

/**
 * Theme CSS custom property each change-state resolves to. The lineage canvas
 * reads these at runtime (getComputedStyle) so light/dark both work; the legend
 * and detail-rail dots reference the same vars from CSS.
 */
export const STATE_VAR: Record<ChangeState, string> = {
	unchanged: '--muted-foreground',
	added: '--positive',
	nonbreaking: '--brand',
	breaking: '--destructive'
};

/** The running pulse + left bar for an in-flight model. */
export const RUNNING_VAR = '--ring';

export const ROLE_LABEL: Record<PlatformRole, string> = {
	custodian: 'Custodian',
	scheme_administrator: 'Scheme administrator',
	wrapper_provider: 'Wrapper provider'
};

export const TRANSPORT_LABEL: Record<Transport, string> = {
	api: 'API',
	webhooks: 'Webhooks',
	sftp: 'SFTP'
};

export const RUN_TRIGGER_LABEL: Record<RunTrigger, string> = {
	cron: 'Cron',
	manual: 'Manual',
	api: 'API',
	stream: 'Stream',
	file: 'File'
};

export type AnyStatus = ConnectionStatus | RunStatus | StreamStatus | FileStatus;

/**
 * The semantic tone `@graphene/ui`'s `StatusBadge` renders each of these statuses in.
 *
 * A MAPPING, not a component. This used to be a `{ variant, dotVar }` table behind a local
 * `StatusBadge.svelte` — a second status chip, with `Badge variant="secondary"` standing in for
 * "good", which is the exact pairing docs/design-language.md rules out. The vocabulary is still
 * this section's own (a run is `succeeded`, a stream is `live`), so translating it lives here;
 * drawing the chip does not.
 */
export const STATUS_TONE: Record<AnyStatus, StatusTone> = {
	connected: 'success',
	succeeded: 'success',
	live: 'success',
	loaded: 'success',
	running: 'info',
	processing: 'info',
	// Degraded is not failed: the platform is answering, just not well. Warning keeps the two
	// distinguishable, where the old table drew it in the same outline as `idle`.
	degraded: 'warning',
	queued: 'neutral',
	cancelling: 'warning',
	cancelled: 'neutral',
	idle: 'neutral',
	paused: 'neutral',
	error: 'danger',
	failed: 'danger'
};

/** In-flight statuses take the badge's pulsing dot. */
export const STATUS_PULSES: ReadonlySet<AnyStatus> = new Set<AnyStatus>([
	'running',
	'processing',
	'cancelling'
]);

export function stateBadgeVariant(state: ChangeState): BadgeVariant {
	return state === 'breaking' ? 'destructive' : state === 'unchanged' ? 'outline' : 'secondary';
}

const escapeHtml = (s: string) =>
	s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

/**
 * Minimal SQL syntax highlighting for the detail rail. Escapes the input
 * before inserting any markup, so the output is safe for {@html}.
 */
export function highlightSql(sql: string): string {
	return escapeHtml(sql)
		.replace(/(--[^\n]*)/g, '<span class="text-muted-foreground italic">$1</span>')
		.replace(/(\/\*[\s\S]*?\*\/)/g, '<span class="text-muted-foreground italic">$1</span>')
		.replace(
			/\b(select|from|where|join|left|union all by name|union|group by|order by|partition by|over|not in|exclude|as|and|or|on|desc|limit|using)\b/gi,
			'<span class="text-brand font-medium">$1</span>'
		)
		.replace(
			/\b(count|sum|min|max|lower|row_number|coalesce|date_trunc|read_json)\b/g,
			'<span class="text-primary">$1</span>'
		);
}
