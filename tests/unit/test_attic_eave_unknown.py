"""KAN-61 : égout non vu (photo levée, sol hors champ, aucune jambette). Le plan généré prend la
borne tirée de la photo (« au plus »), sinon les pentes jusqu'au sol, toujours `inferred` avec une
note ; le bandeau « Plan estimé » le dit. Rien ne change quand l'égout est vu."""

from __future__ import annotations

from uuid import uuid4

import pytest

from openplan.pipeline.attic import AtticEstimate, Estimate
from openplan.pipeline.autoplan import attic_geometry, attic_notices
from openplan.schemas.common import Source


def _estimate(**update: object) -> AtticEstimate:
    base = AtticEstimate(
        camera_height=Estimate(value=1.45, sigma=0.15, source=Source.USER_DEFINED),
        ridge_height=Estimate(value=3.54, sigma=0.5),
        gable_distance=Estimate(value=4.5, sigma=0.7),
        usable_width=Estimate(value=2.7, sigma=0.4),
        floor_assumed=True,
    )
    return base.model_copy(update=update)


def test_unknown_eave_takes_the_bound_from_the_photo() -> None:
    est = _estimate(
        eave_bound=Estimate(value=1.01, sigma=0.15, source=Source.INFERRED, note="borne")
    )
    geometry = attic_geometry(est, uuid4(), uuid4())
    assert geometry is not None
    assert geometry.eave_height.value == pytest.approx(1.01)
    assert geometry.eave_height.source is Source.INFERRED
    assert geometry.eave_height.note is not None and "au plus 1.01 m" in geometry.eave_height.note
    notices = attic_notices(est, geometry)
    assert any(n.startswith("Sol hors champ") for n in notices)
    assert any(
        "Hauteur d'égout non visible sur la photo : supposée au plus 1,01 m" in n
        and "inspecteur" in n
        for n in notices
    )


@pytest.mark.parametrize("bound", [3.6, 0.0, None])
def test_incoherent_or_missing_bound_falls_back_to_slopes_to_the_floor(
    bound: float | None,
) -> None:
    """Repli (a) : borne absente, nulle ou au-dessus du faîtage → pentes jusqu'au sol, dit."""
    est = _estimate(
        eave_bound=None
        if bound is None
        else Estimate(value=bound, sigma=0.15, source=Source.INFERRED)
    )
    geometry = attic_geometry(est, uuid4(), uuid4())
    assert geometry is not None
    assert geometry.eave_height.value == 0.0
    assert geometry.eave_height.source is Source.INFERRED
    assert geometry.eave_height.note is not None and "jusqu'au sol" in geometry.eave_height.note
    assert any(
        "Hauteur d'égout non visible sur la photo : pentes prolongées jusqu'au sol" in n
        for n in attic_notices(est, geometry)
    )


def test_seen_eave_and_seen_floor_change_nothing() -> None:
    """Must have 4 : égout vu, ou sol vu → ni borne ni message."""
    seen = _estimate(eave_heights=[Estimate(value=1.0, sigma=0.15)] * 2)
    geometry = attic_geometry(seen, uuid4(), uuid4())
    assert geometry is not None and geometry.eave_height.value == 1.0
    assert not any("égout" in n for n in attic_notices(seen, geometry))
    floor_seen = _estimate(floor_assumed=False, eave_heights=[Estimate(value=0.0, sigma=0.1)])
    geometry = attic_geometry(floor_seen, uuid4(), uuid4())
    assert geometry is not None and geometry.eave_height.value == 0.0
    assert attic_notices(floor_seen, geometry) == []
