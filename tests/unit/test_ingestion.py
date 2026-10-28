"""Import d'image : EXIF retiré, focale conservée, orientation, hash, suppression."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest
from PIL import Image

from openplan.adapters.exif_camera import (
    CONFIDENCE_DEFAULT,
    CONFIDENCE_WITH_EXIF,
    ExifCameraEstimator,
    focal_px_from_35mm,
)
from openplan.adapters.protocols import ImageArray
from openplan.pipeline.ingestion import (
    ImageNotFoundError,
    IngestionError,
    import_image,
    load_image,
    remove_image,
)
from openplan.scene.factory import new_project
from openplan.scene.ops import UpdateLevel, apply_op
from openplan.schemas import PrivacySettings, Project
from openplan.validation.invariants import check_invariants
from tests.factories import only_level

ORIENTATION = 0x0112
EXIF_IFD = 0x8769
FOCAL_35MM = 0xA405
ARTIST = 0x013B


def jpeg_with_exif(width: int = 640, height: int = 480, *, rotate: bool = True) -> bytes:
    """JPEG paysage avec orientation 6 (à tourner de 90°), focale 28 mm et un nom d'auteur."""
    rng = np.random.default_rng(0)
    pixels = rng.integers(0, 255, size=(height, width, 3), dtype=np.uint8)
    image = Image.fromarray(pixels)
    exif = Image.Exif()
    exif[ARTIST] = "Personne Privée"
    if rotate:
        exif[ORIENTATION] = 6
    exif.get_ifd(EXIF_IFD)[FOCAL_35MM] = 28
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", exif=exif.tobytes(), quality=95)
    return buffer.getvalue()


@pytest.fixture
def project() -> Project:
    return new_project("photos")


def test_import_strips_exif_keeps_focal_and_orients(project: Project, tmp_path: Path) -> None:
    data = jpeg_with_exif()
    after, ref, warnings = import_image(project, tmp_path, data, original_name="../evil.jpg")
    assert ref.path == f"images/{ref.id}.jpg"
    stored = tmp_path / ref.path
    assert stored.is_file()
    assert ref.sha256 == hashlib.sha256(stored.read_bytes()).hexdigest()
    assert ref.exif_stripped is True and ref.focal_35mm == 28.0
    assert (ref.width_px, ref.height_px) == (480, 640), "orientation EXIF appliquée"
    with Image.open(stored) as img:
        assert img.getexif() == {}, "aucune métadonnée dans le fichier stocké"
        assert img.size == (480, 640)
    assert after.images[ref.id] == ref
    assert set(ref.quality) >= {"blur_reblur_ratio", "mean_luminance", "min_side_px"}
    assert any("Résolution faible" in w for w in warnings)
    loaded = load_image(tmp_path, ref)
    assert loaded.pixels.shape == (640, 480, 3) and loaded.focal_35mm == 28.0


def test_import_without_stripping_keeps_bytes(tmp_path: Path) -> None:
    project = new_project("brut").model_copy(
        update={"privacy": PrivacySettings(strip_exif=False, keep_exif_focal=False)}
    )
    data = jpeg_with_exif()
    _, ref, _ = import_image(project, tmp_path, data)
    assert (tmp_path / ref.path).read_bytes() == data
    assert ref.exif_stripped is False and ref.focal_35mm is None
    assert (ref.width_px, ref.height_px) == (480, 640), "dimensions après orientation"
    assert load_image(tmp_path, ref).pixels.shape == (640, 480, 3)


def test_import_png_and_rejections(project: Project, tmp_path: Path) -> None:
    from tests.unit.test_quality import checkerboard

    buffer = io.BytesIO()
    midtones = (checkerboard(size=1024, cell=64) // 2 + 70).astype(np.uint8)  # 70 ou 197
    rgba = np.concatenate([midtones, np.full((1024, 1024, 1), 255, dtype=np.uint8)], axis=-1)
    Image.fromarray(rgba, mode="RGBA").save(buffer, format="PNG")
    after, ref, warnings = import_image(project, tmp_path, buffer.getvalue())
    assert ref.path.endswith(".png") and ref.focal_35mm is None
    assert warnings == [], "damier net, bien exposé, 1024 px : rien à signaler"
    with pytest.raises(IngestionError, match="vide"):
        import_image(after, tmp_path, b"")
    with pytest.raises(IngestionError, match="illisible"):
        import_image(after, tmp_path, b"pas une image du tout")
    bmp = io.BytesIO()
    Image.new("RGB", (8, 8)).save(bmp, format="BMP")
    # KAN-113 : le décodeur BMP n'est plus essayé, le message ne nomme donc plus le format.
    with pytest.raises(IngestionError, match=r"format non pris en charge \(x\.bmp\) : JPEG"):
        import_image(after, tmp_path, bmp.getvalue(), original_name="x.bmp")
    with pytest.raises(IngestionError, match="volumineux"):
        import_image(after, tmp_path, b"\0" * (50 * 1024 * 1024 + 1))


def test_remove_image_detaches_background_and_deletes_file(
    project: Project, tmp_path: Path
) -> None:
    after, ref, _ = import_image(project, tmp_path, jpeg_with_exif(rotate=False))
    level = only_level(after)
    with_bg = apply_op(
        after,
        level.id,
        UpdateLevel(
            fields={
                "background_image": str(ref.id),
                "background_transform": {"scale": 0.01, "opacity": 0.5},
            }
        ),
    )
    assert only_level(with_bg).background_image == ref.id
    assert check_invariants(with_bg) == []

    assert (tmp_path / "thumbnail.jpg").is_file(), "vignette du projet écrite à l'import"
    removed = remove_image(with_bg, tmp_path, ref.id)
    assert ref.id not in removed.images
    assert only_level(removed).background_image is None
    assert only_level(removed).background_transform == {}
    assert not (tmp_path / ref.path).exists()
    assert not (tmp_path / "thumbnail.jpg").exists(), "plus de photo : plus de vignette"
    with pytest.raises(ImageNotFoundError):
        remove_image(removed, tmp_path, ref.id)
    with pytest.raises(ImageNotFoundError, match="absent"):
        load_image(tmp_path, ref)


def test_thumbnail_follows_the_first_remaining_photo(project: Project, tmp_path: Path) -> None:
    """Vignette = première photo, au plus 240 px, sans métadonnées ; supprimer la première
    la refait depuis la suivante."""

    def jpeg(width: int, height: int) -> bytes:
        buffer = io.BytesIO()
        Image.new("RGB", (width, height), (90, 120, 200)).save(buffer, format="JPEG")
        return buffer.getvalue()

    after, wide, _ = import_image(project, tmp_path, jpeg(1600, 800))
    with Image.open(tmp_path / "thumbnail.jpg") as thumb:
        assert thumb.size == (240, 120) and thumb.getexif() == {}
    after, _tall, _ = import_image(after, tmp_path, jpeg(600, 1200))
    with Image.open(tmp_path / "thumbnail.jpg") as thumb:
        assert thumb.size == (240, 120), "la vignette reste celle de la première photo"
    after = remove_image(after, tmp_path, wide.id)
    with Image.open(tmp_path / "thumbnail.jpg") as thumb:
        assert thumb.size == (120, 240), "refaite depuis la photo restante"


def test_dangling_background_is_reported(project: Project) -> None:
    level = only_level(project)
    dangling = apply_op(project, level.id, UpdateLevel(fields={"background_image": str(uuid4())}))
    (violation,) = check_invariants(dangling)
    assert violation.type == "orphan_background_image" and violation.severity == "medium"


def test_exif_camera_estimator() -> None:
    pixels = np.zeros((480, 640, 3), dtype=np.uint8)
    estimator = ExifCameraEstimator()
    with_focal = estimator.estimate(ImageArray(image_id=uuid4(), pixels=pixels, focal_35mm=28))
    assert with_focal.focal_px == pytest.approx(28 / 36 * 640)
    assert with_focal.confidence == CONFIDENCE_WITH_EXIF
    assert with_focal.principal_point == (320.0, 240.0)
    assert with_focal.pitch == 0.0 and with_focal.model_id.startswith("exif_camera@")
    fallback = estimator.estimate(ImageArray(image_id=uuid4(), pixels=pixels))
    assert fallback.focal_px == pytest.approx(0.8 * 640)
    assert fallback.confidence == CONFIDENCE_DEFAULT
    assert focal_px_from_35mm(36.0, 480, 640) == 640.0, "grand côté en portrait"
    with pytest.raises(ValueError, match="HxWx3"):
        ImageArray(image_id=uuid4(), pixels=np.zeros((4, 4), dtype=np.uint8))
