"""Package marker, and it exists for mypy rather than for Python.

`sei:typecheck` runs `mypy src tests` — the only one of the three vendor packages that
typechecks its tests, which is worth keeping. Two files named `conftest.py` under one un-packaged
root are two modules called `conftest` as far as mypy is concerned, and it refuses with
"Duplicate module named conftest" before checking anything at all.

Making this directory a package renames the module and the collision goes. pytest is indifferent:
conftest discovery works the same inside a package.
"""
