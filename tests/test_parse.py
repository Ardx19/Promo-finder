import shutil
import zipfile

import pytest

from promo_finder.parse import ParseError, load_show, parse_episode_text, parse_ts

from .conftest import SHOW_DIR


def test_episode_one_basic(show):
    assert [e.number for e in show.episodes] == [1, 2, 3]
    e1 = show.episode(1)
    first = e1.beats[0]
    assert first.id == "E01-B001"
    assert first.kind == "CAMERA"
    assert (first.start, first.end) == (0.0, 4.8)
    assert len(e1.beats) == 36
    assert e1.logline.startswith("A humiliated maid")
    assert "First episode" in e1.continuity


def test_dialogue_header_splits_speaker_and_emotions(show):
    b = next(b for b in show.episode(1).beats if b.kind == "DIALOGUE")
    assert b.speaker == "VICTORIA"
    assert b.emotions == ["Mocking", "Loud"]
    assert b.text.startswith('"Careful, servant.')


def test_sound_fx_maps_to_sfx(show):
    kinds = {b.kind for b in show.episode(1).beats}
    assert kinds == {"CAMERA", "ACTION", "DIALOGUE", "SFX"}


def test_uppercase_headers_and_missing_scene_end(show):
    e2 = show.episode(2)  # uppercase headers, no SCENE END line
    assert len(e2.beats) == 32
    assert e2.duration == pytest.approx(max(b.end for b in e2.beats))
    assert e2.beats[1].speaker == "MR. CROSS"


def test_multiple_scenes(show):
    e3 = show.episode(3)
    assert {b.scene for b in e3.beats} == {1, 2}


def test_zip_and_folder_give_same_show(tmp_path):
    zpath = tmp_path / "Some Show-scripts (2).zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        for f in SHOW_DIR.glob("*.txt"):
            zf.write(f, f"Some Show/{f.name}")
    from_zip = load_show(zpath)
    from_dir = load_show(SHOW_DIR)
    assert from_zip.title == "Some Show"
    assert [e.model_dump() for e in from_zip.episodes] == [e.model_dump() for e in from_dir.episodes]


def test_episode_order_follows_header_not_filename(tmp_path):
    for f in SHOW_DIR.glob("*.txt"):
        shutil.copy(f, tmp_path / f.name.replace("episode-00", "z-"))
    show = load_show(tmp_path)
    assert [e.number for e in show.episodes] == [1, 2, 3]


def test_file_without_beats_raises_with_name():
    with pytest.raises(ParseError, match="empty.txt"):
        parse_episode_text("EPISODE NUMBER: Episode 4\n--- SCRIPT START ---\n--- SCRIPT END ---\n", "empty.txt")


def test_episode_number_falls_back_to_filename():
    text = "**CAMERA:**\nStart: (00:00.00) | End: (00:02.00)\nA shot.\n"
    ep = parse_episode_text(text, "episode-007-x.txt")
    assert ep.number == 7 and ep.beats[0].id == "E07-B001"


def test_parse_ts_formats():
    assert parse_ts("02:18.00") == 138.0
    assert parse_ts("1:02:03.5") == 3723.5
    assert parse_ts("12.5") == 12.5


def test_beats_between_excludes_camera(show):
    e1 = show.episode(1)
    beats = e1.beats_between(0.0, 8.0)
    assert all(b.kind != "CAMERA" for b in beats)
    assert beats[0].kind == "ACTION"
