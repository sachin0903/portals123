// The form a `complete_form` request asks the client to fill is now the REAL builder form, fetched
// (server-side, ownership-checked) from `GET /documents/requests/{req}/items/{item}/form` and mapped
// to the shared `FormField` shape the DynamicForm renderer consumes. `dataSources` (static reference
// data like nationalities) still comes from the shared bundle — it isn't tenant form state.
import { formDataSources, type FormField } from '@graphene/ui';

export { formDataSources };

/** One field as the render endpoint returns it (FormFieldOut). */
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

/** The render endpoint's response (RequestFormOut). */
export interface ApiRequestForm {
	form_uid: string;
	name: string;
	description: string | null;
	fields: ApiFormField[];
	answers?: Record<string, unknown>;
}

export function toFormField(a: ApiFormField): FormField {
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
