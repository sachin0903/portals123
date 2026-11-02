"""The shipped emails use THREE type sizes and THREE inks, and every ink is readable.

A reader counted six different treatments in a six-line password-reset email: four sizes
(22/15/14/12) and four inks, one of which nobody could read. Hierarchy in a transactional email
is carried by size, weight and ink — a small, closed set of each — and a fourth step of any of
them is not a finer gradation, it is noise the reader has to sort.

The scale is stated in `app/email/shell.py`. This reads the MERGED fragments rather than the
source, because that is the document a recipient's client actually receives.
"""

from __future__ import annotations

import re

import pytest

from app.email import shell as email_shell
from tests.test_email_send_parity import CODES, FIRM, _ctx, _shipped_fragment

#: What the scale allows. The button's label rides the body size, so there is no fourth entry.
ALLOWED_SIZES = {"22px", "15px", email_shell.SMALL_SIZE}

#: The three inks, plus the button label's white. Nothing else: the editor's note box carried a
#: fourth (`#374151`) that `defaults.py` never used, which is the two copies drifting rather than
#: a colour anyone chose.
ALLOWED_INKS = {
    email_shell.INK.lower(),
    email_shell.BODY_INK.lower(),
    email_shell.MUTED_INK.lower(),
    "#ffffff",
}

_SIZE = re.compile(r"font-size:\s*([0-9.]+px)")
#: `color:`, never `background-color:` — the lookbehind is the difference between judging the
#: INK and judging the note box's panel fill, which is a surface and not type.
_HEX = re.compile(r"(?<!background-)color:\s*(#[0-9a-fA-F]{3,8})")

#: The minimum WCAG AA asks of body text.
AA = 4.5


def _contrast(hex_colour: str, background: str = "#ffffff") -> float:
    def luminance(value: str) -> float:
        channels = (int(value[i : i + 2], 16) / 255 for i in (1, 3, 5))
        linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    light, dark = sorted((luminance(hex_colour), luminance(background)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


@pytest.mark.parametrize("code", CODES)
def test_a_shipped_email_uses_only_the_three_sizes(code: str) -> None:
    fragment = _shipped_fragment(code, _ctx(code))
    # `font-size:0` is the accent bar's, which holds no text — it is spacing, not type.
    sizes = {s for s in _SIZE.findall(fragment) if s != "0px"}
    assert sizes <= ALLOWED_SIZES, (
        f"{code}: a size outside the scale — {sorted(sizes - ALLOWED_SIZES)}. "
        f"The scale is {sorted(ALLOWED_SIZES)} (app/email/shell.py)."
    )


@pytest.mark.parametrize("code", CODES)
def test_a_shipped_email_uses_only_the_three_inks(code: str) -> None:
    fragment = _shipped_fragment(code, _ctx(code))
    inks = {c.lower() for c in _HEX.findall(fragment)}
    # The firm's own accent arrives MERGED (the card edge, the button fill) and is theirs to
    # choose, so it is excluded by its actual value rather than by a prefix — a guess there would
    # quietly exempt any ink that happened to share it.
    unexpected = inks - ALLOWED_INKS - {FIRM.accent.lower()}
    assert not unexpected, (
        f"{code}: an ink outside the palette — {sorted(unexpected)}. "
        f"The palette is {sorted(ALLOWED_INKS)} (app/email/shell.py)."
    )


def test_every_ink_we_choose_is_readable_on_the_card() -> None:
    """The one that made this a defect rather than a preference.

    `FAINT_INK` was `#94a3b8` — **2.56:1** on white, under half what AA asks — and it carried the
    footer, the support address, the paste-this-link line and the link itself: everything a
    recipient needs precisely when the button has not worked for them.
    """
    for name in ("INK", "BODY_INK", "MUTED_INK"):
        ink = getattr(email_shell, name)
        ratio = _contrast(ink)
        assert ratio >= AA, f"{name} ({ink}) is {ratio:.2f}:1 on the card — AA asks {AA}:1"


def test_the_unreadable_ink_is_gone_for_good() -> None:
    assert not hasattr(email_shell, "FAINT_INK"), (
        "FAINT_INK is back. It was removed for failing AA at 2.56:1, not for being one colour too "
        "many — see the note in app/email/shell.py before reintroducing it."
    )
