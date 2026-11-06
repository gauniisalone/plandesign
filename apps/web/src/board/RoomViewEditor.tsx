import { useState } from "react";
import {
  EYE_MAX,
  EYE_MIN,
  EYE_STEP,
  eyeValue,
  FOCAL_MAX,
  FOCAL_MIN,
  focalMm,
  isEdited,
  metres,
  type RoomRow,
} from "./roomViews";

/** KAN-141 (maquette 2fb2691, validée par augus) : « Modifier la vue d'une pièce ». Hauteur
 *  d'œil et focale se règlent ici (le point et la pointe du cône se glissent sur le plan) ;
 *  le réglage part au relâcher du curseur (un pas d'historique, pas un par cran). Une vue
 *  modifiée à la main n'est plus recalculée ; « Recalculer » la rend au calcul. */
export function RoomViewEditor({
  row,
  canRender,
  onEye,
  onFocal,
  onReset,
  onRender,
}: {
  row: RoomRow;
  canRender: boolean;
  onEye: (z: number) => void;
  onFocal: (mm: number) => void;
  onReset: () => void;
  onRender: () => void;
}) {
  const camera = row.camera;
  // Le plan change (autre onglet, annuler, « Recalculer ») : le parent remonte ce panneau
  // (sa clé suit la hauteur et la focale), les curseurs repartent des nouvelles valeurs.
  const [eye, setEye] = useState(() => eyeValue(camera.position.z));
  const [mm, setMm] = useState(() => focalMm(camera.focal_px));
  const edited = isEdited(camera);
  // La valeur est relue sur le curseur (au relâcher, la dernière valeur n'est peut-être pas
  // encore dans l'état).
  type Ended = { currentTarget: HTMLInputElement };
  const commitEye = (e: Ended) => {
    const z = eyeValue(Number(e.currentTarget.value));
    if (Math.abs(z - camera.position.z) > 1e-3) onEye(z);
  };
  const commitFocal = (e: Ended) => {
    const v = Math.round(Number(e.currentTarget.value));
    if (v !== focalMm(camera.focal_px)) onFocal(v);
  };
  return (
    <div
      className="rgal-room-edit"
      role="group"
      aria-label={`Modifier la vue : ${row.name}`}
    >
      <b>
        {row.name} · {edited ? "vue modifiée à la main" : "vue calculée"}
      </b>
      <p className="rgal-hint">
        Glissez le point ou la pointe du cône sur le plan, ou réglez ici.
      </p>
      <label>
        Hauteur d'œil
        <input
          type="range"
          min={EYE_MIN}
          max={EYE_MAX}
          step={EYE_STEP}
          value={eye}
          onChange={(e) => setEye(eyeValue(Number(e.target.value)))}
          onPointerUp={commitEye}
          onKeyUp={commitEye}
          onBlur={commitEye}
        />
        <span className="rgal-room-vals">
          <span>{metres(EYE_MIN)}</span>
          <span>{metres(eye)}</span>
          <span>{metres(EYE_MAX)}</span>
        </span>
      </label>
      <label>
        Focale (équivalent)
        <input
          type="range"
          min={FOCAL_MIN}
          max={FOCAL_MAX}
          step={1}
          value={mm}
          onChange={(e) => setMm(Number(e.target.value))}
          onPointerUp={commitFocal}
          onKeyUp={commitFocal}
          onBlur={commitFocal}
        />
        <span className="rgal-room-vals">
          <span>{FOCAL_MIN} mm</span>
          <span>{mm} mm</span>
          <span>{FOCAL_MAX} mm</span>
        </span>
      </label>
      <div className="rgal-rooms-bar">
        <button type="button" disabled={!edited} onClick={onReset}>
          Recalculer
        </button>
        <button
          type="button"
          className="primary rgal-push"
          disabled={!canRender}
          onClick={onRender}
        >
          Rendre cette vue
        </button>
      </div>
      <p className="rgal-hint">
        Une vue modifiée à la main n'est plus recalculée ; « Recalculer » la rend
        au calcul.
      </p>
    </div>
  );
}
