/* Hosted travel disclosures (pipeline/hosted_travel.py). Renders data/hosted_travel.json into
   #hosted-travel: articles that disclose that the reporting trip, the stay or a training programme was
   paid for or hosted by a Chinese state entity, embassy, state media outlet or Chinese government
   programme, and apart from them the weaker class of disclosures that name no host. A flag marks a
   disclosed hosted trip. It is not state origin and is never counted as such. Provisional until the
   flagged lines are checked by hand.
   Loads its own data in the browser (no change to app.js), and exports pure helpers for Node. */
(function (root, factory) {
  var mod = factory();
  if (typeof module === "object" && module.exports) module.exports = mod;
  if (root) root.TrackerHostedTravel = mod;
}(typeof self !== "undefined" ? self : this, function () {
  "use strict";
  var ROWS = 20;
  var CLASS_LABELS = {state_host: "Chinese state host", unnamed_host: "host not named"};
  var BASIS_LABELS = {self: "names the article or its author", label: "disclosure note", tail: "closing line"};

  function esc(s) {
    return String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, function (c) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;"}[c];
    });
  }
  function safeUrl(u) { return /^https?:\/\/[^\s"'<>]+$/.test(String(u || "")) ? String(u) : ""; }
  function plural(n, word, words) { return n + " " + (n === 1 ? word : (words || word + "s")); }
  function classLabel(k) { return CLASS_LABELS[k] || String(k || "").replace(/_/g, " "); }
  function basisLabel(b) { return BASIS_LABELS[b] || String(b || "").replace(/_/g, " "); }
  function num(v) { var n = Number(v); return isFinite(n) && n > 0 ? n : 0; }

  /* Counts for the section. The file's own totals and tallies win, since the articles list is
     capped; without them the articles are counted. by_country is a list sorted by state host count,
     then unnamed host count, then code. */
  function summarise(json) {
    var j = json && typeof json === "object" ? json : {};
    var arts = (Array.isArray(j.articles) ? j.articles : []).filter(function (a) { return a && typeof a === "object"; });
    var tally = {};
    var fileTally = j.by_country && typeof j.by_country === "object" && !Array.isArray(j.by_country) ? j.by_country : null;
    if (fileTally && Object.keys(fileTally).length) {
      Object.keys(fileTally).forEach(function (iso) {
        var v = fileTally[iso] || {};
        tally[iso] = {state_host: num(v.state_host), unnamed_host: num(v.unnamed_host)};
      });
    } else {
      arts.forEach(function (a) {
        var iso = a.country || "unknown";
        tally[iso] = tally[iso] || {state_host: 0, unnamed_host: 0};
        if (a.klass === "state_host" || a.klass === "unnamed_host") tally[iso][a.klass] += 1;
      });
    }
    var byCountry = Object.keys(tally).map(function (iso) { return {iso: iso, state_host: tally[iso].state_host, unnamed_host: tally[iso].unnamed_host}; })
      .filter(function (r) { return r.state_host + r.unnamed_host > 0; });
    byCountry.sort(function (a, b) { return b.state_host - a.state_host || b.unnamed_host - a.unnamed_host || (a.iso < b.iso ? -1 : a.iso > b.iso ? 1 : 0); });
    var count = function (k) { return arts.filter(function (a) { return a.klass === k; }).length; };
    var st = j.state_host !== undefined && j.state_host !== null && isFinite(Number(j.state_host)) ? num(j.state_host) : count("state_host");
    var un = j.unnamed_host !== undefined && j.unnamed_host !== null && isFinite(Number(j.unnamed_host)) ? num(j.unnamed_host) : count("unnamed_host");
    return {
      state_host: st, unnamed_host: un, total: st + un,
      outlets: j.by_outlet && typeof j.by_outlet === "object" ? Object.keys(j.by_outlet).length : 0,
      by_country: byCountry,
      languages: Array.isArray(j.languages) ? j.languages.length : 0,
      patterns_version: j.patterns_version || null,
      status: j.status || "provisional"
    };
  }

  function intro(json, loaded) {
    var s = summarise(json);
    return "A flag here means an article discloses that the reporting trip, the stay or a training programme behind it was paid for or hosted, " +
      "and that the host named is a Chinese state entity, embassy, state media outlet or Chinese government programme. " +
      "It marks a disclosed hosted trip. It is not state origin: flagged articles keep whatever label they have and are never counted as state origin. " +
      "Disclosures that name no host are a weaker class, shown apart. Most hosted trips are never disclosed, so the absence of a flag proves nothing. " +
      "Provisional: the flagged lines have not yet been checked by hand." +
      (loaded && s.languages ? " Patterns in " + plural(s.languages, "language") + (s.patterns_version ? ", version " + s.patterns_version : "") + "." : "");
  }

  function emptyState() {
    return "No article in the monitored outlets has disclosed a trip or programme hosted by a Chinese state body since this check began.";
  }

  function sortKey(a) { return String((a && (a.published_at || a.discovered_at)) || ""); }

  /* The section body as HTML. opts: {countryName(iso), showAll}. */
  function bodyHtml(json, opts) {
    opts = opts || {};
    var name = typeof opts.countryName === "function" ? opts.countryName : function (iso) { return iso; };
    var s = summarise(json);
    var arts = (json && Array.isArray(json.articles) ? json.articles : []).filter(function (a) { return a && typeof a === "object"; });
    arts = arts.slice().sort(function (a, b) {
      var ka = (a.klass === "state_host" ? "1" : "0") + sortKey(a), kb = (b.klass === "state_host" ? "1" : "0") + sortKey(b);
      return ka < kb ? 1 : ka > kb ? -1 : 0;
    });
    var counts = '<p class="pl-line">' + plural(s.state_host, "article") + " with a Chinese state host, " +
      plural(s.unnamed_host, "article") + " with a host not named" + (s.outlets ? ", in " + plural(s.outlets, "outlet") : "") + ".</p>";
    if (!arts.length && !s.total) return counts + '<p class="pl-empty">' + esc(emptyState()) + "</p>";
    var line = s.by_country.map(function (r) {
      return esc(name(r.iso)) + " " + r.state_host + (r.unnamed_host ? " (" + r.unnamed_host + " unnamed)" : "");
    }).join(", ");
    var shown = opts.showAll ? arts : arts.slice(0, ROWS);
    var rows = shown.map(function (a) {
      var href = safeUrl(a.url), title = esc(a.title || "");
      var hosts = Array.isArray(a.hosts) ? a.hosts.join("; ") : "";
      var bases = Array.isArray(a.bases) ? a.bases.map(basisLabel).join("; ") : "";
      return '<tr><td class="pl-date">' + esc(sortKey(a).slice(0, 10)) + "</td>" +
        '<td class="pl-outlet">' + esc(a.outlet || a.outlet_id || "") + (title ? '<span class="pl-title">' +
          (href ? '<a href="' + esc(href) + '" target="_blank" rel="noopener noreferrer">' + title + "</a>" : title) + "</span>" : "") + "</td>" +
        "<td>" + esc(name(a.country || "")) + "</td>" +
        "<td>" + esc(classLabel(a.klass)) + (hosts ? '<span class="pl-title">' + esc(hosts) + "</span>" : "") + "</td>" +
        '<td class="pl-detector">' + esc(a.evidence || "") + (bases ? '<span class="pl-title">' + esc(bases) + "</span>" : "") + "</td></tr>";
    }).join("");
    return counts + (line ? '<p class="pl-line">By country: ' + line + ".</p>" : "") +
      '<div class="pl-table-wrap"><table class="pl-table"><thead><tr><th scope="col">Date</th><th scope="col">Outlet</th><th scope="col">Country</th>' +
      '<th scope="col">Host</th><th scope="col">Disclosure</th></tr></thead><tbody>' + rows + "</tbody></table></div>" +
      (arts.length > ROWS ? '<p class="pl-toggle"><button type="button" id="hosted-travel-toggle">' +
        (opts.showAll ? "Show the first " + ROWS : "Show all " + arts.length) + "</button></p>" : "");
  }

  /* Browser: fetch the file and the country names, fill the section. A file that does not load
     says so; it is not a finding. */
  function boot(doc) {
    var sec = doc.getElementById("hosted-travel"), introEl = doc.getElementById("hosted-travel-intro"), body = doc.getElementById("hosted-travel-body");
    if (!sec || !introEl || !body || typeof fetch !== "function") return;
    var get = function (u) { return fetch(u, {cache: "no-cache"}).then(function (r) { if (!r.ok) throw new Error(u + " " + r.status); return r.json(); }); };
    var names = {};
    var showAll = false, json = null;
    function countryName(iso) { return names[iso] || iso; }
    function draw() {
      body.innerHTML = bodyHtml(json, {countryName: countryName, showAll: showAll});
      var t = doc.getElementById("hosted-travel-toggle");
      if (t) t.addEventListener("click", function () { showAll = !showAll; draw(); var again = doc.getElementById("hosted-travel-toggle"); if (again) again.focus(); });
    }
    Promise.all([
      get("vendor/iso3166.json").catch(function () { return []; }),
      get("country-names.json").catch(function () { return {names: {}}; }),
      get("data/hosted_travel.json").catch(function (e) { if (typeof console !== "undefined") console.error(e); return null; })
    ]).then(function (res) {
      (res[0] || []).forEach(function (r) { if (r && r["alpha-3"]) names[r["alpha-3"]] = r.name; });
      var common = (res[1] && res[1].names) || {};
      Object.keys(common).forEach(function (iso) { names[iso] = common[iso]; });
      json = res[2] && typeof res[2] === "object" ? res[2] : null;
      introEl.textContent = intro(json || {}, !!json);
      if (!json) { body.innerHTML = '<p class="pl-empty">The hosted travel file did not load. Reload the page, or check whether the last export ran.</p>'; return; }
      draw();
    });
  }
  if (typeof document !== "undefined" && document.getElementById) {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", function () { boot(document); });
    else boot(document);
  }

  return {summarise: summarise, intro: intro, emptyState: emptyState, bodyHtml: bodyHtml, classLabel: classLabel,
          basisLabel: basisLabel, CLASS_LABELS: CLASS_LABELS, ROWS: ROWS};
}));
