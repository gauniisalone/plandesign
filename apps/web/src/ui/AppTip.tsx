import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  LONG_PRESS_MS,
  placeTip,
  sameAsName,
  stillPressing,
  TIP_DELAY_MS,
  tipText,
  TOUCH_LINGER_MS,
} from "./tipLogic";

/** KAN-134 : la couche unique des infobulles de l'app. Tout élément qui porte `data-tip` en
 *  a une : au survol (0,4 s), au focus clavier, ou par un appui long au toucher. Après un
 *  appui long, le clic qui suit n'agit pas (l'appui servait à lire). Échap la ferme. Le
 *  texte n'est relié par aria-describedby que s'il dit plus que le nom de l'élément. */

const TIP_ID = "app-tip";

interface Shown {
  el: HTMLElement;
  text: string;
}

function tipTarget(node: EventTarget | null): HTMLElement | null {
  return node instanceof Element
    ? (node.closest<HTMLElement>("[data-tip]") ?? null)
    : null;
}

export function AppTip() {
  const [shown, setShown] = useState<Shown | null>(null);
  const tipRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let timer: number | null = null;
    let linger: number | null = null;
    let press: { el: HTMLElement; x: number; y: number } | null = null;
    /** L'élément dont le prochain clic est mangé (il suit un appui long). */
    let swallow: HTMLElement | null = null;
    let current: HTMLElement | null = null;

    const clear = () => {
      if (timer !== null) window.clearTimeout(timer);
      timer = null;
    };
    const hide = () => {
      clear();
      if (linger !== null) window.clearTimeout(linger);
      linger = null;
      if (current?.getAttribute("aria-describedby") === TIP_ID)
        current.removeAttribute("aria-describedby");
      current = null;
      setShown(null);
    };
    const show = (el: HTMLElement) => {
      const text = tipText(el.dataset.tip);
      if (!text || !el.isConnected) return;
      if (current && current !== el) hide();
      current = el;
      if (!sameAsName(text, el.getAttribute("aria-label")) && !el.hasAttribute("aria-describedby"))
        el.setAttribute("aria-describedby", TIP_ID);
      setShown({ el, text });
    };
    const later = (el: HTMLElement, ms: number) => {
      clear();
      timer = window.setTimeout(() => show(el), ms);
    };

    const over = (e: PointerEvent) => {
      if (e.pointerType === "touch") return;
      const el = tipTarget(e.target);
      if (!el || el === current) return;
      later(el, TIP_DELAY_MS);
    };
    const out = (e: PointerEvent) => {
      if (e.pointerType === "touch") return;
      const el = tipTarget(e.target);
      if (!el) return;
      const to = e.relatedTarget instanceof Node ? e.relatedTarget : null;
      if (to && el.contains(to)) return;
      hide();
    };
    const down = (e: PointerEvent) => {
      if (e.pointerType !== "touch") {
        hide();
        return;
      }
      const el = tipTarget(e.target);
      press = el ? { el, x: e.clientX, y: e.clientY } : null;
      swallow = null;
      if (el) later(el, LONG_PRESS_MS);
      else hide();
    };
    const move = (e: PointerEvent) => {
      if (press && !stillPressing(press, { x: e.clientX, y: e.clientY })) {
        press = null;
        clear();
      }
    };
    const up = (e: PointerEvent) => {
      if (e.pointerType !== "touch") return;
      if (press && current === press.el) {
        // L'appui long a montré l'infobulle : le clic qui suit ne déclenche rien.
        swallow = press.el;
        linger = window.setTimeout(hide, TOUCH_LINGER_MS);
      } else clear();
      press = null;
    };
    const click = (e: MouseEvent) => {
      if (swallow && e.target instanceof Node && swallow.contains(e.target)) {
        e.preventDefault();
        e.stopPropagation();
      }
      swallow = null;
    };
    const menu = (e: Event) => {
      // Le menu du navigateur au toucher prolongé ne recouvre pas l'infobulle.
      if (press && tipTarget(e.target) === press.el) e.preventDefault();
    };
    const focusIn = (e: FocusEvent) => {
      const el = tipTarget(e.target);
      if (!el) return;
      let keyboard = true;
      try {
        keyboard = el.matches(":focus-visible");
      } catch {
        // navigateur sans :focus-visible : le focus suffit
      }
      if (keyboard) show(el);
    };
    const focusOut = () => hide();
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") hide();
    };

    document.addEventListener("pointerover", over, true);
    document.addEventListener("pointerout", out, true);
    document.addEventListener("pointerdown", down, true);
    document.addEventListener("pointermove", move, true);
    document.addEventListener("pointerup", up, true);
    document.addEventListener("pointercancel", hide, true);
    document.addEventListener("click", click, true);
    document.addEventListener("contextmenu", menu, true);
    document.addEventListener("focusin", focusIn, true);
    document.addEventListener("focusout", focusOut, true);
    document.addEventListener("keydown", key, true);
    window.addEventListener("wheel", hide, { passive: true, capture: true });
    window.addEventListener("blur", hide);
    return () => {
      hide();
      document.removeEventListener("pointerover", over, true);
      document.removeEventListener("pointerout", out, true);
      document.removeEventListener("pointerdown", down, true);
      document.removeEventListener("pointermove", move, true);
      document.removeEventListener("pointerup", up, true);
      document.removeEventListener("pointercancel", hide, true);
      document.removeEventListener("click", click, true);
      document.removeEventListener("contextmenu", menu, true);
      document.removeEventListener("focusin", focusIn, true);
      document.removeEventListener("focusout", focusOut, true);
      document.removeEventListener("keydown", key, true);
      window.removeEventListener("wheel", hide, { capture: true });
      window.removeEventListener("blur", hide);
    };
  }, []);

  // Placée une fois mesurée (sa taille dépend du texte), avant d'être peinte.
  useLayoutEffect(() => {
    const tip = tipRef.current;
    if (!shown || !tip) return;
    const r = shown.el.getBoundingClientRect();
    const p = placeTip(
      { left: r.left, top: r.top, width: r.width, height: r.height },
      { width: tip.offsetWidth, height: tip.offsetHeight },
      { width: window.innerWidth, height: window.innerHeight },
    );
    tip.style.left = `${p.left}px`;
    tip.style.top = `${p.top}px`;
    tip.dataset.side = p.side;
    tip.style.visibility = "visible";
  }, [shown]);

  if (!shown) return null;
  return createPortal(
    <div
      ref={tipRef}
      id={TIP_ID}
      role="tooltip"
      className="app-tip"
      style={{ left: 0, top: 0, visibility: "hidden" }}
    >
      {shown.text}
    </div>,
    document.body,
  );
}
