import {
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type Ref,
} from "react";
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import type { Point3 } from "../api/types.gen";
import type { Selection } from "../model";
import { isDark, onThemeChange } from "../theme";
import type { SectionAxis } from "./view3d";

/** Pose d'un point de vue dans le repère du plan (Z vers le haut, lacet depuis +X). */
export interface ViewPose {
  position: Point3;
  yaw: number;
  pitch: number;
}

/** Points de vue de la barre de vues : dessus, face (depuis le sud du plan, −y), côté
 *  (depuis l'est, +x), iso (le cadrage d'ouverture). */
export type CameraPreset = "top" | "front" | "side" | "iso";

/** Direction de la caméra vers la scène, repère three (Y vers le haut, Z = −y du plan). Le
 *  dessus garde un soupçon de profondeur : OrbitControls perd l'orientation à la verticale. */
const PRESET_DIRECTIONS: Record<CameraPreset, [number, number, number]> = {
  top: [0, 1, 0.001],
  front: [0, 0.12, 1],
  side: [1, 0.12, 0],
  iso: [0.9, 0.8, 1.1],
};

/** Fond et grille du viewer selon le thème (le canevas suit le thème de la page). */
function sceneColors(): { background: number; grid: [number, number] } {
  return isDark()
    ? { background: 0x1e1e23, grid: [0x4a4a55, 0x34343c] }
    : { background: 0xf4f4f5, grid: [0xcccccc, 0xe5e5e5] };
}

/** Ce que l'écran Rendre demande au viewer : une vignette depuis une pose, sans Blender. */
export interface Viewer3DHandle {
  snapshotFrom: (
    pose: ViewPose,
    width?: number,
    height?: number,
  ) => string | null;
}

interface Props {
  /** URL du GLB ; doit changer (cache-buster) quand la scène change. */
  url: string;
  selection: Selection;
  /** Éléments masqués (ex. plafonds, qui cachent l'intérieur vu de dehors). */
  hiddenIds?: ReadonlySet<string>;
  onSelect: (s: Selection) => void;
  /** Se placer à cette pose (l'identifiant change à chaque demande). */
  viewFrom?: { pose: ViewPose; id: number } | null;
  /** Le GLB est chargé : les vignettes peuvent être prises. */
  onLoaded?: () => void;
  /** Plan de coupe (E3) : axe et position en mètres (repère du plan) ; `flip` garde l'autre
   *  côté (barre de vues, « Retourner la coupe »). */
  section?: { axis: SectionAxis; value: number; flip?: boolean } | null;
  /** Point de vue demandé par la barre de vues (l'identifiant change à chaque demande). */
  preset?: { kind: CameraPreset; id: number } | null;
  /** Maison de poupée (E3) : murs masqués quand ils sont tournés vers la caméra. */
  cutawayWalls?: readonly string[] | null;
  /** Rayons X (E3) : tout semi-transparent. */
  xray?: boolean;
  ref?: Ref<Viewer3DHandle>;
}

const NO_HIDDEN: ReadonlySet<string> = new Set();
const LOOK_AHEAD_M = 3;
const CUT_COLOR = 0x2b2b2e;
/** KAN-77 : le voile du plan de coupe prend l'ocre du site (--primary), plus d'indigo. */
const SECTION_TINT = 0xa63a2e;
/** Plan repoussé très loin : ne coupe rien. */
const NO_CLIP = 1e6;
/** Un mur est « côté caméra » quand il est du côté de la caméra par rapport au centre. */
const CUTAWAY_DOT = 0.3;

/** Plan de coupe three.js : on garde le côté où la distance est positive. Le plan est Z vers
 *  le haut ; three, Y vers le haut et Z = −y. */
function sectionPlane(
  axis: SectionAxis,
  value: number,
  flip = false,
): THREE.Plane | null {
  let plane: THREE.Plane | null = null;
  if (axis === "z") plane = new THREE.Plane(new THREE.Vector3(0, -1, 0), value);
  if (axis === "x") plane = new THREE.Plane(new THREE.Vector3(-1, 0, 0), value);
  if (axis === "y") plane = new THREE.Plane(new THREE.Vector3(0, 0, 1), value);
  return plane && flip ? plane.negate() : plane;
}

/** Le plan est Z vers le haut ; le GLB (glTF) est Y vers le haut : (x, y, z) → (x, z, −y). */
function toThree(p: Point3): THREE.Vector3 {
  return new THREE.Vector3(p.x, p.z, -p.y);
}

function applyPose(
  camera: THREE.PerspectiveCamera,
  controls: OrbitControls,
  pose: ViewPose,
) {
  const eye = toThree(pose.position);
  const dir = toThree({
    x: Math.cos(pose.yaw) * Math.cos(pose.pitch),
    y: Math.sin(pose.yaw) * Math.cos(pose.pitch),
    z: Math.sin(pose.pitch),
  });
  camera.position.copy(eye);
  controls.target.copy(eye.clone().add(dir.multiplyScalar(LOOK_AHEAD_M)));
  camera.near = 0.05;
  camera.updateProjectionMatrix();
  controls.update();
}

/** Visualisation Three.js du GLB produit par trimesh. Les nœuds portent l'UUID des éléments. */
export function Viewer3D({
  url,
  selection,
  hiddenIds = NO_HIDDEN,
  onSelect,
  viewFrom,
  onLoaded,
  section = null,
  preset = null,
  cutawayWalls = null,
  xray = false,
  ref,
}: Props) {
  const mountRef = useRef<HTMLDivElement>(null);
  const sceneRef = useRef<THREE.Group | null>(null);
  const hiddenRef = useRef(hiddenIds);
  hiddenRef.current = hiddenIds;
  const cutawayRef = useRef(cutawayWalls);
  cutawayRef.current = cutawayWalls;
  const xrayRef = useRef(xray);
  xrayRef.current = xray;
  const planeRef = useRef<THREE.Plane | null>(null);
  const helperRef = useRef<THREE.Mesh | null>(null);
  /** Plan de coupe unique, posé une fois sur chaque matière puis seulement déplacé : changer
   *  la liste des plans d'une matière en cours de rendu faisait échouer three.js (uniformes
   *  absents). Sans coupe, il est repoussé très loin et ne coupe rien. */
  const clipPlane = useRef(
    new THREE.Plane(new THREE.Vector3(0, -1, 0), NO_CLIP),
  );
  const capMaterial = useRef(
    new THREE.MeshBasicMaterial({
      color: CUT_COLOR,
      side: THREE.BackSide,
      clippingPlanes: [clipPlane.current],
    }),
  );
  const [cutCount, setCutCount] = useState(0);
  const onLoadedRef = useRef(onLoaded);
  onLoadedRef.current = onLoaded;
  const presetRef = useRef(preset);
  presetRef.current = preset;
  const rigRef = useRef<{
    renderer: THREE.WebGLRenderer;
    scene: THREE.Scene;
    camera: THREE.PerspectiveCamera;
    controls: OrbitControls;
    /** Cadre la scène depuis une direction (repère three). */
    frameFrom: (direction: THREE.Vector3) => void;
  } | null>(null);
  const selectedId = selection.kind === "element" ? selection.id : null;

  useImperativeHandle(
    ref,
    () => ({
      snapshotFrom: (pose, width = 320, height = 240) => {
        const rig = rigRef.current;
        if (!rig || !sceneRef.current) return null;
        const { renderer, scene, camera, controls } = rig;
        const saved = {
          position: camera.position.clone(),
          target: controls.target.clone(),
          aspect: camera.aspect,
          size: renderer.getSize(new THREE.Vector2()),
        };
        renderer.setSize(width, height, false);
        camera.aspect = width / height;
        applyPose(camera, controls, pose);
        renderer.render(scene, camera);
        const data = renderer.domElement.toDataURL("image/jpeg", 0.75);
        renderer.setSize(saved.size.x, saved.size.y, false);
        camera.aspect = saved.aspect;
        camera.position.copy(saved.position);
        controls.target.copy(saved.target);
        camera.updateProjectionMatrix();
        controls.update();
        return data;
      },
    }),
    [],
  );

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;
    const renderer = new THREE.WebGLRenderer({
      antialias: true,
      preserveDrawingBuffer: true,
    });
    renderer.localClippingEnabled = true;
    renderer.setPixelRatio(window.devicePixelRatio);
    renderer.setSize(mount.clientWidth, mount.clientHeight);
    mount.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0xf4f4f5);
    const camera = new THREE.PerspectiveCamera(
      50,
      mount.clientWidth / mount.clientHeight,
      0.05,
      500,
    );
    camera.position.set(6, 5, 8);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    scene.add(new THREE.HemisphereLight(0xffffff, 0x888877, 1.1));
    const sun = new THREE.DirectionalLight(0xffffff, 1.4);
    sun.position.set(5, 10, 4);
    scene.add(sun);
    let grid: THREE.GridHelper | null = null;
    const paint = () => {
      const colors = sceneColors();
      scene.background = new THREE.Color(colors.background);
      if (grid) {
        scene.remove(grid);
        grid.dispose();
      }
      grid = new THREE.GridHelper(20, 20, colors.grid[0], colors.grid[1]);
      scene.add(grid);
    };
    paint();
    const stopTheme = onThemeChange(paint);

    // Cadrage : la scène entière tient dans le volet, même étroit (vue partagée). Tant que
    // l'utilisateur n'a pas bougé la caméra, un changement de taille recadre (depuis la
    // dernière direction choisie : iso à l'ouverture, ou un point de vue de la barre).
    let framed: { center: THREE.Vector3; size: number } | null = null;
    const lastFramed = new THREE.Vector3();
    const direction = new THREE.Vector3(...PRESET_DIRECTIONS.iso).normalize();
    const frameCamera = () => {
      if (!framed) return;
      const half = THREE.MathUtils.degToRad(camera.fov) / 2;
      const halfWide = Math.atan(Math.tan(half) * camera.aspect);
      const needed = framed.size / 2 / Math.sin(Math.min(half, halfWide));
      const distance = Math.max(framed.size * 1.63, needed * 1.08);
      camera.position
        .copy(framed.center)
        .add(direction.clone().multiplyScalar(distance));
      controls.target.copy(framed.center);
      lastFramed.copy(camera.position);
      camera.updateProjectionMatrix();
      controls.update();
    };
    const frameFrom = (d: THREE.Vector3) => {
      direction.copy(d).normalize();
      frameCamera();
    };
    rigRef.current = { renderer, scene, camera, controls, frameFrom };

    let disposed = false;
    const loader = new GLTFLoader();
    loader.load(
      url,
      (gltf) => {
        if (disposed) return;
        if (sceneRef.current) scene.remove(sceneRef.current);
        const root = gltf.scene;
        root.traverse((obj) => {
          if (obj instanceof THREE.Mesh) {
            const geometry = obj.geometry as THREE.BufferGeometry;
            const hasColors = geometry.getAttribute("color") !== undefined;
            obj.material = new THREE.MeshStandardMaterial({
              vertexColors: hasColors,
              color: hasColors ? 0xffffff : 0xbbbbbb,
              roughness: 0.85,
              metalness: 0,
            });
            obj.userData["baseEmissive"] = 0x000000;
          }
        });
        // Faces coupées (E3) : l'envers des solides fermés, vu par la coupe, en couleur.
        const meshes: THREE.Mesh[] = [];
        root.traverse((obj) => {
          if (obj instanceof THREE.Mesh) meshes.push(obj);
        });
        for (const mesh of meshes) {
          const cap = new THREE.Mesh(mesh.geometry, capMaterial.current);
          cap.userData["cap"] = true;
          cap.visible = false;
          mesh.add(cap);
        }
        scene.add(root);
        sceneRef.current = root;
        applyVisibility(root, hiddenRef.current);
        const box = new THREE.Box3().setFromObject(root);
        if (!box.isEmpty()) {
          const center = box.getCenter(new THREE.Vector3());
          const size = box.getSize(new THREE.Vector3()).length();
          controls.target.copy(center);
          camera.near = size / 100;
          camera.far = size * 40;
          framed = { center, size };
          // Un point de vue demandé avant le chargement (raccourci 1 à 4) s'applique ici.
          const asked = presetRef.current;
          if (asked)
            direction.set(...PRESET_DIRECTIONS[asked.kind]).normalize();
          frameCamera();
        }
        applySelection(root, selectedId);
        applySection(
          root,
          planeRef.current !== null && !xrayRef.current,
          clipPlane.current,
        );
        applyXray(root, xrayRef.current);
        const helper = new THREE.Mesh(
          new THREE.PlaneGeometry(1, 1),
          new THREE.MeshBasicMaterial({
            color: SECTION_TINT,
            transparent: true,
            opacity: 0.08,
            side: THREE.DoubleSide,
            depthWrite: false,
          }),
        );
        helper.visible = false;
        scene.add(helper);
        helperRef.current = helper;
        placeHelper(helper, root, planeRef.current);
        onLoadedRef.current?.();
      },
      undefined,
      (err) => console.error("GLB", err),
    );

    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();
    let downAt: { x: number; y: number } | null = null;
    const onDown = (e: PointerEvent) => {
      downAt = { x: e.clientX, y: e.clientY };
    };
    const onUp = (e: PointerEvent) => {
      if (
        !downAt ||
        Math.hypot(e.clientX - downAt.x, e.clientY - downAt.y) > 4 ||
        !sceneRef.current
      )
        return;
      const rect = renderer.domElement.getBoundingClientRect();
      pointer.set(
        ((e.clientX - rect.left) / rect.width) * 2 - 1,
        -((e.clientY - rect.top) / rect.height) * 2 + 1,
      );
      raycaster.setFromCamera(pointer, camera);
      const hit = raycaster
        .intersectObject(sceneRef.current, true)
        .find((h) => h.object.visible);
      const name = hit?.object.name ?? hit?.object.parent?.name;
      onSelect(
        name
          ? { kind: "element", id: name.split("#")[0] ?? name }
          : { kind: "none" },
      );
    };
    renderer.domElement.addEventListener("pointerdown", onDown);
    renderer.domElement.addEventListener("pointerup", onUp);

    const resize = new ResizeObserver(() => {
      renderer.setSize(mount.clientWidth, mount.clientHeight);
      camera.aspect = mount.clientWidth / mount.clientHeight;
      camera.updateProjectionMatrix();
      if (framed && camera.position.distanceTo(lastFramed) < 1e-3)
        frameCamera();
    });
    resize.observe(mount);

    let frame = 0;
    let lastCut = -1;
    const animate = () => {
      frame = requestAnimationFrame(animate);
      controls.update();
      const root = sceneRef.current;
      const walls = cutawayRef.current;
      if (root && walls) {
        const n = applyCutaway(root, new Set(walls), hiddenRef.current, camera);
        if (n !== lastCut) {
          lastCut = n;
          setCutCount(n);
        }
      } else if (lastCut !== -1) {
        lastCut = -1;
        setCutCount(0);
        if (root) applyVisibility(root, hiddenRef.current);
      }
      renderer.render(scene, camera);
    };
    animate();

    return () => {
      disposed = true;
      cancelAnimationFrame(frame);
      resize.disconnect();
      stopTheme();
      renderer.domElement.removeEventListener("pointerdown", onDown);
      renderer.domElement.removeEventListener("pointerup", onUp);
      controls.dispose();
      renderer.dispose();
      mount.removeChild(renderer.domElement);
      sceneRef.current = null;
      rigRef.current = null;
    };
    // La sélection est appliquée par l'effet suivant ; onSelect est stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url]);

  useEffect(() => {
    if (sceneRef.current) applySelection(sceneRef.current, selectedId);
  }, [selectedId]);

  useEffect(() => {
    if (sceneRef.current) applyVisibility(sceneRef.current, hiddenIds);
  }, [hiddenIds]);

  const sectionKey = section
    ? `${section.axis}:${section.value.toFixed(2)}${section.flip ? ":flip" : ""}`
    : "none";
  useEffect(() => {
    const plane = section
      ? sectionPlane(section.axis, section.value, section.flip)
      : null;
    planeRef.current = plane;
    if (plane) clipPlane.current.copy(plane);
    else clipPlane.current.set(new THREE.Vector3(0, -1, 0), NO_CLIP);
    if (sceneRef.current) {
      applySection(
        sceneRef.current,
        plane !== null && !xray,
        clipPlane.current,
      );
      if (helperRef.current)
        placeHelper(helperRef.current, sceneRef.current, plane);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sectionKey]);
  useEffect(() => {
    if (!sceneRef.current) return;
    applyXray(sceneRef.current, xray);
    // En rayons X, les faces coupées (envers en couleur) se verraient à travers tout.
    applySection(
      sceneRef.current,
      planeRef.current !== null && !xray,
      clipPlane.current,
    );
  }, [xray]);

  useEffect(() => {
    const rig = rigRef.current;
    if (rig && viewFrom) applyPose(rig.camera, rig.controls, viewFrom.pose);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [viewFrom?.id]);

  useEffect(() => {
    const rig = rigRef.current;
    if (rig && preset)
      rig.frameFrom(new THREE.Vector3(...PRESET_DIRECTIONS[preset.kind]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preset?.id]);

  return (
    <div
      className="viewer3d"
      ref={mountRef}
      data-section={sectionKey}
      data-preset={preset?.kind ?? "iso"}
      data-xray={xray ? "on" : "off"}
      data-cutaway={cutawayWalls ? String(cutCount) : "off"}
    />
  );
}

function elementId(obj: THREE.Object3D): string {
  return (obj.name || obj.parent?.name || "").split("#")[0] ?? "";
}

function isCap(obj: THREE.Object3D): boolean {
  return obj.userData["cap"] === true;
}

function applyVisibility(root: THREE.Object3D, hidden: ReadonlySet<string>) {
  root.traverse((obj) => {
    if (obj instanceof THREE.Mesh && !isCap(obj))
      obj.visible = !hidden.has(elementId(obj));
  });
}

/** Coupe : chaque matière garde le côté positif du plan ; les envers coupés en couleur. */
function applySection(
  root: THREE.Object3D,
  active: boolean,
  clip: THREE.Plane,
) {
  root.traverse((obj) => {
    if (!(obj instanceof THREE.Mesh)) return;
    if (isCap(obj)) {
      obj.visible = active && (obj.parent?.visible ?? true);
      return;
    }
    const material = obj.material as THREE.MeshStandardMaterial;
    if (!material.clippingPlanes?.includes(clip)) {
      material.clippingPlanes = [clip];
      material.needsUpdate = true;
    }
  });
}

/** Repère translucide du plan de coupe, à la taille de la scène. */
function placeHelper(
  helper: THREE.Mesh,
  root: THREE.Object3D,
  plane: THREE.Plane | null,
) {
  helper.visible = plane !== null;
  if (!plane) return;
  const box = new THREE.Box3().setFromObject(root);
  const size = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  const span = Math.max(size.x, size.y, size.z) * 1.2;
  helper.scale.set(span, span, 1);
  helper.position.copy(center);
  helper.rotation.set(0, 0, 0);
  const n = plane.normal;
  if (Math.abs(n.y) > 0.5) {
    helper.rotation.x = -Math.PI / 2;
    helper.position.y = plane.constant;
  } else if (Math.abs(n.x) > 0.5) {
    helper.rotation.y = Math.PI / 2;
    helper.position.x = plane.constant;
  } else {
    helper.position.z = -plane.constant;
  }
}

/** Rayons X : tout semi-transparent, sans écrire la profondeur (on voit à travers). */
function applyXray(root: THREE.Object3D, on: boolean) {
  root.traverse((obj) => {
    if (!(obj instanceof THREE.Mesh) || isCap(obj)) return;
    const material = obj.material as THREE.MeshStandardMaterial;
    material.transparent = on;
    material.opacity = on ? 0.28 : 1;
    material.depthWrite = !on;
    material.needsUpdate = true;
  });
}

/** Maison de poupée : les murs du côté de la caméra (par rapport au centre de la scène)
 *  sont masqués ; renvoie leur nombre. */
function applyCutaway(
  root: THREE.Object3D,
  walls: ReadonlySet<string>,
  hidden: ReadonlySet<string>,
  camera: THREE.Camera,
): number {
  const box = new THREE.Box3().setFromObject(root);
  const center = box.getCenter(new THREE.Vector3());
  const toCam = camera.position.clone().sub(center).setY(0).normalize();
  let count = 0;
  root.traverse((obj) => {
    if (!(obj instanceof THREE.Mesh) || isCap(obj)) return;
    const id = elementId(obj);
    if (hidden.has(id)) {
      obj.visible = false;
      return;
    }
    if (!walls.has(id)) {
      obj.visible = true;
      return;
    }
    const c = new THREE.Box3()
      .setFromObject(obj)
      .getCenter(new THREE.Vector3());
    const out = c.sub(center).setY(0);
    const facing =
      out.lengthSq() > 1e-6 && out.normalize().dot(toCam) > CUTAWAY_DOT;
    obj.visible = !facing;
    if (facing) count += 1;
  });
  return count;
}

function applySelection(root: THREE.Object3D, selectedId: string | null) {
  root.traverse((obj) => {
    // Les faces coupées (matière basique, sans émission) ne prennent pas la surbrillance :
    // lui donner un « emissive » cassait le rendu three.js.
    if (!(obj instanceof THREE.Mesh) || isCap(obj)) return;
    const material = obj.material as THREE.MeshStandardMaterial;
    const selected = selectedId !== null && elementId(obj) === selectedId;
    material.emissive = new THREE.Color(selected ? 0x1f5fbf : 0x000000);
    material.emissiveIntensity = selected ? 0.6 : 0;
  });
}
