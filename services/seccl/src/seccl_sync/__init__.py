"""Seccl platform sync on interlace v2.

Webhook-driven change capture (gateway -> interlace streams), timestamp-cursor
extraction where the API filters by date, and SQL set-difference merges
(full_merge) for list endpoints that can only return their full state.
"""
