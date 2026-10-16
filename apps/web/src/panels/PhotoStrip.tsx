import { useRef } from "react";
import { api } from "../api/client";
import type { Op } from "../api/ops";
import type { Level, Project } from "../api/types.gen";
import {
  isExcludedView,
  photosWhere,
  viewsOfImage,
  type Selection,
} from "../model";
import { useHosting } from "../state/hosting";
import { IconPlus, IconSliders } from "../ui/icons";

interface Props {
  project: Project;
  level: Level;
  busy: boolean;
  selection: Selection;
  onSelect: (s: Selection) => void;
  onUpload: (file: File) => void;
  onOp: (op: Op) => void;
  onAdvanced: () => void;
}

/** Bande de vignettes en bas à gauche du canevas : les photos du projet. Un clic met la photo
 *  en fond du plan (ou l'en retire) ; « voir » sélectionne sa caméra dans le plan. Tout le
 *  reste (réglages du fond, vues, suppression) est dans Paramètres avancés › Photos. Une
 *  photo ajoutée est analysée toute seule. */
export function PhotoStrip({
  project,
  level,
  busy,
  selection,
  onSelect,
  onUpload,
  onOp,
  onAdvanced,
}: Props) {
  const fileRef = useRef<HTMLInputElement>(null);
  const online = Boolean(useHosting()?.online);
  const images = Object.values(project.images);
  const selectedId = selection.kind === "element" ? selection.id : null;
  return (
    <div className="float card strip" aria-label="Photos du projet">
      {images.map((image, index) => {
        const views = viewsOfImage(level, image.id);
        const view = views.find((v) => !isExcludedView(v)) ?? views[0];
        const isBackground = level.background_image === image.id;
        const viewing = views.some((v) => v.id === selectedId);
        return (
          <div
            key={image.id}
            className={`strip-item${isBackground ? " active" : ""}${viewing ? " viewing" : ""}`}
          >
            <button
              className="strip-thumb"
              title={
                isBackground
                  ? `Photo ${index + 1} : en fond du plan (cliquer pour la retirer)`
                  : `Photo ${index + 1} : mettre en fond du plan`
              }
              disabled={busy}
              onClick={() =>
                onOp(
                  isBackground
                    ? {
                        op: "update_level",
                        fields: {
                          background_image: null,
                          background_transform: {},
                        },
                      }
                    : {
                        op: "update_level",
                        fields: {
                          background_image: image.id,
                          background_transform: level.background_transform,
                        },
                      },
                )
              }
            >
              <img src={api.imageFileUrl(project.id, image.id)} alt="" />
            </button>
            {view && (
              <button
                className="link strip-view"
                onClick={() => onSelect({ kind: "element", id: view.id })}
                title="Sélectionne la caméra de cette photo dans le plan et montre son champ de vision"
              >
                voir
              </button>
            )}
          </div>
        );
      })}
      <button
        className="icon"
        onClick={() => fileRef.current?.click()}
        disabled={busy}
        aria-label="Ajouter une photo"
        title={`Ajouter une photo : elle est analysée toute seule et complète le plan (JPG, PNG, WEBP ; ${photosWhere(project, online)}, EXIF retiré)`}
      >
        <IconPlus />
      </button>
      <input
        ref={fileRef}
        type="file"
        accept="image/jpeg,image/png,image/webp"
        style={{ display: "none" }}
        disabled={busy}
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) onUpload(file);
          e.target.value = "";
        }}
      />
      {images.length > 0 && (
        <button
          className="icon"
          onClick={onAdvanced}
          aria-label="Réglages des photos"
          title="Fond du plan, vues et suppression des photos (Paramètres avancés › Photos)"
        >
          <IconSliders />
        </button>
      )}
    </div>
  );
}
