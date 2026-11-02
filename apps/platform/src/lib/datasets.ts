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
	/** Shipped by Graphene — no row of the firm's own, so it has no uid but its key. */
	provided?: boolean;
	catalogue_key?: string | null;
}

/** One setting a firm may choose on a provided dataset (DatasetParamOut). */
export interface ApiDatasetParam {
	name: string;
	kind: 'date' | 'int' | 'bool' | 'enum';
	label: string;
	help: string;
	/** VERBATIM, so a relative range like `-12m` survives to be resolved on each render. */
	default: string | number | boolean | null;
	options: { value: string; label: string }[];
	minimum: number | null;
	maximum: number | null;
}

/** An entry in `/datasets/catalogue` (CatalogueEntryOut) — what Graphene provides. */
export interface ApiCatalogueEntry {
	key: string;
	name: string;
	description: string;
	note: string;
	scope: string;
	columns: { name: string; label: string; kind: string }[];
	params: ApiDatasetParam[];
	entitlement: string | null;
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
	inline: DatasetSampleData | null;
	catalogue: { key: string; params: Record<string, string> | null } | null;
}

export interface DatasetVersion {
	uid: string;
	versionNumber: number;
	status: string;
	createdAt: string;
	publishedAt?: string;
	createdBy: string;
	inline?: DatasetSampleData;
	/** For a provided dataset: the entry it draws on and the settings this version was saved with. */
	catalogue?: { key: string; params: Record<string, string> };
}

export function toDatasetVersion(a: ApiDatasetVersion): DatasetVersion {
	return {
		uid: a.uid,
		versionNumber: a.version_number,
		status: a.status,
		createdAt: a.created_at,
		publishedAt: a.published_at ?? undefined,
		createdBy: a.created_by ?? '',
		inline: a.inline ?? undefined,
		catalogue: a.catalogue ? { key: a.catalogue.key, params: a.catalogue.params ?? {} } : undefined
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
		createdBy: a.created_by ?? '',
		provided: a.provided ?? false,
		catalogueKey: a.catalogue_key ?? undefined
	};
}
