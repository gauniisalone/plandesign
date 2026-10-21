import type { ReactionPill } from "../api/client";

/** KAN-78, Must have 10 : ce qu'il faut animer entre deux lectures des pastilles (fonction
 *  pure, testée par `npm run test:unit`).
 *  - `pops` : ma réaction vient d'être donnée (la pastille fait « pop ») ;
 *  - `slides` : le nombre a changé (le chiffre glisse) ;
 *  - `faded` : j'ai retiré ma réaction, la pastille reste (fondu) ;
 *  - `floats` : la réaction d'un autre membre est arrivée (un petit emoji monte) ;
 *  - `leaving` : la pastille est passée à 0 (elle se replie avant de disparaître). */

export interface PillFx {
  pops: string[];
  slides: string[];
  faded: string[];
  floats: string[];
  leaving: ReactionPill[];
}

export const pillId = (p: Pick<ReactionPill, "item_id" | "key">) =>
  `${p.item_id}|${p.key}`;

export function diffPills(before: ReactionPill[], now: ReactionPill[]): PillFx {
  const old = new Map(before.map((p) => [pillId(p), p]));
  const fx: PillFx = {
    pops: [],
    slides: [],
    faded: [],
    floats: [],
    leaving: [],
  };
  for (const p of now) {
    const id = pillId(p);
    const b = old.get(id);
    if (b && b.count !== p.count) fx.slides.push(id);
    if (p.mine && !b?.mine) fx.pops.push(id);
    else if (!p.mine && b?.mine) fx.faded.push(id);
    else if (!b || p.count > b.count) fx.floats.push(id);
    old.delete(id);
  }
  fx.leaving = [...old.values()];
  return fx;
}
