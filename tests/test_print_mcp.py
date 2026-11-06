"""Tests for the parts of print-mcp that need neither Photoshop nor a printer.

The DEVMODE struct layout and the mirrored-copy geometry are the two places where a
silent mistake changes what comes out of the printer, so both are pinned here.
Run with `python -m pytest tests` or `python tests/test_print_mcp.py`.
"""

from __future__ import annotations

import ctypes
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from print_mcp import printers, runner  # noqa: E402
from print_mcp.runner import _read_dpi, mirrored_copy  # noqa: E402


class _Img:
    """Just enough of a PIL image for _read_dpi, which only looks at .info."""

    def __init__(self, info: dict) -> None:
        self.info = info


def test_devmode_offsets() -> None:
    """The offsets the module docstring claims, checked against ctypes."""
    offsets = {name: getattr(printers.DEVMODEW, name).offset for name, _ in printers.DEVMODEW._fields_}
    assert offsets["dmFields"] == 72
    assert offsets["dmPrintQuality"] == 90
    assert offsets["dmColor"] == 92
    assert offsets["dmFormName"] == 102
    # The colour-management tail. These four are read by the whole module and a
    # wrong offset would silently read the driver's private block.
    assert offsets["dmICMMethod"] == 188
    assert offsets["dmICMIntent"] == 192
    assert offsets["dmMediaType"] == 196
    assert offsets["dmDitherType"] == 200
    assert ctypes.sizeof(printers.DEVMODEW) == 220
    assert struct.calcsize("<32s" + "H" * 3 + "I" + "h" * 13 + "32s") < 220


def test_dmfields_aliases_are_the_same_bit() -> None:
    """DM_ICMMETHOD aliases DM_ORIENTATION's bit, which is why the comment exists."""
    assert printers.DM_ICMMETHOD == printers.DM_ORIENTATION
    assert printers.DM_MEDIATYPE == printers.DM_PAPERLENGTH


def test_set_devmode_sends_the_patched_driver_extra() -> None:
    """dmDriverExtra must describe the block that is actually appended.

    The order used to be body = bytes(dm) then dm.dmDriverExtra = len(extra), so the
    header sent to the spooler carried the previous length.
    """
    dm = printers.DEVMODEW()
    dm.dmDriverExtra = 10
    patched = printers.DEVMODEW.from_buffer_copy(bytes(dm))
    patched.dmDriverExtra = 42
    body = bytes(patched)
    assert struct.unpack_from("<H", body, 70)[0] == 42


def test_mirror_keeps_size_and_dpi(tmp_path: Path) -> None:
    from PIL import Image

    source = tmp_path / "photo.jpg"
    Image.new("RGB", (3543, 2480), (200, 120, 60)).save(source, dpi=(300, 300))

    target, dpi = mirrored_copy(source, tmp_path / "work")
    assert dpi == (300, 300)
    with Image.open(target) as after:
        assert after.size == (3543, 2480)
        assert tuple(round(v) for v in after.info["dpi"]) == (300, 300)


def test_mirror_actually_flips(tmp_path: Path) -> None:
    from PIL import Image

    source = tmp_path / "marker.png"
    image = Image.new("RGB", (10, 10), (0, 0, 0))
    image.putpixel((1, 5), (255, 0, 0))
    image.save(source)

    target, _ = mirrored_copy(source, tmp_path / "work")
    with Image.open(target) as after:
        assert after.getpixel((1, 5)) == (0, 0, 0)
        assert after.getpixel((8, 5)) == (255, 0, 0)


def test_mirror_carries_the_embedded_icc(tmp_path: Path) -> None:
    from PIL import Image

    icc = b"\x00\x00\x02\x0c" + b"fake icc payload for the test" * 4
    source = tmp_path / "tagged.png"
    Image.new("RGB", (8, 8), (1, 2, 3)).save(source, icc_profile=icc)
    target, _ = mirrored_copy(source, tmp_path / "work")
    with Image.open(target) as after:
        assert after.info.get("icc_profile") == icc


def test_original_is_never_modified(tmp_path: Path) -> None:
    from PIL import Image

    source = tmp_path / "orig.tif"
    Image.new("RGB", (6, 4), (9, 9, 9)).save(source, dpi=(200, 200))
    before = source.read_bytes()
    mirrored_copy(source, tmp_path / "work")
    assert source.read_bytes() == before


def test_read_dpi_prefers_a_positive_tuple() -> None:
    assert _read_dpi(_Img({"dpi": (300, 300)})) == (300, 300)
    assert _read_dpi(_Img({"dpi": (0, 300)})) is None
    assert _read_dpi(_Img({})) is None
    # jfif_density_units is a unit code, not a resolution, and must not be read as one
    assert _read_dpi(_Img({"jfif_density_units": (1, 1)})) is None


def test_read_dpi_missing_dpi_is_not_invented(tmp_path: Path) -> None:
    from PIL import Image

    source = tmp_path / "nodpi.jpg"
    Image.new("RGB", (100, 100)).save(source)
    with Image.open(source) as after:
        assert _read_dpi(after) is None


def test_collect_files_order_and_filter(tmp_path: Path) -> None:
    for name in ("b.jpg", "a.jpg", "c.txt", ".hidden.jpg", "~tmp.jpg"):
        (tmp_path / name).write_bytes(b"x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "d.jpg").write_bytes(b"x")

    flat = [p.name for p in runner.collect_files(str(tmp_path))]
    assert flat == ["a.jpg", "b.jpg"]

    deep = [p.name for p in runner.collect_files(str(tmp_path), recursive=True)]
    assert deep == ["a.jpg", "b.jpg", "d.jpg"]

    assert runner.collect_files(str(tmp_path / "c.txt")) == []


def test_unmirrorable_extensions_exclude_layered_files() -> None:
    assert ".psd" not in runner.MIRRORABLE_EXTENSIONS
    assert ".psb" not in runner.MIRRORABLE_EXTENSIONS
    assert {".jpg", ".png", ".tif"} <= runner.MIRRORABLE_EXTENSIONS


def test_blocking_separates_notes_from_problems() -> None:
    assert runner.blocking(["note: fine", "mirror is on: fine"]) == []
    assert runner.blocking(["note: fine", "printer X is not installed"]) == [
        "printer X is not installed"
    ]


def test_profile_name_collision_is_refused(tmp_path: Path) -> None:
    original = runner.PROFILE_DIR
    runner.PROFILE_DIR = tmp_path
    try:
        runner.save_profile(runner.PrintProfile(name="a/b", printer="p"))
        try:
            runner.save_profile(runner.PrintProfile(name="ab", printer="p"))
        except runner.PrintError as exc:
            assert "already holds" in str(exc)
        else:
            raise AssertionError("colliding profile name was not refused")
        # The same name again is an overwrite and must stay allowed.
        runner.save_profile(runner.PrintProfile(name="a/b", printer="p2"))
    finally:
        runner.PROFILE_DIR = original


def test_profile_roundtrip(tmp_path: Path) -> None:
    original = runner.PROFILE_DIR
    runner.PROFILE_DIR = tmp_path
    try:
        profile = runner.PrintProfile(name="sublim", printer="p", mirror=True, delay_seconds=2)
        runner.save_profile(profile)
        loaded = runner.load_profile("sublim")
        assert loaded == profile
        assert [p.name for p in runner.list_profiles()] == ["sublim"]
        assert runner.delete_profile("sublim") is True
        assert runner.delete_profile("sublim") is False
    finally:
        runner.PROFILE_DIR = original


def main() -> int:
    tests = [(name, fn) for name, fn in sorted(globals().items()) if name.startswith("test_")]
    failures = 0
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        for name, fn in tests:
            # A folder per test: several of these assert on a directory listing,
            # so a shared one would let a previous test's files change the result.
            tmp = root / name
            tmp.mkdir()
            wants_tmp = "tmp_path" in fn.__code__.co_varnames[: fn.__code__.co_argcount]
            try:
                fn(tmp) if wants_tmp else fn()
                print(f"ok    {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())