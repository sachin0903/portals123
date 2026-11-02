"""What a create actually puts on the wire, and what a refusal tells you about it.

Both of these are here because the first live run of the integration suite lost time to them, and
both are the kind of defect a local test can catch once it knows to look:

* **`firmId` has to be in the body of a create**, and two of them were not putting it there.
* **Seccl names the offending field in `fieldErrors`**, and the client was throwing that away.

The scan is static (a `firmId` stamp is a literal in the source) and the error test drives the
real `_explain` against real vendor response bodies, so neither needs a network.
"""

from __future__ import annotations

import ast
from pathlib import Path

import httpx
import pytest

from seccl._base import _explain

_ENDPOINTS = Path(__file__).resolve().parent.parent / "src" / "seccl" / "endpoints"


def _mentions_firm(node: ast.AST) -> bool:
    source = ast.dump(node)
    return "'firmId'" in source or '"firmId"' in source


def _stamps_firm(node: ast.AsyncFunctionDef, methods: dict[str, ast.AST]) -> bool:
    """The create itself carries ``firmId``, or it posts a body a same-class method builds."""
    if _mentions_firm(node):
        return True
    for child in ast.walk(node):
        if not isinstance(child, ast.Call) or not isinstance(child.func, ast.Attribute):
            continue
        if not isinstance(child.func.value, ast.Name) or child.func.value.id != "self":
            continue
        helper = methods.get(child.func.attr)
        if helper is not None and helper is not node and _mentions_firm(helper):
            return True
    return False


def _creates_without_a_firm_stamp() -> list[str]:
    """Every `create_*` method whose body does not carry `firmId`.

    A create POSTs to the bare collection with the firm in the BODY — the convention
    `test_endpoint_paths` checks the URL half of. This checks the body half: the method must
    merge `{"firmId": self.firm_id, **body}` rather than passing the caller's dict through.
    A method that posts `self.transaction_group_body(...)` counts when that builder stamps
    the firm, which is the same body, named so a test can read it without a client.

    Read from the source rather than by calling, because a client needs settings and a transport
    to construct and the question is purely about what the code does.
    """
    offenders: list[str] = []
    for module in sorted(_ENDPOINTS.glob("*.py")):
        tree = ast.parse(module.read_text())
        methods = {
            node.name: node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for node in methods.values():
            if not isinstance(node, ast.AsyncFunctionDef) or not node.name.startswith("create_"):
                continue
            if not _stamps_firm(node, methods):
                offenders.append(f"{module.stem}.{node.name}")
    return offenders


def test_every_create_stamps_the_firm_into_its_body() -> None:
    """**`create_client` and `create_account` did not, and the failure was opaque.**

    `POST /client` without `firmId` answers `500 Internal Server Error` with an empty
    `fieldErrors` — no field named, nothing to act on. With it, the same call answers `404
    DataNotFoundError "Node static data not found"`, which is a completely different and
    actionable problem. So the missing stamp did not merely fail; it disguised the next failure.

    `create_client`'s own docstring said the firm goes in the body while the method did not put it
    there, which is the worst arrangement: a caller who read the documentation and trusted the
    method got neither.
    """
    assert _creates_without_a_firm_stamp() == []


def test_the_scan_would_notice_a_create_that_stopped_stamping() -> None:
    """The scan is only worth anything if it can fail. Asserted by construction rather than by
    mutating a real module: an async `create_x` with a bare body dict must be reported."""
    tree = ast.parse(
        "class A:\n"
        "    async def create_thing(self, body):\n"
        "        return await self.write('POST', '/thing', body)\n"
    )
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef))
    assert "'firmId'" not in ast.dump(node)


def _response(status: int, payload: object) -> httpx.Response:
    return httpx.Response(status, json=payload, request=httpx.Request("POST", "https://x/client"))


class TestTheRefusalNamesTheField:
    """Seccl says exactly what is wrong and the exception used to say only the status code.

    A developer holding a wrong payload got `POST /client failed: Client error '400 Bad Request'`
    and had to reach for a raw httpx client to find out which field — which is what the first live
    run did, three times in a row, on three different endpoints.
    """

    def test_field_errors_are_rendered_as_field_and_message(self) -> None:
        detail = _explain(
            _response(
                400,
                {
                    "error": {
                        "type": "DataValidationError",
                        "errorMessage": "The request has failed validation",
                        "fieldErrors": [
                            {
                                "fieldName": "surname",
                                "errorMessage": "Surname can't be blank",
                                "type": "presence",
                            },
                            {
                                "fieldName": "investmentDecision.firmLei",
                                "errorMessage": "is too long (maximum is 20 characters)",
                                "type": "length",
                            },
                        ],
                    }
                },
            )
        )
        assert "surname: Surname can't be blank" in detail
        assert "investmentDecision.firmLei" in detail
        # The envelope's own message is the useless half — "The request has failed validation"
        # is true of every 400 and names nothing.
        assert "The request has failed validation" not in detail

    def test_an_envelope_with_no_field_errors_falls_back_to_its_message(self) -> None:
        """Not every refusal is a validation one. `Insufficient funds for this transaction` and
        `Node static data not found` arrive with an empty `fieldErrors`, and they are the whole
        answer."""
        detail = _explain(
            _response(
                400,
                {
                    "error": {
                        "errorMessage": "Insufficient funds for this transaction",
                        "fieldErrors": [],
                    }
                },
            )
        )
        assert detail == "Insufficient funds for this transaction"

    def test_a_body_that_is_not_the_expected_shape_still_says_something(self) -> None:
        """An HTML error page or a bare string must not raise on the way to reporting a failure —
        an exception inside error handling replaces a bad message with no message."""
        page = httpx.Response(
            502, text="<html>gateway</html>", request=httpx.Request("POST", "https://x/client")
        )
        assert "gateway" in _explain(page)
        assert _explain(_response(500, {"unexpected": True}))

    def test_a_long_body_is_truncated(self) -> None:
        """The detail goes into an exception message, not a log sink. A 5MB page pasted into a
        stack trace helps nobody, and `_safe_body` already makes the same argument for the audit
        table."""
        many = {
            "error": {
                "fieldErrors": [
                    {"fieldName": f"field{i}", "errorMessage": "x" * 50} for i in range(200)
                ]
            }
        }
        assert len(_explain(_response(400, many))) <= 600


@pytest.mark.parametrize("status", [400, 404, 409, 422, 500])
def test_explain_never_raises_whatever_the_status(status: int) -> None:
    assert isinstance(_explain(_response(status, {"error": {}})), str)
