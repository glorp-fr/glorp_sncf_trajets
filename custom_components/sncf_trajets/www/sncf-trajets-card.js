/* Glorp SNCF Trajets card — compact list */
const VERSION = "0.1.1";
const TZ = "Europe/Paris";

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const hm = (iso) => {
  try {
    const d = new Date(iso);
    if (isNaN(d)) return "--:--";
    return d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit", timeZone: TZ });
  } catch {
    return "--:--";
  }
};

const day = (iso) => {
  try {
    const d = new Date(iso);
    if (isNaN(d)) return "";
    return d.toLocaleDateString("fr-FR", { weekday: "long", timeZone: TZ });
  } catch {
    return "";
  }
};

function row(t, threshold) {
  const label = `${esc(t.mode ?? "")} ${esc(t.number ?? "")}`.trim();
  if (t.cancelled) {
    return `<div class="row cancelled"><span class="time">🚆 ${hm(t.base_departure)}</span><span class="num">${label}</span><span class="status bad">❌ Supprimé</span></div>`;
  }
  const delayMin = Number(t.delay_minutes) || 0;
  if (delayMin > 0) {
    const cls = delayMin >= threshold ? "warn" : "minor";
    return `<div class="row"><span class="time">🚆 <s>${hm(t.base_departure)}</s> ${hm(t.departure)}</span><span class="num">${label}</span><span class="status ${cls}">⚠️ +${delayMin}</span></div>`;
  }
  return `<div class="row"><span class="time">🚆 ${hm(t.base_departure)}</span><span class="num">${label}</span><span class="status ok">✅ À l'heure</span></div>`;
}

function renderContent(stateObj, config) {
  if (!stateObj) return `<div class="empty">Entité introuvable</div>`;
  const a = stateObj.attributes || {};
  const from = a.from_name || a.friendly_name || "SNCF";
  const to = a.to_name || "";
  const title = config.title || (to ? `${from} → ${to}` : from);
  const sub = a.is_future_window
    ? `<div class="sub">${esc(day(a.window_start))} ${hm(a.window_start)}–${hm(a.window_end)}</div>`
    : "";
  const trainsArr = Array.isArray(a.trains) ? a.trains : [];
  const rows = trainsArr.length
    ? trainsArr.map((t) => row(t, a.threshold ?? 5)).join("")
    : `<div class="empty">${stateObj.state === "unavailable" ? "Données indisponibles" : "Aucun train direct dans la plage"}</div>`;
  const disruptions = Array.isArray(a.disruptions) ? a.disruptions : [];
  const disr = disruptions.length
    ? `<div class="disruption">${disruptions.map((d) => `ℹ️ ${esc(d)}`).join("<br>")}</div>`
    : "";
  const upd = a.last_update ? `MAJ ${hm(a.last_update)}` : "";
  const stale = a.stale ? ` · <span class="bad">données non à jour</span>` : "";
  return `<div class="header">${esc(title)}</div>${sub}<div class="rows">${rows}</div>${disr}<div class="footer">${upd}${stale}</div>`;
}

const STYLE = `
  ha-card { padding: 12px 16px; }
  .header { font-size: 1.1em; font-weight: 500; }
  .sub { color: var(--secondary-text-color); font-size: .9em; text-transform: capitalize; }
  .rows { margin-top: 8px; }
  .row { display: grid; grid-template-columns: auto 1fr auto; gap: 8px; padding: 4px 0; align-items: center; }
  .row.cancelled { opacity: .55; }
  .num { color: var(--secondary-text-color); }
  .ok { color: var(--success-color, #2e7d32); }
  .warn { color: var(--warning-color, #ef6c00); font-weight: 600; }
  .minor { color: var(--secondary-text-color); }
  .bad { color: var(--error-color, #c62828); }
  .empty { color: var(--secondary-text-color); padding: 8px 0; }
  .disruption { margin-top: 8px; padding-top: 8px; border-top: 1px solid var(--divider-color); font-size: .9em; }
  .footer { margin-top: 8px; color: var(--secondary-text-color); font-size: .8em; }
`;

class SncfTrajetsCard extends HTMLElement {
  setConfig(config) {
    if (!config.entity) throw new Error("entity requis");
    this._config = config;
    this._last = undefined;
    if (this._hass) this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _render() {
    if (!this._config || !this._hass) return;
    const stateObj = this._hass.states[this._config.entity];
    if (stateObj === this._last) return;
    this._last = stateObj;
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
      this.shadowRoot.innerHTML = `<style>${STYLE}</style><ha-card><div id="c"></div></ha-card>`;
    }
    this.shadowRoot.getElementById("c").innerHTML = renderContent(stateObj, this._config);
  }

  getCardSize() {
    return 2 + (this._last?.attributes?.trains?.length || 1);
  }

  static getConfigElement() {
    return document.createElement("sncf-trajets-card-editor");
  }

  static getStubConfig(hass) {
    const entity = Object.keys(hass.states).find(
      (e) => e.startsWith("sensor.") && hass.states[e].attributes.trains !== undefined && hass.states[e].attributes.from_name
    );
    return { entity: entity || "" };
  }
}

class SncfTrajetsCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = config;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _render() {
    if (!this._hass || !this._config) return;
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.schema = [
        { name: "entity", required: true, selector: { entity: { integration: "sncf_trajets", domain: "sensor", device_class: "timestamp" } } },
        { name: "title", selector: { text: {} } },
      ];
      this._form.computeLabel = (s) => ({ entity: "Trajet (capteur Prochain train)", title: "Titre (optionnel)" })[s.name];
      this._form.addEventListener("value-changed", (ev) => {
        this.dispatchEvent(new CustomEvent("config-changed", { detail: { config: { ...this._config, ...ev.detail.value } }, bubbles: true, composed: true }));
      });
      this.appendChild(this._form);
    }
    this._form.hass = this._hass;
    this._form.data = this._config;
  }
}

if (!customElements.get("sncf-trajets-card")) {
  customElements.define("sncf-trajets-card", SncfTrajetsCard);
  customElements.define("sncf-trajets-card-editor", SncfTrajetsCardEditor);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "sncf-trajets-card",
    name: "Glorp SNCF Trajets",
    description: "Prochains trains et perturbations d'un trajet SNCF",
    preview: true,
  });
}
globalThis.SncfTrajetsCard = { renderContent, VERSION };
