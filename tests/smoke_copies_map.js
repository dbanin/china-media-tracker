/* Smoke test for the pure helpers of docs/copies_map.js (Same text, many countries). Run with:
   node tests/smoke_copies_map.js (tests/test_copies_map.py runs it under pytest). */
var CM = require("../docs/copies_map.js");
function assert(cond, msg) { if (!cond) { console.error("FAIL: " + msg); process.exit(1); } }

var data = {
  version: "1", min_countries: 2, window: {start: "2026-09-28", end: "2026-10-28"},
  alteration: {status: "provisional", shown: true, bands: ["verbatim", "light", "moderate", "substantial"]},
  agencies: {xinhua: "Xinhua", cgtn: "CGTN", unnamed: "Not named"},
  outlets: {us_paper: {name: "US Paper"}, it_paper: {name: "Italian <Paper>"}, aap_release_widget: {name: "AAP press release pages of 10 ACM mastheads", shared_widget: true},
            us_wire: {name: "A Release Wire"}},
  texts: {
    t1: {agency: "xinhua", first_seen: "2026-10-01", headline: null, placements: [
      {o: "us_paper", c: "USA", d: "2026-10-01", b: "light"}, {o: "it_paper", c: "ITA", d: "2026-10-03"},
      {o: "aap_release_widget", c: "AUS", d: "2026-10-01"}, {o: "us_wire", c: "WLD", d: "2026-10-01"}]},
    t2: {agency: "cgtn", first_seen: "2026-09-01", headline: "CGTN launches News Pal", headline_lang: "en", placements: [
      {o: "us_paper", c: "USA", d: "2026-09-01"}, {o: "it_paper", c: "ITA", d: "2026-09-30"}]}
  },
  scopes: {
    last_30d: {ranked: [{id: "t1", countries: 3, outlets: 4}], shared_texts: 1, texts: 5,
               countries: {USA: {texts: 1, placements: 1}, ITA: {texts: 2, placements: 2}, AUS: {texts: 1, placements: 1}, WLD: {texts: 1, placements: 1}}},
    all_time: {ranked: [{id: "t1", countries: 3, outlets: 4}, {id: "t2", countries: 2, outlets: 2}, {id: "gone", countries: 2, outlets: 2}], shared_texts: 3, texts: 9,
               countries: {USA: {texts: 2, placements: 2}, ITA: {texts: 2, placements: 2}, AUS: {texts: 1, placements: 1}, WLD: {texts: 1, placements: 1}}}
  }
};

// Dates and windows
assert(CM.fmtDate("2026-10-03") === "3 Oct 2026", "fmtDate");
assert(CM.daysBetween("2026-09-30", "2026-10-02") === 2, "daysBetween across months");
assert(CM.windowText(data.window) === "28 Sep 2026 to 27 Oct 2026", "window text ends the day before end: " + CM.windowText(data.window));
assert(CM.inScope("2026-10-27", "last_30d", data) && !CM.inScope("2026-10-28", "last_30d", data) && !CM.inScope("2026-09-27", "last_30d", data), "window bounds");

// Counting matches the export's rule: WLD never a country, each outlet once
var c = CM.counts(CM.scopeEntries(data.texts.t1, "all_time", data));
assert(c.countries === 3 && c.outlets === 4, "counts " + JSON.stringify(c));
var c2 = CM.counts(CM.scopeEntries(data.texts.t2, "last_30d", data));
assert(c2.countries === 1 && c2.outlets === 1, "30 day scope drops the old copy");

// Per country rows, first copy first, days after the first copy
var rows = CM.byCountry(data.texts.t1, "all_time", data);
assert(rows[rows.length - 1].iso === "ITA" && rows[rows.length - 1].after === 2, "Italy ran it two days after: " + JSON.stringify(rows));
assert(rows.filter(function (r) { return r.iso === "USA"; })[0].outlets[0].b === "light", "band kept");
assert(CM.shadeValue(rows[0], "outlets") === 1 && CM.shadeValue(rows[rows.length - 1], "days") === 2, "shade values");

// Tiers and colours
assert(CM.tierIndex(1, "outlets") === 0 && CM.tierIndex(4, "outlets") === 2 && CM.tierIndex(40, "outlets") === 3, "outlet tiers");
assert(CM.tierIndex(0, "days") === 0 && CM.tierIndex(9, "days") === 4 && CM.tierIndex(null, "days") === -1, "day tiers");
assert(CM.tierIndex(25, "texts") === 4 && CM.tierIndex(0, "texts") === -1, "text tiers, zero is unshaded");
assert(CM.colorFor(0, "days") === CM.colorFor(3, "outlets"), "same day is the darkest shade, as the most outlets");
assert(CM.colorFor(-1, "outlets") === null, "no colour outside the tiers");

// Lists, titles and the tooltip
var r = CM.ranked(data, "all_time");
assert(r.length === 2 && r[0].id === "t1", "ranked keeps only described texts");
assert(CM.textTitle(data, "t1") === "Xinhua text first seen 1 Oct 2026", "title without a shown headline");
assert(CM.textTitle(data, "t2") === "CGTN launches News Pal", "headline when shown");
assert(CM.listMeta(data, r[0]) === "Xinhua · first seen 1 Oct 2026 · 3 countries, 4 outlets", "list meta: " + CM.listMeta(data, r[0]));
var ita = rows[rows.length - 1];
var lines = CM.tipLines(data, ita, "Italy");
assert(lines[0] === "Italy: 1 outlet, first 2 days after the first copy", "tip head: " + lines[0]);
assert(lines[1] === "Italian <Paper>, 3 Oct 2026 (2 days after the first copy)", "tip line raw (escaped when drawn): " + lines[1]);
var usa = rows.filter(function (x) { return x.iso === "USA"; })[0];
var ul = CM.tipLines(data, usa, "United States");
assert(ul[1] === "US Paper, 1 Oct 2026, light changes", "band in the tip: " + ul[1]);
assert(/provisional/.test(ul[ul.length - 1]), "provisional said in the tip");
var off = JSON.parse(JSON.stringify(data)); off.alteration = {status: "withheld", shown: false};
assert(CM.tipLines(off, usa, "United States")[1] === "US Paper, 1 Oct 2026", "no band while not shown");
assert(!CM.bandsShown(off) && CM.bandsShown(data) && CM.provisional(data), "band flags");
assert(CM.esc("<b>") === "&lt;b&gt;", "esc");

// The all texts summary leaves the worldwide stratum off the map
var s = CM.summary(data, "all_time");
assert(!s.WLD && s.USA.texts === 2, "summary drops WLD");
var sr = CM.summaryRows(data, "all_time");
assert(sr[0].iso === "ITA" && sr[1].iso === "USA" && sr[2].iso === "AUS", "summary rows by texts, then copies, then name: " + JSON.stringify(sr));

// Countries without an outline at 110m are named, never the worldwide stratum
assert(JSON.stringify(CM.offMap(["SGP", "WLD", "AUS", "BHR"], {AUS: true})) === '["BHR","SGP"]', "off map");
assert(CM.offMap(["SGP"], {}).length === 0 && CM.offMap(["SGP"], null).length === 0, "nothing before the map is drawn");



// Ties: equal countries and outlets share a rank; the count line states ties left off the list
var tl = [{countries: 3, outlets: 5}, {countries: 3, outlets: 4}, {countries: 3, outlets: 4}, {countries: 2, outlets: 3}];
assert(JSON.stringify(CM.rankNumbers(tl)) === "[1,2,2,4]", "rank numbers: " + JSON.stringify(CM.rankNumbers(tl)));
assert(CM.countText({shared_texts: 30, texts: 400, tied_beyond: 2}, 25) ===
  "30 state texts ran in two or more countries, of 400 state texts in all. The 25 run in the most countries are listed; 2 more texts tie with the last one listed and are not shown.",
  "count text with ties: " + CM.countText({shared_texts: 30, texts: 400, tied_beyond: 2}, 25));
assert(/1 more text ties with the last one listed and is not shown/.test(CM.countText({shared_texts: 26, texts: 40, tied_beyond: 1}, 25)), "one tie");
assert(CM.countText({shared_texts: 3, texts: 9}, 3) === "3 state texts ran in two or more countries, of 9 state texts in all.", "no cut");

// Malformed shapes read as nothing, never a throw
assert(CM.ranked({scopes: {all_time: {ranked: {x: 1}}}, texts: {}}, "all_time").length === 0, "ranked not a list");
assert(CM.scopeEntries({placements: null}, "all_time", {}).length === 0 && CM.scopeEntries({placements: {}}, "all_time", {}).length === 0, "placements not a list");
assert(CM.byCountry({placements: "x"}, "all_time", {}).length === 0, "byCountry on bad placements");
console.log("copies_map smoke: ok");
