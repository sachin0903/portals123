"""The anonymiser must not hand two different Seccl clients the same identity.

It did. `fake_person` derived BOTH name components from one seed —
`FIRST_NAMES[(s+1) % 40]` and `SURNAMES[(s+7) % 30]` — so the pair was a function of
`(s mod 40, s mod 30)`, which takes only `lcm(40, 30) = 120` values because the pool sizes share a
factor of 10. That is a 10x collapse of the 40 x 30 = 1200 space, giving roughly a 31% chance of a
collision in a ten-client book. Two real clients (CX38D07BB, CXF421C15) duly became the same
"Max Oakden", with the same address and the same email, so the demo dataset showed what looked like
a duplicated client.

The email mattered more than the name: it was built from the name alone, so every name collision was
also an email collision — indistinguishable records, and a false positive for the duplicate-email
guard on client create. A shared surname is realistic; a shared email is not.
"""

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

anonymise = pytest.importorskip("anonymise_seccl")


def _ids(n: int) -> list[str]:
    return [f"CX{i:07X}" for i in range(n)]


def test_the_name_pools_carry_no_duplicates_of_their_own() -> None:
    """A repeated entry in a pool would shrink the space silently (COMPANY_SUFFIX has one)."""
    assert len(set(anonymise.FIRST_NAMES)) == len(anonymise.FIRST_NAMES)
    assert len(set(anonymise.SURNAMES)) == len(anonymise.SURNAMES)


def test_the_two_clients_that_actually_collided_now_differ() -> None:
    """The regression case, by id. Both were 'Max Oakden' with one email between them."""
    a = anonymise.fake_person("CX38D07BB")
    b = anonymise.fake_person("CXF421C15")
    assert a["name"] != b["name"]
    assert a["email"] != b["email"]


def test_emails_are_unique_across_a_realistic_book() -> None:
    """The load-bearing property: two clients must never share an email, at any book size."""
    for n in (10, 100, 1000, 4000):
        emails = [anonymise.fake_person(i)["email"] for i in _ids(n)]
        assert len(set(emails)) == n, f"{n - len(set(emails))} duplicate emails at n={n}"


def test_the_name_space_is_the_full_cross_product_not_the_lcm() -> None:
    """Pins the actual fix. If both components are ever re-derived from a single seed, the reachable
    pair count drops back to lcm(40, 30) = 120 and this fails."""
    first_n, surname_n = len(anonymise.FIRST_NAMES), len(anonymise.SURNAMES)
    collapsed = math.lcm(first_n, surname_n)
    names = {anonymise.fake_person(i)["name"] for i in _ids(20_000)}
    assert len(names) > collapsed * 2, (
        f"only {len(names)} distinct names reachable; the single-seed bug caps it at {collapsed}"
    )
    assert len(names) <= first_n * surname_n


def test_identities_stay_stable_across_runs() -> None:
    """Determinism is the point of the seeding — a re-run must not churn every record."""
    assert anonymise.fake_person("CX38D07BB") == anonymise.fake_person("CX38D07BB")
