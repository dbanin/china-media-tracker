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
  var CAVEAT = "Timing, not cause: a raised window says state text was more common around the date, not that the date caused it. Windows that begin on or before 28 October 2026 are a retrospective test, chosen while their data could be seen (the list was committed that evening); later ones were fixed in advance.";
  var CHANCE = 0.025;   /* calendar_test.CHANCE_RAISED: windows without a rise called raised by chance */

  function esc(s) { return M ? M.esc(s) : String(s === null || s === undefined ? "" : s); }
  function shortDate(day) {
    var p = String(day || "").split("-");
    if (p.length !== 3) return String(day || "");
    return Number(p[2]) + " " + (MONTHS[Number(p[1]) - 1] || p[1]) + " " + p[0];
  }
  var baseYear = "";   /* the compile year: other years are written out */
  function dayMonth(day) {
    var p = String(day || "").split("-");
    if (p.length !== 3) return String(day || "");
    return Number(p[2]) + " " + (MONTHS[Number(p[1]) - 1] || p[1]) + (baseYear && p[0] !== baseYear ? " " + p[0] : "");
  }
  function span(a, b) { return b && b !== a ? dayMonth(a) + " to " + dayMonth(b) : dayMonth(a); }
  function nextDay(day) {
    var p = String(day || "").split("-");
    if (p.length !== 3) return String(day || "");
    var d = new Date(Date.UTC(Number(p[0]), Number(p[1]) - 1, Number(p[2]) + 1));
    return d.toISOString().slice(0, 10);
  }
  function num(v) { return typeof v === "number" ? (Math.round(v * 100) / 100).toFixed(2) : "n/a"; }
  function safeUrl(u) { return /^https:\/\/[^\s"'<>]+$/.test(String(u || "")) ? String(u) : ""; }

  /* One comparison in words: the ratio with its interval, or why there is none. */
  function compText(t, against) {
    if (!t) return "";
    if (t.state === "tested") {
      var iv = typeof t.ci_high === "number" ? num(t.ci_low) + " to " + num(t.ci_high) : "from " + num(t.ci_low) + ", no upper bound";
      var s = num(t.ratio) + " times " + against + " (95 percent interval " + iv + ", " + t.window_days + " days against " + t.comparison_days + ")";
      if (typeof t.p_shift === "number") s += ", shifted windows p " + num(t.p_shift);
      return s;
    }
    if (t.state === "no_comparison_volume") return "no state origin on the " + against;
    return "too few days against " + against + " (" + (t.window_days || 0) + " usable window days, " + (t.comparison_days || 0) + " comparison days)";
  }
  function verdict(t) {
    if (!t || t.state !== "tested") return "";
    if (t.ci_low > 1) return "raised";
    if (typeof t.ci_high === "number" && t.ci_high < 1) return "lower";
    return "no clear difference";
  }
  /* The verdict as shown: the normal days' when they were tested, else the other days'; "so far" while the
     window runs. */
  function shownVerdict(w) {
    var c = (w && w.comparisons) || {};
    var v = verdict(c.normal) || verdict(c.other);
    return v && w.state === "in_progress" ? v + " so far" : v;
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
    return m.mentioning + " of " + m.items + " state origin headlines in the window carry one of its keywords" +
      (typeof m.comparison_share === "number" ? " (" + Math.round(m.comparison_share * 1000) / 10 + " percent on comparison days)" : "") + ".";
  }

  /* The windows that have begun, by kind, and the next upcoming ones. */
  function lists(data) {
    var ws = (data && data.windows) || [];
    var begun = ws.filter(function (w) { return w && w.state && w.state !== "upcoming"; });
    var up = ws.filter(function (w) { return w && w.state === "upcoming" && w.kind === "forward"; });
    return {retro: begun.filter(function (w) { return w.kind === "retrospective"; }),
            /* a retrospective window whose first day is not yet complete in the record (it began on the
               compile day) is listed with the retrospective ones */
            retroUpcoming: ws.filter(function (w) { return w && w.state === "upcoming" && w.kind === "retrospective"; }),
            forward: begun.filter(function (w) { return w.kind === "forward"; }),
            later: ws.filter(function (w) { return w && w.kind === "added_later"; }),
            upcoming: up.slice(0, SHOW_UPCOMING), moreUpcoming: Math.max(0, up.length - SHOW_UPCOMING),
            undated: (data && data.undated) || []};
  }

  var MIN_COMPARISON = 14;   /* calendar_test.MIN_COMPARISON_DAYS */
  var names = {};
  function windowHtml(w) {
    var c = w.comparisons || {}, n = c.normal, o = c.other;
    var v = shownVerdict(w);
    var lines = [];
    if (w.kind === "added_later") lines.push("Added or changed on " + shortDate(w.added_on) + ", after the pre-registration.");
    if (w.state === "in_progress") lines.push("Window still running to " + dayMonth(w.window_end) + "; figures are for its days so far.");
    if ((!n || n.state !== "tested") && (!o || o.state !== "tested")) {
      lines.push("Too few days to test: " + ((o && o.window_days) || 0) + " usable window days, " + ((n && n.comparison_days) || 0) +
                 " normal and " + ((o && o.comparison_days) || 0) + " other comparison days.");
    } else {
      lines.push("Against normal days: " + compText(n, "normal days") + ".");
      lines.push("Against other days: " + compText(o, "other days") + ".");
    }
    if (typeof w.normal_possible === "number" && w.normal_possible < MIN_COMPARISON && (!n || n.state !== "tested"))
      lines.push("Normal days can never be enough here: at most " + w.normal_possible + " lie within 56 days outside every window.");
    var ov = (w.overlaps || []).map(function (id) { return names[id] || id; });
    if (ov.length) lines.push("Shares days with " + ov.join(", ") + ", so the results are not independent.");
    var mt = mentionText(o && o.state === "tested" ? o : n);
    if (mt) lines.push(mt);
    var ex = excludedText(w);
    if (ex) lines.push(ex);
    var url = safeUrl(w.source_url);
    return '<li class="cal-item' + (v ? "" : " cal-muted") + '">' +
      '<span class="cal-date">' + esc(span(w.start, w.end)) + '</span>' +
      '<span class="cal-name">' + esc(w.name) + (v ? ' <span class="cal-verdict">' + esc(v) + '</span>' : '') + '</span>' +
      '<span class="cal-why">' + esc(lines.join(" ")) + sourceLink(w) + '</span></li>';
  }
  function sourceLink(w) {
    var url = safeUrl(w.source_url);
    return url ? ' <a href="' + esc(url) + '" target="_blank" rel="noopener" aria-label="' + esc("Source for " + w.name + " (opens in a new tab)") + '">Source</a>' : '';
  }
  function upcomingHtml(w) {
    var later = w.kind === "added_later" ? " Added or changed on " + shortDate(w.added_on) + ", after the pre-registration." : "";
    return '<li class="cal-item cal-muted"><span class="cal-date">' + esc(span(w.start, w.end)) + '</span>' +
      '<span class="cal-name">' + esc(w.name) + '</span><span class="cal-why">' +
      esc("Window " + span(w.window_start, w.window_end) + ", " + (w.status === "expected" ? "dates expected, not yet announced" : w.status === "announced" ? "dates announced" : "date fixed") + "." + later) +
      sourceLink(w) + '</span></li>';
  }
  /* How many ended windows were tested and called raised, beside how many would be by chance alone. */
  function chanceText(data) {
    var ws = ((data && data.windows) || []).filter(function (w) { return w && w.state === "tested"; });
    var tested = 0, raised = 0;
    ws.forEach(function (w) {
      var c = w.comparisons || {}, t = c.normal && c.normal.state === "tested" ? c.normal : c.other;
      if (t && t.state === "tested") { tested += 1; if (t.ci_low > 1) raised += 1; }
    });
    if (!tested) return "";
    var exp = tested * CHANCE;
    var chance = exp >= 0.95 ? "about " + (Math.round(exp * 10) / 10) + " of these " + tested : "fewer than 1 of these " + tested;
    return raised + " of " + tested + " ended windows with a test look raised (against normal days where they could be tested, else other days). If no date mattered, about 1 in 40 tested windows would still look raised by chance, " +
      chance + ", and more where daily counts swing widely; overlapping windows rise and fall together.";
  }

  function render(block, data, api) {
    var slot = api && api.slot;
    if (!slot) return;
    if (M && M.isOff(block)) { slot.hidden = true; slot.innerHTML = ""; return; }
    slot.hidden = false;
    var name = "Propaganda calendar" + (M ? M.provisionalSuffix(block) : "");
    var html = '<div class="m-calendar" role="region" aria-label="' + esc(name) + '"><div class="cal-head"><span class="cal-title" role="heading" aria-level="3">Propaganda calendar</span>' + (M ? M.statusChip(block) : "") + '</div>';
    if (M && M.isWithheld(block)) { slot.innerHTML = html + M.emptyState(block, data, "propaganda calendar") + '</div>'; return; }
    if (!data || !Array.isArray(data.windows)) { slot.innerHTML = html + (M ? M.emptyState(block, data, "propaganda calendar") : "") + '</div>'; return; }
    var L = lists(data);
    baseYear = String(data.compiled_on || "").slice(0, 4);
    names = {};
    data.windows.forEach(function (w) { if (w && w.id) names[w.id] = w.name; });
    html += '<p class="cal-sub">Chinese political dates fixed in advance, each with ' + esc(data.lead_days) + ' days before and ' + esc(data.lag_days) +
      ' after, set against normal days outside every window (same weekdays) and, while those are few, against every other usable day, which includes the windows of other events. The list was compiled on ' +
      esc(shortDate(data.compiled_on)) + ' and committed as the pre-registration. ' + esc(data.normal_days) + ' normal days so far; ' +
      esc(Math.round((data.calendar_days_in_windows || 0) * 100)) + ' percent of the days the calendar covers fall in some window.</p>';
    html += '<p class="cal-label">Windows beginning on or before ' + esc(shortDate(data.compiled_on)) + ': retrospective</p>';
    html += L.retro.length || L.retroUpcoming.length ? '<ul class="cal-list">' + L.retro.map(windowHtml).join("") + L.retroUpcoming.map(upcomingHtml).join("") + '</ul>' : '<p class="cal-more">None yet.</p>';
    html += '<p class="cal-label">Windows beginning from ' + esc(shortDate(nextDay(data.compiled_on))) + ': fixed in advance</p>';
    if (L.forward.length) html += '<ul class="cal-list">' + L.forward.map(windowHtml).join("") + '</ul>';
    html += L.upcoming.length ? '<ul class="cal-list">' + L.upcoming.map(upcomingHtml).join("") + '</ul>' : (L.forward.length ? '' : '<p class="cal-more">None yet.</p>');
    if (L.moreUpcoming) html += '<p class="cal-more">And ' + L.moreUpcoming + ' more windows to 31 December 2027.</p>';
    if (L.later.length) html += '<p class="cal-label">Added or changed after the pre-registration</p><ul class="cal-list">' +
      L.later.map(function (w) { return w.state === "upcoming" ? upcomingHtml(w) : windowHtml(w); }).join("") + '</ul>';
    if (L.undated.length) html += '<p class="cal-more">Expected, not yet dated: ' + esc(L.undated.map(function (u) { return u.name + (u.expected_period ? " (" + u.expected_period + ")" : ""); }).join("; ")) + '.</p>';
    var ch = chanceText(data);
    if (ch) html += '<p class="cal-more">' + esc(ch) + '</p>';
    html += '<p class="cal-foot">' + esc(CAVEAT) + '</p>';
    slot.innerHTML = html + '</div>';
  }

  function findingsExtra() { return ""; }

  var spec = {render: render, findingsExtra: findingsExtra};
  if (M) M.register(ID, spec);
  return {ID: ID, render: render, findingsExtra: findingsExtra, lists: lists, compText: compText, verdict: verdict,
          excludedText: excludedText, mentionText: mentionText, shortDate: shortDate, nextDay: nextDay, chanceText: chanceText,
          shownVerdict: shownVerdict, CAVEAT: CAVEAT};
}));
