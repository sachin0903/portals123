// The email surfaces' shared primitives. First (and so far only) member: the sandboxed frame every
// email-body preview renders through — the modal, the editor's live pane and any list thumbnail.
// Author-controlled HTML has exactly one safe rendering path in this product, and it is a component
// rather than an attribute people copy (see email-preview-frame.svelte's header).
//
// Deliberately NOT re-exported from the package root: `EmailPreviewFrame` is feature-specific, and
// the root namespace is for primitives every portal composes. Consumers import the subpath
// `@graphene/ui/email` (see pkgs/ui package.json `exports`), exactly as `@graphene/ui/book/listing`
// and `@graphene/ui/automations` do.
export { default as EmailPreviewFrame } from './email-preview-frame.svelte';
