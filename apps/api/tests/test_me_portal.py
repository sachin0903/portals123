"""``GET /me/portal`` returns the portal already resolved for this request.

Classifying the token again would drop the portal the caller named. A staff login that holds
both platform and operations, through one shared application, would come back as platform,
and the admin portal would refuse a session that does have access.
"""

import asyncio

from app.auth import Principal
from app.routers.me import my_portal


def test_me_portal_keeps_the_portal_already_resolved() -> None:
    assert asyncio.run(my_portal(Principal(user_id="user_1", portal="operations"))) == {
        "portal": "operations"
    }
    assert asyncio.run(my_portal(Principal(user_id="user_1"))) == {"portal": None}
