/**
 * Datasets API ↔ UI mapping for the platform portal. The FastAPI `/datasets` surface speaks
 * snake_case (DatasetOut / DatasetDetailOut); the shared `@graphene/ui` `Dataset` type is camelCase.
 * These mappers are the single translation point — pages work in the UI shape, loaders touch the API
 * one (mirrors `$lib/forms.ts`).
 */
import type { Dataset, DatasetFormat, DatasetScope } from '@graphene/ui';

/** The `/datasets` list shape (DatasetOut). */
export interface ApiDataset {
	uid: string;
	code: string;
	name: string;
	description: string | null;
	format: string;
	scope: string;
	enabled: boolean;
	version_count: number;
	published_version_number: number | null;
	created_at: string;
	created_by: string | null;
}

/** A tabular preview / resolved snapshot of a dataset's data. */
export interface DatasetSampleData {
	columns: string[];
	rows: unknown[][];
}

/** The `/datasets/{uid}` shape (DatasetDetailOut) — a dataset plus its optional sample + columns. */
export interface ApiDatasetDetail extends ApiDataset {
	sample: DatasetSampleData | null;
	columns: string[];
}

/** A row in `/datasets/{uid}/versions` (DatasetVersionOut) — the authoring history. */
export interface ApiDatasetVersion {
	uid: string;
	version_number: number;
	status: string;
	created_at: string;
	published_at: string | null;
	created_by: string | null;
	sql_query: string | null;
	inline: DatasetSampleData | null;
}

export interface DatasetVersion {
	uid: string;
	versionNumber: number;
	status: string;
	createdAt: string;
	publishedAt?: string;
	createdBy: string;
	sqlQuery?: string;
	inline?: DatasetSampleData;
}

export function toDatasetVersion(a: ApiDatasetVersion): DatasetVersion {
	return {
		uid: a.uid,
		versionNumber: a.version_number,
		status: a.status,
		createdAt: a.created_at,
		publishedAt: a.published_at ?? undefined,
		createdBy: a.created_by ?? '',
		sqlQuery: a.sql_query ?? undefined,
		inline: a.inline ?? undefined
	};
}

export function toDataset(a: ApiDataset): Dataset {
	return {
		uid: a.uid,
		code: a.code,
		name: a.name,
		description: a.description ?? undefined,
		format: a.format as DatasetFormat,
		scope: a.scope as DatasetScope,
		enabled: a.enabled,
		versionCount: a.version_count,
		publishedVersionNumber: a.published_version_number ?? undefined,
		createdAt: a.created_at,
		createdBy: a.created_by ?? ''
	};
}
