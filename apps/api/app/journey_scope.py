"""Whose journeys a staff caller may see.

The listing, the detail, every write against a journey uid, and staff search all
ask this. Keeping the rule on the list alone is how a scoped adviser found a
journey the list had hidden and then opened or cancelled it: these reads run as
``service_role``, so there is no RLS backstop.

The SQL expects ``flows.executions e`` and ``crm.applications a``. ``:see_all``
is true for a caller who reads the whole firm (``contacts:read_all``, or an
operator granted that on the ``?tenant=`` path).
"""

from __future__ import annotations

from app.auth import Principal, is_system
from app.permissions import CONTACTS_READ_ALL, satisfies

# Assigned to the caller, the adviser on it, or a journey for a client whose
# team they are on — whether that client is a profile case or the execution's
# own subject. The same ways the clients list scopes a book.
IN_BOOK_SQL = (
    "(:see_all "
    " OR e.assignee_user_id = auth.uid() "
    " OR COALESCE(a.adviser_id, e.created_by) = auth.uid() "
    " OR EXISTS (SELECT 1 FROM flows.cases book_case "
    "            JOIN crm.client_advisers book_ca "
    "              ON book_ca.contact_id = book_case.subject_id "
    "            WHERE book_case.execution_id = e.id "
    "              AND book_case.kind = 'profile' "
    "              AND book_ca.adviser_id = auth.uid()) "
    " OR (e.subject_type = 'contact' AND EXISTS ("
    "      SELECT 1 FROM crm.client_advisers book_subj "
    "      WHERE book_subj.contact_id = e.subject_id "
    "        AND book_subj.adviser_id = auth.uid())))"
)


def sees_whole_firm(principal: Principal) -> bool:
    """True when the caller is not limited to their own book."""
    return is_system(principal) or satisfies(set(principal.permissions), CONTACTS_READ_ALL.slug)
