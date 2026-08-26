from __future__ import annotations

from dataclasses import dataclass

from stock_scout.notifications.telegram import TelegramConfig
from stockscout_eod.notifications import (
    DeliveryProgress,
    deliver_parts,
    digest_content_hash,
)


@dataclass
class Ledger:
    progress: DeliveryProgress

    def __post_init__(self) -> None:
        self.marks: list[int] = []

    def get(self, **_kwargs) -> DeliveryProgress:
        return self.progress

    def mark(self, **kwargs) -> None:
        self.marks.append(kwargs["last_successful_part"])


def test_delivery_resumes_at_first_unsent_part(monkeypatch) -> None:
    ledger = Ledger(DeliveryProgress(last_successful_part=1))
    seen: list[tuple[list[str], int]] = []

    def fake_send(_cfg, parts, *, start_part, on_part_sent):
        seen.append((parts, start_part))
        for index in range(start_part + 1, len(parts) + 1):
            on_part_sent(index, len(parts))
        return True

    monkeypatch.setattr("stockscout_eod.notifications.send_message_parts", fake_send)
    assert deliver_parts(
        TelegramConfig("token", "chat"),
        digest_key="daily:2026-08-21",
        parts=["(1/3) first", "(2/3) second", "(3/3) third"],
        ledger=ledger,
    )
    assert seen[0][1] == 1
    assert ledger.marks == [2, 3]


def test_completed_delivery_is_deduplicated(monkeypatch) -> None:
    ledger = Ledger(DeliveryProgress(last_successful_part=2, completed=True))
    monkeypatch.setattr(
        "stockscout_eod.notifications.send_message_parts",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not send")),
    )
    assert deliver_parts(
        TelegramConfig("token", "chat"),
        digest_key="daily:2026-08-21",
        parts=["first", "second"],
        ledger=ledger,
    )


def test_digest_hash_covers_part_boundaries_and_content() -> None:
    assert digest_content_hash(["ab", "c"]) != digest_content_hash(["a", "bc"])
