// Pure, client-safe presentation helpers. MUST NOT import server-only modules ($env/private,
// etc.) — these are used inside `.svelte` components (the client bundle). API/server helpers
// live in `$lib/api` (which does import private env), so keep the two apart.

// (`clientName` lives in `@graphene/ui` — book/clients — with the shared book surfaces;
// import it from there. Compact GBP for dense CRM surfaces is `formatGBPCompact`, also from
// `@graphene/ui` — see docs/gotchas.md.)
