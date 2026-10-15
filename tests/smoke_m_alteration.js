/* Smoke test for docs/m_alteration.js (the alteration score panel). Run with: node tests/smoke_m_alteration.js
   or under jsc through scratchpad/measures/run_jsc_measures.sh. */
var M = require("../docs/measures.js");
var A = require("../docs/m_alteration.js");
function assert(cond, msg) { if (!cond) { console.error("FAIL: " + msg); process.exit(1); } }

var prov = {status: "provisional", label: "Alteration score", check: {state: "none", n: 0, coded: 0}};
function api() {
  return {id: "alteration", slot: {hidden: true, innerHTML: ""}, section: null,
          meta: {routes: [{id: "wire_credit", label: "Wire credit line or dateline"}]}, names: {}, esc: M.esc,
          countryName: function (i) { return i === "WLD" ? "Worldwide distribution" : i === "ITA" ? "Italy" : i; }, M: M};
}
var data = {
  version: "1", bands: A.BANDS, outlet_min_n: 5,
  totals: {copies: 40, scored: 20, bands: {verbatim: 10, light: 5, moderate: 3, substantial: 2}, median_novel: 0.02,
           median_kept: 0.9, headline_compared: 30, headline_rewritten: 6},
  coverage: {copies: 40, reference: 12, monitored: 10, none: 18, none_by_reason: {translation: 11, no_state_credit: 7},
             translation: 14, scored: 20},
  by_route: [{id: "wire_credit", n: 30, scored: 18, bands: {verbatim: 9, light: 5, moderate: 2, substantial: 2}},
             {id: "uncredited_copy", label: "Uncredited copy", n: 5, scored: 2, bands: {verbatim: 1, light: 0, moderate: 1, substantial: 0}},
             {id: "unattributed", n: 5, scored: 0, bands: {verbatim: 0, light: 0, moderate: 0, substantial: 0}}],
  by_outlet: [{id: "it_libero", name: "Libero <i>", country: "ITA", n: 9, scored: 8, median_novel: 0.31, median_kept: 0.7,
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

/* Populated: chip, a bar per scored route, the outlet table, coverage, escaping. */
var a = api();
M.get("alteration").render(prov, data, a);
var h = a.slot.innerHTML;
assert(a.slot.hidden === false, "shown when provisional");
assert(h.indexOf("m-chip-provisional") !== -1, "status chip");
assert((h.match(/class="alt-row"/g) || []).length === 3, "all routes plus two scored routes");
assert(h.indexOf("Wire credit line or dateline") !== -1 && h.indexOf("Uncredited copy") !== -1, "route labels from meta and data");
assert(h.indexOf("unattributed") === -1, "a route with nothing scored has no bar");
assert(h.indexOf("alt-verbatim") !== -1 && h.indexOf("width:50%") !== -1, "segment widths");
assert(h.indexOf("Libero &lt;i&gt;") !== -1 && h.indexOf("<i>") === -1, "outlet names escaped");
assert(h.indexOf("Worldwide distribution") !== -1 && h.indexOf("Italy") !== -1, "countries named");
assert(h.indexOf(">31%<") !== -1 && h.indexOf(">8<") !== -1, "median score and n in the table");
assert(h.indexOf("Of 20 copies scored against their original, 50% ran the state text verbatim") !== -1, "lead sentence");
assert(h.indexOf("20% carry a rewritten headline") !== -1, "headline changes reported separately");
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
assert(a.slot.innerHTML.indexOf("No outlet has 5 scored copies yet") !== -1, "empty outlet table says so");

console.log("alteration smoke ok");
