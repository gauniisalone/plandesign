"""Intégration P50 (KAN-147 × KAN-159) : la garde de RAM PORTABLE de KAN-147 (Linux par
/proc/meminfo, Windows par GlobalMemoryStatusEx) produit le code `ram_basse` de KAN-159, avec la
valeur relevée et le seuil, jusqu'à l'échec que le poste envoie au serveur, avec ou sans le
contrôle de fidélité de KAN-154 (la boucle `once` d'engine.render). Lectures simulées : ni
Windows réel, ni GPU."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest

from openplan.adapters.neural import zimage
from openplan.adapters.neural.zimage import RAM_MIN_MIB, Stop, ZImageStoppedError, watch
from openplan.cli.render_worker import engine
from openplan.cli.render_worker.engine import EngineFailedError, guard_failure
from openplan.cli.render_worker.sandbox import BAKE, CAMERA, OUT, PROJECT

LOW_MIB = 900  # sous RAM_MIN_MIB (1 500)
LINUX_REAL = zimage._linux_available_mib
WINDOWS_REAL = zimage._windows_available_mib


class _Proc:
    """Un calcul qui tourne encore au premier tour de `watch`."""

    def poll(self) -> int | None:
        return None


class _Kernel32:
    """GlobalMemoryStatusEx simulé : remplit la structure reçue, comme Windows."""

    def __init__(self, avail_bytes: int) -> None:
        self.avail = avail_bytes

    def GlobalMemoryStatusEx(self, ref: Any) -> int:  # noqa: N802 - nom de l'API Win32
        ref._obj.ullAvailPhys = self.avail
        return 1


def _low_ram_on(platform: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """La RAM disponible vaut LOW_MIB sur la plateforme demandée, lue par le VRAI chemin de
    KAN-147 (ram_available_mib → lecture Linux ou Windows), seule la source étant simulée."""
    monkeypatch.setattr(sys, "platform", platform)  # le `sys` que lit ram_available_mib
    if platform == "win32":
        api = _Kernel32(LOW_MIB * 1024 * 1024)
        monkeypatch.setattr(zimage, "_windows_available_mib", lambda: WINDOWS_REAL(api))
        # Sous Windows, /proc/meminfo n'existe pas : le lire serait l'ancien défaut de KAN-147.
        monkeypatch.setattr(zimage, "_linux_available_mib", lambda: None)
    else:
        meminfo = tmp_path / "meminfo"
        meminfo.write_text(f"MemTotal: 16314568 kB\nMemAvailable: {LOW_MIB * 1024} kB\n", "ascii")
        monkeypatch.setattr(zimage, "_linux_available_mib", lambda: LINUX_REAL(meminfo))


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_the_portable_ram_guard_stops_with_ram_basse(
    platform: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _low_ram_on(platform, monkeypatch, tmp_path)
    assert zimage.ram_available_mib() == LOW_MIB
    killed: list[bool] = []
    # `read_ram` n'est PAS injecté : c'est la lecture par défaut de watch (KAN-147).
    stop, _peak = watch(_Proc(), kill=lambda: killed.append(True), read_vram=lambda: 1000)
    assert stop is not None
    assert (stop.code, stop.measured, stop.threshold) == ("ram_basse", LOW_MIB, RAM_MIN_MIB)
    assert killed == [True]
    # Et l'échec que le poste enverra : le code et les deux entiers, jamais le texte.
    failed = guard_failure(ZImageStoppedError(stop.text, stop))
    assert (failed.reason, failed.measured, failed.threshold) == ("ram_basse", LOW_MIB, RAM_MIN_MIB)
    assert "Mio" not in str(failed)


@pytest.mark.parametrize("fidelity_check", ["", "1"])
@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_engine_render_reports_ram_basse_with_or_without_the_fidelity_loop(
    platform: str, fidelity_check: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """La résolution du conflit d'engine.py (boucle `once` de KAN-154 + guard_failure de
    KAN-159) : l'arrêt `ram_basse` du garde-fou portable remonte tel quel."""
    _low_ram_on(platform, monkeypatch, tmp_path)
    monkeypatch.setenv("OPENPLAN_FIDELITY_CHECK", fidelity_check)
    fake = types.ModuleType("p50_fake_zimage")

    def render_scene(*_args: object, **_kwargs: object) -> object:
        stop, _ = watch(_Proc(), kill=lambda: None, read_vram=lambda: 1000)
        assert stop is not None
        raise ZImageStoppedError(stop.text, stop)

    fake.render_scene = render_scene  # type: ignore[attr-defined]
    fake.ZImageUnavailable = zimage.AdapterUnavailableError  # type: ignore[attr-defined]
    fake.ZImageVramGuard = ZImageStoppedError  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "p50_fake_zimage", fake)
    monkeypatch.setattr(engine, "ZIMAGE_MODULE", "p50_fake_zimage")
    args = (tmp_path / PROJECT, tmp_path / BAKE, tmp_path / CAMERA, tmp_path / OUT)
    with pytest.raises(EngineFailedError) as caught:
        engine.render("zimage", *args)
    assert (caught.value.reason, caught.value.measured, caught.value.threshold) == (
        "ram_basse",
        LOW_MIB,
        RAM_MIN_MIB,
    )


def test_an_old_guard_without_a_cause_still_reads_vram() -> None:
    assert guard_failure(ZImageStoppedError("arrêté")).reason == "vram"
    assert guard_failure(ZImageStoppedError("x", Stop("inconnu", "x"))).reason == "vram"  # type: ignore[arg-type]
