// A firm's own view of the custodians feeding its book: which are connected, when data last
// landed, and whether any of it did not make it.
//
// Under book/ and NOT on the package root, like `book/listing` and `book/tasks`: `DataSource` is
// far too generic a name for the `@graphene/ui` namespace. Consumers import the subpath
// `@graphene/ui/book/platforms` (see pkgs/ui package.json `exports`).
//
// Shared rather than app-local because the admin portal mounts the same surface over a firm's book
// (the portal waterfall), and the firm's read of itself and operations' read of that firm must not
// be two different components that can disagree about what "up to date" means.
export { default as DataSources } from './data-sources.svelte';
export {
	DATA_SOURCE_LABEL,
	dataSourceState,
	type DataSource,
	type DataSourceRun,
	type DataSourceState
} from './types.js';
