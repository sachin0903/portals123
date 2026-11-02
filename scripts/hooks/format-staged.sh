#!/usr/bin/env bash
# Format the STAGED files and re-stage what changed — the pre-commit half of the git hooks.
#
# moon's hooks are plain shell commands, so the file-scoping lefthook did declaratively
# (`glob:` + `{staged_files}` + `stage_fixed: true`) lives here instead. That is the whole of what
# was lost in the move, and it is worth keeping: formatting the WHOLE repo on every commit is slow
# enough that people start passing --no-verify, and the project `format` tasks are `--check` only.
#
# Reads NUL-delimited paths so a filename with a space cannot split into two, and filters on
# ACMR (added/copied/modified/renamed) so a staged DELETION is not handed to a formatter that would
# fail on the missing file.
#
# One hazard is inherited deliberately rather than fixed: a file with both staged and unstaged
# changes gets all of it staged, because the formatter rewrites the working copy and this re-adds
# the file. lefthook's `stage_fixed` did exactly the same, and a stash-based fix would change
# behaviour nobody asked to change. Commit hunks of a half-edited file and you will notice it.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

staged() {
  # $1 is an extended-glob pattern of extensions, e.g. 'ts|svelte'.
  git diff --cached --name-only --diff-filter=ACMR -z \
    | while IFS= read -r -d '' path; do
        [[ $path =~ \.($1)$ ]] && printf '%s\0' "$path"
      done
}

# `git add` only what a formatter touched; nothing staged means nothing to do, and passing an empty
# argument list to prettier would format the entire repository.
run_on() {
  local pattern="$1"
  shift
  local -a files=()
  while IFS= read -r -d '' path; do files+=("$path"); done < <(staged "$pattern")
  ((${#files[@]})) || return 0
  "$@" "${files[@]}"
  git add -- "${files[@]}"
}

run_on 'js|ts|svelte|json|css|md' pnpm exec prettier --write
run_on 'py' uv run --project apps/api ruff format
# `--fix` writes what it can; anything left is a real finding and its non-zero exit stops the commit,
# which is what lefthook's lint-py did.
run_on 'py' uv run --project apps/api ruff check --fix
