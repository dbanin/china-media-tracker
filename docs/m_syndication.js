/* Syndication deals (measure syndication, pipeline/syndication.py). Renders data/syndication.json into
   #syndication-body: a timeline of publicly announced agreements between outlets and Chinese state media,
   newest first, each with its source link, and a before and after table for the deals collection can
   compare. Hidden while the measure is off. A comparison shows timing, not cause, and most deals predate
   collection. While provisional it says the sources were verified, not hand coded (data.verification).
   Loaded in the browser after measures.js, and in Node or jsc with module.exports. */
(function (root, factory) {
  var M = root && root.TrackerMeasures;
  if (!M && typeof require === "function") M = require("./measures.js");
  var mod = factory(M);
  if (typeof module === "object" && module.exports) module.exports = mod;
}(typeof self !== "undefined" ? self : this, function (M) {
  "use strict";
  var ID = "syndication";
  var WORDING = "sources verified on 2026-10-28, not hand coded";   /* fallback for data.verification.wording */
  var VERIFY_TAGS = {verified: "source checked", source_moved: "source moved, new page checked", unverified: "source not verified",
                     unchecked: "source not checked yet"};
  var MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
                "October", "November", "December"];

  function esc(s) { return M.esc(s); }

  /* A date at its own precision: "17 December 2025", "May 2025" or "2019". */
  function fmtDate(s) {
    s = String(s || "");
    var m = /^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?$/.exec(s);
    if (!m) return s;
    if (!m[2]) return m[1];
    var month = MONTHS[parseInt(m[2], 10) - 1] || m[2];
    return m[3] ? parseInt(m[3], 10) + " " + month + " " + m[1] : month + " " + m[1];
  }
  function pct(v) { return typeof v === "number" ? (Math.round(v * 1000) / 10).toFixed(1) + "%" : "n/a"; }
  function pts(v) {
    if (typeof v !== "number") return "n/a";
    var x = Math.round(v * 1000) / 10;
    return (x > 0 ? "+" : x < 0 ? "−" : "") + Math.abs(x).toFixed(1) + " pts";
  }
  function safeUrl(u) { return /^https:\/\/[^\s"'<>]+$/.test(String(u || "")) ? String(u) : ""; }
  function link(url, text) {
    var u = safeUrl(url);
    return u ? '<a href="' + esc(u) + '" target="_blank" rel="noopener noreferrer">' + esc(text || u) +
      '<span class="syn-sr"> (opens in a new tab)</span></a>' : esc(text || "");
  }

  /* Entries that are one agreement: the same source page, partner, kind and date (a deal that covers
     several monitored outlets is listed once for each). */
  function agreementCount(timeline) {
    var seen = {};
    (timeline || []).forEach(function (e) {
      var src = (e.source && e.source.url) || e.id;
      seen[[src, e.partner_label || e.partner, e.kind, e.signed_on].join("|")] = true;
    });
    return Object.keys(seen).length;
  }

  /* "Most deals (47 of the 51 entries) were signed before ..." only above half; otherwise the count. */
  function predateLine(data) {
    var c = data.counts || {}, n = (data.timeline || []).length, p = c.predates_collection || 0;
    var start = esc(fmtDate(data.collection_start));
    if (!n || !p) return "";
    if (2 * p > n) return 'Most deals were signed before collection began on ' + start + ' (' + esc(String(p)) + ' of the ' +
      esc(String(n)) + ' entries), so they appear on the timeline only, with no comparison.';
    return esc(String(p)) + ' of the ' + esc(String(n)) + ' entries were signed before collection began on ' + start +
      ' and appear on the timeline only, with no comparison.';
  }
  function outletLabel(e, api) {
    var c = e.country && api && typeof api.countryName === "function" ? api.countryName(e.country) : e.country;
    return esc(e.outlet_name || e.outlet_id || "") + (c ? ' <span class="syn-country">' + esc(c) + '</span>' : "");
  }

  /* One timeline entry. */
  function entryHtml(e, api) {
    var src = e.source || {};
    var tags = [];
    tags.push(e.confidence === "stated" ? "announced by a party" : "reported by a third party");
    if (e.predates_collection) tags.push("before collection began");
    if (!e.monitored) tags.push("outlet not monitored");
    if (e.ended_on) tags.push("ended " + fmtDate(e.ended_on));
    if (e.effective_from) tags.push("in force from " + fmtDate(e.effective_from));
    var v = e.verification || {status: "unchecked"};
    if (VERIFY_TAGS[v.status]) tags.push(VERIFY_TAGS[v.status] + (v.status !== "unverified" && v.status !== "unchecked" && v.checked_on ? " " + fmtDate(v.checked_on) : ""));
    var links = (e.links && e.links.length ? e.links : [{url: src.url, label: src.title, publisher: src.publisher}]).map(function (l) {
      return link(l.url, l.label || l.url) + (l.publisher ? ' <span class="syn-pub">(' + esc(l.publisher) + ')</span>' : "");
    }).join("; ");
    return '<li class="syn-entry' + (e.predates_collection ? " syn-before" : "") + '">' +
      '<div class="syn-date">' + esc(fmtDate(e.signed_on)) + '</div>' +
      '<div class="syn-what"><strong>' + outletLabel(e, api) + '</strong> and ' + esc(e.partner_label || e.partner) +
      ', ' + esc(e.kind_label || e.kind) + '</div>' +
      '<div class="syn-tags">' + esc(tags.join(" · ")) + '</div>' +
      (e.notes ? '<p class="syn-notes">' + esc(e.notes) + '</p>' : "") +
      '<div class="syn-src">Source: ' + links + '</div></li>';
  }

  /* The newest SHOWN entries, then the rest behind a native disclosure, so 51 deals do not fill a phone screen. */
  var SHOWN = 6;
  function timelineHtml(timeline, api) {
    var head = timeline.slice(0, SHOWN), rest = timeline.slice(SHOWN);
    var html = '<ol class="syn-timeline">' + head.map(function (e) { return entryHtml(e, api); }).join("") + '</ol>';
    if (rest.length) html += '<details class="syn-more"><summary>Show the other ' + esc(String(rest.length)) +
      (rest.length === 1 ? ' entry' : ' entries') + '</summary><ol class="syn-timeline" start="' + (SHOWN + 1) + '">' +
      rest.map(function (e) { return entryHtml(e, api); }).join("") + '</ol></details>';
    return html;
  }

  function sideCell(s) {
    if (!s) return '<td class="num">n/a</td>';
    if (!s.sufficient) return '<td class="num">' + esc(String(s.china || 0)) + ' China articles, too few</td>';
    var ci = s.ci ? ' <span class="syn-ci">(' + pct(s.ci[0]) + " to " + pct(s.ci[1]) + ')</span>' : "";
    return '<td class="num">' + pct(s.share) + ci + '<br><span class="syn-n">' + esc(String(s.state_origin)) + " of " + esc(String(s.china)) + '</span></td>';
  }

  /* What else changed in a window. Several ruleset or gate changes read as one line each (how many, first
     and last day); the full list stays one click away. */
  function confounderHtml(list) {
    list = list || [];
    if (!list.length) return "";
    var groups = {ruleset_change: [], gate_change: []}, rest = [];
    list.forEach(function (x) { if (groups[x.type]) groups[x.type].push(x); else rest.push(x); });
    var lines = [];
    [["ruleset_change", "signature ruleset"], ["gate_change", "relevance gate"]].forEach(function (g) {
      var xs = groups[g[0]];
      if (xs.length === 1) lines.push(esc(xs[0].text));
      else if (xs.length > 1) {
        var days = xs.map(function (x) { return String(x.date || ""); }).sort();
        lines.push("the " + g[1] + " changed " + xs.length + " times inside the window, from " + esc(fmtDate(days[0])) +
          " to " + esc(fmtDate(days[days.length - 1])));
      }
    });
    rest.forEach(function (x) { lines.push(esc(x.text)); });
    var html = lines.join("; ");
    if (lines.length < list.length) {
      html += '<details class="syn-all"><summary>All ' + list.length + '</summary><ul>' +
        list.map(function (x) { return "<li>" + esc(x.text) + "</li>"; }).join("") + '</ul></details>';
    }
    return html;
  }

  /* The before and after table, or a line saying why there is none. */
  function compareHtml(block, data, api) {
    var rows = data.comparisons || [];
    var head = '<h3>Before and after' + M.provisionalSuffix(block) + '</h3>';
    var predate = predateLine(data);
    var intro = '<p class="m-empty">State origin share of each outlet\'s China coverage over the ' + esc(String(data.window_days || 28)) +
      ' days before and after a deal. It shows timing, not cause. ' + predate + '</p>';
    if (!rows.length) {
      var days = esc(String(data.window_days || 28)), from = String(data.eligible_from || ""), until = String(data.eligible_until || "");
      /* Early on the range is empty (its end falls before its start): say when the first comparison can come. */
      var when = from && until && until < from
        ? 'No deal can be compared yet: a comparison needs ' + days + ' days of collection on each side of a deal, so the earliest deal it can take was signed on ' +
          esc(fmtDate(from)) + ', and collection has not yet run ' + days + ' days past that. Each side also needs '
        : 'No deal yet falls where a comparison is possible: one needs a deal signed on a known day or month between ' +
          esc(fmtDate(from)) + ' and ' + esc(fmtDate(until)) + ', with ';
      return head + '<p class="m-empty">' + when + esc(String(data.min_china_per_side || 50)) +
        ' China articles. A comparison, when there is one, shows timing, not cause. ' + predate + '</p>';
    }
    var body = rows.map(function (c) {
      var change = c.sufficient ? pts(c.difference) + (c.difference_ci ? ' <span class="syn-ci">(' + pts(c.difference_ci[0]) + " to " + pts(c.difference_ci[1]) + ')</span>' : "")
                                : esc(c.reason || "too few articles");
      var conf = confounderHtml(c.confounders);
      return '<tr' + (c.confounded ? ' class="syn-confounded"' : "") + '><td>' + outletLabel(c, api) + '</td><td>' + esc(fmtDate(c.signed_on)) + '</td>' +
        sideCell(c.before) + sideCell(c.after) + '<td class="num">' + change + '</td><td>' + (conf || "none found") + '</td></tr>';
    }).join("");
    return head + intro + '<div class="m-table-wrap"><table class="m-table syn-table"><thead><tr><th>Outlet</th><th>Signed</th>' +
      '<th class="num">Before</th><th class="num">After</th><th class="num">Change</th><th>Also changed</th></tr></thead><tbody>' +
      body + '</tbody></table></div>';
  }

  function render(block, data, api) {
    var slot = api && api.slot;
    if (!slot) return;
    if (!M.hideIfOff(slot, block)) { slot.innerHTML = ""; return; }
    if (M.isWithheld(block) || !data || !data.timeline || !data.timeline.length) {
      slot.innerHTML = M.emptyState(block, data, "syndication deals");
      return;
    }
    var counts = data.counts || {};
    var n = data.timeline.length, deals = agreementCount(data.timeline);
    var lead = '<p class="m-empty">' + esc(String(n)) + (n === 1 ? ' entry' : ' entries') +
      (deals !== n ? ' for ' + esc(String(deals)) + ' agreements (an agreement that covers several outlets is listed once for each)' : '') +
      ', each with the public page that states it' +
      (counts.predates_collection ? '; ' + esc(String(counts.predates_collection)) + ' of the ' + esc(String(n)) + ' were signed before collection began on ' + esc(fmtDate(data.collection_start)) + ' and appear here only' : '') + '.</p>';
    var wording = (data.verification && data.verification.wording) || WORDING;
    /* A side file from before the source checks has no unchecked count: count the entries without a check. */
    var unchecked = typeof counts.unchecked === "number" ? counts.unchecked
      : data.timeline.filter(function (e) { return !e.verification || !VERIFY_TAGS[e.verification.status] || e.verification.status === "unchecked"; }).length;
    var verified = M.statusOf(block) !== "provisional" ? ''
      : unchecked >= n ? '<p class="m-empty syn-verified">Provisional: not hand coded, and the sources have not been checked yet.</p>'
      : '<p class="m-empty syn-verified">Provisional: ' + esc(wording) + '.' +
        (counts.source_moved ? ' ' + esc(String(counts.source_moved)) + ' source' + (counts.source_moved === 1 ? ' has' : 's have') + ' moved and now cite' + (counts.source_moved === 1 ? 's' : '') + ' a page that states the deal.' : '') +
        (counts.unverified ? ' ' + esc(String(counts.unverified)) + ' could not be verified and ' + (counts.unverified === 1 ? 'is' : 'are') + ' marked.' : '') +
        (unchecked ? ' ' + esc(String(unchecked)) + (unchecked === 1 ? ' entry has' : ' entries have') + ' not been checked yet and ' + (unchecked === 1 ? 'is' : 'are') + ' marked.' : '') + '</p>';
    slot.innerHTML =
      '<div class="m-panel syn-panel"><h3>Timeline ' + M.statusChip(block) + '</h3>' + verified + lead +
      timelineHtml(data.timeline, api) + '</div>' +
      '<div class="m-panel syn-panel">' + compareHtml(block, data, api) + '</div>';
  }

  var spec = {render: render};
  if (M) M.register(ID, spec);
  return {ID: ID, WORDING: WORDING, SHOWN: SHOWN, render: render, timelineHtml: timelineHtml, fmtDate: fmtDate, safeUrl: safeUrl, pts: pts, agreementCount: agreementCount,
          confounderHtml: confounderHtml};
}));
