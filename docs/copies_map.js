/* Same text, many countries: the copies world map, from data/copies_map.json (pipeline/copies_map.py).
   Pick one of the most widely copied state texts in the side list and the map shades the countries whose
   outlets ran it, by number of outlets or by days after the first copy; the tooltip and the table under the
   map list the outlets, their days and, while the alteration measure is provisional or checked, the
   alteration band of each scored copy. "All texts" shades each country by how many shared state texts
   (run in two or more countries) its outlets ran. Display only: no count, share or label reads it.

   The pure helpers (no DOM, no d3) are exported for Node (tests/smoke_copies_map.js). In the browser,
   app.js calls mount({topo, numToIso, names, monitored, projection}) once its data has loaded, passing
   the main map's Robinson projection; the side file is fetched here, quietly, and the section stays
   hidden when it is missing or empty. */
(function (root, factory) {
  var mod = factory();
  if (typeof module === "object" && module.exports) module.exports = mod;
  else root.TrackerCopiesMap = mod;
}(typeof self !== "undefined" ? self : this, function () {
  "use strict";
  var WLD = "WLD";
  var SCOPES = {last_30d: "Last 30 complete days", all_time: "All time"};
  var BAND_TEXT = {verbatim: "verbatim", light: "light changes", moderate: "moderate changes", substantial: "substantial changes"};
  /* Legend tiers: [low, high] inclusive, high null for open ended. */
  var TIERS = {
    outlets: [[1, 1, "1 outlet"], [2, 2, "2 outlets"], [3, 4, "3 to 4"], [5, null, "5 or more"]],
    days: [[0, 0, "same day"], [1, 1, "1 day after"], [2, 3, "2 to 3 days"], [4, 7, "4 to 7 days"], [8, null, "8 days or more"]],
    texts: [[1, 1, "1 text"], [2, 3, "2 to 3"], [4, 9, "4 to 9"], [10, 24, "10 to 24"], [25, null, "25 or more"]]
  };
  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  function esc(s) {
    return String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, function (c) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;"}[c];
    });
  }
  function num(n) { return String(Math.round(n || 0)).replace(/\B(?=(\d{3})+(?!\d))/g, ","); }
  function plural(n, one, many) { return num(n) + " " + (n === 1 ? one : (many || one + "s")); }
  function fmtDate(day) {
    var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(day || ""));
    return m ? parseInt(m[3], 10) + " " + MONTHS[parseInt(m[2], 10) - 1] + " " + m[1] : "";
  }
  function dayNumber(day) {
    var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(day || ""));
    return m ? Date.UTC(+m[1], +m[2] - 1, +m[3]) / 864e5 : NaN;
  }
  function daysBetween(a, b) { var x = dayNumber(a), y = dayNumber(b); return isNaN(x) || isNaN(y) ? null : Math.round(y - x); }
  /* The last day of a window given as {start, end} with end exclusive. */
  function windowText(win) {
    if (!win || !win.start || !win.end) return "";
    var last = new Date((dayNumber(win.end) - 1) * 864e5).toISOString().slice(0, 10);
    return fmtDate(win.start) + " to " + fmtDate(last);
  }

  function inScope(day, scope, data) {
    var w = data && data.window;
    if (scope !== "last_30d") return true;
    return !!(w && day >= w.start && day < w.end);
  }
  /* A text's placements in the scope. */
  function scopeEntries(text, scope, data) {
    var ps = text && text.placements;
    return (Array.isArray(ps) ? ps : []).filter(function (p) { return p && inScope(p.d, scope, data); });
  }
  /* {countries, outlets} as the export ranks them: the worldwide stratum is never a country. */
  function counts(entries) {
    var c = {}, o = {};
    entries.forEach(function (p) { if (p.c !== WLD) c[p.c] = 1; o[p.o + "|" + p.c] = 1; });
    return {countries: Object.keys(c).length, outlets: Object.keys(o).length};
  }
  /* Per country rows for one text: [{iso, outlets: [{o, d, b, after}], n, first, after}], first copy first. */
  function byCountry(text, scope, data) {
    var first = text && text.first_seen, acc = {};
    scopeEntries(text, scope, data).forEach(function (p) {
      var r = acc[p.c] || (acc[p.c] = {iso: p.c, outlets: [], n: 0, first: null});
      r.outlets.push({o: p.o, d: p.d, b: p.b || null, after: daysBetween(first, p.d)});
      r.n += 1;
      if (!r.first || p.d < r.first) r.first = p.d;
    });
    return Object.keys(acc).map(function (k) {
      var r = acc[k];
      r.outlets.sort(function (x, y) { return x.d < y.d ? -1 : x.d > y.d ? 1 : String(x.o).localeCompare(String(y.o)); });
      r.after = daysBetween(first, r.first);
      return r;
    }).sort(function (x, y) { return x.first < y.first ? -1 : x.first > y.first ? 1 : y.n - x.n || String(x.iso).localeCompare(String(y.iso)); });
  }
  function tierIndex(v, mode) {
    var t = TIERS[mode] || [];
    if (v === null || v === undefined || isNaN(v)) return -1;
    for (var i = 0; i < t.length; i++) if (v >= t[i][0] && (t[i][1] === null || v <= t[i][1])) return i;
    return -1;
  }
  /* The value a country is shaded by in the one text view. */
  function shadeValue(row, mode) { return mode === "days" ? row.after : row.n; }
  function agencyName(data, id) { return ((data && data.agencies) || {})[id] || id || "Not named"; }
  function outletName(data, id) { var o = ((data && data.outlets) || {})[id]; return (o && o.name) || id; }
  /* The ranked texts of a scope that the file describes. */
  function ranked(data, scope) {
    var s = data && data.scopes && data.scopes[scope];
    return (s && Array.isArray(s.ranked) ? s.ranked : []).filter(function (r) { return r && data.texts && data.texts[r.id]; });
  }
  function textTitle(data, id) {
    var t = (data.texts || {})[id] || {};
    if (t.headline) return t.headline;
    return agencyName(data, t.agency) + " text first seen " + fmtDate(t.first_seen);
  }
  function bandsShown(data) { return !!(data && data.alteration && data.alteration.shown); }
  function provisional(data) { return !!(data && data.alteration && data.alteration.status === "provisional"); }
  /* One outlet line for the tooltip and the table: "Name, 3 Oct (2 days after), light changes". */
  function outletLine(data, p, countryName) {
    var s = outletName(data, p.o) + ", " + fmtDate(p.d);
    if (p.after) s += " (" + plural(p.after, "day") + " after the first copy)";
    if (bandsShown(data) && p.b) s += ", " + (BAND_TEXT[p.b] || p.b);
    return s;
  }
  /* The tooltip lines for a country under one text. */
  function tipLines(data, row, name) {
    var lines = [name + ": " + plural(row.n, "outlet") + (row.after === 0 ? ", on the day of the first copy" :
      row.after ? ", first " + plural(row.after, "day") + " after the first copy" : "")];
    row.outlets.forEach(function (p) { lines.push(outletLine(data, p)); });
    if (bandsShown(data) && row.outlets.some(function (p) { return p.b; })) {
      lines.push("Alteration bands" + (provisional(data) ? " are provisional" : " checked by hand") + ": the share of new words against the original.");
    }
    return lines;
  }
  /* The all texts view: {iso: {texts, placements}} of a scope, without the worldwide stratum. */
  function summary(data, scope) {
    var c = (data && data.scopes && data.scopes[scope] && data.scopes[scope].countries) || {}, out = {};
    Object.keys(c).forEach(function (k) { if (k !== WLD) out[k] = c[k]; });
    return out;
  }
  function summaryRows(data, scope) {
    var s = summary(data, scope);
    return Object.keys(s).map(function (k) { return {iso: k, texts: s[k].texts, placements: s[k].placements}; })
      .sort(function (x, y) { return y.texts - x.texts || y.placements - x.placements || x.iso.localeCompare(y.iso); });
  }
  /* Countries in isos the basemap has no outline for (Singapore, Bahrain and other small states at 110m),
     never the worldwide stratum; [] before the map is drawn. */
  function offMap(isos, drawn) {
    if (!drawn || !Object.keys(drawn).length) return [];
    return (isos || []).filter(function (k) { return k !== WLD && !drawn[k]; }).sort();
  }
  /* Rank numbers that state ties: texts with the same countries and outlets share a rank (1, 2, 2, 4). */
  function rankNumbers(list) {
    var out = [];
    (list || []).forEach(function (r, i) {
      var p = list[i - 1];
      out.push(p && p.countries === r.countries && p.outlets === r.outlets ? out[i - 1] : i + 1);
    });
    return out;
  }
  /* The count line: shared texts of all texts in the window, how many are listed, and ties at the cut. */
  function countText(sc, listed) {
    var shared = (sc && sc.shared_texts) || 0, tied = (sc && sc.tied_beyond) || 0;
    var s = plural(shared, "state text") + " ran in two or more countries" + (sc && sc.texts ? ", of " + num(sc.texts) + " state texts in all" : "") + ".";
    if (shared > listed) {
      s += " The " + num(listed) + " run in the most countries are listed";
      s += tied ? "; " + plural(tied, "more text ties", "more texts tie") + " with the last one listed and " + (tied === 1 ? "is" : "are") + " not shown." : ".";
    }
    return s;
  }
  /* The side list entry's line under the title. */
  function listMeta(data, r) {
    var t = data.texts[r.id] || {};
    return agencyName(data, t.agency) + " · first seen " + fmtDate(t.first_seen) + " · " +
      plural(r.countries, "country", "countries") + ", " + plural(r.outlets, "outlet");
  }

  /* ---------------------------------------------------------------- browser */
  var S = {data: null, scope: "last_30d", view: "one", mode: "outlets", selected: null, opts: null, features: null, path: null};
  var RAMP = ["#f7d3cb", "#ef8a73", "#d23a2e", "#9e0f17", "#3d0306"];
  var NOT_RUN = "#23262a", UNMONITORED = "url(#cm-hatch)";

  function el(id) { return typeof document !== "undefined" ? document.getElementById(id) : null; }
  function cname(iso) { var n = S.opts && S.opts.names; return iso === WLD ? "Worldwide distribution" : (n && n[iso]) || iso; }
  function colorFor(i, mode) {
    var t = TIERS[mode] || [], n = t.length;
    if (i < 0) return null;
    /* Fewer days after the first copy is the darker shade, as more outlets is. */
    var k = mode === "days" ? n - 1 - i : i;
    return RAMP[Math.round(k * (RAMP.length - 1) / Math.max(1, n - 1))];
  }

  function mount(opts) {
    S.opts = opts || {};
    var sec = el("copies-map");
    if (!sec || typeof fetch !== "function") return;
    fetch("data/copies_map.json", {cache: "no-cache"}).then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (data) {
        S.data = data;
        /* A failed export writes the empty structure without generated_at: say nothing rather than "none". */
        if (!data || typeof data !== "object" || !data.generated_at) throw new Error("no copies map in this export");
        if (!data.scopes || !(ranked(data, "all_time").length || ranked(data, "last_30d").length)) {
          sec.hidden = false;
          el("cm-body").hidden = true;
          el("cm-empty").hidden = false;
          el("cm-empty").textContent = "No counted state text has run at monitored outlets in two or more countries yet, so there is nothing to draw.";
          return;
        }
        if (!ranked(data, "last_30d").length) S.scope = "all_time";
        sec.hidden = false;
        setup();
        render();
      }).catch(function (e) {
        /* Missing, malformed or unreadable: the section stays out of the page, never half drawn, and so
           does its link in the page navigation. */
        sec.hidden = true;
        Array.prototype.forEach.call(document.querySelectorAll('a[href="#copies-map"]'), function (a) { a.hidden = true; });
        if (typeof console !== "undefined") console.warn("copies map not shown:", e && e.message ? e.message : e);
      });
  }

  function setup() {
    var svg = d3.select("#cm-map"), o = S.opts;
    svg.selectAll("*").remove();
    var defs = svg.append("defs");
    var pat = defs.append("pattern").attr("id", "cm-hatch").attr("patternUnits", "userSpaceOnUse").attr("width", 6).attr("height", 6).attr("patternTransform", "rotate(45)");
    pat.append("rect").attr("width", 6).attr("height", 6).attr("fill", "#141618");
    pat.append("line").attr("x1", 0).attr("y1", 0).attr("x2", 0).attr("y2", 6).attr("stroke", "#2b2e33").attr("stroke-width", 1.4);
    var projection = (o.projection ? o.projection() : d3.geoNaturalEarth1());
    projection.fitSize([960, 500], {type: "Sphere"});
    S.path = d3.geoPath(projection);
    svg.append("path").attr("class", "sphere").attr("d", S.path({type: "Sphere"}));
    var g = svg.append("g");
    if (o.topo && window.topojson) {
      var features = topojson.feature(o.topo, o.topo.objects.countries).features, byName = {"Kosovo": "XKX"};
      features.forEach(function (f) { f.iso = (o.numToIso || {})[String(parseInt(f.id, 10))] || byName[(f.properties && f.properties.name) || ""] || null; });
      S.features = features;
      S.drawn = {};
      features.forEach(function (f) { if (f.iso) S.drawn[f.iso] = true; });
      g.selectAll("path").data(features).enter().append("path").attr("class", "country").attr("d", S.path)
        .on("mousemove", function (ev, f) { showTip(ev, f.iso); })
        .on("mouseleave", hideTip)
        .on("click", function (ev, f) { showTip(ev, f.iso, true); });
    }
    bindSegment("cm-scope", "data-scope", function (v) { S.scope = v; S.selected = null; render(); });
    bindSegment("cm-view", "data-view", function (v) { S.view = v; render(); });
    bindSegment("cm-mode", "data-mode", function (v) { S.mode = v; render(); });
  }
  function bindSegment(id, attr, fn) {
    var g = el(id);
    if (!g) return;
    Array.prototype.forEach.call(g.querySelectorAll("button"), function (b) {
      b.addEventListener("click", function () { fn(b.getAttribute(attr)); });
    });
  }
  function syncSegment(id, attr, v) {
    var g = el(id);
    if (!g) return;
    Array.prototype.forEach.call(g.querySelectorAll("button"), function (b) {
      var on = b.getAttribute(attr) === v;
      b.classList.toggle("active", on);
      b.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  function render() {
    var data = S.data, list = ranked(data, S.scope);
    if (S.scope === "last_30d" && !list.length) list = [];
    if (!S.selected || !list.some(function (r) { return r.id === S.selected; })) S.selected = list.length ? list[0].id : null;
    syncSegment("cm-scope", "data-scope", S.scope);
    syncSegment("cm-view", "data-view", S.view);
    syncSegment("cm-mode", "data-mode", S.mode);
    el("cm-mode-wrap").hidden = S.view !== "one";
    var sc = (data.scopes || {})[S.scope] || {};
    el("cm-window").textContent = S.scope === "last_30d" ? windowText(data.window) : "Since collection began";
    el("cm-count").textContent = countText(sc, list.length);
    renderList(list);
    renderMap();
    renderTable();
    /* One short line for screen readers, not the whole table, each time the view changes. */
    var st = el("cm-status");
    if (st) {
      var r = S.selected && list.filter(function (x) { return x.id === S.selected; })[0];
      st.textContent = S.view === "one" && r ? "Showing " + textTitle(data, r.id) + ": " + plural(r.countries, "country", "countries") + ", " + plural(r.outlets, "outlet") + ". " + SCOPES[S.scope] + "."
        : "Showing shared state texts by country. " + SCOPES[S.scope] + ".";
    }
  }
  function renderList(list) {
    var ol = el("cm-list"), data = S.data;
    if (!list.length) { ol.innerHTML = '<li class="cm-none">No state text ran in two or more countries in this window.</li>'; return; }
    var ranks = rankNumbers(list);
    ol.innerHTML = list.map(function (r, i) {
      var t = data.texts[r.id] || {}, on = r.id === S.selected;
      var tie = (ranks[i - 1] === ranks[i]) || (ranks[i + 1] === ranks[i]);
      return '<li><button type="button" class="cm-item' + (on ? " active" : "") + '" data-id="' + esc(r.id) + '" aria-pressed="' + on + '">' +
        '<span class="cm-rank"' + (tie ? ' title="Tied on countries and outlets"' : "") + '>' + ranks[i] + (tie ? '<span class="cm-sr"> (tied)</span>' : "") +
        '</span><span class="cm-text"><span class="cm-title" dir="auto"' +
        (t.headline && t.headline_lang ? ' lang="' + esc(t.headline_lang) + '"' : "") + '>' + esc(textTitle(data, r.id)) + '</span>' +
        '<span class="cm-meta">' + esc(listMeta(data, r)) + '</span></span></button></li>';
    }).join("");
    Array.prototype.forEach.call(ol.querySelectorAll("button.cm-item"), function (b) {
      b.addEventListener("click", function () {
        var id = b.getAttribute("data-id");
        S.selected = id; S.view = "one"; render();
        /* The list is drawn again: keep the keyboard on the text just picked. */
        var again = Array.prototype.filter.call(ol.querySelectorAll("button.cm-item"), function (x) { return x.getAttribute("data-id") === id; })[0];
        if (again && typeof again.focus === "function") again.focus();
      });
    });
  }
  function rowsByIso() {
    var out = {};
    if (S.view === "one" && S.selected) byCountry(S.data.texts[S.selected], S.scope, S.data).forEach(function (r) { out[r.iso] = r; });
    return out;
  }
  function renderMap() {
    var mon = S.opts.monitored || null, rows = rowsByIso(), sum = summary(S.data, S.scope), mode = S.view === "one" ? S.mode : "texts";
    d3.select("#cm-map").selectAll("path.country").attr("fill", function (f) {
      var v = S.view === "one" ? (rows[f.iso] ? shadeValue(rows[f.iso], S.mode) : null) : (sum[f.iso] ? sum[f.iso].texts : null);
      var c = v === null ? null : colorFor(tierIndex(v, mode), mode);
      if (c) return c;
      return mon && !mon[f.iso] ? UNMONITORED : NOT_RUN;
    }).classed("cm-on", function (f) { return S.view === "one" ? !!rows[f.iso] : !!sum[f.iso]; });
    var label = S.view === "one" && S.selected ? "Map of the countries whose outlets ran: " + textTitle(S.data, S.selected) + ". The table below lists them."
      : "Map of how many shared state texts each country's outlets ran. The table below lists them.";
    d3.select("#cm-map").attr("aria-label", label);
    var lg = TIERS[mode].map(function (t, i) {
      return '<span class="cm-key"><i style="background:' + colorFor(i, mode) + '"></i>' + esc(t[2]) + '</span>';
    }).join("");
    lg += '<span class="cm-key"><i style="background:' + NOT_RUN + '"></i>' + (S.view === "one" ? "did not run it" : "none") + '</span>';
    if (mon) lg += '<span class="cm-key"><i class="cm-hatch-key"></i>not monitored</span>';
    var wld = S.view === "one" ? rows[WLD] : null;
    var off = offMap(Object.keys(S.view === "one" ? rows : sum), S.drawn).map(cname);
    el("cm-legend").innerHTML = '<div class="cm-keys">' + lg + '</div>' +
      (off.length ? '<p class="cm-wld">Too small to draw at this scale, listed in the table: ' + esc(off.join(", ")) + '.</p>' : "") +
      (wld ? '<p class="cm-wld">Also on ' + esc(plural(wld.n, "release wire")) + ' (worldwide distribution, never drawn): ' +
        esc(wld.outlets.map(function (p) { return outletName(S.data, p.o); }).join(", ")) + '.</p>' : "");
  }
  function renderTable() {
    var box = el("cm-detail"), data = S.data;
    if (S.view !== "one") {
      var srows = summaryRows(data, S.scope);
      box.innerHTML = '<h3 class="cm-h3">Shared state texts by country, ' + esc(SCOPES[S.scope].toLowerCase()) + '</h3>' +
        (srows.length ? '<table class="cm-table"><thead><tr><th scope="col">Country</th><th scope="col">Shared texts</th><th scope="col">Outlet copies</th></tr></thead><tbody>' +
          srows.map(function (r) { return '<tr><th scope="row">' + esc(cname(r.iso)) + '</th><td>' + num(r.texts) + '</td><td>' + num(r.placements) + '</td></tr>'; }).join("") +
          '</tbody></table>' : '<p class="cm-none">No country ran a shared state text in this window.</p>');
      return;
    }
    if (!S.selected) { box.innerHTML = ""; return; }
    var t = data.texts[S.selected] || {}, rows = byCountry(t, S.scope, data);
    /* The alteration column only where a copy of this text was scored (paired with an original). */
    var scored = rows.some(function (r) { return r.outlets.some(function (p) { return p.b; }); });
    var shown = bandsShown(data) && scored;
    var chip = shown && provisional(data) ? ' <span class="m-chip m-chip-provisional" title="No hand check of the alteration measure has passed yet.">Provisional</span>' : "";
    var orig = t.original ? (t.original.kind === "reference" ? "Paired with the state outlet's own page" : "Paired with an earlier monitored copy") +
      (t.original.published ? ", published " + fmtDate(t.original.published) : "") + ". " : "";
    var note = shown ? "Alteration band: the share of new words in the copy against the original" + (provisional(data) ? ", provisional." : ".")
      : !bandsShown(data) ? "Alteration bands are not shown while the alteration measure is " + ((data.alteration && data.alteration.status) || "off") + "."
      : "No copy of this text was paired with an original and scored for alteration" + (S.scope === "last_30d" ? " in this window." : ".");
    box.innerHTML = '<h3 class="cm-h3" dir="auto"' + (t.headline && t.headline_lang ? ' lang="' + esc(t.headline_lang) + '"' : "") + '>' + esc(textTitle(data, S.selected)) + '</h3>' +
      '<p class="cm-sub">' + esc(agencyName(data, t.agency)) + " text, first seen " + esc(fmtDate(t.first_seen)) + ". " + esc(orig) + esc(note) + '</p>' +
      '<table class="cm-table"><thead><tr><th scope="col">Country</th><th scope="col">Outlet and day</th>' +
      (shown ? '<th scope="col">Alteration' + chip + '</th>' : "") + '</tr></thead><tbody>' +
      rows.map(function (r) {
        return r.outlets.map(function (p, i) {
          return '<tr>' + (i === 0 ? '<th scope="row" rowspan="' + r.outlets.length + '">' + esc(cname(r.iso)) + '</th>' : "") +
            '<td>' + esc(outletName(data, p.o)) + ', ' + esc(fmtDate(p.d)) + (p.after ? ' <span class="cm-after">+' + plural(p.after, "day") + '</span>' : "") + '</td>' +
            (shown ? '<td>' + esc(p.b ? BAND_TEXT[p.b] || p.b : "not scored") + '</td>' : "") + '</tr>';
        }).join("");
      }).join("") + '</tbody></table>';
  }
  function showTip(ev, iso, sticky) {
    var tip = el("cm-tip"), sec = el("copies-map");
    if (!tip || !iso) return hideTip();
    var html;
    if (S.view === "one" && S.selected) {
      var r = rowsByIso()[iso];
      html = r ? tipLines(S.data, r, cname(iso)).map(function (l, i) { return i ? '<div>' + esc(l) + '</div>' : '<div class="t-name">' + esc(l) + '</div>'; }).join("")
        : '<div class="t-name">' + esc(cname(iso)) + '</div><div>' + ((S.opts.monitored && !S.opts.monitored[iso]) ? "Not monitored." : "No monitored outlet ran this text.") + '</div>';
    } else {
      var s = summary(S.data, S.scope)[iso];
      html = '<div class="t-name">' + esc(cname(iso)) + '</div><div>' + (s ? plural(s.texts, "shared state text") + ", " + plural(s.placements, "outlet copy", "outlet copies")
        : ((S.opts.monitored && !S.opts.monitored[iso]) ? "Not monitored." : "No shared state text.")) + '</div>';
    }
    tip.innerHTML = html;
    tip.style.display = "block";
    var box = sec.getBoundingClientRect(), x = ev.clientX - box.left + 14, y = ev.clientY - box.top + 14;
    var w = tip.offsetWidth || 240;
    if (x + w > box.width - 8) x = Math.max(8, box.width - w - 8);
    tip.style.left = x + "px";
    tip.style.top = y + "px";
  }
  function hideTip() { var tip = el("cm-tip"); if (tip) tip.style.display = "none"; }

  return {
    WLD: WLD, TIERS: TIERS, SCOPES: SCOPES, esc: esc, fmtDate: fmtDate, daysBetween: daysBetween, windowText: windowText,
    inScope: inScope, scopeEntries: scopeEntries, counts: counts, byCountry: byCountry, tierIndex: tierIndex,
    shadeValue: shadeValue, ranked: ranked, textTitle: textTitle, outletLine: outletLine, tipLines: tipLines,
    summary: summary, summaryRows: summaryRows, offMap: offMap, listMeta: listMeta, bandsShown: bandsShown, provisional: provisional,
    rankNumbers: rankNumbers, countText: countText,
    colorFor: colorFor, mount: mount
  };
}));
