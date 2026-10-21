import { expect, test } from "@playwright/test";

/** Correctif de KAN-79 : sous la CSP que l'API envoie en production, onnxruntime-web doit
 *  vraiment s'initialiser (backend wasm, sans profondeur simulée). Sans 'wasm-unsafe-eval'
 *  dans script-src, WebAssembly.instantiate est refusé : « no available backend found …
 *  previous call to 'initWasm()' failed ». Le front de développement n'envoie pas de CSP : la
 *  page de contrôle ci-dessous, servie sur la même origine, porte celle de l'API (lue dans sa
 *  réponse) et charge la bibliothèque comme `depth/browserDepth.ts` (/ort/, mêmes fichiers que
 *  le front construit), sans isolation cross-origin (pas de COOP/COEP en production). */

/** Modèle ONNX minuscule : Identity sur un float32 [1] (opset 13), encodé à la main. */
const TINY_MODEL =
  "CAcSDnBsYW5kZXNpZ24tZTJlOjoKEwoBeBIBeRoBbiIISWRlbnRpdHkSAWdaDwoBeBIKCggIARIECgIIAWIPCgF5EgoKCAgBEgQKAggBQgQKABAN";

const CHECK = `
const done = (r) => { window.__ortResult = r; };
try {
  const ort = await import("/ort/ort.webgpu.min.mjs");
  ort.env.wasm.wasmPaths = "/ort/"; // comme depth/browserDepth.ts, rien d'autre
  const bytes = Uint8Array.from(atob("${TINY_MODEL}"), (c) => c.charCodeAt(0));
  const session = await ort.InferenceSession.create(bytes, { executionProviders: ["wasm"] });
  const out = await session.run({ x: new ort.Tensor("float32", new Float32Array([0.72]), [1]) });
  done("ok:" + out.y.data[0].toFixed(2));
} catch (e) {
  done("erreur: " + (e && e.message ? e.message : String(e)));
}
`;

test("correctif KAN-79 : onnxruntime-web s'initialise sous la CSP de l'app (wasm, sans isolation)", async ({
  page,
  request,
}) => {
  const health = await request.get("/api/health");
  const csp = health.headers()["content-security-policy"] ?? "";
  expect(csp, "la CSP de l'API").toContain("script-src 'self'");
  const errors: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text());
  });
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.route("**/__ort-csp-check.html", (route) =>
    route.fulfill({
      status: 200,
      headers: {
        "Content-Type": "text/html; charset=utf-8",
        "Content-Security-Policy": csp,
      },
      body: '<!doctype html><meta charset="utf-8"><title>ORT</title><script type="module" src="/__ort-csp-check.js"></script>',
    }),
  );
  await page.route("**/__ort-csp-check.js", (route) =>
    route.fulfill({
      status: 200,
      headers: { "Content-Type": "text/javascript; charset=utf-8" },
      body: CHECK,
    }),
  );
  await page.goto("/__ort-csp-check.html");
  await expect
    .poll(
      () =>
        page.evaluate(
          () => (window as { __ortResult?: string }).__ortResult ?? "",
        ),
      { timeout: 60_000 },
    )
    .not.toBe("");
  const result = await page.evaluate(
    () => (window as { __ortResult?: string }).__ortResult,
  );
  expect(result).toBe("ok:0.72");
  expect(await page.evaluate(() => self.crossOriginIsolated)).toBe(false);
  expect(errors, "aucune erreur de console").toEqual([]);
});
