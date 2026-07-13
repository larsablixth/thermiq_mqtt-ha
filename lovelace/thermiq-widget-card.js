/* thermiq-widget-card
 *
 * Renders the ThermIQ heat-pump SVG widget from a Jinja2 template file
 * served out of /config/www, using HA's render_template websocket
 * subscription. Updates are applied by DOM *morphing* (in-place diff of
 * attributes and text nodes) instead of innerHTML replacement, so CSS
 * animations keep running across re-renders — the pump clock can tick
 * every 30 s without restarting every arrow and impeller.
 *
 * Usage (as an entities-card row or a standalone card):
 *   type: custom:thermiq-widget-card
 *   template_url: /local/thermiq/heatpump_widget.j2   # default
 *   entity_prefix: thermiq_mqtt_vp1                   # default; set to
 *                          # thermiq_mqtt_<id> if your entry id isn't vp1
 *
 * Editing workflow: edit www/thermiq/heatpump_widget.j2 in the config
 * repo, push it to /config/www/thermiq/ on the HA host, reload the page.
 * No lovelace-storage surgery needed.
 */

const VERSION = "1.1.0";
const DEFAULT_URL = "/local/thermiq/heatpump_widget.j2";
const DEFAULT_PREFIX = "thermiq_mqtt_vp1"; // integration domain + entry id

/* ---- DOM morphing ------------------------------------------------- */

// Elements are compatible when node type + tag match and their ids (if
// any) agree; incompatible nodes are replaced, compatible ones morphed.
function compatible(a, b) {
  if (a.nodeType !== b.nodeType) return false;
  if (a.nodeType !== Node.ELEMENT_NODE) return true;
  if (a.nodeName !== b.nodeName) return false;
  return (a.id || "") === (b.id || "");
}

function morphElement(live, next) {
  // Sync attributes; skip identical values so style recalc / animation
  // state is only touched when something actually changed.
  for (const attr of Array.from(next.attributes)) {
    if (live.getAttribute(attr.name) !== attr.value) {
      live.setAttribute(attr.name, attr.value);
    }
  }
  for (const attr of Array.from(live.attributes)) {
    if (!next.hasAttribute(attr.name)) live.removeAttribute(attr.name);
  }
  // Rewriting <style> text restarts the animations it defines — only
  // touch it when the CSS itself changed.
  if (live.nodeName === "STYLE") {
    if (live.textContent !== next.textContent) {
      live.textContent = next.textContent;
    }
    return;
  }
  morphChildren(live, next);
}

function morphChildren(live, next) {
  // Map id'd live children for keyed matching: conditional flow paths
  // appearing/disappearing must not knock later siblings out of place.
  const byId = new Map();
  for (const c of live.children) {
    if (c.id) byId.set(c.id, c);
  }
  const kept = new Set();
  let cursor = live.firstChild;

  for (const n of Array.from(next.childNodes)) {
    let match = null;
    if (n.nodeType === Node.ELEMENT_NODE && n.id) {
      const cand = byId.get(n.id);
      if (cand && !kept.has(cand) && cand.nodeName === n.nodeName) match = cand;
    } else {
      // Positional match: advance past already-kept/moved nodes.
      let probe = cursor;
      while (probe && (kept.has(probe) || !compatible(probe, n))) {
        // Only look one step past incompatible whitespace-ish noise;
        // otherwise treat as structural change and insert fresh.
        if (kept.has(probe)) {
          probe = probe.nextSibling;
          continue;
        }
        probe = null;
      }
      match = probe;
    }

    if (match) {
      if (match !== cursor) {
        live.insertBefore(match, cursor); // move into position
      } else {
        cursor = cursor.nextSibling;
      }
      kept.add(match);
      if (n.nodeType === Node.ELEMENT_NODE) {
        morphElement(match, n);
      } else if (match.nodeValue !== n.nodeValue) {
        match.nodeValue = n.nodeValue;
      }
    } else {
      // New structure (a flow that just started): adopt the fresh node.
      const adopted = n; // moving it out of the parsed fragment is fine
      live.insertBefore(adopted, cursor);
      kept.add(adopted);
    }
  }

  // Drop live nodes that no longer exist in the new render.
  for (const c of Array.from(live.childNodes)) {
    if (!kept.has(c)) live.removeChild(c);
  }
}

/* ---- The card ------------------------------------------------------ */

class ThermiqWidgetCard extends HTMLElement {
  setConfig(config) {
    this._config = { template_url: DEFAULT_URL, ...config };
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._started) {
      this._started = true;
      this._init();
    }
  }

  getCardSize() {
    return 5;
  }

  async _init() {
    this.style.display = "block";
    this._root = document.createElement("div");
    this.appendChild(this._root);
    try {
      const resp = await fetch(this._config.template_url, { cache: "no-store" });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      this._template = await resp.text();
      const prefix = this._config.entity_prefix || DEFAULT_PREFIX;
      if (prefix !== DEFAULT_PREFIX) {
        this._template = this._template.split(DEFAULT_PREFIX).join(prefix);
      }
    } catch (e) {
      this._root.innerHTML =
        `<div style="color:var(--error-color,red);padding:8px;">` +
        `thermiq-widget-card: cannot load ${this._config.template_url}: ${e.message}</div>`;
      return;
    }
    if (this.isConnected) this._subscribe();
  }

  _subscribe() {
    if (this._unsubPromise || !this._template || !this._hass) return;
    this._unsubPromise = this._hass.connection.subscribeMessage(
      (msg) => this._onRender(msg),
      {
        type: "render_template",
        template: this._template,
        report_errors: true,
      }
    );
    this._unsubPromise.catch((e) => {
      this._unsubPromise = null;
      this._root.innerHTML =
        `<div style="color:var(--error-color,red);padding:8px;">` +
        `thermiq-widget-card: template subscription failed: ${e.message || e.code || e}</div>`;
    });
  }

  _onRender(msg) {
    if (msg.error) {
      // Transient render errors (entity briefly unavailable): keep the
      // last good frame on screen, log for debugging.
      console.warn("thermiq-widget-card: template error:", msg.error);
      return;
    }
    if (typeof msg.result !== "string") return;
    const tpl = document.createElement("template");
    tpl.innerHTML = msg.result;
    morphChildren(this._root, tpl.content);
  }

  connectedCallback() {
    if (this._started && this._template) this._subscribe();
  }

  disconnectedCallback() {
    const p = this._unsubPromise;
    this._unsubPromise = null;
    if (p) p.then((unsub) => unsub()).catch(() => {});
  }
}

customElements.define("thermiq-widget-card", ThermiqWidgetCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "thermiq-widget-card",
  name: "ThermIQ Widget Card",
  description:
    "Animated heat-pump schematic rendered from a Jinja2 template file with flicker-free DOM morphing.",
});

console.info(
  `%c THERMIQ-WIDGET-CARD %c v${VERSION} `,
  "color:white;background:#0288d1;font-weight:700;",
  "color:#0288d1;background:white;font-weight:700;"
);
