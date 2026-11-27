// Tests of the departure board card, run with `node --test tests/frontend/`.
// The card has no build step nor dependency: it is loaded as is in a bare
// context, with just enough of the browser for its last lines to run.

import { readFileSync } from "node:fs";
import { test } from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";

const SOURCE = readFileSync(
  new URL("../../custom_components/tam_montpellier/frontend/tam-board-card.js", import.meta.url),
  "utf8",
);

function loadCard() {
  const context = vm.createContext({
    console: { info() {}, warn() {}, error() {} },
    HTMLElement: class {},
    customElements: {
      get: () => undefined,
      define() {},
      whenDefined: () => new Promise(() => {}),
    },
    window: {},
    document: { createElement: () => ({}) },
    setTimeout: () => 0,
  });
  vm.runInContext(SOURCE, context);
  return context;
}

const card = loadCard();

function minutesState(attributes) {
  return {
    entity_id: "sensor.comedie_minutes",
    state: "3",
    last_updated: "2026-09-23T06:12:00+00:00",
    attributes: {
      line: "1",
      line_color: "#005CA9",
      line_text_color: "#FFFFFF",
      departures: [{ minutes: 3, destination: "Mosson", source: "realtime" }],
      ...attributes,
    },
  };
}

function hassWith(states, entities) {
  return { states, entities, devices: {}, themes: { darkMode: false }, language: "fr" };
}

test("the header comes from the attributes, whatever the device name", () => {
  const state = minutesState({
    friendly_name: "Maison (Tram 1) Minutes avant le prochain passage",
    stop_name: "Comédie",
    direction: "Mosson",
  });
  const hass = hassWith({ [state.entity_id]: state }, {});
  const html = card.render(state, hass.states, hass, {}, undefined, undefined);
  assert.match(html, /<div class="stop">Comédie<\/div>/);
  assert.match(html, /Direction Mosson/);
  assert.doesNotMatch(html, /Maison/);
});

test("without the attributes, the header is read from the name", () => {
  const state = minutesState({
    friendly_name: "Comédie → Mosson (Tram 1) Minutes avant le prochain passage",
  });
  const hass = hassWith({ [state.entity_id]: state }, {});
  const html = card.render(state, hass.states, hass, {}, undefined, undefined);
  assert.match(html, /<div class="stop">Comédie<\/div>/);
  assert.match(html, /Direction Mosson/);
});

test("entities are found by their translation key", () => {
  const states = {
    "sensor.a": { entity_id: "sensor.a", state: "3", attributes: {} },
    "sensor.b": { entity_id: "sensor.b", state: "Aucune perturbation", attributes: {} },
    "sensor.c": { entity_id: "sensor.c", state: "4", attributes: { line: "1" } },
  };
  const entities = {
    "sensor.a": { entity_id: "sensor.a", platform: "tam_montpellier", device_id: "d1", translation_key: "minutes_to_next_departure" },
    "sensor.b": { entity_id: "sensor.b", platform: "tam_montpellier", device_id: "d1", translation_key: "alert_message" },
    "sensor.c": { entity_id: "sensor.c", platform: "tam_montpellier", device_id: "l1", translation_key: "vehicles_in_service" },
  };
  const found = card.stopEntities(hassWith(states, entities), "d1");
  assert.equal(found.minutes, "sensor.a");
  assert.equal(found.alert, "sensor.b");
  assert.deepEqual({ ...found.fleet }, { 1: "sensor.c" });
});

test("without translation keys, entities are found by their attributes", () => {
  const states = {
    "sensor.a": minutesState({}),
    "sensor.b": { entity_id: "sensor.b", state: "x", attributes: { alert_count: 0 } },
  };
  states["sensor.a"].entity_id = "sensor.a";
  const entities = {
    "sensor.a": { entity_id: "sensor.a", platform: "tam_montpellier", device_id: "d1" },
    "sensor.b": { entity_id: "sensor.b", platform: "tam_montpellier", device_id: "d1" },
  };
  const found = card.stopEntities(hassWith(states, entities), "d1");
  assert.equal(found.minutes, "sensor.a");
  assert.equal(found.alert, "sensor.b");
});
