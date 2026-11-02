## What & why

<!-- What does this change do, and what problem does it solve? -->

## Checklist

- [ ] Commits follow Conventional Commits
- [ ] Lint, format, type-check and tests pass locally (`moon run :lint :format :check :typecheck :test`)
- [ ] If the API surface changed, the generated client was regenerated (`moon run api-client:generate`)

## Database

- [ ] No schema change, **or** migration is backwards-compatible (expand/contract) with the running version
- [ ] Any new tenant-scoped table ships its RLS policy in the same migration
- [ ] Long operations use `CONCURRENTLY` / a sane `lock_timeout`

## Security & compliance

- [ ] No secrets committed
- [ ] Access/permission changes are intentional and least-privilege
- [ ] Auditable actions write to the audit log
