/* Smoke test for docs/m_alteration.js (the alteration score panel). Run with: node tests/smoke_m_alteration.js
   or under jsc through scratchpad/measures/run_jsc_measures.sh. */
var M = require("../docs/measures.js");
var A = require("../docs/m_alteration.js");
function assert(cond, msg) { if (!cond) { console.error("FAIL: " + msg); process.exit(1); } }
function un(h) { return String(h).replace(/&#39;/g, "'").replace(/&#039;/g, "'"); }

var prov = {status: "provisional", label: "Alteration score", check: {state: "none", n: 0, coded: 0}};
function api() {
  return {id: "alteration", slot: {hidden: true, innerHTML: ""}, section: null,
          meta: {routes: [{id: "wire_credit", label: "Wire credit line or dateline"}]}, names: {}, esc: M.esc,
          countryName: function (i) { return i === "WLD" ? "Worldwide distribution" : i === "ITA" ? "Italy" : i; }, M: M};
}
var data = {
  version: "1", bands: A.BANDS, outlet_min_n: 5,
  totals: {copies: 40, scored: 20, bands: {verbatim: 10, light: 5, moderate: 3, substantial: 2}, median_novel: 0.02,
           median_kept: 0.9, headline_compared: 30, headline_rewritten: 6, items: 7, originals: 8,
           against: {reference: {scored: 0, items: 0, originals: 0, bands: {}, item_bands: {}},
                     monitored: {scored: 20, items: 4, originals: 5, bands: {verbatim: 10, light: 5, moderate: 3, substantial: 2},
                                 item_bands: {verbatim: 1, light: 1, moderate: 1, substantial: 1}, median_kept_items: 0.9}}},
  coverage: {copies: 40, reference: 12, monitored: 10, none: 18, none_by_reason: {translation: 11, no_state_credit: 7},
             translation: 14, scored: 20},
  by_route: [{id: "wire_credit", n: 30, scored: 18, bands: {verbatim: 9, light: 5, moderate: 2, substantial: 2}},
             {id: "uncredited_copy", label: "Uncredited copy", n: 5, scored: 2, bands: {verbatim: 1, light: 0, moderate: 1, substantial: 0}},
             {id: "unattributed", n: 5, scored: 0, bands: {verbatim: 0, light: 0, moderate: 0, substantial: 0}}],
  reference_sources: [{source: "ref_xinhua_en", items: 300, statuses: {fetched: 120, expired: 170, failed: 4, paywalled: 0}, flags: []},
                      {source: "ref_global_times_en", items: 100, statuses: {fetched: 40, expired: 60}, share_date_only_after_day_one: 0.45,
                       flags: ["date only after day one"]}],
  by_outlet: [{id: "it_libero", name: "Libero <i>", country: "ITA", n: 9, scored: 8, originals: 6, median_novel: 0.31, median_kept: 0.7,
               bands: {verbatim: 2, light: 2, moderate: 2, substantial: 2}},
              {id: "us_wire", name: "PR Wire", country: "WLD", n: 6, scored: 6, median_novel: 0, median_kept: 1,
               bands: {verbatim: 6, light: 0, moderate: 0, substantial: 0}}]
};

/* Shares and the coverage line. */
var s = A.shares(data.totals);
assert(Math.abs(s.verbatim - 0.5) < 1e-9 && Math.abs(s.substantial - 0.1) < 1e-9, "band shares from counts");
assert(A.shares({bands: {}}) === null && A.shares(null) === null, "no shares without scored copies");
var cov = A.coverageLine(data);
assert(cov.indexOf("Of 40 state origin copies, 22 have an original (12 from a reference source, 10 an earlier monitored copy) and 20 could be scored") === 0, "coverage counts: " + cov);
assert(cov.indexOf("11 translations with no earlier copy") !== -1 && cov.indexOf("7 no state publisher named") !== -1 && cov.indexOf("In all 14 copies are translations") !== -1, "coverage reasons");
assert(A.coverageLine({}) === "", "no coverage line without copies");
var cov2 = A.coverageLine({coverage: {copies: 40, items: 15, originals: 9, reference: 2, monitored: 10, scored: 5, sister_only: 7, none_by_reason: {}}});
assert(cov2.indexOf("Of 40 state origin copies (15 underlying items), 12 have an original (2 from a reference source, 10 an earlier monitored copy; 9 distinct originals)") === 0, "items and originals: " + cov2);
assert(cov2.indexOf("7 copies had an earlier copy only at a sister outlet of the same media group") !== -1, "sister outlets are not originals");
var src = A.sourcesLine(data);
assert(src.indexOf("Xinhua English 120 of 300 fetched, 170 expired unread, 4 failed") !== -1, "reference status: " + src);
assert(src.indexOf("Global Times 40 of 100 fetched, 60 expired unread, 45% still date only after a day") !== -1, "date only flag");
/* The lead: no reference pairs yet, so the figures are worded against an earlier monitored copy, over items. */
var ld = un(A.lead(data));
assert(ld.indexOf("No copy has been scored against the state outlet's own text yet") !== -1, "says no reference pair: " + ld);
assert(ld.indexOf("Against an earlier copy of the same item at another monitored outlet, outside the copy's own media group (4 distinct items, 20 copies, 5 originals), 25% of the items repeated that copy verbatim") !== -1, "item shares worded against a monitored copy: " + ld);
assert(ld.indexOf("ran the state text verbatim") === -1, "never claims the state text without a reference pair");
var withRef = JSON.parse(JSON.stringify(data));
withRef.totals.against.reference = {scored: 6, items: 2, originals: 2, bands: {verbatim: 6}, item_bands: {verbatim: 2}, median_kept_items: 1};
ld = un(A.lead(withRef));
assert(ld.indexOf("Against the state outlet") !== -1 && ld.indexOf("(2 distinct items, 6 copies, 2 originals), 100% of the items were run verbatim") !== -1, "reference lead: " + ld);
assert(ld.indexOf("No copy has been scored") === -1 && ld.indexOf("Against an earlier copy") !== -1, "both kinds, apart");

/* Populated: chip, a bar per scored route, the outlet table, coverage, escaping. */
var a = api();
M.get("alteration").render(prov, data, a);
var h = un(a.slot.innerHTML);
assert(a.slot.hidden === false, "shown when provisional");
assert(h.indexOf("m-chip-provisional") !== -1, "status chip");
assert((h.match(/class="alt-row"/g) || []).length === 4, "the monitored kind, all routes and two scored routes");
assert(h.indexOf("Against an earlier monitored copy") !== -1 && h.indexOf("4 items, 20 copies") !== -1, "a bar per kind, over items");
assert(h.indexOf("Against the state outlet's own text <span") === -1, "no bar for a kind with nothing scored");
assert(h.indexOf("Xinhua English 120 of 300 fetched") !== -1, "reference sources' status shown");
assert(h.indexOf(">6<") !== -1 && h.indexOf("Originals") !== -1, "distinct originals in the outlet table");
assert(h.indexOf("Wire credit line or dateline") !== -1 && h.indexOf("Uncredited copy") !== -1, "route labels from meta and data");
assert(h.indexOf("unattributed") === -1, "a route with nothing scored has no bar");
assert(h.indexOf("alt-verbatim") !== -1 && h.indexOf("width:50%") !== -1, "segment widths");
assert(h.indexOf("Libero &lt;i&gt;") !== -1 && h.indexOf("<i>") === -1, "outlet names escaped");
assert(h.indexOf("Worldwide distribution") !== -1 && h.indexOf("Italy") !== -1, "countries named");
assert(h.indexOf(">31%<") !== -1 && h.indexOf(">8<") !== -1, "median score and n in the table");
assert(h.indexOf("25% of the items repeated that copy verbatim") !== -1, "lead sentence");
assert(h.indexOf("20% of the 30 copies with a headline compared carry a rewritten headline") !== -1, "headline changes reported separately");
var old = JSON.parse(JSON.stringify(data));
delete old.totals.against;
assert(A.lead(old).indexOf("Of 20 copies scored against an original (a state outlet") !== -1 && A.lead(old).indexOf("ran the state text") === -1, "an older file: neutral wording");
assert(h.indexOf("(provisional)") !== -1, "provisional suffix on the outlet ranking");
assert(!/[\u2013\u2014]/.test(h), "no en or em dashes in the panel");

/* Empty, withheld and off. */
a = api();
M.get("alteration").render(prov, {totals: {scored: 0}, coverage: {copies: 5, reference: 0, monitored: 0, none: 5, none_by_reason: {translation: 5}, scored: 0}}, a);
assert(a.slot.innerHTML.indexOf("Nothing to show yet") !== -1 && a.slot.innerHTML.indexOf("Of 5 state origin copies") !== -1, "nothing scored still shows coverage");
a = api();
M.get("alteration").render({status: "withheld", label: "Alteration score", check: {state: "failed", n: 80, coded: 80, statistic: "pair precision", value: 0.8, threshold: 0.9}}, data, a);
assert(a.slot.innerHTML.indexOf("withheld") !== -1 && a.slot.innerHTML.indexOf("alt-row") === -1, "withheld shows no figure");
a = api();
M.get("alteration").render({status: "off"}, data, a);
assert(a.slot.hidden === true && a.slot.innerHTML === "", "off hides and clears");
a = api();
data.by_outlet = [];
M.get("alteration").render(prov, data, a);
assert(a.slot.innerHTML.indexOf("No outlet has scored copies of 5 distinct originals yet") !== -1, "empty outlet table says so");

console.log("alteration smoke ok");
