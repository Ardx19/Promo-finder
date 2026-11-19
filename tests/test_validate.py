import pytest

from promo_finder.models import Proposal
from promo_finder.parse import parse_episode_text
from promo_finder.models import Show
from promo_finder.validate import Reject, Validator

PROMO = {"min_duration_s": 20.0, "max_duration_s": 60.0, "hook_window_s": 3.0, "end_tolerance_s": 2.5,
         "extend_min_cliff": 3.0, "min_segment_s": 4.0, "max_segments": 3}


def bid(show, ep, needle):
    """Find a beat id in episode `ep` whose text contains `needle`."""
    for b in show.episode(ep).beats:
        if needle in b.text:
            return b.id
    raise AssertionError(f"no beat containing {needle!r} in E{ep}")


def all_beat_times(show):
    return {(e.number, t) for e in show.episodes for b in e.beats for t in (b.start, b.end)} | \
           {(e.number, e.duration) for e in show.episodes} | {(e.number, 0.0) for e in show.episodes}


def test_valid_single_segment(show):
    v = Validator(show, PROMO)
    hook = bid(show, 1, "empties her wine glass")
    cliff = bid(show, 1, "ominous cliffhanger peak")
    c = v.build(Proposal(hook_beat=hook, cliffhanger_beat=cliff, segments=[(hook, cliff)]), "t-1")
    seg = c.segments[0]
    hb = show.beat(hook)
    assert seg.start <= hb.start <= seg.start + 3.0
    assert 20.0 <= c.duration <= 60.0
    assert seg.end >= show.beat(cliff).end
    assert c.hook_text.startswith("[ACTION] Victoria slowly empties")


def test_every_emitted_time_is_a_real_beat_time(show):
    v = Validator(show, PROMO)
    hook = bid(show, 2, "slaps Maya hard")
    cliff = bid(show, 2, "cliffhanger sting")
    c = v.build(Proposal(hook_beat=hook, cliffhanger_beat=cliff, segments=[(hook, cliff)]), "t-1")
    times = all_beat_times(show)
    for s in c.segments:
        assert (s.episode, s.start) in times and (s.episode, s.end) in times


def test_late_hook_is_resnapped_into_window(show):
    """Covers AE1: proposer starts long before the hook; start moves to within 3 s of the hook."""
    v = Validator(show, PROMO)
    start = show.episode(1).beats[0].id
    hook = bid(show, 1, "Know your place")  # dialogue at ~16.6 s
    cliff = bid(show, 1, "ominous cliffhanger peak")
    c = v.build(Proposal(hook_beat=hook, cliffhanger_beat=cliff, segments=[(start, cliff)]), "t-1")
    assert show.beat(hook).start - c.segments[0].start <= 3.0 + 1e-6


def test_hook_late_when_no_cut_in_window():
    text = """EPISODE NUMBER: Episode 1
**CAMERA:**
Start: (00:00.00) | End: (00:10.00)
One long shot.
**ACTION:**
Start: (00:05.00) | End: (00:06.00)
She slaps him.
**CAMERA:**
Start: (00:10.00) | End: (00:30.00)
Another long shot.
**SOUND FX:**
Start: (00:28.00) | End: (00:30.00)
Cliffhanger sting.
"""
    show = Show(slug="x", title="x", episodes=[parse_episode_text(text, "e1.txt")])
    v = Validator(show, PROMO)
    with pytest.raises(Reject) as e:
        v.build(Proposal(hook_beat="E01-B002", cliffhanger_beat="E01-B004", segments=[("E01-B002", "E01-B004")]), "t")
    assert e.value.reason == "hook_late"


def test_unknown_beat_rejected(show):
    """Covers AE2."""
    v = Validator(show, PROMO)
    good, bad = v.validate([Proposal(hook_beat="E07-B999", cliffhanger_beat="E01-B002", segments=[("E07-B999", "E01-B002")])], "t")
    assert good == [] and bad[0].reason == "unknown_beat"


def test_cut_never_inside_dialogue(show):
    v = Validator(show, PROMO)
    for ep in show.episodes:
        for t in v.safe_cuts(ep):
            assert not any(d.start + 0.01 < t < d.end - 0.01 for d in ep.dialogue)


def test_too_long_rejected_not_trimmed(show):
    v = Validator(show, PROMO)
    first = show.episode(1).beats[1].id  # action at 0.5 s
    cliff = bid(show, 1, "ominous cliffhanger peak")  # ends ~67 s
    with pytest.raises(Reject) as e:
        v.build(Proposal(hook_beat=first, cliffhanger_beat=cliff, segments=[(first, cliff)]), "t")
    assert e.value.reason == "bad_duration"


def test_too_short_without_later_cliff_rejected(show):
    v = Validator(show, PROMO)
    hook = bid(show, 1, "Careful, servant")
    cliff = bid(show, 1, "Yes, Miss Victoria")
    with pytest.raises(Reject) as e:
        v.build(Proposal(hook_beat=hook, cliffhanger_beat=cliff, segments=[(hook, cliff)]), "t")
    assert e.value.reason == "bad_duration"


def test_too_short_extends_to_next_strong_cliff(show):
    hook = bid(show, 1, "Careful, servant")
    cliff = bid(show, 1, "Yes, Miss Victoria")
    too_early = bid(show, 1, "Know your place")  # strong, but still < 20 s from the start
    strong = bid(show, 1, "everything changes")
    v = Validator(show, PROMO, cliff_scores={too_early: 4.0, strong: 4.0})
    c = v.build(Proposal(hook_beat=hook, cliffhanger_beat=cliff, segments=[(hook, cliff)]), "t")
    assert c.cliffhanger_beat == strong and c.duration >= 20.0


def test_cliffhanger_before_last_segment_is_resolved_ending(show):
    v = Validator(show, PROMO)
    hook = bid(show, 1, "empties her wine glass")
    cliff = bid(show, 1, "Sharp gasp")
    later = bid(show, 1, "doors boom")
    with pytest.raises(Reject) as e:
        v.build(Proposal(hook_beat=hook, cliffhanger_beat=cliff,
                         segments=[(hook, cliff), (later, bid(show, 1, "It isn't"))]), "t")
    assert e.value.reason in ("resolved_ending", "bad_segments")


def test_stitched_two_episodes(show):
    v = Validator(show, PROMO)
    hook = bid(show, 1, "doors boom")
    a_end = bid(show, 1, "ominous cliffhanger peak")
    b_start = bid(show, 2, "slaps Maya hard")
    cliff = bid(show, 2, "Never again")
    c = v.build(Proposal(hook_beat=hook, cliffhanger_beat=cliff, segments=[(hook, a_end), (b_start, cliff)]), "t")
    assert c.episodes == [1, 2] and len(c.segments) == 2
    assert 20.0 <= c.duration <= 60.0


def test_non_consecutive_episodes_rejected(show):
    v = Validator(show, PROMO)
    with pytest.raises(Reject) as e:
        v.build(Proposal(hook_beat="E01-B002", cliffhanger_beat="E03-B010",
                         segments=[("E01-B002", "E01-B010"), ("E03-B002", "E03-B010")]), "t")
    assert e.value.reason == "bad_segments"


def test_out_of_order_segments_rejected(show):
    v = Validator(show, PROMO)
    with pytest.raises(Reject) as e:
        v.build(Proposal(hook_beat="E02-B002", cliffhanger_beat="E01-B030",
                         segments=[("E02-B002", "E02-B020"), ("E01-B010", "E01-B030")]), "t")
    assert e.value.reason == "bad_segments"


FIT = {**PROMO, "fit_length": True, "fit_target_max_s": 45.0, "fit_min_cliff": 1.0, "open_on_any_beat": True,
       "fit_backward": True, "fit_min_hook": 2.5}


def test_fit_extends_short_promo_to_best_unresolved_beat(show):
    hook = bid(show, 1, "Careful, servant")
    cliff = bid(show, 1, "Yes, Miss Victoria")
    weak, strong = bid(show, 1, "Know your place"), bid(show, 1, "everything changes")
    v = Validator(show, FIT, cliff_scores={weak: 1.5, strong: 4.0})
    c = v.build(Proposal(hook_beat=hook, cliffhanger_beat=cliff, segments=[(hook, cliff)]), "t")
    assert c.cliffhanger_beat == strong and 20 <= c.duration <= 45 and c.adjusted.startswith("extended from")


def test_fit_trims_long_promo_instead_of_rejecting(show):
    first = show.episode(1).beats[1].id
    cliff = bid(show, 1, "ominous cliffhanger peak")
    mid = bid(show, 1, "Know your place")
    v = Validator(show, FIT, cliff_scores={mid: 4.0})
    c = v.build(Proposal(hook_beat=first, cliffhanger_beat=cliff, segments=[(first, cliff)]), "t")
    assert c.duration <= 60 and c.cliffhanger_beat == mid and c.adjusted.startswith("trimmed")


def test_fit_starts_earlier_on_strong_hook_at_episode_end(show):
    """Too short and the episode is ending: open on an earlier strong hook, keep the same ending."""
    late_hook = bid(show, 1, "It isn't")
    cliff = bid(show, 1, "ominous cliffhanger peak")
    earlier = bid(show, 1, "doors boom")
    v = Validator(show, FIT, cliff_scores={}, hook_scores={earlier: 4.0})
    c = v.build(Proposal(hook_beat=late_hook, cliffhanger_beat=cliff, segments=[(late_hook, cliff)]), "t")
    assert c.hook_beat == earlier and c.cliffhanger_beat == cliff and c.duration >= 20
    assert c.adjusted.startswith("started earlier")


def test_open_on_any_clean_beat_avoids_hook_late():
    text = """EPISODE NUMBER: Episode 1
**CAMERA:**
Start: (00:00.00) | End: (00:10.00)
One long shot.
**ACTION:**
Start: (00:05.00) | End: (00:06.00)
She slaps him.
**CAMERA:**
Start: (00:10.00) | End: (00:30.00)
Another long shot.
**SOUND FX:**
Start: (00:28.00) | End: (00:30.00)
Cliffhanger sting.
"""
    show = Show(slug="x", title="x", episodes=[parse_episode_text(text, "e1.txt")])
    c = Validator(show, FIT).build(Proposal(hook_beat="E01-B002", cliffhanger_beat="E01-B004",
                                            segments=[("E01-B002", "E01-B004")]), "t")
    assert c.segments[0].start == 5.0  # opens exactly on the slap, mid-shot


def test_opening_never_mid_line(show):
    v = Validator(show, FIT)
    for ep in show.episodes:
        for t in v.opening_points(ep):
            assert not any(d.start + 0.01 < t < d.end - 0.01 for d in ep.dialogue)


def test_duplicates_dropped(show):
    v = Validator(show, PROMO)
    hook = bid(show, 1, "empties her wine glass")
    cliff = bid(show, 1, "ominous cliffhanger peak")
    p = Proposal(hook_beat=hook, cliffhanger_beat=cliff, segments=[(hook, cliff)])
    good, bad = v.validate([p, p], "t")
    assert len(good) == 1 and bad[0].reason == "duplicate"
