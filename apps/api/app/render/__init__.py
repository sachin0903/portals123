"""Document render pipeline: Jinja2 (sandboxed) + WeasyPrint/Playwright PDF renderers.

Ported (focused, synchronous) from the platform-1 prototype. Given a template version's saved
HTML/CSS and a variables context, ``jinja_engine.render`` substitutes the tokens and a
``DocumentRenderer`` turns the result into PDF bytes. The async PGMQ queue + worker, the
dataset/chart/disclosure resolution and calc globals are deferred — see docs/document-generation.md.
"""
