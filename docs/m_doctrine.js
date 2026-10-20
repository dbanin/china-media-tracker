/* Doctrine terms (measure doctrine): how often China coverage uses the Chinese state's own set phrases,
   by kind of article, from data/doctrine.json (pipeline/doctrine.py). Fills #doctrine-body with the
   status chip, the lead figure (the share of independent journalism using the vocabulary), the term
   families ranked by their rate per 1,000 China articles in the chosen window, and one weekly timeline
   for a chosen family. Families common beyond Chinese state usage are muted; a family withheld by its
   hand check shows no figure. Windows follow the map: they end on the last complete day, the day the
   export ran being partial. A redraw replaces the section's markup, so the control the reader used
   (a window button or the family list, each with its own id) gets the keyboard focus back afterwards.
   Loaded in the browser after measures.js, and in Node or jsc with module.exports. */
(function (root, factory) {
  var M = root && root.TrackerMeasures;
  if (!M && typeof require === "function") M = require("./measures.js");
  var mod = factory(M);
  if (typeof module === "object" && module.exports) module.exports = mod;
}(typeof self !== "undefined" ? self : this, function (M) {
  "use strict";
  var ID = "doctrine";
  var MIN_BASE = 20;        /* fewer articles of a kind than this in the window: no rate */
  var WINDOWS = [{v: "1", label: "That day"}, {v: "7", label: "7 days"}, {v: "30", label: "30 days"}, {v: "all", label: "Total"}];
  var KINDS = [{i: 0, key: "china", label: "All China coverage"}, {i: 1, key: "a", label: "State origin"},
               {i: 2, key: "b", label: "Unchecked state sourcing"}, {i: 3, key: "c", label: "Independent journalism"}];
  var view = {windowDays: "30", family: null, last: null};

  function esc(s) { return M.esc(s); }

  /* The day before or after an ISO date, in UTC. */
  function shiftDay(d, n) {
    var t = Date.UTC(+d.slice(0, 4), +d.slice(5, 7) - 1, +d.slice(8, 10)) + n * 86400000;
    return new Date(t).toISOString().slice(0, 10);
  }

  /* Every day the file has, oldest first, from the first discovery on. */
  function allDays(data, meta) {
    var seen = {}, out = [];
    [data && data.totals, data && data.days].forEach(function (m) {
      Object.keys(m || {}).forEach(function (d) { if (!seen[d]) { seen[d] = true; out.push(d); } });
    });
    out.sort();
    var first = meta && meta.first_discovered;
    return out.filter(function (d) { return !first || d >= first; });
  }

  /* The last day the export saw in full (app.js lastCompleteDay): the export day itself is partial. */
  function lastCompleteDay(data, meta) {
    var days = allDays(data, meta);
    if (!days.length) return null;
    var gen = meta && meta.generated_at ? String(meta.generated_at).slice(0, 10) : null;
    var i = days.length - 1;
    if (i > 0 && gen && days[i] >= gen) i -= 1;
    return days[i];
  }

  /* The days of a window ending on end: w days, or every day to end for "all". */
  function windowDays(data, meta, end, w) {
    if (!end) return [];
    var start = w === "all" ? "" : shiftDay(end, -(Number(w) - 1));
    return allDays(data, meta).filter(function (d) { return d <= end && d >= start; });
  }

  /* Totals and family cells summed over the days and every country, the wires included. */
  function aggregate(data, days) {
    var tot = [0, 0, 0, 0, 0, 0, 0, 0], fams = {};
    (days || []).forEach(function (d) {
      var t = (data.totals || {})[d] || {};
      Object.keys(t).forEach(function (iso) { (t[iso] || []).forEach(function (v, i) { if (i < 8) tot[i] += v || 0; }); });
      var c = (data.days || {})[d] || {};
      Object.keys(c).forEach(function (iso) {
        var per = c[iso] || {};
        Object.keys(per).forEach(function (fid) {
          var f = fams[fid] || (fams[fid] = [0, 0, 0, 0, 0]);
          (per[fid] || []).forEach(function (v, i) { if (i < 5) f[i] += v || 0; });
        });
      });
    });
    return {totals: tot, fams: fams};
  }

  /* Per 1,000 articles, or null when the base is too small to say anything. */
  function rate(n, base) { return base >= MIN_BASE ? (n || 0) * 1000 / base : null; }
  function fmtRate(r) { return r === null ? "n/a" : r >= 100 ? String(Math.round(r)) : (Math.round(r * 10) / 10).toFixed(1); }
  function pct(n, base) { return base >= MIN_BASE ? (Math.round(n * 1000 / base) / 10).toFixed(1) + "%" : "n/a"; }

  /* Unchecked state sourcing is shown only when the site shows it elsewhere (app.js relayFor). */
  function relayVisible(meta) {
    var m = meta || {};
    var measured = m.relay_measured !== undefined ? !!m.relay_measured : !!m.llm_calls_total;
    var publishable = m.relay_publishable !== undefined ? !!m.relay_publishable : !!(m.b_counts_settled && measured);
    return publishable || (measured && m.relay_provisional === true);
  }

  function familyStatus(f, block) {
    var s = (block && block.summary && block.summary.family_status) || {};
    return (f && f.status) || s[f && f.id] || "provisional";
  }

  /* Families ranked by their rate per 1,000 China articles: distinctive ones first, then the muted. */
  function ranked(data, agg, block) {
    var base = agg.totals[0];
    return (data.families || []).map(function (f) {
      var c = agg.fams[f.id] || [0, 0, 0, 0, 0];
      return {id: f.id, label: f.label, distinctive: f.distinctive !== false, status: familyStatus(f, block),
              cell: c, rate: rate(c[0], base)};
    }).sort(function (a, b) {
      if (a.distinctive !== b.distinctive) return a.distinctive ? -1 : 1;
      var ra = a.status === "withheld" ? -1 : (a.rate || 0), rb = b.status === "withheld" ? -1 : (b.rate || 0);
      return rb - ra || (b.cell[0] - a.cell[0]) || (a.label < b.label ? -1 : 1);
    });
  }

  /* Rolling seven day blocks ending on end, back to the first day: a family's rate per 1,000 China
     articles in each, oldest first. Blocks align on end, so none is partial. */
  function weekly(data, meta, fid, end) {
    var days = allDays(data, meta).filter(function (d) { return d <= end; });
    if (!days.length) return [];
    var out = [], stop = days[0], e = end;
    while (e >= stop) {
      var s = shiftDay(e, -6);
      var agg = aggregate(data, days.filter(function (d) { return d >= s && d <= e; }));
      var c = agg.fams[fid] || [0, 0, 0, 0, 0];
      out.unshift({start: s < stop ? stop : s, end: e, n: c[0], base: agg.totals[0], rate: rate(c[0], agg.totals[0])});
      e = shiftDay(s, -1);
    }
    return out;
  }

  function timelineSvg(weeks, label) {
    if (!weeks.length) return "";
    var w = 600, h = 120, pad = 4, max = 0;
    weeks.forEach(function (x) { if (x.rate !== null && x.rate > max) max = x.rate; });
    var bw = (w - 2) / weeks.length, bars = "";
    weeks.forEach(function (x, i) {
      var title = x.start + " to " + x.end + ": " + (x.rate === null ? "fewer than " + MIN_BASE + " China articles" : fmtRate(x.rate) + " per 1,000 China articles (" + x.n + " of " + x.base + ")");
      if (x.rate === null) {
        bars += '<rect class="dc-bar-na" x="' + (1 + i * bw + 1).toFixed(1) + '" y="' + (h - pad - 2) + '" width="' + Math.max(1, bw - 2).toFixed(1) + '" height="2"><title>' + esc(title) + '</title></rect>';
        return;
      }
      var bh = max > 0 ? (h - 2 * pad) * x.rate / max : 0;
      bars += '<rect class="dc-bar" x="' + (1 + i * bw + 1).toFixed(1) + '" y="' + (h - pad - bh).toFixed(1) + '" width="' + Math.max(1, bw - 2).toFixed(1) + '" height="' + Math.max(bh, x.rate > 0 ? 1 : 0).toFixed(1) + '"><title>' + esc(title) + '</title></rect>';
    });
    return '<svg class="dc-spark" viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none" role="img" aria-label="' + esc(label) + '">' +
      '<line class="dc-axis" x1="0" x2="' + w + '" y1="' + (h - pad) + '" y2="' + (h - pad) + '"></line>' + bars + '</svg>' +
      /* Labels sit outside the stretched drawing, so they keep their shape at any width. */
      '<div class="dc-ticks"><span>' + esc(weeks[0].start) + '</span><span>' + esc(max > 0 ? "peak " + fmtRate(max) + " per 1,000" : "no matches") +
      '</span><span>' + esc(weeks[weeks.length - 1].end) + '</span></div>';
  }

  function windowLabel(end, w) {
    if (w === "all") return "total to " + end;
    if (Number(w) === 1) return end;
    return Number(w) + " days ending " + end;
  }

  /* The id of the element in slot that has the focus, or null. */
  function focusedId(slot) {
    var d = typeof document !== "undefined" ? document : null, el = d && d.activeElement;
    if (!el || !el.id || !slot || typeof slot.contains !== "function" || !slot.contains(el)) return null;
    return el.id;
  }

  /* Give the focus back to the element with this id inside slot, after its markup was replaced. */
  function restoreFocus(slot, id) {
    if (!id || !slot) return false;
    var el = typeof slot.querySelector === "function" ? slot.querySelector("#" + id) : null;
    if (!el && typeof document !== "undefined" && document.getElementById) el = document.getElementById(id);
    if (!el || typeof el.focus !== "function") return false;
    try { el.focus({preventScroll: true}); } catch (e) { el.focus(); }
    return true;
  }

  function windowId(v) { return "dc-window-" + String(v).replace(/[^A-Za-z0-9_-]/g, ""); }

  function draw(focusId) {
    var a = view.last;
    if (!a) return;
    var block = a.block, data = a.data, api = a.api, slot = api.slot, meta = api.meta || {};
    var end = lastCompleteDay(data, meta);
    var days = windowDays(data, meta, end, view.windowDays);
    var agg = aggregate(data, days), t = agg.totals;
    var showB = relayVisible(meta);
    var rows = ranked(data, agg, block);
    var suffix = M.provisionalSuffix(block);
    var keep = focusId || focusedId(slot);
    var html = '<p class="dc-status">' + M.statusChip(block) + '</p>';
    html += '<div class="dc-controls control"><label id="dc-window-label">Window, ending on the last complete day</label><div class="segment window" role="group" aria-labelledby="dc-window-label">' +
      WINDOWS.map(function (x) {
        var on = x.v === view.windowDays;
        return '<button type="button" id="' + windowId(x.v) + '" data-dc-window="' + x.v + '" aria-pressed="' + (on ? "true" : "false") + '"' + (on ? ' class="active"' : '') + '>' + esc(x.label) + '</button>';
      }).join("") + '</div></div>';
    if (!t[0]) {
      html += '<p class="m-empty">No China coverage tagged with doctrine terms in ' + esc(windowLabel(end, view.windowDays)) + '.</p>';
      slot.innerHTML = html;
      restoreFocus(slot, keep);
      return;
    }
    html += '<div class="dc-lead"><span class="dc-lead-num">' + esc(pct(t[7], t[3])) + '</span><span class="dc-lead-text">of independent journalism about China used at least one of these phrases' + esc(suffix) +
      ', ' + esc(windowLabel(end, view.windowDays)) + ', against ' + esc(pct(t[5], t[1])) + ' of state origin' +
      (showB ? ' and ' + esc(pct(t[6], t[2])) + ' of unchecked state sourcing' : '') + '. Muted families are left out of this share.</span></div>';
    var kinds = KINDS.filter(function (k) { return k.i !== 2 || showB; });
    html += '<div class="m-table-wrap"><table class="m-table dc-table"><thead><tr><th scope="col">Family</th>' +
      kinds.map(function (k) { return '<th scope="col" class="num">' + esc(k.label) + '</th>'; }).join("") +
      '<th scope="col" class="num">Uses</th></tr></thead><tbody>';
    rows.forEach(function (r) {
      var cls = (r.distinctive ? "" : "dc-muted") + (r.status === "withheld" ? " dc-withheld" : "");
      var name = esc(r.label) + (r.distinctive ? "" : ' <span class="dc-note">common beyond state usage</span>') +
        (r.status === "checked" ? ' <span class="dc-note">checked</span>' : "");
      html += '<tr' + (cls.trim() ? ' class="' + cls.trim() + '"' : '') + '><th scope="row">' + name + '</th>';
      if (r.status === "withheld") {
        html += '<td class="num" colspan="' + (kinds.length + 1) + '">Withheld by its hand check</td></tr>';
        return;
      }
      kinds.forEach(function (k) {
        var v = rate(r.cell[k.i], t[k.i]);
        html += '<td class="num" title="' + esc(r.cell[k.i] + " of " + t[k.i] + " articles") + '">' + esc(fmtRate(v)) + '</td>';
      });
      html += '<td class="num">' + esc(r.cell[4]) + '</td></tr>';
    });
    html += '</tbody></table></div>';
    html += '<p class="dc-foot">Per 1,000 China articles of each kind in the window, counting only articles already tagged. A rate needs at least ' + MIN_BASE + ' articles of its kind.' +
      (showB ? '' : ' Unchecked state sourcing is not shown while its counts are withheld.') + tagNote(data) + '</p>';
    /* One weekly timeline for a chosen family. */
    var pickable = rows.filter(function (r) { return r.status !== "withheld"; });
    if (!view.family || !pickable.some(function (r) { return r.id === view.family; })) view.family = pickable.length ? pickable[0].id : null;
    if (view.family) {
      var fam = pickable.filter(function (r) { return r.id === view.family; })[0];
      html += '<div class="dc-week"><div class="control"><label for="dc-family">Weekly, for one family</label><select id="dc-family" data-dc-family>' +
        pickable.map(function (r) { return '<option value="' + esc(r.id) + '"' + (r.id === view.family ? ' selected' : '') + '>' + esc(r.label) + '</option>'; }).join("") +
        '</select></div>' + timelineSvg(weekly(data, meta, view.family, end), fam.label + " per 1,000 China articles, by week") +
        '<p class="dc-foot">' + esc(fam.label) + ' per 1,000 China articles in each seven day block ending ' + esc(end) + '. Hover a bar for its count.</p></div>';
    }
    slot.innerHTML = html;
    /* The family list may be gone (every family withheld): then the window button keeps the focus. */
    if (!restoreFocus(slot, keep) && keep === "dc-family") restoreFocus(slot, windowId(view.windowDays));
  }

  function tagNote(data) {
    var s = data.tagged_share, nb = data.tagged_without_body;
    var out = "";
    if (typeof s === "number" && s < 1) out += " " + (Math.round(s * 1000) / 10).toFixed(1) + "% of relevant articles are tagged so far.";
    if (typeof nb === "number" && nb > 0) out += " " + (Math.round(nb * 1000) / 10).toFixed(1) + "% of tags read only the headline and summary, as the body has not been read; those find fewer phrases.";
    return esc(out);
  }

  function onClick(e) {
    var t = e && e.target;
    while (t && t.getAttribute && !t.getAttribute("data-dc-window")) t = t.parentNode;
    if (!t || !t.getAttribute) return;
    view.windowDays = t.getAttribute("data-dc-window");
    draw(windowId(view.windowDays));
  }
  function onChange(e) {
    var t = e && e.target;
    if (!t || !t.getAttribute || !t.hasAttribute("data-dc-family")) return;
    view.family = t.value;
    draw("dc-family");
  }

  function render(block, data, api) {
    var slot = api && api.slot;
    if (!slot) return;
    if (!M.hideIfOff(slot, block)) { slot.innerHTML = ""; return; }
    if (M.isWithheld(block) || !data || !data.families || !(data.totals || data.days)) {
      slot.innerHTML = M.emptyState(block, data, "doctrine terms");
      return;
    }
    view.last = {block: block, data: data, api: api};
    slot.onclick = onClick;
    slot.onchange = onChange;
    draw();
  }

  var spec = {render: render};
  if (M) M.register(ID, spec);
  return {ID: ID, render: render, lastCompleteDay: lastCompleteDay, windowDays: windowDays, aggregate: aggregate,
          ranked: ranked, weekly: weekly, rate: rate, relayVisible: relayVisible, view: view,
          onClick: onClick, onChange: onChange, restoreFocus: restoreFocus, windowId: windowId};
}));
