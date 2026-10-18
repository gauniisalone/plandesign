import { expect, test } from "@playwright/test";
import path from "node:path";
import { createEmptyProject, goSpace } from "./helpers";

/** T7a : l'accent se compare en un clic dans Réglages, à côté du thème : Indigo (défaut,
 *  tant qu'augus n'a pas tranché), Violet, Provence ; retenu par le navigateur ; clair et
 *  sombre. Captures de chaque accent sur l'espace de travail, pour la comparaison côte à
 *  côte. */

const SHOTS = path.resolve("e2e/screenshots");
const PRIMARY: Record<string, Record<string, string>> = {
  light: {
    Indigo: "",
    Violet: "rgb(161, 0, 255)",
    Provence: "rgb(176, 80, 44)",
  },
  dark: {
    Indigo: "",
    Violet: "rgb(176, 74, 255)",
    Provence: "rgb(196, 98, 60)",
  },
};

test("T7a : accent Indigo / Violet / Provence dans Réglages, clair et sombre", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await createEmptyProject(page, "E2E accent");
  await goSpace(page, "board");
  const html = page.locator("html");
  const generate = page.getByRole("button", { name: "Générer maintenant" });
  const background = () =>
    generate.evaluate((el) => getComputedStyle(el).backgroundColor);
  await expect(html).not.toHaveAttribute("data-accent", /.+/);

  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    const seen: string[] = [];
    for (const name of ["Indigo", "Violet", "Provence"]) {
      await page.keyboard.press(",");
      const group = page.getByRole("group", { name: "Accent" });
      await group.getByRole("button", { name }).click();
      await expect(group.getByRole("button", { name })).toHaveAttribute(
        "aria-pressed",
        "true",
      );
      await page.keyboard.press(",");
      if (name === "Indigo") {
        await expect(html).not.toHaveAttribute("data-accent", /.+/);
        // L'indigo d'avant T7a, inchangé (sa valeur exacte, en oklch, est celle du thème).
        await expect.poll(background).toMatch(/oklch\(0\.(511|62) /);
      } else {
        await expect(html).toHaveAttribute("data-accent", name.toLowerCase());
        await expect.poll(background).toBe(PRIMARY[scheme]![name]);
      }
      seen.push(await background());
      await page.screenshot({
        path: path.join(SHOTS, `t7a_${name.toLowerCase()}_${scheme}.png`),
      });
    }
    expect(new Set(seen).size, `${scheme} : trois accents distincts`).toBe(3);
  }

  // Retenu par ce navigateur.
  await page.keyboard.press(",");
  await page
    .getByRole("group", { name: "Accent" })
    .getByRole("button", { name: "Provence" })
    .click();
  await page.reload();
  await expect(html).toHaveAttribute("data-accent", "provence");
  await expect(generate).toBeVisible();
  await page.keyboard.press(",");
  await page
    .getByRole("group", { name: "Accent" })
    .getByRole("button", { name: "Indigo" })
    .click();
  await page.reload();
  await expect(html).not.toHaveAttribute("data-accent", /.+/);
});
