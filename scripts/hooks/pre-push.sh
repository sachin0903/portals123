#!/usr/bin/env bash
# The pre-push half of the git hooks: the gates CI runs, scoped to what this branch changed.
#
# SCOPED LIKE CI. Every moon step is `moon ci --base`, the affected-detection CI's own `moon ci`
# uses, so it expands to real downstream dependents (a change to pkgs/ui re-checks platform, admin
# and client) and skips the rest. A change to a ROOT config file is treated as affecting everything,
# same as CI. Pushing a branch that touched one app costs that app, not the whole repo.
#
# THE BASE IS WHERE THIS BRANCH LEFT MAIN, from `origin/main`, not local `main`. Local main is
# whatever it was at the last pull: when it is behind, every commit since counts as "changed by this
# branch" and the affected set grows to most of the repo. `origin/main` moves with every fetch.
#
# "Changed" means the WORKING TREE against that base, uncommitted edits included, because that is
# what the checks read: `moon ci --base` without `--head` compares the working tree, and the
# client-drift test below does the same.
#
# Steps run in order and `set -e` stops at the first failure. Inside a step moon runs the tasks side
# by side and prefixes every output line with its task, so a failure still says where it is.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

base=$(git merge-base HEAD origin/main 2>/dev/null || git merge-base HEAD main)

# The whole uv WORKSPACE, never `--project apps/api`: that reconciles the one shared venv down to
# apps/api's closure and uninstalls every sibling member's packages (services/*, and everything
# under them). CI does not notice because its venv starts empty; a local one does not.
uv sync --frozen

# CLIENT DRIFT: regenerate the TS API client + permission constants and fail if the committed files
# disagree. Only when something they are generated FROM changed: the API, the Python packages it
# imports, the client package itself, or a lockfile (a generator upgrade changes the output too).
# It is not a moon task with inputs, so moon cannot skip it; this check is what does. CI's
# `client-drift` job runs it on every pull request regardless, so a miss here is caught there.
drift_inputs=(
  apps/api
  pkgs/api-client
  pkgs/api-log
  pkgs/book-sink
  pkgs/morgan-lloyd
  pkgs/seccl
  pkgs/sei
  pyproject.toml
  uv.lock
  pnpm-lock.yaml
)
if ! git diff --quiet "$base" -- "${drift_inputs[@]}"; then
  moon run api-client:generate api:generate-permissions
  git diff --exit-code pkgs/api-client/src pkgs/api-client/permissions.ts
else
  echo "pre-push: client drift skipped, nothing it is generated from changed since $base"
fi

# Static checks in ONE call, so lint, svelte-check and mypy run at the same time.
moon ci :lint :check :typecheck --base "$base"

# Tests on their own, AFTER the static checks: several suites are timing-sensitive under load
# (see pkgs/ui/vite.config.ts), so they do not share the machine with svelte-check.
#
# `auth:test` runs on every push, affected or not; `moon run` never skips a task for being
# unaffected. When it is affected too, the second pass is a cache hit.
moon run auth:test
moon ci :test --base "$base"
