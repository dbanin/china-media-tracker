/* Propaganda calendar (measure calendar, pipeline/calendar_test.py): whether state origin volume rises
   around Chinese political dates fixed in advance (sources/political_calendar.yaml). Rendered into
   #findings-calendar under the theme chart, below the unusual days: the windows that began before the
   calendar was compiled (a retrospective test) and the ones after it (a forward test, fixed in advance),
   each with its rate ratio against normal days and against other days, or the reason it has none.
   Report only and provisional. Loaded in the browser after measures.js, and in Node with module.exports. */
(function (root, factory) {
  var M = root && root.TrackerMeasures;
  if (!M && typeof require === "function") M = require("./measures.js");
  var mod = factory(M);
  if (typeof module === "object" && module.exports) module.exports = mod;
}(typeof self !== "undefined" ? self : this, function (M) {
  "use strict";
  var ID = "calendar";
  var SHOW_UPCOMING = 6;
  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  var EXCLUDED = {
    first_day: "the partial first day",
    outage: "a collection outage",
    labelling_incomplete: "labelling not finished",
    ruleset_change: "a ruleset change",
    gate_change: "a relevance gate change",
    backfill: "a backfill read",
    outside_record: "days outside the record"
  };
  var CAVEAT = "Timing, not cause: a raised window says state text was more common around the date, not that the date caused it, and with many windows some look raised by chance. Windows before 28 October 2026 are a retrospective test, chosen while their data could be seen; later ones were fixed in advance.";

  function esc(s) { return M ? M.esc(s) : String(s === null || s === undefined ? "" : s); }
  function shortDate(day) {
    var p = String(day || "").split("-");
    if (p.length !== 3) return String(day || "");
    return Number(p[2]) + " " + (MONTHS[Number(p[1]) - 1] || p[1]) + " " + p[0];
  }
  function dayMonth(day) {
    var p = String(day || "").split("-");
    if (p.length !== 3) return String(day || "");
    return Number(p[2]) + " " + (MONTHS[Number(p[1]) - 1] || p[1]);
  }
  function span(a, b) { return b && b !== a ? dayMonth(a) + " to " + dayMonth(b) : dayMonth(a); }
  function num(v) { return typeof v === "number" ? (Math.round(v * 100) / 100).toFixed(2) : "n/a"; }
  function safeUrl(u) { return /^https:\/\/[^\s"'<>]+$/.test(String(u || "")) ? String(u) : ""; }

  /* One comparison in words: the ratio with its interval, or why there is none. */
  function compText(t, against) {
    if (!t) return "";
    if (t.state === "tested") {
      var s = num(t.ratio) + " times " + against + " (95 percent interval " + num(t.ci_low) + " to " + num(t.ci_high) + ", " + t.window_days + " days against " + t.comparison_days + ")";
      if (typeof t.p_shift === "number") s += ", shifted windows p " + num(t.p_shift);
      return s;
    }
    if (t.state === "no_comparison_volume") return "no state origin on the " + against;
    return "too few days against " + against + " (" + (t.window_days || 0) + " usable window days, " + (t.comparison_days || 0) + " comparison days)";
  }
  function verdict(t) {
    if (!t || t.state !== "tested") return "";
    if (t.ci_low > 1) return "raised";
    if (t.ci_high < 1) return "lower";
    return "no clear difference";
  }
  function excludedText(w) {
    var ex = w.excluded || {}, out = [];
    Object.keys(EXCLUDED).forEach(function (k) { if (ex[k]) out.push(ex[k] + " for " + EXCLUDED[k]); });
    var mix = ((w.comparisons || {}).other || {}).label_mix_days;
    if (mix) out.push(mix + " labelled under other rules");
    return out.length ? "Days left out: " + out.join(", ") + "." : "";
  }
  function mentionText(t) {
    var m = t && t.mentions;
    if (!m || !m.items) return "";
    return m.mentioning + " of " + m.items + " state origin headlines in the window name it" +
      (typeof m.comparison_share === "number" ? " (" + Math.round(m.comparison_share * 1000) / 10 + " percent on comparison days)" : "") + ".";
  }

  /* The windows that have begun, by kind, and the next upcoming ones. */
  function lists(data) {
    var ws = (data && data.windows) || [];
    var begun = ws.filter(function (w) { return w && w.state && w.state !== "upcoming"; });
    var up = ws.filter(function (w) { return w && w.state === "upcoming"; });
    return {retro: begun.filter(function (w) { return w.kind === "retrospective"; }),
            forward: begun.filter(function (w) { return w.kind !== "retrospective"; }),
            upcoming: up.slice(0, SHOW_UPCOMING), moreUpcoming: Math.max(0, up.length - SHOW_UPCOMING),
            undated: (data && data.undated) || []};
  }

  function windowHtml(w) {
    var c = w.comparisons || {}, n = c.normal, o = c.other;
    var v = verdict(n) || verdict(o);
    var lines = [];
    if (w.state === "in_progress") lines.push("Window still running; figures are for its days so far.");
    if ((!n || n.state !== "tested") && (!o || o.state !== "tested")) {
      lines.push("Too few days to test: " + ((o && o.window_days) || 0) + " usable window days, " + ((n && n.comparison_days) || 0) +
                 " normal and " + ((o && o.comparison_days) || 0) + " other comparison days.");
    } else {
      lines.push("Against normal days: " + compText(n, "normal days") + ".");
      lines.push("Against other days: " + compText(o, "other days") + ".");
    }
    var mt = mentionText(o && o.state === "tested" ? o : n);
    if (mt) lines.push(mt);
    var ex = excludedText(w);
    if (ex) lines.push(ex);
    var url = safeUrl(w.source_url);
    return '<li class="cal-item' + (v ? "" : " cal-muted") + '">' +
      '<span class="cal-date">' + esc(span(w.start, w.end)) + '</span>' +
      '<span class="cal-name">' + esc(w.name) + (v ? ' <span class="cal-verdict">' + esc(v) + '</span>' : '') + '</span>' +
      '<span class="cal-why">' + esc(lines.join(" ")) + (url ? ' <a href="' + esc(url) + '" target="_blank" rel="noopener">Source</a>' : '') + '</span></li>';
  }
  function upcomingHtml(w) {
    var url = safeUrl(w.source_url);
    return '<li class="cal-item cal-muted"><span class="cal-date">' + esc(span(w.start, w.end)) + '</span>' +
      '<span class="cal-name">' + esc(w.name) + '</span><span class="cal-why">' +
      esc("Window " + span(w.window_start, w.window_end) + ", " + (w.status === "expected" ? "dates expected, not yet announced" : w.status === "announced" ? "dates announced" : "date fixed") + ".") +
      (url ? ' <a href="' + esc(url) + '" target="_blank" rel="noopener">Source</a>' : '') + '</span></li>';
  }

  function render(block, data, api) {
    var slot = api && api.slot;
    if (!slot) return;
    if (M && M.isOff(block)) { slot.hidden = true; slot.innerHTML = ""; return; }
    slot.hidden = false;
    var name = "Propaganda calendar" + (M ? M.provisionalSuffix(block) : "");
    var html = '<div class="m-calendar" role="region" aria-label="' + esc(name) + '"><div class="cal-head"><span class="cal-title">Propaganda calendar</span>' + (M ? M.statusChip(block) : "") + '</div>';
    if (M && M.isWithheld(block)) { slot.innerHTML = html + M.emptyState(block, data, "propaganda calendar") + '</div>'; return; }
    if (!data || !Array.isArray(data.windows)) { slot.innerHTML = html + (M ? M.emptyState(block, data, "propaganda calendar") : "") + '</div>'; return; }
    var L = lists(data);
    html += '<p class="cal-sub">Chinese political dates fixed in advance, each with ' + esc(data.lead_days) + ' days before and ' + esc(data.lag_days) +
      ' after, set against normal days outside every window (same weekdays) and, as a conservative check, every other usable day. The list was compiled on ' +
      esc(shortDate(data.compiled_on)) + ' and committed as the pre-registration. ' + esc(data.normal_days) + ' normal days so far; ' +
      esc(Math.round((data.calendar_days_in_windows || 0) * 100)) + ' percent of the days the calendar covers fall in some window.</p>';
    html += '<p class="cal-label">Before ' + esc(shortDate(data.compiled_on)) + ': retrospective</p>';
    html += L.retro.length ? '<ul class="cal-list">' + L.retro.map(windowHtml).join("") + '</ul>' : '<p class="cal-more">None yet.</p>';
    html += '<p class="cal-label">From ' + esc(shortDate(data.compiled_on)) + ': fixed in advance</p>';
    if (L.forward.length) html += '<ul class="cal-list">' + L.forward.map(windowHtml).join("") + '</ul>';
    html += L.upcoming.length ? '<ul class="cal-list">' + L.upcoming.map(upcomingHtml).join("") + '</ul>' : (L.forward.length ? '' : '<p class="cal-more">None yet.</p>');
    if (L.moreUpcoming) html += '<p class="cal-more">And ' + L.moreUpcoming + ' more windows to 31 December 2027.</p>';
    if (L.undated.length) html += '<p class="cal-more">Expected, not yet dated: ' + esc(L.undated.map(function (u) { return u.name + (u.expected_period ? " (" + u.expected_period + ")" : ""); }).join("; ")) + '.</p>';
    html += '<p class="cal-foot">' + esc(CAVEAT) + '</p>';
    slot.innerHTML = html + '</div>';
  }

  function findingsExtra() { return ""; }

  var spec = {render: render, findingsExtra: findingsExtra};
  if (M) M.register(ID, spec);
  return {ID: ID, render: render, findingsExtra: findingsExtra, lists: lists, compText: compText, verdict: verdict,
          excludedText: excludedText, mentionText: mentionText, shortDate: shortDate, CAVEAT: CAVEAT};
}));
