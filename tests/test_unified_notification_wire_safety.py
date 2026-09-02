from __future__ import annotations

from stockscout_unified.notifications import _wire_safe_parts


def test_strict_legacy_bottom_wire_safety_escapes_all_unescaped_reserved_chars() -> None:
    raw = r"*Bottom* +4.2% | setup (watch) already\-escaped"
    [safe] = _wire_safe_parts([raw], strict=True)

    assert safe == r"\*Bottom\* \+4\.2% \| setup \(watch\) already\-escaped"


def test_modern_series_wire_safety_only_repairs_legacy_pipe_separator() -> None:
    raw = r"*Next* +4.2% | already\-escaped"
    [safe] = _wire_safe_parts([raw])

    assert safe == r"*Next* +4.2% \| already\-escaped"
