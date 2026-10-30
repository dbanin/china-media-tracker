/* The research measures on the site: a registry of renderers and the shared provisional helpers.
   Each measure has a block in meta.measures (pipeline/measures.py) and, when it runs, a side file
   data/<file>. Each docs/m_<id>.js registers its renderer here; app.js loads the side files, calls
   every renderer in its own try/catch, and adds the marks, findings lines and notes they provide.

   register(id, {render, sparkMarks?, findingsExtra?})
     render(block, data, api)        fills api.slot (the measure's own element). block is
                                     meta.measures[id] or undefined, data the parsed side file or null.
     sparkMarks(block, data, api, spark)  draws on the map timeline; spark = {svg (d3 selection), x(day)
                                     -> px or null, days, w, h}. May return a sentence for the hint.
     findingsExtra(block, data, api) returns HTML for the slot under What we found, or "".
   api = {id, slot, section, meta, names, esc, countryName, M, C}.

   No DOM access outside hideIfOff, so it can be tested in Node or jsc. Loaded in the browser as
   window.TrackerMeasures and in Node via module.exports. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) { module.exports = factory(); }
  else { root.TrackerMeasures = factory(); }
}(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var STATUSES = ["off", "provisional", "checked", "withheld"];
  var STATUS_LABELS = {provisional: "Provisional", checked: "Checked by hand", withheld: "Withheld"};
  /* Where each measure lives on the page: its section, and its own slot inside it. */
  var SECTIONS = [
    {id: "copies", measures: ["alteration", "speed"]},
    {id: "doctrine", measures: ["doctrine"]},
    {id: "engagement", measures: ["engagement"]},
    {id: "syndication", measures: ["syndication"]},
    {id: "findings-measures", measures: ["spikes"]},
    {id: "findings-calendar-wrap", measures: ["calendar"]}
  ];
  var SLOTS = {alteration: "copies-alteration", speed: "copies-speed", doctrine: "doctrine-body",
               engagement: "engagement-body", syndication: "syndication-body", spikes: "findings-spikes",
               calendar: "findings-calendar"};

  var registry = {}, order = [];

  function register(id, spec) {
    if (!id || typeof id !== "string") throw new Error("register: a measure id is required");
    if (!spec || typeof spec.render !== "function") throw new Error("register: " + id + " needs a render function");
    if (!registry[id]) order.push(id);
    registry[id] = spec;
    return spec;
  }
  function get(id) { return registry[id] || null; }
  function ids() { return order.slice(); }

  function esc(s) {
    return String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, function (c) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;"}[c];
    });
  }

  /* A block's status, "off" for anything missing or unknown. */
  function statusOf(block) {
    var s = block && block.status;
    return STATUSES.indexOf(s) === -1 ? "off" : s;
  }
  function isOff(block) { return statusOf(block) === "off"; }
  function isWithheld(block) { return statusOf(block) === "withheld"; }

  /* "12 of 40 coded" style progress, or "" when there is no sample. */
  function checkProgress(block) {
    var c = block && block.check;
    if (!c || !c.n) return "";
    return (c.coded || 0) + " of " + c.n + " coded";
  }
  function fmtValue(v) { return typeof v === "number" ? (Math.round(v * 100) / 100).toFixed(2) : "n/a"; }

  /* The hover text of the status chip: what the status means for this measure now. */
  function statusTitle(block) {
    var s = statusOf(block), c = (block && block.check) || {};
    if (s === "checked") return "Checked by hand" + (c.checked_on ? " on " + c.checked_on : "") + ": " + (c.statistic || "score") + " " + fmtValue(c.value) + " against " + fmtValue(c.threshold) + ".";
    if (s === "withheld") return "Withheld: the latest completed hand check" + (c.checked_on ? ", on " + c.checked_on + "," : "") + " scored " + (c.statistic || "score") + " " + fmtValue(c.value) + ", below " + fmtValue(c.threshold) + ".";
    if (s === "provisional") {
      var p = checkProgress(block);
      return "Provisional: no hand check of this measure has passed yet" + (p ? " (" + p + ")" : "") + ".";
    }
    return "";
  }

  /* An HTML chip naming the status, or "" when the measure is off. */
  function statusChip(block) {
    var s = statusOf(block);
    if (s === "off") return "";
    return '<span class="m-chip m-chip-' + s + '" title="' + esc(statusTitle(block)) + '">' + esc(STATUS_LABELS[s]) + '</span>';
  }

  /* " (provisional)" or " (withheld)" after a figure's label, "" when checked or off. */
  function provisionalSuffix(block) {
    var s = statusOf(block);
    return s === "provisional" ? " (provisional)" : s === "withheld" ? " (withheld)" : "";
  }

  /* Hide a section or slot when its measure is off. Returns true when it stays visible. */
  function hideIfOff(sectionEl, block) {
    var visible = !isOff(block);
    if (sectionEl) sectionEl.hidden = !visible;
    return visible;
  }

  /* Whether a section shows: when at least one of its measures is not off. */
  function sectionVisible(section, blocks) {
    return (section.measures || []).some(function (id) { return !isOff((blocks || {})[id]); });
  }

  function label(id, block) { return (block && block.label) || id; }

  /* Measures whose provisional basis is not a pending hand check (syndication: sources verified, not hand coded). */
  var PROVISIONAL_BASIS = {syndication: "its sources were verified, but it is not hand coded"};

  /* One full-notes line per provisional or withheld measure, never for the short strip. */
  function noticeLines(blocks) {
    var out = [];
    Object.keys(blocks || {}).forEach(function (id) {
      var b = blocks[id], s = statusOf(b), name = label(id, b), p = checkProgress(b);
      if (s === "provisional") out.push(name + " is provisional: it is a new measure, report only, and " + (PROVISIONAL_BASIS[id] || "no hand check of it has passed yet") + (p ? " (" + p + ")" : "") + ". Its figures may change, and none of them changes a count on the map.");
      else if (s === "withheld") out.push(name + " is withheld: " + statusTitle(b).replace(/^Withheld: /, "") + " Its figures are not shown until a new hand check passes.");
    });
    return out;
  }

  /* What a slot says when the measure runs but has nothing to show. what names it without an article
     ("alteration score"). */
  function emptyState(block, data, what) {
    what = what || "measure";
    if (isWithheld(block)) return '<p class="m-empty">' + esc(label(what, block)) + ' is withheld: ' + esc(statusTitle(block).replace(/^Withheld: /, "")) + ' Its figures are not shown until a new hand check passes.</p>';
    if (!data) return '<p class="m-empty">The ' + esc(what) + ' file did not load. Reload the page, or check whether the last export ran.</p>';
    return '<p class="m-empty">Nothing to show yet for the ' + esc(what) + '.</p>';
  }

  /* The method section's paragraphs: each measure that is not off, with its chip and method text. */
  function methodParagraphs(blocks) {
    var out = [];
    Object.keys(blocks || {}).forEach(function (id) {
      var b = blocks[id];
      if (isOff(b) || !b.method_text) return;
      String(b.method_text).split(/\n\s*\n/).forEach(function (para, i) {
        if (!para.trim()) return;
        out.push('<p>' + (i === 0 ? '<strong>' + esc(label(id, b)) + '.</strong> ' + statusChip(b) + ' ' : '') + esc(para.trim()) + '</p>');
      });
    });
    return out;
  }

  /* The same paragraphs, one closed disclosure per measure named in its summary, so the methodology lists the
     measures without their full method text filling the page; find in page still opens a closed one. */
  function methodBlocks(blocks) {
    var out = [];
    Object.keys(blocks || {}).forEach(function (id) {
      var b = blocks[id];
      if (isOff(b) || !b.method_text) return;
      var paras = String(b.method_text).split(/\n\s*\n/).filter(function (p) { return p.trim(); })
        .map(function (p) { return '<p>' + esc(p.trim()) + '</p>'; });
      if (paras.length) out.push('<details class="m-method"><summary><strong>' + esc(label(id, b)) + '</strong> ' + statusChip(b) + '</summary>' + paras.join("") + '</details>');
    });
    return out;
  }

  /* Run every registered renderer for the blocks, each in its own try/catch. getApi(id) gives its api.
     Returns the ids that failed, with their messages. */
  function renderAll(blocks, dataById, getApi) {
    var failed = [];
    order.forEach(function (id) {
      try { registry[id].render((blocks || {})[id], (dataById || {})[id] || null, getApi(id)); }
      catch (e) { failed.push(id + (e && e.message ? " (" + e.message + ")" : "")); if (typeof console !== "undefined") console.error("measure " + id + " failed", e); }
    });
    return failed;
  }

  /* Each registered findingsExtra that is not off, joined; failures are skipped and logged. */
  function findingsHtml(blocks, dataById, getApi) {
    var parts = [];
    order.forEach(function (id) {
      var spec = registry[id], b = (blocks || {})[id];
      if (!spec.findingsExtra || isOff(b)) return;
      try { var h = spec.findingsExtra(b, (dataById || {})[id] || null, getApi(id)); if (h) parts.push(h); }
      catch (e) { if (typeof console !== "undefined") console.error("measure " + id + " findings failed", e); }
    });
    return parts.join("");
  }

  /* Each registered sparkMarks that is not off; returns the hint sentences they give. */
  function sparkAll(blocks, dataById, getApi, spark) {
    var hints = [];
    order.forEach(function (id) {
      var spec = registry[id], b = (blocks || {})[id];
      if (!spec.sparkMarks || isOff(b)) return;
      try { var h = spec.sparkMarks(b, (dataById || {})[id] || null, getApi(id), spark); if (h) hints.push(h); }
      catch (e) { if (typeof console !== "undefined") console.error("measure " + id + " timeline marks failed", e); }
    });
    return hints;
  }

  return {STATUSES: STATUSES, STATUS_LABELS: STATUS_LABELS, SECTIONS: SECTIONS, SLOTS: SLOTS,
          register: register, get: get, ids: ids, esc: esc, statusOf: statusOf, isOff: isOff, isWithheld: isWithheld,
          checkProgress: checkProgress, statusTitle: statusTitle, statusChip: statusChip, provisionalSuffix: provisionalSuffix,
          hideIfOff: hideIfOff, sectionVisible: sectionVisible, noticeLines: noticeLines, emptyState: emptyState, methodBlocks: methodBlocks,
          methodParagraphs: methodParagraphs, renderAll: renderAll, findingsHtml: findingsHtml, sparkAll: sparkAll};
}));
