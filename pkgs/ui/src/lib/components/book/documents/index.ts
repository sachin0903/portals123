// The shared documents vocabulary + surfaces for the portal waterfall: the platform portal's
// /documents pages and the admin portal's per-tenant view render the same book components.
//
// Deliberately NOT re-exported from the package root: the documents vocabulary is wide and
// several names (`TaskRow`, `ActivityRow`, `toRow`…) would collide with or crowd the
// `@graphene/ui` namespace. Consumers import the subpath `@graphene/ui/book/documents` (see
// pkgs/ui package.json `exports`), exactly as `@graphene/ui/book/listing` does.

// The extracted page cores (the platform's /documents pages became thin mounts of these; the
// admin portal mounts them read-only over its own loaders by passing no `actions`).
export { default as DocumentsLibrary } from './documents-library.svelte';
export { default as DocumentsLibrarySkeleton } from './documents-library-skeleton.svelte';
export type { DocumentsLibraryActions } from './documents-library.svelte';
export { default as DocumentDetail } from './document-detail.svelte';
export type { DocumentDetailActions } from './document-detail.svelte';
export { default as DocumentUploadDialog } from './document-upload-dialog.svelte';
export { default as RequestDialog } from './request-dialog.svelte';
// Shared by the library and the client record's tab.
export { default as DocumentsBulkBar } from './documents-bulk-bar.svelte';
export { default as DocumentThumb } from './document-thumb.svelte';
export { default as RenderState } from './render-state.svelte';
export { default as SelectBox } from './select-box.svelte';
export * from './selection.js';

// Injected navigation contracts + the notify dialog's prefill shape (`./types.js`).
export type { DocumentsLibraryLinks, DocumentDetailLinks } from './types.js';

// The API ↔ row mapping + request/emails vocabulary (`./documents.js`).
export * from './documents.js';
// The pure display layer: view types, status/tone/event chrome, formatters (`./display.js`).
export * from './display.js';
// Icon + accent per stored file format (lucide components — client bundle only).
export { FORMAT_META } from './format-meta.js';
