"""Estimateur de caméra sans modèle : focale depuis l'EXIF, sinon une valeur par défaut.

Orientation : pitch et roll sont supposés nuls (photo tenue droite). C'est une hypothèse,
pas une mesure : la confiance est basse et l'estimateur par points de fuite (5b) la remplace
dès que des lignes suffisantes sont trouvées.
"""

from __future__ import annotations

from openplan.adapters.protocols import CameraParameters, ImageArray

MODEL_ID = "exif_camera@0.1"
FULL_FRAME_WIDTH_MM = 36.0
"""Largeur du capteur 24×36 auquel se rapporte la focale « équivalente 35 mm »."""
DEFAULT_FOCAL_RATIO = 0.8
"""Sans EXIF : focale = 0,8 × grand côté en pixels (≈ 29 mm équivalent, courant sur mobile)."""
CONFIDENCE_WITH_EXIF = 0.7
CONFIDENCE_DEFAULT = 0.2


def focal_px_from_35mm(focal_35mm: float, width_px: int, height_px: int) -> float:
    """La focale équivalente 35 mm se rapporte au grand côté (36 mm) du capteur."""
    return focal_35mm / FULL_FRAME_WIDTH_MM * max(width_px, height_px)


class ExifCameraEstimator:
    model_id = MODEL_ID

    def estimate(self, image: ImageArray) -> CameraParameters:
        w, h = image.width, image.height
        if image.focal_35mm is not None:
            focal_px = focal_px_from_35mm(image.focal_35mm, w, h)
            confidence = CONFIDENCE_WITH_EXIF
        else:
            focal_px = DEFAULT_FOCAL_RATIO * max(w, h)
            confidence = CONFIDENCE_DEFAULT
        return CameraParameters(
            focal_px=focal_px,
            principal_point=(w / 2.0, h / 2.0),
            pitch=0.0,
            roll=0.0,
            confidence=confidence,
            model_id=self.model_id,
        )
