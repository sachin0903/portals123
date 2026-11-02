"""A package, not a bare directory — `test_live_lifecycle` imports `anchors` relatively, and
without this pytest's rootdir insertion makes that "attempted relative import with no known
parent package". It also keeps mypy from seeing two modules called `conftest`.
"""
