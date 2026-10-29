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
  var VERIFY_TAGS = {verified: "source checked", source_moved: "source moved, new page checked", unverified: "source not verified"};
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
    return u ? '<a href="' + esc(u) + '" target="_blank" rel="noopener noreferrer">' + esc(text || u) + '</a>' : esc(text || "");
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
    var v = e.verification || {};
    if (VERIFY_TAGS[v.status]) tags.push(VERIFY_TAGS[v.status] + (v.status !== "unverified" && v.checked_on ? " " + fmtDate(v.checked_on) : ""));
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

  function sideCell(s) {
    if (!s) return '<td class="num">n/a</td>';
    if (!s.sufficient) return '<td class="num">' + esc(String(s.china || 0)) + ' China articles, too few</td>';
    var ci = s.ci ? ' <span class="syn-ci">(' + pct(s.ci[0]) + " to " + pct(s.ci[1]) + ')</span>' : "";
    return '<td class="num">' + pct(s.share) + ci + '<br><span class="syn-n">' + esc(String(s.state_origin)) + " of " + esc(String(s.china)) + '</span></td>';
  }

  /* The before and after table, or a line saying why there is none. */
  function compareHtml(block, data, api) {
    var rows = data.comparisons || [];
    var head = '<h3>Before and after' + M.provisionalSuffix(block) + '</h3>';
    var predate = 'Most deals were signed before collection began on ' + esc(fmtDate(data.collection_start)) +
      ', so they appear on the timeline only, with no comparison.';
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
      var conf = (c.confounders || []).map(function (x) { return esc(x.text); }).join("; ");
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
    var lead = '<p class="m-empty">' + esc(String(data.timeline.length)) + ' agreements, each with the public page that states it' +
      (counts.predates_collection ? '; ' + esc(String(counts.predates_collection)) + ' were signed before collection began on ' + esc(fmtDate(data.collection_start)) + ' and appear here only' : '') + '.</p>';
    var wording = (data.verification && data.verification.wording) || WORDING;
    var verified = M.statusOf(block) === "provisional"
      ? '<p class="m-empty syn-verified">Provisional: ' + esc(wording) + '.' +
        (counts.source_moved ? ' ' + esc(String(counts.source_moved)) + ' source' + (counts.source_moved === 1 ? ' has' : 's have') + ' moved and now cite' + (counts.source_moved === 1 ? 's' : '') + ' a page that states the deal.' : '') +
        (counts.unverified ? ' ' + esc(String(counts.unverified)) + ' could not be verified and ' + (counts.unverified === 1 ? 'is' : 'are') + ' marked.' : '') + '</p>'
      : '';
    slot.innerHTML =
      '<div class="m-panel syn-panel"><h3>Timeline ' + M.statusChip(block) + '</h3>' + verified + lead +
      '<ol class="syn-timeline">' + data.timeline.map(function (e) { return entryHtml(e, api); }).join("") + '</ol></div>' +
      '<div class="m-panel syn-panel">' + compareHtml(block, data, api) + '</div>';
  }

  var spec = {render: render};
  if (M) M.register(ID, spec);
  return {ID: ID, WORDING: WORDING, render: render, fmtDate: fmtDate, safeUrl: safeUrl, pts: pts};
}));
