"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""
<%
_extra = depends_on is not None or branch_labels is not None
%>
% if _extra:
from collections.abc import Sequence

% endif
from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

# revision identifiers, used by Alembic.
## ``revision`` and ``down_revision`` are read as attributes. ``branch_labels``
## and ``depends_on`` are loaded with getattr(..., None), so an unread ``= None``
## is omitted. The binding is the in-file read of the two required names.
revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
% if branch_labels is not None:
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
% endif
% if depends_on is not None:
depends_on: str | Sequence[str] | None = ${repr(depends_on)}
% endif
_ = (revision, down_revision)  # Alembic reads these names off the module


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
