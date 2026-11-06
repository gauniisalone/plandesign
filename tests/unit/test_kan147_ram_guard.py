"""KAN-147 (prérequis du client Windows) : la garde de RAM du rendu Z-Image lit la mémoire
disponible sous Linux (/proc/meminfo, inchangé) ET sous Windows (GlobalMemoryStatusEx, ctypes
de la bibliothèque standard). Lectures simulées : aucun Windows réel n'est nécessaire."""

from __future__ import annotations

import ctypes
from pathlib import Path
from typing import Any

import pytest

from openplan.adapters.neural import zimage
from openplan.adapters.neural.zimage import (
    RAM_MIN_MIB,
    _linux_available_mib,
    _windows_available_mib,
    ram_available_mib,
    watch,
)

MEMINFO = """MemTotal:       16314568 kB
MemFree:          912340 kB
MemAvailable:    6291456 kB
Buffers:          123456 kB
"""


class _Kernel32:
    """GlobalMemoryStatusEx simulé : remplit la structure reçue, comme Windows."""

    def __init__(self, avail_bytes: int, ok: bool = True) -> None:
        self.avail = avail_bytes
        self.ok = ok
        self.length_seen = 0

    def GlobalMemoryStatusEx(self, ref: Any) -> int:  # noqa: N802 - nom de l'API Win32
        status = ref._obj
        self.length_seen = status.dwLength
        status.ullAvailPhys = self.avail
        return 1 if self.ok else 0


def test_linux_reads_memavailable_as_before(tmp_path: Path) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(MEMINFO, encoding="ascii")
    assert _linux_available_mib(meminfo) == 6291456 // 1024  # 6144 Mio


@pytest.mark.parametrize("text", ["", "MemTotal: 1 kB\n", "MemAvailable: beaucoup kB\n"])
def test_linux_unreadable_gives_none(tmp_path: Path, text: str) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(text, encoding="ascii")
    assert _linux_available_mib(meminfo) is None
    assert _linux_available_mib(tmp_path / "absent") is None


def test_windows_reads_avail_phys() -> None:
    api = _Kernel32(avail_bytes=12 * 1024**3)
    assert _windows_available_mib(api) == 12 * 1024
    # La structure est annoncée avec sa taille (exigence de l'API Win32).
    assert api.length_seen == 64


def test_windows_failure_gives_none() -> None:
    assert _windows_available_mib(_Kernel32(0, ok=False)) is None
    assert _windows_available_mib(object()) is None  # API absente


def test_the_platform_picks_the_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(zimage, "_windows_available_mib", lambda: 8000)
    monkeypatch.setattr(zimage, "_linux_available_mib", lambda: 3000)
    assert ram_available_mib("win32") == 8000
    assert ram_available_mib("linux") == 3000


class _Proc:
    def __init__(self, polls: int) -> None:
        self.polls = polls

    def poll(self) -> int | None:
        self.polls -= 1
        return None if self.polls >= 0 else 0


def test_the_guard_now_stops_on_low_ram_under_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Le même seuil et le même message qu'aujourd'hui sous Linux : il ne restait que … Mio."""
    low = (RAM_MIN_MIB - 200) * 1024 * 1024
    monkeypatch.setattr(
        ctypes, "windll", type("W", (), {"kernel32": _Kernel32(low)})(), raising=False
    )
    killed: list[bool] = []
    reason, _ = watch(
        _Proc(5),
        kill=lambda: killed.append(True),
        read_vram=lambda: 3000,
        read_ram=lambda: ram_available_mib("win32"),
        sleep=lambda _s: None,
    )
    assert killed == [True]
    assert (
        reason
        == f"il ne restait que {RAM_MIN_MIB - 200} Mio de mémoire vive (minimum {RAM_MIN_MIB})"
    )


def test_linux_threshold_and_message_unchanged(tmp_path: Path) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(f"MemAvailable: {(RAM_MIN_MIB - 1) * 1024} kB\n", encoding="ascii")
    reason, _ = watch(
        _Proc(5),
        kill=lambda: None,
        read_vram=lambda: 3000,
        read_ram=lambda: _linux_available_mib(meminfo),
        sleep=lambda _s: None,
    )
    assert (
        reason == f"il ne restait que {RAM_MIN_MIB - 1} Mio de mémoire vive (minimum {RAM_MIN_MIB})"
    )
