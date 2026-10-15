/* Engagement (measure engagement): aggregate counts of public Facebook and Instagram posts linking to
   state origin articles, looked up by hand in the Meta Content Library and imported by
   pipeline/engagement.py. Hidden while the measure is off (it ships switched off). When it runs it shows
   one line on how many of the articles looked up were found, a small table by country with the status
   chip, and the limits in one sentence. Counts only: the side file holds no address, post or account.
   Loaded in the browser after measures.js, and in Node or jsc with module.exports. */
(function (root, factory) {
  var M = root && root.TrackerMeasures;
  if (!M && typeof require === "function") M = require("./measures.js");
  var mod = factory(M);
  if (typeof module === "object" && module.exports) module.exports = mod;
}(typeof self !== "undefined" ? self : this, function (M) {
  "use strict";
  var ID = "engagement";
  var MAX_ROWS = 10;

  /* 12345 as "12,345"; anything that is not a number as "n/a". */
  function fmt(n) {
    if (typeof n !== "number" || !isFinite(n)) return "n/a";
    return String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }
  function pct(v) { return typeof v === "number" && isFinite(v) ? Math.round(v * 100) + "%" : "n/a"; }

  function countryLabel(iso, api) {
    if (iso === "WLD") return "Worldwide distribution";
    var name = api && typeof api.countryName === "function" ? api.countryName(iso) : null;
    return name || iso || "";
  }

  /* The country rows the table shows: most posts first, at most MAX_ROWS, and never CHN. */
  function countryRows(data) {
    return ((data && data.by_country) || []).filter(function (r) { return r && r.id && r.id !== "CHN"; })
      .slice().sort(function (a, b) { return (b.posts_n || 0) - (a.posts_n || 0) || (b.found || 0) - (a.found || 0); })
      .slice(0, MAX_ROWS);
  }

  function render(block, data, api) {
    var slot = api && api.slot;
    if (!slot) return;
    if (!M.hideIfOff(slot, block)) { slot.innerHTML = ""; return; }
    var cov = data && data.coverage;
    if (M.isWithheld(block) || !data || !cov || !cov.looked_up) {
      slot.innerHTML = M.emptyState(block, data, "engagement");
      return;
    }
    var esc = M.esc, k = data.min_cell || 5, rows = countryRows(data);
    var last = data.query_dates && data.query_dates.last;
    var html = '<div class="m-panel m-eng">';
    html += '<p class="m-eng-lead">' + M.statusChip(block) + ' Of <strong>' + fmt(cov.looked_up) + '</strong> state origin articles looked up, <strong>' +
      fmt(cov.found) + '</strong> (' + pct(cov.share_found) + ') were linked from at least one public Facebook or Instagram post' +
      esc(M.provisionalSuffix(block)) + '.</p>';
    if (rows.length) {
      html += '<div class="m-table-wrap"><table class="m-table m-eng-table"><thead><tr><th>Country</th><th class="num">Looked up</th>' +
        '<th class="num">Found</th><th class="num">Posts</th><th class="num">Reactions</th><th class="num">Shares</th><th class="num">Comments</th></tr></thead><tbody>';
      rows.forEach(function (r) {
        html += '<tr><td>' + esc(countryLabel(r.id, api)) + '</td><td class="num">' + fmt(r.urls) + '</td><td class="num">' + fmt(r.found) +
          '</td><td class="num">' + fmt(r.posts_n) + '</td><td class="num">' + fmt(r.reactions) + '</td><td class="num">' + fmt(r.shares) +
          '</td><td class="num">' + fmt(r.comments) + '</td></tr>';
      });
      html += '</tbody></table></div>';
    }
    html += '<p class="m-eng-foot">Reach, not supply: counts of public posts carrying the link, looked up by hand' +
      (last ? ' on ' + esc(last) : '') + '. A cell of 1 to ' + esc(k - 1) + ' posts is suppressed and adds nothing, so every sum is a lower bound. ' +
      'Private groups, other platforms and copies without the link are not counted, and no figure on the map changes.</p>';
    html += '</div>';
    slot.innerHTML = html;
  }

  var spec = {render: render};
  if (M) M.register(ID, spec);
  return {ID: ID, render: render, fmt: fmt, countryRows: countryRows, MAX_ROWS: MAX_ROWS};
}));
