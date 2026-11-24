/* Smoke test for placements and distinct underlying items in the browser (docs/compute.js aggregateWindow,
   itemsInWindow, itemsText, paidReleaseText, rankCountries; docs/app.js itemsNote), loaded like
   tests/smoke_feed_recent.js. Run by tests/test_unique_items.py under node. */
(function () {
  var C = window.TrackerCompute, H = window.TrackerApp && window.TrackerApp.helpers;
  function assert(cond, msg) { if (!cond) throw new Error("FAIL: " + msg); }
  /* One paid release at four Canadian papers and on a wire, over two days; a wire credit item; an unchecked item. */
  var months = {"2026-11": {days: {
    "2026-11-01": {countries: {CAN: {A: 4, B: 2, uniqA: 1, uniqAB: 2}, WLD: {A: 1, uniqA: 1, uniqAB: 1}},
                   routes: {CAN: {distribution_stamp: 4}, WLD: {distribution_stamp: 1}},
                   underlying_items: {CAN: {A: [5], B: [9], Ap: [5]}, WLD: {A: [5], Ap: [5]}}},
    "2026-11-02": {countries: {CAN: {A: 2, uniqA: 2, uniqAB: 2}},
                   routes: {CAN: {distribution_stamp: 1, wire_credit: 1}},
                   underlying_items: {CAN: {A: [5, 7], Ap: [5]}}}}}};
  var a = C.aggregateWindow(months, "2026-11-02", null, "both");
  var can = a.countries.CAN;
  assert(can.A === 6 && can.uniqA === 2 && can.uniqB === 1 && can.uniqAB === 3, "per country items: " + JSON.stringify(can));
  assert(can.Ap === 5 && can.uniqAp === 1, "the paid route's placements and items: " + JSON.stringify(can));
  assert(a.world.uniqA === 2 && a.world.Ap === 6 && a.world.uniqAp === 1, "the wire's copy is one item worldwide: " + JSON.stringify(a.world));
  assert(a.worldParts.country.uniqA === 2 && a.worldParts.wires.uniqA === 1, "each part counts its own items");
  var both = C.itemsInWindow(a, "both", false), ctry = C.itemsInWindow(a, "country", false), wires = C.itemsInWindow(a, "wires", true);
  assert(both.state_origin === 7 && both.state_origin_items === 2 && both.paid_release === 6 && both.paid_release_items === 1, JSON.stringify(both));
  assert(ctry.state_origin === 6 && ctry.unchecked === 2 && ctry.unchecked_items === 1, JSON.stringify(ctry));
  assert(wires.state_origin === 1 && wires.unchecked === null && wires.unchecked_items === null, "withheld nulls unchecked: " + JSON.stringify(wires));
  assert(C.itemsInWindow(null, "both") === null && C.itemsInWindow({countries: {}}, "both") === null, "no item figures, no block");
  /* A labelled key the page does not know yet is counted like the others, not dropped or fatal. */
  var extra = {"2026-11": {days: {"2026-11-03": {countries: {CAN: {A: 1}}, underlying_items: {CAN: {A: [1], S: [2]}}}}}};
  assert(C.aggregateWindow(extra, "2026-11-03", null, "both").countries.CAN.uniqA === 1, "an unknown key leaves the counts alone");
  /* Days from an export before the item ids: summed, and no paid items claimed. */
  var old = {"2026-10": {days: {"2026-10-01": {countries: {CAN: {A: 3, uniqA: 2, uniqAB: 2}}, routes: {CAN: {distribution_stamp: 3}}}}}};
  var o = C.aggregateWindow(old, "2026-10-01", null, "both");
  assert(o.underlyingSummed && o.countries.CAN.uniqA === 2 && o.countries.CAN.Ap === 3 && o.countries.CAN.uniqAp === 0, JSON.stringify(o.countries.CAN));
  assert(C.itemsText(2312, 1907) === "2,312 placements of 1,907 distinct underlying items", C.itemsText(2312, 1907));
  assert(C.itemsText(1, 1) === "1 placement of 1 distinct underlying item" && C.itemsText(null, 3) === "", "plurals and missing figures");
  assert(C.paidReleaseText(719, 563) === "719 placements of 563 distinct underlying items came as paid press releases through a distribution service", "paid text");
  assert(C.paidReleaseText(0, 0) === "", "no paid clause for none");
  assert(C.setPaidReleaseRoutes(null).join() === "distribution_stamp" && C.paidReleaseCount({distribution_stamp: 2, wire_credit: 9}) === 2, "the paid routes");
  /* The CSV of the current view carries the paid route, blank under human labels and under a route filter. */
  var latest = {countries: {CAN: {outlets_active: 4, coverage: "monitored", warnings: []}}};
  var rows = C.rankCountries(a, latest, "count_a", "all", {CAN: "Canada"}, function () { return {relay: {measured: true, publishable: true}}; }, "both");
  assert(rows[0].state_origin_paid_release === 5 && rows[0].state_origin_paid_release_underlying_items === 1, JSON.stringify(rows[0]));
  var rev = C.rankCountries(a, latest, "count_a", "reviewed", {}, function () { return {relay: {measured: true, publishable: true}}; }, "both");
  assert(rev[0].state_origin_paid_release === "", "blank for human labels");
  /* The card note: only beside its own number. */
  assert(H && H.itemsNote, "itemsNote is exposed");
  assert(H.itemsNote(both, "state_origin", 7, true) === " Counted once per outlet that ran it: 7 placements of 2 distinct underlying items; 6 placements of 1 distinct underlying item came as paid press releases through a distribution service.", H.itemsNote(both, "state_origin", 7, true));
  assert(H.itemsNote(both, "state_origin", 8, true) === "", "never beside a count over other days");
  assert(H.itemsNote(wires, "unchecked", null, false) === "", "nothing for a withheld count");
  assert(!/Category [ABC]/.test(H.itemsNote(both, "state_origin", 7, true)), "no category letters");
  console.log("unique items smoke ok");
})();
