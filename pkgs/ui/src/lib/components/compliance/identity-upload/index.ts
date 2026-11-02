// New identity photos for a client, uploaded on their behalf: one dialog for Graphene operations
// (the admin AML console) and the firm's advisers (the client's Compliance tab). Each portal posts
// to its own form action and says who reads the note.
export { default as IdentityUploadDialog } from './identity-upload-dialog.svelte';
export { default as IdentityUploadButton } from './identity-upload-button.svelte';
export {
	IDENTITY_DOCUMENT_TYPES,
	IDENTITY_PHOTO_FIELDS,
	documentTypeLabel,
	uploadSlots,
	type IdentityDocumentType,
	type IdentityPhotoField,
	type IdentityPhotoSlot,
	type IdentityUpload
} from './identity-upload.js';
