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
 *   data-more   URL for a trailing "see all events" link.
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

  function buildCard(event) {
    var card = el("li", "fhdsa-events__card");
    card.style.setProperty("--card-color", event.color || "");
    card.setAttribute("data-committee", event.committee || "");
    card.setAttribute("data-source", event.source || "");

    card.appendChild(el("span", "fhdsa-events__badge", event.committee || "General"));
    if (event.source === "national") {
      card.appendChild(el("span", "fhdsa-events__flag", "National / Regional"));
    }

    card.appendChild(el("p", "fhdsa-events__date", formatDate(event)));

    var heading = el("h3", "fhdsa-events__title");
    if (event.url) {
      var titleLink = el("a", null, event.title);
      titleLink.href = event.url;
      titleLink.rel = "noopener";
      heading.appendChild(titleLink);
    } else {
      heading.textContent = event.title;
    }
    card.appendChild(heading);

    if (event.location) {
      var meta = el("div", "fhdsa-events__meta");
      meta.appendChild(el("span", null, event.location));
      card.appendChild(meta);
    }

    // Bare URLs are dropped from the excerpt: the RSVP link is already rendered
    // as its own control, and a raw actionnetwork.org URL eats the whole card.
    var excerpt = (event.description || "")
      .replace(/<[^>]*>/g, " ")
      .replace(/https?:\/\/\S+/gi, "")
      .replace(/\s+/g, " ")
      .trim();
    if (excerpt) {
      if (excerpt.length > 160) excerpt = excerpt.slice(0, 159).trim() + "…";
      card.appendChild(el("p", "fhdsa-events__excerpt", excerpt));
    }

    if (event.url) {
      var link = el("a", "fhdsa-events__link", "Details & RSVP →");
      link.href = event.url;
      link.rel = "noopener";
      card.appendChild(link);
    }

    return card;
  }

  function renderGrid(events) {
    var grid = el("ul", "fhdsa-events__grid");
    events.forEach(function (event) { grid.appendChild(buildCard(event)); });
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

    var more = root.getAttribute("data-more");
    if (more) {
      var wrap = el("p", "fhdsa-events__more");
      var link = el("a", "fhdsa-events__link", "See all events →");
      link.href = more;
      wrap.appendChild(link);
      root.appendChild(wrap);
    }
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
