from promo_finder.heuristics import baseline_proposal, cliff_score, hook_score, propose_rules
from promo_finder.models import Beat
from promo_finder.validate import Validator

PROMO = {"min_duration_s": 20.0, "max_duration_s": 60.0, "hook_window_s": 3.0, "end_tolerance_s": 2.5,
         "extend_min_cliff": 3.0, "min_segment_s": 4.0, "max_segments": 3, "tail_baseline_s": 45.0}


def beat(kind, text, emotions=(), start=0.0, end=1.0):
    return Beat(id="E01-B001", episode=1, scene=1, kind=kind, start=start, end=end, text=text,
                speaker="X" if kind == "DIALOGUE" else None, emotions=list(emotions))


def test_slap_beats_tea():
    slap, t = hook_score(beat("ACTION", "She slaps him across the face."))
    tea, _ = hook_score(beat("ACTION", "She pours tea into a cup and smiles."))
    assert slap > tea and t == "violence"


def test_emotion_tags_raise_hook_score():
    calm, _ = hook_score(beat("DIALOGUE", '"You should go."', ["Gentle"]))
    cold, _ = hook_score(beat("DIALOGUE", '"You should go."', ["Cold", "Cruel"]))
    assert cold > calm


def test_cliffhanger_sfx_scores_high(show):
    ep = show.episode(1)
    b = next(b for b in ep.beats if "ominous cliffhanger peak" in b.text)
    assert cliff_score(b, ep) >= 4.0


def test_rules_proposals_mostly_validate(show):
    v = Validator(show, PROMO)
    total_good = 0
    for ep in show.episodes:
        good, bad = v.validate(propose_rules(ep, PROMO), f"r{ep.number}")
        total_good += len(good)
    assert total_good >= 3


def test_tail_baseline_is_last_45s_snapped(show):
    ep = show.episode(1)
    v = Validator(show, PROMO)
    good, bad = v.validate([baseline_proposal(ep, PROMO)], "b")
    assert len(good) == 1
    seg = good[0].segments[0]
    assert seg.end >= ep.duration - 1.5
    assert ep.duration - 48.0 <= seg.start <= ep.duration - 42.0


def test_short_episode_has_no_baseline():
    from promo_finder.models import Episode
    ep = Episode(number=1, beats=[beat("ACTION", "x", start=0, end=5)], duration=10.0)
    assert baseline_proposal(ep, PROMO) is None
