import type {
	Form,
	FormEntityType,
	FormField,
	FormScoring,
	FormStatus
} from '../../forms/types.js';
import type { DocumentType } from '../../templates/types.js';

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

export const toForm = (form: ApiForm): Form => ({
	uid: form.uid,
	code: form.code,
	name: form.name,
	description: form.description ?? undefined,
	status: form.status as FormStatus,
	entityType: form.entity_type as FormEntityType,
	documentType: (form.document_type as DocumentType | null) ?? undefined,
	questionnaireDatasetCode: form.questionnaire_dataset_code ?? undefined,
	scoring: form.scoring ?? null,
	fieldCount: form.field_count,
	createdAt: form.created_at,
	createdBy: form.created_by ?? ''
});

export const toField = (field: ApiFormField): FormField => ({
	uid: field.uid,
	fieldKey: field.field_key,
	fieldType: field.field_type,
	label: field.label,
	section: field.section,
	description: field.description,
	required: field.required,
	conditions: field.conditions,
	validation: field.validation,
	options: field.options,
	sortOrder: field.sort_order
});

export const toResponse = (response: ApiFormResponse): FormResponseRow => ({
	uid: response.uid,
	submittedAt: response.submitted_at,
	submittedBy: response.submitted_by,
	via: response.via,
	answers: response.answers
});

export const toFieldPayload = (field: FormField): Record<string, unknown> => ({
	uid: field.uid,
	field_key: field.fieldKey,
	field_type: field.fieldType,
	label: field.label,
	section: field.section,
	description: field.description,
	required: field.required,
	conditions: field.conditions,
	validation: field.validation,
	options: field.options,
	sort_order: field.sortOrder
});
