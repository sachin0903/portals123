-- crm.contacts.gender -- required by Morgan Lloyd, held nowhere.
--
-- `cam_createcontact` rejects a contact without `gendercode`, and we had no column to supply
-- it from: `app/integrations/morgan_lloyd/contacts.py::build_contact` only sends the field
-- when the client dict carries a `gender` key, and the endpoint's SELECT never produced one.
-- So every push to Morgan Lloyd failed with
--   400 0x80040224 "gendercode is required"
-- and `platform.links` held zero morgan_lloyd contact rows -- the drawdown journey, which
-- addresses a client by their Dynamics GUID, could never be started for anybody.
--
-- Expand phase: one nullable column, no rewrite, no policy change. crm.contacts already has
-- its RLS policies and its audit.attach from 0003; adding a column inherits both.
--
-- VALUES. Dynamics' own option set on this org is exactly {1 Male, 2 Female} -- verified
-- against EntityDefinitions(LogicalName='contact')/Attributes(LogicalName='gendercode'), which
-- offers no "unspecified" member. We store the wider set anyway, because our record should not
-- be narrowed by a counterparty's: 'male' and 'female' map to 1 and 2, and anything else is
-- honest locally and refused at the Morgan Lloyd boundary with a message naming the problem,
-- rather than being silently coerced to one or the other. Guessing from `title` was the
-- alternative and is not acceptable -- it is wrong for Dr/Prof/Mx, and writing a guess about
-- someone's gender onto a permanent back-office record is precisely what this integration's
-- required-field validation exists to prevent.
ALTER TABLE crm.contacts
  ADD COLUMN IF NOT EXISTS gender text
    CONSTRAINT contacts_gender_check
    CHECK (gender IS NULL OR gender IN ('male', 'female', 'other', 'not_specified'));

COMMENT ON COLUMN crm.contacts.gender IS
  'Client gender. Required by Morgan Lloyd (cam_createcontact gendercode); their option set '
  'admits only male/female, so other/not_specified are stored but cannot be pushed there.';
