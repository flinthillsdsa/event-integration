/* Flint Hills DSA event cards.
 *
 * Renders one or more `.fhdsa-events` containers from the static events.json
 * that the GitHub Action regenerates every few hours. No API key is involved:
 * the browser only ever fetches a committed JSON file.
 *
 * Markup to paste into a WordPress Custom HTML block:
 *
 *   <div class="fhdsa-events" data-mode="compact" data-limit="6"></div>
 *
 * Attributes (all optional):
 *   data-mode   "compact" (flat grid, no headings) | "full" (month sections
 *               plus committee filter chips). Default "full".
 *   data-limit  max events to render. Default: all.
 *   data-source "all" | "chapter" | "national". Default "all".
 *   data-src    override the events.json URL.
 *
 * Cards never navigate away. Clicking one opens a modal holding the full
 * details, including the Action Network RSVP link when the event has one.
 */
(function () {
  "use strict";

  var DEFAULT_SRC = "https://flinthillsdsa.github.io/event-integration/events.json";
  var SELECTOR = ".fhdsa-events";

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function parseDate(value) {
    var parsed = new Date(value);
    return isNaN(parsed.getTime()) ? null : parsed;
  }

  function formatDate(event) {
    var start = parseDate(event.start);
    if (!start) return "";
    var datePart = start.toLocaleDateString(undefined, {
      weekday: "short", month: "long", day: "numeric"
    });
    if (event.allDay) return datePart + " · all day";
    var timePart = start.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
    return datePart + " · " + timePart;
  }

  function monthKey(event) {
    var start = parseDate(event.start);
    if (!start) return "";
    return start.toLocaleDateString(undefined, { month: "long", year: "numeric" });
  }

  function plainText(html) {
    return (html || "").replace(/<br\s*\/?>/gi, "\n").replace(/<[^>]*>/g, " ");
  }

  function formatFullDate(event) {
    var start = parseDate(event.start);
    var end = parseDate(event.end);
    if (!start) return "";
    var datePart = start.toLocaleDateString(undefined, {
      weekday: "long", month: "long", day: "numeric", year: "numeric"
    });
    if (event.allDay) return datePart + " · all day";
    var opts = { hour: "numeric", minute: "2-digit" };
    var text = datePart + " · " + start.toLocaleTimeString(undefined, opts);
    if (end && end > start) text += " – " + end.toLocaleTimeString(undefined, opts);
    return text;
  }

  // The card is a button, not a link: it opens the modal rather than navigating.
  // Compact mode shows only the colour bar, badge, date and title; everything
  // else lives in the modal.
  function buildCard(event, onOpen) {
    var item = el("li", "fhdsa-events__item");
    var card = el("button", "fhdsa-events__card");
    card.type = "button";
    card.style.setProperty("--card-color", event.color || "");
    card.setAttribute("data-committee", event.committee || "");
    card.setAttribute("data-source", event.source || "");
    card.setAttribute("aria-haspopup", "dialog");

    card.appendChild(el("span", "fhdsa-events__badge", event.committee || "General"));
    if (event.source === "national") {
      card.appendChild(el("span", "fhdsa-events__flag", "National / Regional"));
    }
    card.appendChild(el("p", "fhdsa-events__date", formatDate(event)));
    card.appendChild(el("h3", "fhdsa-events__title", event.title));

    card.addEventListener("click", function () { onOpen(event, card); });

    item.appendChild(card);
    return item;
  }

  // --- modal ---------------------------------------------------------------
  // One dialog is shared by every container on the page; it is created lazily
  // and repopulated on each open.

  var dialog = null;
  var lastFocused = null;

  function closeDialog() {
    if (!dialog) return;
    if (dialog.open && typeof dialog.close === "function") {
      dialog.close();                      // fires the "close" event
    } else {
      dialog.removeAttribute("open");
      if (lastFocused && lastFocused.focus) lastFocused.focus();
    }
  }

  function ensureDialog() {
    if (dialog) return dialog;

    dialog = el("dialog", "fhdsa-events__dialog");
    dialog.setAttribute("aria-labelledby", "fhdsa-events-dialog-title");

    var close = el("button", "fhdsa-events__close");
    close.type = "button";
    close.setAttribute("aria-label", "Close");
    close.innerHTML = "&times;";
    close.addEventListener("click", closeDialog);

    dialog.appendChild(close);
    dialog.appendChild(el("div", "fhdsa-events__dialog-body"));

    // Clicking the backdrop (the dialog element itself, outside its content)
    // closes it, matching what people expect from a modal.
    dialog.addEventListener("click", function (evt) {
      if (evt.target === dialog) closeDialog();
    });
    dialog.addEventListener("close", function () {
      if (lastFocused && lastFocused.focus) lastFocused.focus();
    });
    // showModal() gives Escape-to-close for free. The setAttribute("open")
    // fallback below does not, so handle the key explicitly for that path.
    dialog.addEventListener("keydown", function (evt) {
      if (evt.key === "Escape" && typeof dialog.showModal !== "function") {
        evt.preventDefault();
        closeDialog();
      }
    });

    document.body.appendChild(dialog);
    return dialog;
  }

  function openModal(event, trigger) {
    var node = ensureDialog();
    var body = node.querySelector(".fhdsa-events__dialog-body");
    body.textContent = "";
    node.style.setProperty("--card-color", event.color || "");

    body.appendChild(el("span", "fhdsa-events__badge", event.committee || "General"));
    if (event.source === "national") {
      body.appendChild(el("span", "fhdsa-events__flag", "National / Regional"));
    }

    var heading = el("h2", "fhdsa-events__dialog-title", event.title);
    heading.id = "fhdsa-events-dialog-title";
    body.appendChild(heading);

    body.appendChild(el("p", "fhdsa-events__dialog-date", formatFullDate(event)));

    if (event.location) {
      body.appendChild(el("p", "fhdsa-events__dialog-location", event.location));
    }

    // The description is plain text from the calendar. Render its paragraphs,
    // minus any bare URL, since the RSVP link gets its own button below.
    var text = plainText(event.description).replace(/https?:\/\/\S+/gi, "");
    text.split(/\n{1,}/).forEach(function (para) {
      var trimmed = para.replace(/[ \t]+/g, " ").trim();
      if (!trimmed) return;
      // Removing the URL can strip a line down to its label ("RSVP:",
      // "Sign up -"), which reads as a broken sentence next to the button.
      if (trimmed.length <= 24 && /[:\-–—]$/.test(trimmed)) return;
      body.appendChild(el("p", "fhdsa-events__dialog-text", trimmed));
    });

    if (event.url) {
      var rsvp = el("a", "fhdsa-events__rsvp", "RSVP on Action Network →");
      rsvp.href = event.url;
      rsvp.target = "_blank";
      rsvp.rel = "noopener noreferrer";
      body.appendChild(rsvp);
    }

    lastFocused = trigger || document.activeElement;
    if (typeof node.showModal === "function") {
      node.showModal();
    } else {
      node.setAttribute("open", "");           // very old browsers: inline fallback
    }
    node.querySelector(".fhdsa-events__close").focus();
  }

  function renderGrid(events) {
    var grid = el("ul", "fhdsa-events__grid");
    events.forEach(function (event) { grid.appendChild(buildCard(event, openModal)); });
    return grid;
  }

  function renderSections(root, events, mode) {
    var body = root.querySelector(".fhdsa-events__body");
    body.textContent = "";

    if (!events.length) {
      body.appendChild(el("p", "fhdsa-events__status", "No upcoming events match that filter."));
      return;
    }

    if (mode !== "full") {
      body.appendChild(renderGrid(events));
      return;
    }

    var order = [];
    var groups = {};
    events.forEach(function (event) {
      var key = monthKey(event);
      if (!groups[key]) { groups[key] = []; order.push(key); }
      groups[key].push(event);
    });

    order.forEach(function (key) {
      var section = el("section", "fhdsa-events__section");
      section.appendChild(el("h3", "fhdsa-events__month", key));
      section.appendChild(renderGrid(groups[key]));
      body.appendChild(section);
    });
  }

  function renderFilters(root, data, state, rerender) {
    var committees = (data.committees || []).slice();
    if (committees.length < 2) return;

    var list = el("div", "fhdsa-events__filters");
    list.setAttribute("role", "group");
    list.setAttribute("aria-label", "Filter events by committee");

    var options = [{ name: "All events", color: null }].concat(committees);
    options.forEach(function (option) {
      var isAll = option.color === null;
      var chip = el("button", "fhdsa-events__chip");
      chip.type = "button";
      chip.setAttribute("aria-pressed", String(isAll));
      if (!isAll) {
        var dot = el("span", "fhdsa-events__chip-dot");
        dot.style.setProperty("--chip-color", option.color);
        chip.appendChild(dot);
      }
      chip.appendChild(el("span", null, option.name));

      chip.addEventListener("click", function () {
        state.committee = isAll ? null : option.name;
        Array.prototype.forEach.call(list.children, function (other) {
          other.setAttribute("aria-pressed", String(other === chip));
        });
        rerender();
      });

      list.appendChild(chip);
    });

    root.insertBefore(list, root.querySelector(".fhdsa-events__body"));
  }

  function mount(root, data) {
    var mode = root.getAttribute("data-mode") === "compact" ? "compact" : "full";
    var limit = parseInt(root.getAttribute("data-limit"), 10);
    var sourceFilter = root.getAttribute("data-source") || "all";
    var state = { committee: null };

    var all = (data.events || []).filter(function (event) {
      return sourceFilter === "all" || event.source === sourceFilter;
    });

    function visible() {
      var events = all;
      if (state.committee) {
        events = events.filter(function (event) { return event.committee === state.committee; });
      }
      return isNaN(limit) ? events : events.slice(0, limit);
    }

    function rerender() { renderSections(root, visible(), mode); }

    root.textContent = "";
    root.appendChild(el("div", "fhdsa-events__body"));
    if (mode === "full") renderFilters(root, data, state, rerender);
    rerender();
  }

  function init() {
    var roots = Array.prototype.slice.call(document.querySelectorAll(SELECTOR));
    if (!roots.length) return;

    roots.forEach(function (root) {
      root.appendChild(el("p", "fhdsa-events__status", "Loading events…"));
    });

    // Group by URL so several blocks on one page share a single request.
    var byUrl = {};
    roots.forEach(function (root) {
      var url = root.getAttribute("data-src") || DEFAULT_SRC;
      (byUrl[url] = byUrl[url] || []).push(root);
    });

    Object.keys(byUrl).forEach(function (url) {
      fetch(url, { credentials: "omit" })
        .then(function (response) {
          if (!response.ok) throw new Error("HTTP " + response.status);
          return response.json();
        })
        .then(function (data) {
          byUrl[url].forEach(function (root) { mount(root, data); });
        })
        .catch(function (error) {
          if (window.console) console.error("fhdsa-events:", error);
          byUrl[url].forEach(function (root) {
            root.textContent = "";
            root.appendChild(el("p", "fhdsa-events__status",
              "Events could not be loaded right now."));
          });
        });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
