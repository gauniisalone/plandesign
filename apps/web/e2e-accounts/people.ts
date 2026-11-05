import {
  test as base,
  type Browser,
  type BrowserContext,
  type Page,
} from "@playwright/test";

/** Les navigateurs des personnes d'un test (une adresse IP chacune), et leur fermeture.
 *
 *  Avant, chaque spec ouvrait ses contextes sans les refermer : avec un seul worker, les pages
 *  s'accumulaient d'un test à l'autre, chacune avec sa WebSocket, et le runner ralentissait
 *  jusqu'à faire dépasser les 90 s (view-reads.spec:235 sur Postgres, run e2e de e6784e8).
 *
 *  Un seul mécanisme : `person()` note le contexte qu'elle ouvre, et la fixture automatique
 *  de `test` les ferme tous à la fin de chaque test, réussi ou non. Une spec qui appelle
 *  `person()` importe donc `test` d'ici, pas de @playwright/test. */

const opened: BrowserContext[] = [];
let armed = false;

export const test = base.extend<{ closePeople: void }>({
  closePeople: [
    // Playwright exige un premier paramètre déstructuré, même vide.
    // eslint-disable-next-line no-empty-pattern
    async ({}, provide) => {
      armed = true;
      // Rendu à la fin du test, qu'il ait réussi ou échoué (après les afterEach).
      await provide();
      armed = false;
      // Fermer un contexte déjà fermé par le test ne fait rien.
      for (const context of opened.splice(0)) await context.close();
    },
    { auto: true },
  ],
});

export interface PersonOptions {
  viewport?: { width: number; height: number };
  storageState?: Awaited<ReturnType<BrowserContext["storageState"]>>;
}

/** Le navigateur d'une personne, avec son adresse IP (X-Forwarded-For), fermé en fin de test. */
export async function person(
  browser: Browser,
  ip: string,
  options: PersonOptions = {},
): Promise<Page> {
  if (!armed) {
    throw new Error(
      "person() : importer `test` de ./people (sinon ses contextes ne sont jamais fermés)",
    );
  }
  const context = await browser.newContext({
    baseURL: test.info().project.use.baseURL,
    viewport: options.viewport ?? { width: 1400, height: 900 },
    extraHTTPHeaders: { "X-Forwarded-For": ip },
    storageState: options.storageState,
  });
  opened.push(context);
  return context.newPage();
}
