/**
 * Shared email-template editor (GrapesJS canvas, Insert-field menu, preview modal, list dialogs) as a `@graphene/ui/email-editor` subpath so grapesjs stays off the root; mounted by platform + admin, app-agnostic — save/publish/navigation come from the app.
 */
export { default as EmailEditor } from './email-editor.svelte';
export { default as EmailTemplateEditorPage } from './email-template-editor-page.svelte';
export { default as PreviewDialog } from './preview-dialog.svelte';
export { default as VersionHistoryDialog } from './version-history-dialog.svelte';
export { default as TemplateSettingsDialog } from './template-settings-dialog.svelte';
// Image picker + its inbox-fetch rules are exported separately: the send-side checks read the rules too.
export { default as ImageSource } from './image-picker/image-source.svelte';
export {
	classifyImageSource,
	describeImageSource,
	isUsableInEmail,
	isMergeSource,
	checkPastedLink,
	tenantRelayPath
} from './image-picker/source.js';
export type { ImageSourceKind, ImageLibrary, LibraryImage } from './image-picker/source.js';
export { createPreviewFetcher } from './preview.js';
export { defaultAudienceFor, readAudience } from './preview.js';
export type { EmailPreview, PreviewAudience, PreviewPayload, PreviewResult } from './preview.js';
export { createTestSender, testSendFeedback } from './send-test.js';
export type { TestSendOutcome, TestSendResult, TestSendFeedback } from './send-test.js';
export { mergeFieldsFor } from './merge-fields.js';
export type { MergeField, MergeFieldGroup } from './merge-fields.js';
export { defaultComposeFields } from './default-content.js';
export type { EmailDefaultContent } from './default-content.js';
// `EMAIL_SWATCH_KEYS` deliberately NOT re-exported (re-deriving could pick a different subset than `emailBrandColours` shows); its own test imports it directly.
export {
	emailBrandColours,
	emailMastheadImage,
	emailMastheadPreview,
	type MastheadPreview
} from './brand-colours.js';

export {
	EmailTemplatesListing,
	emailTemplateState,
	categoryFor,
	codeState,
	createBlocker,
	DEFAULT_CATEGORY
} from './listing/index.js';
export type {
	EmailTemplateRow,
	EmailCodeOption,
	EmailTemplatesListingLinks,
	EmailTemplatesListingActions,
	EmailTemplateState,
	EmailTemplateStateInput,
	CodeState,
	CreateBlocker,
	TemplateCodeRow
} from './listing/index.js';
