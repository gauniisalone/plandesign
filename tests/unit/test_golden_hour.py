"""Chantier H, lot H4 : préréglage « heure dorée », distinct du soir."""

from __future__ import annotations

from openplan.pipeline.autopilot import LIGHT_TO_DAYLIGHT
from openplan.pipeline.render import DAYLIGHT_LABELS, daylight_spec


def test_golden_hour_is_its_own_preset_softer_than_the_evening() -> None:
    gold = daylight_spec("heure_doree", 90.0)
    evening = daylight_spec("soir", 90.0)
    assert "heure dorée" in DAYLIGHT_LABELS["heure_doree"]
    assert evening["sun"]["elevation_deg"] < gold["sun"]["elevation_deg"] < 30.0
    # plus chaude qu'un midi, moins orangée que le soir
    g, e = gold["sun"]["color"], evening["sun"]["color"]
    assert g[2] > e[2], "moins orangée que le soir"
    assert gold["sun"]["color"][0] >= gold["sun"]["color"][2]
    assert gold["sky_strength"] > evening["sky_strength"]


def test_the_ambiance_golden_hour_now_renders_as_golden_hour() -> None:
    assert LIGHT_TO_DAYLIGHT["heure_doree"] == "heure_doree"
    assert LIGHT_TO_DAYLIGHT["soir"] == "soir"


def test_golden_hour_warms_the_sky_softens_the_sun_and_the_fill() -> None:
    gold = daylight_spec("heure_doree", 90.0)
    assert gold["sky_tint"] is True and gold["sun"]["angle_deg"] > 1.5
    assert gold["fill_color"] is not None and gold["fill_color"][0] > gold["fill_color"][2]
    for other in ("matin", "midi", "soir"):
        spec = daylight_spec(other, 90.0)
        assert spec["sky_tint"] is False and spec["fill_color"] is None, (
            "les autres heures ne changent pas"
        )
