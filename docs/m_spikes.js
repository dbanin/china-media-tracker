/* Unusual days (measure spikes, pipeline/spikes.py): days on which a country, or the worldwide wires,
   carried far more state origin placements than in its previous 28 complete days. Rendered into
   #findings-spikes under What we found: the most recent clean days first, then the ones to check, muted
   with their reasons. Each day opens the country on the map at that day. sparkMarks puts a small block
   on the map timeline for every unusual day in the current Distribution scope.

   Selecting a country and a day sends a "tracker:select" event on document with the detail
   {iso, day, measure: "a", window: "day", distribution: "both"}: the figures an unusual day is about
   are that country's state origin placements on that one day, across country outlets and wires alike,
   so the panel must show those and not whatever measure, window and distribution were last chosen.
   app.js handles it through its public hook (window.TrackerApp). Only on a page without that hook does
   it use the page's own controls: the State origin, That day and Both buttons (clicked when not already
   active), the timeline scrubber (#scrub, set and sent an input event, exactly as dragging it does) and
   the ranked list row of the country (clicked, as a reader would). Loaded in the browser after
   measures.js, and in Node or jsc with module.exports. */
(function (root, factory) {
  var M = root && root.TrackerMeasures;
  if (!M && typeof require === "function") M = require("./measures.js");
  var mod = factory(M);
  if (typeof module === "object" && module.exports) module.exports = mod;
}(typeof self !== "undefined" ? self : this, function (M) {
  "use strict";
  var ID = "spikes";
  var SHOW = 5, RECENT_DAYS = 30, OUTLET_CAP = 3, WINDOW_DAYS = 28;
  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  /* What each confounder that touches the count means, in a few words. rediscovery touches only the
     rate per discovered item, so it never makes a day one to check and is not listed as a reason. */
  var REASONS = {
    new_outlet: "a newly added outlet",
    backfill: "a backfill read",
    outage_recovery: "a collector catching up after a gap",
    relay_incomplete: "an incomplete day for the collector on the owner's machine",
    ruleset_change: "relabelling after a ruleset change is not finished",
    gate_change: "a relevance gate change",
    saturation: "saturated feeds on the days before",
    llm_ceiling: "candidates still waiting for the model",
    denominator_spike: "a surge in everything collected"
  };
  var lastDays = null;   /* the map timeline's days, from the last sparkMarks call */

  function shiftDay(day, n) {
    var t = Date.parse(day + "T00:00:00Z");
    if (isNaN(t)) return day;
    return new Date(t + n * 86400000).toISOString().slice(0, 10);
  }
  function shortDate(day) {
    var p = String(day || "").split("-");
    if (p.length !== 3) return String(day || "");
    return Number(p[2]) + " " + (MONTHS[Number(p[1]) - 1] || p[1]);
  }
  function countReasons(s) {
    return ((s && s.confounders) || []).filter(function (c) { return c && c.affects !== "rate" && REASONS[c.reason]; });
  }
  function reasonText(s) {
    var seen = {}, out = [];
    countReasons(s).forEach(function (c) { if (!seen[c.reason]) { seen[c.reason] = 1; out.push(REASONS[c.reason]); } });
    return out.join("; ");
  }
  function descriptorText(s) {
    var d = (s && s.descriptors) || [], out = [];
    if (d.indexOf("one_item") !== -1) out.push("mostly one item");
    if (d.indexOf("one_outlet") !== -1) out.push("mostly one outlet");
    return out.join(", ");
  }

  /* The unusual days within the last RECENT_DAYS complete days: clean ones and ones to check, newest
     first, each list cut to SHOW, with the number left out of each. */
  function recentLists(data) {
    var spikes = ((data && data.spikes) || []).slice();
    var last = (data && data.last_complete_day) || spikes.reduce(function (m, s) { return s.date > m ? s.date : m; }, "");
    var since = last ? shiftDay(last, 1 - RECENT_DAYS) : "";
    var recent = spikes.filter(function (s) { return s && s.date && s.date >= since && (!last || s.date <= last); });
    recent.sort(function (a, b) { return a.date < b.date ? 1 : a.date > b.date ? -1 : (b.count || 0) - (a.count || 0); });
    var clean = recent.filter(function (s) { return s.status === "clean"; });
    var check = recent.filter(function (s) { return s.status !== "clean"; });
    return {since: since, last: last, clean: clean.slice(0, SHOW), check: check.slice(0, SHOW),
            moreClean: Math.max(0, clean.length - SHOW), moreCheck: Math.max(0, check.length - SHOW)};
  }

  /* What the day carried, and what was tested against what was expected. Since version 2 the tested
     count is distinct underlying items with each outlet capped (s.tested), which the expected figure is
     in; an older file without it compared placements. */
  function whatText(s) {
    var exp = typeof s.expected === "number" ? (s.expected < 1 ? "under 1" : "about " + Math.round(s.expected)) : "n/a";
    var placed = s.count + " state origin placements" + (typeof s.uniq === "number" && s.uniq !== s.count ? " (" + s.uniq + " underlying items)" : "");
    if (typeof s.tested !== "number") return placed + " against " + exp + " expected";
    return placed + "; " + s.tested + " counted, no outlet adding more than " + OUTLET_CAP + ", against " + exp + " expected";
  }

  function itemHtml(s, api, muted) {
    var esc = M.esc, name = api && api.countryName ? api.countryName(s.country) : s.country;
    var what = whatText(s);
    var desc = descriptorText(s), why = reasonText(s);
    var title = countReasons(s).map(function (c) { return c.detail; }).join(" ");
    return '<li class="sp-item' + (muted ? ' sp-muted' : '') + '"><button type="button" class="sp-day" data-iso="' + esc(s.country) + '" data-day="' + esc(s.date) + '"' +
      (title ? ' title="' + esc(title) + '"' : '') + ' aria-label="' + esc(name + ", " + s.date + ": " + what + (why ? ". To check: " + why : "")) + '">' +
      '<span class="sp-date">' + esc(shortDate(s.date)) + '</span>' +
      '<span class="sp-what"><span class="sp-country">' + esc(name) + '</span> ' + esc(what) + (desc ? '<span class="sp-desc">, ' + esc(desc) + '</span>' : '') + '</span>' +
      (muted && why ? '<span class="sp-why">To check: ' + esc(why) + '</span>' : '') +
      '</button></li>';
  }

  /* The number of tests and the model's estimate of chance flags, with its caveat: the model assumes
     each country's days vary as they did before, and real days are lumpier, so it is a rough guide. */
  function chanceText(data) {
    var t = data && data.tests, e = data && data.expected_false_flags;
    if (!t) return "";
    var est = typeof e !== "number" ? "" : e < 0.1 ? "under 0.1" : "about " + (Math.round(e * 10) / 10);
    return t.toLocaleString("en-US") + " country days tested" + (data.first_tested ? " since " + shortDate(data.first_tested) : "") + "." +
      (est ? " The model puts the days it would flag by chance alone at " + est + ", but real days are lumpier than it assumes, so read that as a rough guide, not a promise." : "");
  }

  /* Why days are to check while relabelling is unfinished, said plainly: n is how many of the listed
     days to check carry that reason. */
  function relabelText(list) {
    var n = 0, last = "";
    (list || []).forEach(function (s) {
      ((s && s.confounders) || []).forEach(function (c) {
        if (c && c.reason === "ruleset_change") { n += 1; if (c.ruleset_date && c.ruleset_date > last) last = c.ruleset_date; }
      });
    });
    if (!n) return "";
    return (n === 1 ? "One of these days is" : n + " of these days are") + " to check because the labelling rules changed" +
      (last ? " on " + shortDate(last) : "") + " and the days before " + (n === 1 ? "it" : "them") + " have not all been labelled again under the new rules, so the day and the days it is compared with were judged differently. " +
      "Such a day clears once that relabelling is done; a change stops affecting new days " + WINDOW_DAYS + " days after it.";
  }

  function render(block, data, api) {
    var slot = api && api.slot;
    if (!slot) return;
    if (!M.hideIfOff(slot, block)) { slot.innerHTML = ""; return; }
    var esc = M.esc;
    /* The chip names the status beside the heading ("Unusual days", then Provisional), so the heading
       carries no suffix of its own; the aria label reads them together. */
    var head = '<div class="sp-head" role="heading" aria-level="3" aria-label="' + esc("Unusual days" + M.provisionalSuffix(block)) + '"><span class="sp-title">Unusual days</span> ' + M.statusChip(block) + '</div>';
    if (M.isWithheld(block) || !data || !Array.isArray(data.spikes)) {
      slot.innerHTML = '<div class="m-spikes">' + head + M.emptyState(block, data && Array.isArray(data.spikes) ? data : null, "unusual days") + '</div>';
      return;
    }
    var L = recentLists(data);
    var html = '<div class="m-spikes">' + head +
      '<p class="sp-sub">Days on which a country carried far more state origin items than in its previous 28 days, counting each item once and no more than ' + OUTLET_CAP + ' from any one outlet. Select one to open that day on the map, for state origin across country outlets and wires.</p>';
    if (!L.clean.length && !L.check.length) {
      html += '<p class="m-empty">No unusual days in the last ' + RECENT_DAYS + ' complete days' + (L.last ? ', to ' + esc(shortDate(L.last)) : '') + '.</p>';
    } else {
      if (L.clean.length) html += '<ul class="sp-list">' + L.clean.map(function (s) { return itemHtml(s, api, false); }).join("") + '</ul>' +
        (L.moreClean ? '<p class="sp-more">And ' + L.moreClean + ' more in the last ' + RECENT_DAYS + ' days.</p>' : '');
      else html += '<p class="m-empty">None in the last ' + RECENT_DAYS + ' days is clear of the checks below.</p>';
      if (L.check.length) {
        var rl = relabelText(L.check);
        html += '<p class="sp-label">To check: these may come from how the data was collected rather than from a surge of state text.</p>' +
          '<ul class="sp-list">' + L.check.map(function (s) { return itemHtml(s, api, true); }).join("") + '</ul>' +
          (L.moreCheck ? '<p class="sp-more">And ' + L.moreCheck + ' more to check in the last ' + RECENT_DAYS + ' days.</p>' : '') +
          (rl ? '<p class="sp-relabel">' + esc(rl) + '</p>' : '');
      }
    }
    var ch = chanceText(data);
    if (ch) html += '<p class="sp-foot">' + esc(ch) + '</p>';
    slot.innerHTML = html + '</div>';
    bind(slot);
  }

  /* One delegated listener per slot, so re-rendering never stacks handlers. */
  function bind(slot) {
    if (!slot || slot.__spikesBound || typeof slot.addEventListener !== "function") return;
    slot.__spikesBound = true;
    slot.addEventListener("click", function (ev) {
      var t = ev.target, btn = t && t.closest ? t.closest("[data-iso][data-day]") : null;
      if (!btn) return;
      ev.preventDefault();
      select(btn.getAttribute("data-iso"), btn.getAttribute("data-day"), typeof document !== "undefined" ? document : null);
    });
  }

  /* The event detail: an unusual day is about one country's state origin placements on that one day,
     over country outlets and wires together, so that is what the panel is asked to show. */
  function selectDetail(iso, day) {
    return {iso: iso, day: day, measure: "a", window: "day", distribution: "both"};
  }

  /* Click a control button when it is not already the active one, as a reader would. */
  function press(doc, selector) {
    var b = doc.querySelector ? doc.querySelector(selector) : null;
    if (!b || typeof b.click !== "function") return false;
    if (!(b.classList && b.classList.contains && b.classList.contains("active"))) b.click();
    return true;
  }

  /* Open iso on the map at day: through app (window.TrackerApp unless given) when it is there, otherwise
     through the page's own controls. Returns what it did. */
  function select(iso, day, doc, app) {
    var done = {event: false, day: false, country: false, controls: false};
    if (!doc) return done;
    if (app === undefined) app = typeof window !== "undefined" ? window.TrackerApp : null;
    try {
      if (typeof CustomEvent === "function") { doc.dispatchEvent(new CustomEvent("tracker:select", {detail: selectDetail(iso, day)})); done.event = true; }
    } catch (e) { /* an old browser without CustomEvent: the controls below still work */ }
    /* app.js offers window.TrackerApp and opens the event itself; the controls are for a page without it. */
    if (app && done.event) return done;
    var m = press(doc, '[data-measure-group] button[data-measure="a"]');
    var w = press(doc, '[data-window-group] button[data-window="1"]');
    var dsel = press(doc, '[data-distribution-group] button[data-distribution="both"]');
    done.controls = m && w && dsel;
    var scrub = doc.getElementById ? doc.getElementById("scrub") : null;
    var k = lastDays ? lastDays.indexOf(day) : -1;
    if (scrub && k !== -1) {
      scrub.value = String(k);
      scrub.dispatchEvent(new Event("input", {bubbles: true}));
      done.day = true;
    }
    var row = doc.querySelector ? doc.querySelector('#bars .bar-row[data-iso="' + String(iso).replace(/[^A-Za-z0-9:_-]/g, "") + '"]') : null;
    if (row && typeof row.click === "function") { row.click(); done.country = true; }
    var card = doc.getElementById ? doc.getElementById("map-card") : null;
    if (card && card.scrollIntoView) card.scrollIntoView({behavior: "smooth", block: "start"});
    return done;
  }

  /* The Distribution toggle's current value, read from the page: both, country or wires. */
  function scopeOf(doc) {
    var b = doc && doc.querySelector ? doc.querySelector("[data-distribution-group] button.active") : null;
    return (b && b.getAttribute("data-distribution")) || "both";
  }
  function inScope(iso, scope) {
    if (scope === "wires") return iso === "WLD";
    if (scope === "country") return iso !== "WLD";
    return true;
  }

  /* One mark per day with an unusual day in scope: filled when any of them is clean, hollow when every
     one is to check. */
  function marksFor(data, days, scope) {
    var by = {};
    ((data && data.spikes) || []).forEach(function (s) {
      if (!s || !s.date || !inScope(s.country, scope) || (days && days.indexOf(s.date) === -1)) return;
      var m = by[s.date] || (by[s.date] = {day: s.date, clean: false, spikes: []});
      m.spikes.push(s);
      if (s.status === "clean") m.clean = true;
    });
    return Object.keys(by).sort().map(function (d) { return by[d]; });
  }

  function sparkMarks(block, data, api, spark) {
    if (!spark || !spark.svg || typeof spark.x !== "function") return "";
    lastDays = spark.days || null;
    var marks = marksFor(data, spark.days, scopeOf(typeof document !== "undefined" ? document : null));
    var name = api && api.countryName ? api.countryName : function (i) { return i; };
    marks.forEach(function (m) {
      var px = spark.x(m.day);
      if (px === null || px === undefined) return;
      /* A small block, not a circle: the timeline stretches its drawing to the width of the page. */
      spark.svg.append("rect").attr("class", "spike-mark" + (m.clean ? "" : " spike-check"))
        .attr("x", px - 4).attr("y", 3).attr("width", 8).attr("height", 6)
        .append("title").text("Unusual day " + m.day + M.provisionalSuffix(block) + ": " + m.spikes.map(function (s) {
          return name(s.country) + ", " + s.count + " placements" + (s.status === "clean" ? "" : ", to check");
        }).join("; "));
    });
    if (!marks.length) return "";
    return "Small blocks near the top are unusual days" + M.provisionalSuffix(block) + ": a country carried far more state origin placements than in its previous 28 days; hollow ones are days to check.";
  }

  /* The unusual days are listed in their own slot under What we found, so nothing goes in the box itself. */
  function findingsExtra() { return ""; }

  var spec = {render: render, sparkMarks: sparkMarks, findingsExtra: findingsExtra};
  if (M) M.register(ID, spec);
  return {ID: ID, render: render, sparkMarks: sparkMarks, findingsExtra: findingsExtra, recentLists: recentLists,
          reasonText: reasonText, descriptorText: descriptorText, marksFor: marksFor, select: select, shortDate: shortDate,
          selectDetail: selectDetail, whatText: whatText, chanceText: chanceText, relabelText: relabelText, REASONS: REASONS};
}));
