import { test } from "node:test";
import assert from "node:assert/strict";

const definedElements = {};
globalThis.HTMLElement = class {
  attachShadow(opts) {
    const root = {
      innerHTML: "",
      getElementById: (id) => ({ innerHTML: "" }),
    };
    this.shadowRoot = root;
    return root;
  }
};
globalThis.customElements = {
  define(name, cls) {
    definedElements[name] = cls;
  },
  get(name) {
    return definedElements[name];
  },
};
globalThis.window = globalThis;
await import("../../custom_components/sncf_trajets/www/sncf-trajets-card.js");
const { renderContent } = globalThis.SncfTrajetsCard;

const t = (number, dep, delay = 0, cancelled = false) => ({
  number, mode: "TER",
  base_departure: `2026-10-05T${dep}:00+02:00`,
  departure: new Date(Date.parse(`2026-10-05T${dep}:00+02:00`) + delay * 60000).toISOString(),
  delay_minutes: delay, cancelled,
});
const state = (attrs) => ({
  state: "2026-10-05T05:42:00+00:00",
  attributes: {
    from_name: "La Verpillière", to_name: "Lyon Part-Dieu", trains: [], disruptions: [],
    is_future_window: false, stale: false, threshold: 5,
    window_start: "2026-10-05T07:30:00+02:00", window_end: "2026-10-05T09:30:00+02:00",
    last_update: "2026-10-05T07:31:00+02:00", ...attrs,
  },
});

test("rows reflect status", () => {
  const html = renderContent(state({ trains: [t("17714", "07:42"), t("17716", "08:12", 7), t("17718", "08:42", 0, true)], disruptions: ["Mouvement social"] }), {});
  assert.match(html, /La Verpillière → Lyon Part-Dieu/);
  assert.match(html, /17714[\s\S]*À l'heure/);
  assert.match(html, /<s>08:12<\/s>[\s\S]*\+7/);
  assert.match(html, /class="row cancelled"[\s\S]*17718[\s\S]*Supprimé/);
  assert.match(html, /Mouvement social/);
});

test("empty and stale and future window", () => {
  const html = renderContent(state({ stale: true, is_future_window: true }), { title: "Boulot" });
  assert.match(html, /Boulot/);
  assert.match(html, /Aucun train direct dans la plage/);
  assert.match(html, /données non à jour/);
  assert.match(html, /07:30–09:30/);
});

test("escapes html", () => {
  const html = renderContent(state({ disruptions: ["<img src=x>"] }), {});
  assert.doesNotMatch(html, /<img/);
});

test("unavailable state with empty attributes", () => {
  const html = renderContent({ state: "unavailable", attributes: {} }, {});
  assert.match(html, /Données indisponibles/);
  assert.doesNotMatch(html, /Aucun train direct/);
});

test("train with invalid departure does not throw", () => {
  const html = renderContent(state({ trains: [{ number: "17714", mode: "TER", base_departure: "invalid", departure: "also invalid", delay_minutes: 0, cancelled: false }] }), {});
  assert.match(html, /17714/);
});

test("hostile title mode number is escaped", () => {
  const html = renderContent(state({ trains: [{ number: "<img>", mode: "<svg>", base_departure: "2026-10-05T07:42:00+02:00", departure: "2026-10-05T07:42:00Z", delay_minutes: 0, cancelled: false }] }), { title: "<script>alert(1)</script>" });
  assert.doesNotMatch(html, /<img/);
  assert.doesNotMatch(html, /<svg/);
  assert.doesNotMatch(html, /<script/);
});

test("on-time row contains all expected parts", () => {
  const html = renderContent(state({ trains: [t("17714", "07:42"), t("17716", "08:12", 7)] }), {});
  const rows = html.split('class="row"');
  assert(rows.length >= 2, "should have at least 2 row divs");
  const firstRow = rows[1];
  assert.match(firstRow, /17714/);
  assert.match(firstRow, /À l'heure/);
});

test("card re-renders on config change", () => {
  // Get the real SncfTrajetsCard class
  const SncfTrajetsCard = definedElements["sncf-trajets-card"];
  assert(SncfTrajetsCard, "SncfTrajetsCard class should be registered");

  // Set up a fake shadow root that tracks innerHTML writes
  let contentHolder = { innerHTML: "" };
  const fakeRoot = {
    innerHTML: "",
    getElementById: (id) => (id === "c" ? contentHolder : null),
  };

  // Create a real instance and override attachShadow to use our fake root
  const el = new SncfTrajetsCard();
  el.attachShadow = () => {
    el.shadowRoot = fakeRoot;
    return fakeRoot;
  };

  // Test state with a train
  const testState = state({ trains: [t("100", "08:00")] });

  // Step 1: setConfig with title "A"
  el.setConfig({ entity: "sensor.test", title: "A" });

  // Step 2: set hass (triggers _render via setter)
  el.hass = { states: { "sensor.test": testState } };

  // Step 3: check that holder contains "A"
  assert.match(contentHolder.innerHTML, /A/, "first render should contain title A");
  const firstHTML = contentHolder.innerHTML;

  // Step 4: setConfig with title "B" (should trigger re-render automatically)
  el.setConfig({ entity: "sensor.test", title: "B" });

  // Step 5: check that holder contains "B" (re-render should have happened via _render call in setConfig)
  assert.match(contentHolder.innerHTML, /B/, "second render should contain title B");
  const secondHTML = contentHolder.innerHTML;

  assert.notStrictEqual(firstHTML, secondHTML, "should have different HTML after title change");
});
