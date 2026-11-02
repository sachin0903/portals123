/**
 * Templates API ↔ UI mapping for the platform portal. The FastAPI `/templates` surface speaks
 * snake_case (TemplateOut / VersionOut); the shared `@graphene/ui` `Template`/`TemplateVersion`
 * types are camelCase. Single translation point — pages work in the UI shapes, loaders/actions in
 * the API ones (mirrors `$lib/documents.ts` / `$lib/forms.ts`).
 *
 * A version's `schema` is opaque (the GrapesJS editor's PersistBlob); it flows through untyped.
 */
import type {
	Template,
	TemplateVersion,
	TemplateStatus,
	DocumentType,
	Renderer
} from '@graphene/ui';

export interface ApiTemplate {
	uid: string;
	code: string;
	name: string;
	document_type: string;
	description: string | null;
	linked_datasets: string[];
	input_form_code: string | null;
	version_count: number;
	current_version_number: number | null;
	current_version_status: string | null;
	archived_at: string | null;
	created_at: string;
	created_by: string | null;
}

export interface ApiVersion {
	uid: string;
	version_number: number;
	status: string;
	renderer: string;
	created_at: string;
	created_by: string | null;
	published_at: string | null;
	published_by: string | null;
}

export interface ApiVersionDetail extends ApiVersion {
	schema: Record<string, unknown>;
}

export function toTemplate(a: ApiTemplate): Template {
	return {
		uid: a.uid,
		code: a.code,
		name: a.name,
		documentType: a.document_type as DocumentType,
		description: a.description ?? undefined,
		currentVersionNumber: a.current_version_number ?? undefined,
		currentVersionStatus: (a.current_version_status as TemplateStatus | null) ?? undefined,
		archivedAt: a.archived_at ?? undefined,
		createdAt: a.created_at,
		createdBy: a.created_by ?? '',
		versionCount: a.version_count,
		linkedDatasets: a.linked_datasets,
		inputFormCode: a.input_form_code ?? undefined
	};
}

export function toVersion(a: ApiVersion): TemplateVersion {
	return {
		uid: a.uid,
		versionNumber: a.version_number,
		status: a.status as TemplateStatus,
		renderer: a.renderer as Renderer,
		createdAt: a.created_at,
		createdBy: a.created_by ?? '',
		publishedAt: a.published_at ?? undefined,
		publishedBy: a.published_by ?? undefined
	};
}
