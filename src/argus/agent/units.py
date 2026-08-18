"""Restate the scale a periodic table declares in its header. No LLM call.

A periodic report puts the scale in a header above the table and writes the
cell in that scale, so a figure is seven or eight digits and its meaning
depends on a line the model has to carry across the table. A recorded run read
a figure in hundred-million units as if it were in millions and reached the
opposite conclusion on a comparison, with the evidence stating the scale
correctly the whole time.
See docs/design-rationale.md#unit-line
"""
from __future__ import annotations

import re

#: Scales a disclosure table declares, with their value in won. Units outside
#: this table are counts rather than amounts and carry no multiplier.
SCALES = {"천원": 1_000, "백만원": 1_000_000, "억원": 100_000_000,
          "십억원": 1_000_000_000, "조원": 1_000_000_000_000}

_HEADER = re.compile(r"[（(]\s*단위\s*[:：]?\s*([^)）]{1,12}?)\s*[)）]")


def unit_line(evidence: str) -> str:
    """One line naming each scale the evidence declares, in order of first use."""
    seen: list[str] = []
    for match in _HEADER.finditer(evidence or ""):
        name = "".join(match.group(1).split())
        if name in SCALES and name not in seen:
            seen.append(name)
    if not seen:
        return ""
    parts = ["%s = %s원" % (name, format(SCALES[name], ",")) for name in seen]
    return ("- [결정적 단위] 위 표의 금액 단위: " + " · ".join(parts)
            + " (표의 숫자에 이 배수를 곱한 값이 실제 금액이다)")
