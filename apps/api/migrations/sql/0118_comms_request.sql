-- 0118_comms_request -- the wire request, distinct from the preview body.
--
-- 0111 stored `html` as the Activity preview: the author's original, because inlined `cid:`
-- images only resolve in a mail client. The Raw tab then dumped that same string, so "HTML" and
-- "Raw" were two views of the preview rather than the preview vs what was POSTed.
--
-- `request` is the JSON httpx sent (or would have sent): method, url, and the body (from / to /
-- subject / html — the WIRE html, cid: included — plus reply_to and attachment metadata).
-- Attachment BYTES are dropped — they are the inlined images and can dwarf the rest of the row;
-- filenames and content-ids remain so a reader can see they went. The Authorization header is
-- never stored: it is not on the JSON body, and must not be reconstructed onto this column.

ALTER TABLE comms.messages ADD COLUMN IF NOT EXISTS request jsonb;
