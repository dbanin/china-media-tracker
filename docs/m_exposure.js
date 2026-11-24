/* Exposure (measure exposure): where outlets placed state origin items, from data/exposure.json
   (pipeline/exposure.py). One line on how many newsroom placements were found only in a press release,
   sponsored or partner section and how many came through a general news feed, one on the homepage reads,
   a table by country with the status chip, and the limits in one sentence. Counts only: the side file
   holds no address and no headline. Hidden while the measure is off (it ships switched off).
   Loaded in the browser after measures.js, and in Node or jsc with module.exports. */
(function (root, factory) {
  var M = root && root.TrackerMeasures;
  if (!M && typeof require === "function") M = require("./measures.js");
  var mod = factory(M);
  if (typeof module === "object" && module.exports) module.exports = mod;
}(typeof self !== "undefined" ? self : this, function (M) {
  "use strict";
  var ID = "exposure";
  var MAX_ROWS = 12;

  /* 12345 as "12,345"; anything that is not a number as "n/a". */
  function fmt(n) {
    if (typeof n !== "number" || !isFinite(n)) return "n/a";
    return String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }
  /* A share as a percentage: one decimal below 10 percent, so a small share does not read as none. */
  function pct(v) {
    if (typeof v !== "number" || !isFinite(v)) return "n/a";
    var p = v * 100;
    return (p > 0 && p < 10 ? (Math.round(p * 10) / 10).toFixed(1) : String(Math.round(p))) + "%";
  }
  function num(v) { return typeof v === "number" && isFinite(v) ? v : 0; }

  function countryLabel(iso, api) {
    if (iso === "WLD") return "Worldwide distribution";
    var name = api && typeof api.countryName === "function" ? api.countryName(iso) : null;
    return name || iso || "";
  }

  /* The country rows the table shows: most placements first (then by code, so ties keep one order), at most
     MAX_ROWS, and never CHN. */
  function countryRows(data) {
    return ((data && data.by_country) || []).filter(function (r) { return r && r.id && r.id !== "CHN" && num(r.placements) > 0; })
      .slice().sort(function (a, b) { return num(b.placements) - num(a.placements) || (String(a.id) < String(b.id) ? -1 : String(a.id) > String(b.id) ? 1 : 0); })
      .slice(0, MAX_ROWS);
  }

  function leadLine(block, data, esc) {
    var n = data.newsrooms || {}, w = data.window || {};
    var span = w.first && w.last ? (w.first === w.last ? " on " + esc(w.first) : " from " + esc(w.first) + " to " + esc(w.last)) : "";
    return '<p class="m-exp-lead">' + M.statusChip(block) + ' Of <strong>' + fmt(n.placements) + '</strong> state origin placements at newsrooms' + span +
      ', <strong>' + fmt(n.release_only) + '</strong> (' + pct(n.share_release_only) + ') were found only in a press release, sponsored or partner section, and <strong>' +
      fmt(n.news_feed) + '</strong> came through a general news feed' + esc(M.provisionalSuffix(block)) + '.</p>';
  }

  function wireLine(data) {
    var wires = num((data.release_kinds || {}).distribution_wire);
    if (!wires) return "";
    return '<p class="m-exp-note">The ' + fmt(wires) + ' placement' + (wires === 1 ? "" : "s") + ' on distribution wires ' +
      (wires === 1 ? "is a release" : "are releases") + ' by definition and ' + (wires === 1 ? "is" : "are") + ' counted apart from the newsrooms.</p>';
  }

  function homepageLine(data, esc) {
    var h = data.homepage || {}, n = data.newsrooms || {};
    if (!num(h.reads_ok)) {
      return '<p class="m-exp-note">Homepage reads have not started yet, so nothing here says yet whether a homepage linked to these items.</p>';
    }
    var since = h.first ? String(h.first).slice(0, 10) : null;
    var after = num(h.check_after_hours) || 12;
    if (!num(n.checked)) {
      return '<p class="m-exp-note">Homepages have been read' + (since ? ' since ' + esc(since) : '') + ', but no newsroom placement was discovered within ' +
        fmt(after) + ' hours of a read of its outlet\'s homepage yet.</p>';
    }
    var line = '<p class="m-exp-note">Homepages have been read about once a run' + (since ? ' since ' + esc(since) : '') + '. Of <strong>' + fmt(n.checked) +
      '</strong> newsroom placements whose outlet\'s homepage was read within ' + fmt(after) + ' hours of their discovery, <strong>' + fmt(n.on_homepage) +
      '</strong> (' + pct(n.share_on_homepage) + ') were linked from it at least once';
    if (num(n.release_only_checked)) {
      line += '; of the ' + fmt(n.release_only_checked) + ' among them found only in a release section, ' + fmt(n.release_only_on_homepage) + ' were';
    }
    return line + '.</p>';
  }

  function render(block, data, api) {
    var slot = api && api.slot;
    if (!slot) return;
    if (!M.hideIfOff(slot, block)) { slot.innerHTML = ""; return; }
    if (M.isWithheld(block) || !data || !data.newsrooms || !num((data.totals || {}).placements)) {
      slot.innerHTML = M.emptyState(block, data, "exposure measure");
      return;
    }
    var esc = M.esc, rows = countryRows(data), reads = num((data.homepage || {}).reads_ok) > 0;
    var html = '<div class="m-panel m-exp">' + leadLine(block, data, esc) + wireLine(data) + homepageLine(data, esc);
    if (rows.length) {
      html += '<div class="m-table-wrap"><table class="m-table m-exp-table"><thead><tr><th>Country</th><th class="num">Placements</th>' +
        '<th class="num">General news feed</th><th class="num">Release section only</th>' +
        (reads ? '<th class="num">Homepage read</th><th class="num">On the homepage</th>' : '') + '</tr></thead><tbody>';
      rows.forEach(function (r) {
        html += '<tr><td>' + esc(countryLabel(r.id, api)) + '</td><td class="num">' + fmt(r.placements) + '</td><td class="num">' + fmt(r.news_feed) +
          '</td><td class="num">' + fmt(r.release_only) + '</td>' +
          (reads ? '<td class="num">' + fmt(r.checked) + '</td><td class="num">' + fmt(r.on_homepage) + '</td>' : '') + '</tr>';
      });
      html += '</tbody></table></div>';
    }
    html += '<p class="m-exp-foot">Where an item was first found, and whether a homepage linked to it, not how many people read it. ' +
      'Runs come about once an hour, so a link that stayed on a homepage for less than an hour between two reads is missed and the homepage figures are lower bounds. ' +
      'No figure on the map changes.</p>';
    html += '</div>';
    slot.innerHTML = html;
  }

  var spec = {render: render};
  if (M) M.register(ID, spec);
  return {ID: ID, render: render, fmt: fmt, pct: pct, countryRows: countryRows, MAX_ROWS: MAX_ROWS};
}));
