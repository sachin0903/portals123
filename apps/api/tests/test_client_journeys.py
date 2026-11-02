"""How a client's journey is assembled for the record — no database."""

from app.flows.client_journeys import assemble_forms


def test_a_saved_draft_is_kept_and_an_unopened_step_is_not() -> None:
    forms = assemble_forms(
        [
            {
                "step_key": "fact_find",
                "title": "Fact find",
                "status": "waiting",
                "form_uid": "frm_fact",
                "answers": '{"first_name": "Ada"}',
                "outputs": {},
                "submitted_at": None,
                "submitted_by": None,
            },
            {
                "step_key": "risk",
                "title": "Risk",
                "status": "pending",
                "form_uid": "frm_risk",
                "answers": None,
                "outputs": None,
                "submitted_at": None,
                "submitted_by": None,
            },
        ],
        [
            {
                "form_uid": "frm_fact",
                "field_key": "first_name",
                "label": "First name",
                "section": "Personal details",
                "field_type": "text",
                "options": None,
            },
            {
                "form_uid": "frm_risk",
                "field_key": "q1",
                "label": "Question",
                "section": "Risk",
                "field_type": "segmented",
                "options": None,
            },
        ],
    )
    assert [form["step_key"] for form in forms] == ["fact_find"]
    assert forms[0]["answers"] == {"first_name": "Ada"}
    assert forms[0]["fields"] == [
        {
            "key": "first_name",
            "label": "First name",
            "section": "Personal details",
            "field_type": "text",
            "options": None,
        }
    ]


def test_fields_stay_on_the_form_they_belong_to() -> None:
    forms = assemble_forms(
        [
            {
                "step_key": "fact_find",
                "title": "Fact find",
                "status": "fulfilled",
                "form_uid": "frm_fact",
                "answers": {"first_name": "Ada"},
                "outputs": {},
                "submitted_at": None,
                "submitted_by": "Grace Hopper",
            },
            {
                "step_key": "risk",
                "title": "Attitude to risk",
                "status": "fulfilled",
                "form_uid": "frm_risk",
                "answers": {"q1": "a"},
                "outputs": {"band": "cautious", "score": 18, "label": "Cautious"},
                "submitted_at": None,
                "submitted_by": None,
            },
        ],
        [
            {
                "form_uid": "frm_risk",
                "field_key": "q1",
                "label": "  ",
                "section": None,
                "field_type": "segmented",
                "options": [{"value": "a", "label": "Agree"}],
            }
        ],
    )
    by_key = {form["step_key"]: form for form in forms}
    assert by_key["fact_find"]["fields"] == []
    assert by_key["fact_find"]["submitted_by"] == "Grace Hopper"
    assert by_key["risk"]["fields"][0]["label"] == "q1"
    assert by_key["risk"]["outputs"]["label"] == "Cautious"


def test_a_fulfilled_form_with_no_answers_is_still_the_record() -> None:
    forms = assemble_forms(
        [
            {
                "step_key": "acknowledge",
                "title": None,
                "status": "fulfilled",
                "form_uid": None,
                "answers": {},
                "outputs": {},
                "submitted_at": None,
                "submitted_by": None,
            }
        ],
        [],
    )
    assert forms[0]["title"] == "acknowledge"
    assert forms[0]["fields"] == []
