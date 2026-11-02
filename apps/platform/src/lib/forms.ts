/**
 * Forms API ↔ UI mapping for the platform (adviser) portal. The FastAPI `/forms` surface speaks
 * snake_case (FormOut / FormFieldOut); the shared `@graphene/ui` `Form`/`FormField` types are
 * camelCase. These mappers are the single translation point — the pages work in the UI shapes and
 * only the loaders/actions touch the API ones (mirrors `$lib/documents.ts`).
 */
import type {
	Form,
	FormField,
	FormScoring,
	FormStatus,
	FormEntityType,
	DocumentType
} from '@graphene/ui';

/** The `/forms` list/get shape (FormOut). */
export interface ApiForm {
	uid: string;
	code: string;
	name: string;
	description: string | null;
	status: string;
	entity_type: string;
	document_type: string | null;
	questionnaire_dataset_code: string | null;
	scoring?: FormScoring | null;
	field_count: number;
	created_at: string;
	created_by: string | null;
}

/** The `/forms/{uid}/fields` shape (FormFieldOut). */
export interface ApiFormField {
	uid: string;
	field_key: string;
	field_type: string;
	label: string;
	section: string | null;
	description: string | null;
	required: boolean;
	conditions: FormField['conditions'];
	validation: FormField['validation'];
	options: FormField['options'];
	sort_order: number;
}

export function toForm(a: ApiForm): Form {
	return {
		uid: a.uid,
		code: a.code,
		name: a.name,
		description: a.description ?? undefined,
		status: a.status as FormStatus,
		entityType: a.entity_type as FormEntityType,
		documentType: (a.document_type as DocumentType | null) ?? undefined,
		questionnaireDatasetCode: a.questionnaire_dataset_code ?? undefined,
		scoring: a.scoring ?? null,
		fieldCount: a.field_count,
		createdAt: a.created_at,
		createdBy: a.created_by ?? ''
	};
}

export function toField(a: ApiFormField): FormField {
	return {
		uid: a.uid,
		fieldKey: a.field_key,
		fieldType: a.field_type,
		label: a.label,
		section: a.section,
		description: a.description,
		required: a.required,
		conditions: a.conditions,
		validation: a.validation,
		options: a.options,
		sortOrder: a.sort_order
	};
}

/** One submission in `GET /forms/{uid}/responses` (FormResponseListItem). */
export interface ApiFormResponse {
	uid: string;
	submitted_at: string;
	submitted_by: string | null;
	via: string;
	answers: Record<string, unknown>;
}

export interface FormResponseRow {
	uid: string;
	submittedAt: string;
	submittedBy: string | null;
	via: string;
	answers: Record<string, unknown>;
}

export function toResponse(a: ApiFormResponse): FormResponseRow {
	return {
		uid: a.uid,
		submittedAt: a.submitted_at,
		submittedBy: a.submitted_by,
		via: a.via,
		answers: a.answers
	};
}

/** A FormField as the `PUT /forms/{uid}/fields` request expects each entry (FormFieldIn). */
export function toFieldPayload(f: FormField): Record<string, unknown> {
	return {
		uid: f.uid,
		field_key: f.fieldKey,
		field_type: f.fieldType,
		label: f.label,
		section: f.section,
		description: f.description,
		required: f.required,
		conditions: f.conditions,
		validation: f.validation,
		options: f.options,
		sort_order: f.sortOrder
	};
}
