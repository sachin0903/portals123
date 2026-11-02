"""Morgan Lloyd CRM sync on interlace v2.

The CRM (Dynamics 365) exposes no webhooks and no updated-since filters on its
list endpoints, so this service is poll-only: scheduled pulls land as SQL
set-difference merges (``full_merge``) where the entity has a documented key,
and full replacements for the envelope-style action listings.
"""
