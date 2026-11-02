"""Every task notice clicks through to the TASK, not the task inbox — pure.

A task notification's default link is ``/tasks/{task.uid}``: single braces, the ``{dotted.key}``
grammar ``app.notifications.render`` compiles, so it resolves to the task's own page at delivery.
A jinja-style ``{{ task.uid }}`` would ship literal braces in the href, and a bare ``/tasks`` sends
an assignee to a listing to hunt for the row the notice is about. It is also the one destination
the notice can carry: ``consumer._click_link`` has no task branch, so the default IS the deep link.

The brace is load-bearing twice over — it is what flips the consumers' context gate
(``"{" in rule.link``, consumer.py / email_consumer.py) and makes them render the link at all.
"""

from __future__ import annotations

from app.notifications import catalogue, render

TASK_LINK = "/tasks/{task.uid}"


def _task_audiences() -> list[tuple[str, catalogue.Audience]]:
    return [
        (trig.key, aud)
        for trig in catalogue.CATALOGUE
        if trig.subject_kind == "task"
        for aud in trig.audiences
        if aud.default_link is not None
    ]


def test_every_task_audience_with_a_link_deep_links_to_the_task() -> None:
    audiences = _task_audiences()
    assert {key for key, _ in audiences} == {"task.assigned", "task.due_soon", "task.completed"}
    for key, aud in audiences:
        assert aud.default_link == TASK_LINK, f"{key}/{aud.key}"


def test_the_link_carries_a_token_so_the_consumers_resolve_a_context() -> None:
    # The gate both consumers apply before rendering a link.
    for key, aud in _task_audiences():
        assert aud.default_link and "{" in aud.default_link, f"{key}/{aud.key}"


def test_the_link_renders_to_the_task_uid() -> None:
    ctx = {"task": {"uid": "ab12cd34ef56", "title": "Sign the declaration"}}
    for key, aud in _task_audiences():
        assert render.render(aud.default_link or "", ctx) == "/tasks/ab12cd34ef56", key


def test_the_default_matches_the_link_target_the_builder_offers() -> None:
    # An author picking "The task" in the builder and a firm on the shipped default must land on
    # the same page.
    for trig in catalogue.CATALOGUE:
        if trig.subject_kind != "task":
            continue
        hrefs = [t["href"] for t in catalogue.link_targets_for(trig) if t["key"] == "task"]
        assert hrefs == [TASK_LINK], trig.key
