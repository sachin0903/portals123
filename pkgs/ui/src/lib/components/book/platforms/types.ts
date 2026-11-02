/**
 * A firm's own view of the custodians feeding its book.
 *
 * Structurally typed, like `book/tasks`' row types, so both portals can mount the same component
 * over their own loaders — the admin's read of one firm and the firm's read of itself are the
 * same shape by construction.
 */

/** What one merge run did for this firm. Counts only — an entity's own name is operations' view. */
export type DataSourceRun = {
	run_at: string;
	created: number;
	updated: number;
	rejected: number;
	/** Entities that failed and left the walk to carry on, so the book is PARTIAL. */
	failed: number;
};

export type DataSource = {
	provider: string;
	name: string;
	role: string;
	description: string;
	/** `api` | `webhooks` | `sftp` — how the data arrives. */
	transports: string[];
	/** Stamped by the merge. Null means nothing has ever landed for this firm. */
	last_merged_at: string | null;
	/** Null when nothing has run — which is a different answer from a run that wrote nothing. */
	last_run: DataSourceRun | null;
};

/** How current a source is, for the badge. */
export type DataSourceState = 'current' | 'stale' | 'partial' | 'never';

/** Anything older than this reads as stale: every custodian here syncs at least daily. */
const STALE_AFTER_HOURS = 36;

/**
 * The state a source is in, from what it last did.
 *
 * `never` is its own state and not a kind of `stale`. "Nothing has ever landed for this firm" is a
 * setup problem someone has to be told about — a connection enabled with no active storage row
 * registers no models at all and runs silently forever — where "stale" is a run that has not
 * happened lately. Collapsing them is how the pipeline's whole class of silent failure hides.
 */
export function dataSourceState(source: DataSource, now: Date): DataSourceState {
	if (!source.last_merged_at) return 'never';
	if (source.last_run?.failed) return 'partial';
	const hours = (now.getTime() - new Date(source.last_merged_at).getTime()) / 3_600_000;
	return hours > STALE_AFTER_HOURS ? 'stale' : 'current';
}

export const DATA_SOURCE_LABEL: Record<DataSourceState, string> = {
	current: 'Up to date',
	stale: 'Not updated recently',
	partial: 'Partly updated',
	never: 'No data yet'
};
