"""A client has a mobile and a phone, both plain text.

Revision ID: 0240_contact_mobile_phone
Revises: 0239_contact_closed_lists
Create Date: 2026-10-08

``alembic_version.version_num`` is ``varchar(32)``; this id is 24 characters.

The DDL and its reasoning live in the sibling SQL file. Downgrade puts the
labelled list back from ``phone`` and renames ``mobile`` to ``mobile_phone``.
A label that was home or work is not restored: the number comes back as ``other``.
"""

from pathlib import Path

from alembic import op

revision: str = "0240_contact_mobile_phone"
down_revision: str | None = "0239_contact_closed_lists"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

_INDEX = "contacts_search_trgm"


def _rebuild_index() -> None:
    # CONCURRENTLY cannot run in the migration transaction. Drop first so a retry
    # heals an INVALID shell left by a cancelled build. Dropping `_search` already
    # drops the index; this DROP is the retry.
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = '5s'")
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS crm.{_INDEX}")
        op.execute(
            f"CREATE INDEX CONCURRENTLY {_INDEX} "
            "ON crm.contacts USING gin (_search gin_trgm_ops) WHERE deleted_at IS NULL"
        )
        op.execute("SET lock_timeout = DEFAULT")


def upgrade() -> None:
    # A database that applied this work as `0236_contact_mobile_phone` already renamed the
    # column. Re-running the RENAME would fail; the identity-photo / named-portal line still
    # has to apply, which is why 0244 merges the old id rather than stamping past it.
    has_old_column = (
        op.get_bind()
        .exec_driver_sql(
            "SELECT EXISTS ("
            "  SELECT 1 FROM information_schema.columns"
            "  WHERE table_schema = 'crm' AND table_name = 'contacts'"
            "    AND column_name = 'mobile_phone')"
        )
        .scalar()
    )
    if not has_old_column:
        return
    sql = (
        (_SQL_DIR / "0240_contact_mobile_and_phone.sql")
        .read_text(encoding="utf-8")
        .replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)
    _rebuild_index()


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        SET LOCAL lock_timeout = '5s';
        ALTER TABLE crm.contacts DROP COLUMN _search;

        ALTER TABLE crm.contacts
          ADD COLUMN phone_numbers jsonb;

        ALTER TABLE crm.contacts
          ADD CONSTRAINT contacts_phone_numbers_is_array
          CHECK (phone_numbers IS NULL OR jsonb_typeof(phone_numbers) = 'array');

        SET LOCAL ROLE service_role;
        UPDATE crm.contacts
           SET phone_numbers = jsonb_build_array(
                 jsonb_build_object('label', 'other', 'number', phone)
               )
         WHERE NULLIF(phone, '') IS NOT NULL;
        RESET ROLE;

        ALTER TABLE crm.contacts RENAME COLUMN mobile TO mobile_phone;

        COMMENT ON COLUMN crm.contacts.mobile_phone IS
          'The client''s PRIMARY MOBILE — the one number staff call.';
        COMMENT ON COLUMN crm.contacts.phone IS
          'Retained for one expand/contract release. No current reader or writer.';

        CREATE OR REPLACE FUNCTION search.phone_text(numbers jsonb)
        RETURNS text
        LANGUAGE sql
        IMMUTABLE
        PARALLEL SAFE
        RETURN (
          SELECT NULLIF(btrim(string_agg(COALESCE(elem ->> 'number', ''), ' ')), '')
            FROM jsonb_array_elements(COALESCE(numbers, '[]'::jsonb)) AS elem
        );
        GRANT EXECUTE ON FUNCTION search.phone_text(jsonb) TO authenticated, service_role;

        ALTER TABLE crm.contacts ADD COLUMN _search text GENERATED ALWAYS AS (
          search.normalise(
            uid, name, first_name, middle_name, surname, email,
            mobile_phone, search.phone_text(phone_numbers), national_identifier
          )
        ) STORED;

        SET LOCAL ROLE service_role;
        UPDATE documents.form_fields AS ff
           SET field_type = 'phoneNumbers',
               label = 'Secondary number'
          FROM documents.forms AS f
         WHERE ff.form_id = f.id
           AND f.code IN ('applicant_core', 'jisa_registered_contact')
           AND ff.field_key = 'other_numbers'
           AND ff.field_type = 'phoneNumber';

        UPDATE documents.form_responses
           SET answers = jsonb_set(
                 answers,
                 '{other_numbers}',
                 jsonb_build_array(
                   jsonb_build_object('label', 'other', 'number', answers ->> 'other_numbers')
                 )
               )
         WHERE jsonb_typeof(answers -> 'other_numbers') = 'string'
           AND NULLIF(answers ->> 'other_numbers', '') IS NOT NULL;

        UPDATE flows.flow_versions
           SET definition = replace(
                 replace(definition::text, '"target": "mobile"', '"target": "mobile_phone"'),
                 '"target": "phone"', '"target": "phone_numbers"'
               )::jsonb
         WHERE definition::text LIKE '%%"target": "mobile"%%'
            OR definition::text LIKE '%%"target": "phone"%%';
        RESET ROLE;
        """
    )
    _rebuild_index()
