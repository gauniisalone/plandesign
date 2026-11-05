import { createHash } from "node:crypto";
import type { APIRequestContext, Page } from "@playwright/test";
import { createBdd, defineParameterType, test as base } from "playwright-bdd";
import { hasWeights, PHOTO_WEIGHTS } from "../../e2e/helpers";

/** KAN-103 : la bibliothèque de pas partagée. Les .feature (en français) se lisent comme les
 *  Must have ; les pas reprennent les helpers des e2e existants. Un scénario garde son état
 *  (personnes, projet, poste de rendu) dans `ctx`, neuf à chaque scénario. */

export interface Person {
  page: Page;
  email: string;
  ip: string;
}

export interface Ctx {
  /** Suffixe propre au scénario : les adresses e-mail ne se partagent pas entre scénarios
   *  (3 codes par adresse et par quart d'heure). */
  suffix: string;
  people: Map<string, Person>;
  /** Le jeton de l'invitation de chaque invité, par prénom. */
  invitations: Map<string, string>;
  project?: { id: string; level: string };
  /** Un compte d'éléments relevé avant un geste, pour vérifier que rien n'a été ajouté. */
  before?: number;
  worker?: { api: APIRequestContext; requestId: string; lease: string };
}

export const test = base.extend<{ ctx: Ctx }>({
  // Playwright exige un premier paramètre déstructuré, même vide.
  // eslint-disable-next-line no-empty-pattern
  ctx: async ({}, provide, testInfo) => {
    const suffix = createHash("sha256").update(testInfo.title).digest("hex").slice(0, 6);
    const ctx: Ctx = { suffix, people: new Map(), invitations: new Map() };
    await provide(ctx);
    await ctx.worker?.api.dispose();
    // Les navigateurs ouverts par person() (comptes.ts) : fermés en fin de scénario, sinon
    // leurs pages et leurs WebSockets s'accumulent d'un scénario à l'autre (un seul worker).
    // Fermer un contexte déjà fermé ne fait rien.
    for (const who of ctx.people.values()) await who.page.context().close();
  },
});

export const { Given, When, Then, BeforeScenario } = createBdd(test);

/** Les rôles tels qu'on les écrit dans un .feature, et leur nom dans l'API. */
defineParameterType({
  name: "rôle",
  regexp: /éditeur|éditrice|lecteur|lectrice/,
  transformer: (s: string) => (s.startsWith("é") ? "editor" : "viewer"),
});

// Must have 4 : les sauts conditionnels restent visibles, par tag, avec leur raison (listés
// comme « skipped » par Playwright, puis dans le rapport des tests sautés).
BeforeScenario({ tags: "@poids" }, async ({ page, $test, $testInfo }) => {
  $test.skip(
    !(await hasWeights(page, PHOTO_WEIGHTS)),
    "@poids : poids de l'analyse de la photo absents (en ligne, CI)",
  );
  // Avec les poids, l'analyse d'une photo prend jusqu'à 3 min (comme slice.spec).
  $testInfo.setTimeout(240_000);
});

BeforeScenario({ tags: "@gpu" }, async ({ $test }) => {
  $test.skip(process.env.OPENPLAN_GPU_TEST !== "1", "@gpu : créneau GPU du PO (OPENPLAN_GPU_TEST=1)");
});
