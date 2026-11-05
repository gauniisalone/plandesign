// KAN-138 : la connexion de présence n'est plus refaite toutes les 7 à 8 min dans un onglet en
// arrière-plan (minuterie bridée), et un réseau mort est toujours vu. Lancé par
// `npm run test:unit`.
import assert from "node:assert/strict";
import { test } from "node:test";

import { FRESH, heard, tick, type Beat } from "../src/state/heartbeat.ts";

const PING_MS = 25_000;
const SILENCE_MS = 60_000;
const RTT_MS = 80;

/** Joue des tics aux instants donnés ; `answers` : le serveur répond-il au ping de ce tic ?
 *  Renvoie l'instant où la connexion est tenue pour morte, ou null. */
function play(ticks: number[], answers: (at: number) => boolean): number | null {
  let beat: Beat = FRESH;
  for (const at of ticks) {
    const next = tick(beat, at, SILENCE_MS);
    if (next.dead) return at;
    beat = next.beat; // le ping part
    if (answers(at)) beat = heard(); // son pong arrive (RTT_MS plus tard, avant le tic suivant)
  }
  return null;
}

const every = (step: number, until: number, from = step) =>
  Array.from({ length: Math.floor((until - from) / step) + 1 }, (_, i) => from + i * step);

test("onglet visible, serveur qui répond : jamais coupée", () => {
  assert.equal(play(every(PING_MS, 30 * 60_000), () => true), null);
});

test("onglet en arrière-plan : minuterie bridée à une fois par minute après 5 min, jamais coupée", () => {
  // Les tics normaux jusqu'à 5 min, puis un par minute (et même un par 70 s) : chaque ping a
  // sa réponse. Avant KAN-138, le premier tic bridé trouvait un dernier pong vieux de plus de
  // 60 s et refaisait la connexion.
  const visible = every(PING_MS, 5 * 60_000);
  const throttled = every(70_000, 40 * 60_000, 5 * 60_000 + 70_000);
  assert.equal(play([...visible, ...throttled], () => true), null);
});

test("réseau mort : vue après 60 s sans réponse au premier ping, au tic suivant", () => {
  const silentFrom = 2 * 60_000;
  const dead = play(every(PING_MS, 10 * 60_000), (at) => at < silentFrom);
  // Le premier ping sans réponse part à 125 s ; mort au premier tic après 185 s, soit 200 s.
  assert.equal(dead, 200_000);
});

test("réseau mort dans un onglet bridé : vue au premier tic après 60 s", () => {
  const ticks = every(60_000, 20 * 60_000, 6 * 60_000);
  const dead = play(ticks, (at) => at < 8 * 60_000);
  // Ping sans réponse à 480 s, tic suivant à 540 s (60 s pile : pas encore), puis 600 s.
  assert.equal(dead, 600_000);
});

test("un message quelconque du serveur compte comme une réponse", () => {
  const sent = tick(FRESH, 1_000, SILENCE_MS);
  assert.equal(sent.beat.pendingSince, 1_000);
  assert.equal(tick(heard(), 1_000 + SILENCE_MS + RTT_MS, SILENCE_MS).dead, false);
  // Deux pings sans réponse : l'attente part du premier, pas du second.
  const second = tick(sent.beat, 26_000, SILENCE_MS);
  assert.equal(second.beat.pendingSince, 1_000);
  assert.equal(tick(second.beat, 61_001, SILENCE_MS).dead, true);
});
