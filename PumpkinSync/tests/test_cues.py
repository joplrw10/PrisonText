from __future__ import annotations

import pytest

from pumpkinsync.cues import merge_short_cues, validate_cues
from pumpkinsync.models import Cue


def test_validate_cues_accepts_contiguous_full_timeline() -> None:
    cues = [
        Cue(start=0.0, end=0.5, shape="X"),
        Cue(start=0.5, end=1.0, shape="C", energy=0.6),
    ]

    validate_cues(cues, duration=1.0)


def test_validate_cues_rejects_gap_and_unknown_shape() -> None:
    with pytest.raises(ValueError, match="gap"):
        validate_cues(
            [Cue(start=0.0, end=0.3, shape="X"), Cue(start=0.4, end=1.0, shape="C")],
            duration=1.0,
        )

    with pytest.raises(ValueError, match="shape"):
        validate_cues([Cue(start=0.0, end=1.0, shape="Z")], duration=1.0)


def test_merge_short_cues_absorbs_transient_and_compacts_neighbors() -> None:
    cues = [
        Cue(0.0, 0.20, "X"),
        Cue(0.20, 0.23, "A", energy=0.2),
        Cue(0.23, 0.50, "C", energy=0.7),
        Cue(0.50, 0.70, "C", energy=0.6),
    ]

    merged = merge_short_cues(cues, min_duration=0.08)

    assert [(c.start, c.end, c.shape) for c in merged] == [
        (0.0, 0.20, "X"),
        (0.20, 0.70, "C"),
    ]
    assert 0.6 <= merged[1].energy <= 0.7
