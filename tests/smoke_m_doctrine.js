/* Smoke test for docs/m_doctrine.js. Run with: node tests/smoke_m_doctrine.js (or under jsc through
   scratchpad/measures/run_jsc_measures.sh). Wrapped in a function so it shares no names with the other
   smoke scripts the jsc shim loads into one global scope. */
(function () {
  var M = require("../docs/measures.js");
  var D = require("../docs/m_doctrine.js");
  function assert(cond, msg) { if (!cond) { console.error("FAIL: " + msg); process.exit(1); } }

  var prov = {status: "provisional", label: "Doctrine terms", check: {state: "none", n: 0, coded: 0},
              summary: {family_status: {}}};
  var meta = {first_discovered: "2026-09-01", generated_at: "2026-10-10T06:00:00+00:00",
              relay_measured: true, relay_publishable: false, relay_provisional: true};
  /* Three days: the 8th and 9th complete, the 10th the partial export day. */
  var data = {
    version: "t", languages: ["es"], tagged_share: 0.5, tagged_without_body: 0.9,
    families: [{id: "shared_future", label: "Community <shared> future", distinctive: true, status: "provisional"},
               {id: "global_south", label: "Global South", distinctive: false, status: "provisional"},
               {id: "hegemonism", label: "Hegemonism", distinctive: true, status: "withheld"}],
    totals: {
      "2026-10-08": {ITA: [40, 10, 10, 20, 8, 5, 1, 2], WLD: [10, 0, 0, 10, 1, 0, 0, 1]},
      "2026-10-09": {ITA: [50, 20, 10, 20, 6, 4, 1, 1]},
      "2026-10-10": {ITA: [99, 0, 0, 99, 99, 0, 0, 99]}
    },
    days: {
      "2026-10-08": {ITA: {shared_future: [8, 5, 1, 2, 11], global_south: [3, 0, 0, 3, 3], hegemonism: [2, 2, 0, 0, 2]},
                     WLD: {shared_future: [1, 0, 0, 1, 1]}},
      "2026-10-09": {ITA: {shared_future: [6, 4, 1, 1, 6], global_south: [9, 1, 0, 8, 9]}},
      "2026-10-10": {ITA: {shared_future: [99, 0, 0, 99, 99]}}
    }
  };

  assert(D.lastCompleteDay(data, meta) === "2026-10-09", "the export day is partial: the window ends the day before");
  assert(D.windowDays(data, meta, "2026-10-09", "1").join() === "2026-10-09", "that day");
  assert(D.windowDays(data, meta, "2026-10-09", "7").join() === "2026-10-08,2026-10-09", "seven days");
  assert(D.windowDays(data, meta, "2026-10-09", "all").length === 2, "total stops at the last complete day");
  var agg = D.aggregate(data, ["2026-10-08", "2026-10-09"]);
  assert(agg.totals[0] === 100 && agg.totals[3] === 50 && agg.totals[7] === 4, "totals sum countries and the wires");
  assert(agg.fams.shared_future.join() === "15,9,2,4,18", "family cells sum");
  assert(D.rate(15, 100) === 150 && D.rate(1, 10) === null, "rate per 1,000 needs a base of 20");
  var r = D.ranked(data, agg, prov);
  assert(r[0].id === "shared_future" && r[r.length - 1].id === "global_south", "distinctive first, muted last");
  assert(r[1].id === "hegemonism" && r[1].status === "withheld", "a withheld family ranks below the shown ones");
  var w = D.weekly(data, meta, "shared_future", "2026-10-09");
  assert(w.length === 1 && w[0].n === 15 && w[0].base === 100, "one seven day block back to the first day");
  assert(D.relayVisible(meta) === true && D.relayVisible({relay_measured: true, relay_publishable: false}) === false, "relay visibility as app.js");

  /* Render: off, withheld, empty file, and a populated file. */
  var slot = {hidden: false, innerHTML: "stale"};
  var api = {id: "doctrine", slot: slot, section: null, meta: meta, names: {}, esc: M.esc, countryName: function (i) { return i; }, M: M};
  M.get("doctrine").render({status: "off"}, data, api);
  assert(slot.hidden === true && slot.innerHTML === "", "off hides and clears");
  M.get("doctrine").render({status: "withheld", label: "Doctrine terms", check: {statistic: "precision", value: 0.7, threshold: 0.9}}, data, api);
  assert(slot.hidden === false && slot.innerHTML.indexOf("withheld") !== -1 && slot.innerHTML.indexOf("dc-table") === -1, "withheld shows no figure");
  M.get("doctrine").render(prov, {}, api);
  assert(slot.innerHTML.indexOf("m-empty") !== -1, "an empty file says so");
  M.get("doctrine").render(prov, data, api);
  var h = slot.innerHTML;
  assert(h.indexOf("m-chip-provisional") !== -1, "status chip");
  assert(h.indexOf("dc-lead-num") !== -1 && h.indexOf(">8.0%<") !== -1, "lead: 4 of 50 independent journalism articles");
  assert(h.indexOf("(provisional)") !== -1, "the lead is marked provisional");
  assert(h.indexOf("Community &lt;shared&gt; future") !== -1 && h.indexOf("<shared>") === -1, "labels are escaped");
  assert(h.indexOf("dc-muted") !== -1 && h.indexOf("common beyond state usage") !== -1, "non-distinctive families are muted");
  assert(h.indexOf("Withheld by its hand check") !== -1, "a withheld family shows no figure");
  assert(h.indexOf(">150<") !== -1, "shared future: 15 of 100 China articles is 150 per 1,000");
  assert(h.indexOf("Unchecked state sourcing") !== -1, "unchecked state sourcing shown while provisional");
  assert(h.indexOf("dc-spark") !== -1 && h.indexOf('data-dc-family') !== -1, "weekly timeline with a family picker");
  assert(h.indexOf("2026-10-10") === -1, "the partial export day appears nowhere");
  assert(!/[–—]/.test(h), "no dashes in the rendered text");

  /* The window buttons and the family picker redraw through the slot's own handlers. */
  var focused = [];
  slot.querySelector = function (q) { return slot.innerHTML.indexOf('id="' + q.slice(1) + '"') !== -1 ? {focus: function () { focused.push(q); }} : null; };
  slot.onclick({target: {getAttribute: function (k) { return k === "data-dc-window" ? "1" : null; }}});
  assert(D.view.windowDays === "1" && slot.innerHTML.indexOf("2026-10-09") !== -1, "window button redraws");
  assert(focused.join(",") === "#dc-window-1", "the window button gets the focus back after the redraw");
  assert(slot.innerHTML.indexOf('id="dc-window-1" data-dc-window="1" aria-pressed="true" class="active"') !== -1, "the pressed button says so");
  slot.onchange({target: {value: "global_south", getAttribute: function () { return null; }, hasAttribute: function (k) { return k === "data-dc-family"; }}});
  assert(D.view.family === "global_south" && slot.innerHTML.indexOf('value="global_south" selected') !== -1, "family picker redraws");
  assert(focused.join(",") === "#dc-window-1,#dc-family", "the family list gets the focus back after the redraw");
  delete slot.querySelector;

  /* Unchecked state sourcing is hidden where the site withholds it. */
  M.get("doctrine").render(prov, data, {id: "doctrine", slot: slot, meta: {first_discovered: "2026-09-01", generated_at: "2026-10-10T06:00:00+00:00", relay_measured: true, relay_publishable: false}});
  assert(slot.innerHTML.indexOf(">Unchecked state sourcing<") === -1 && slot.innerHTML.indexOf("not shown while") !== -1, "unchecked state sourcing withheld");
  D.view.windowDays = "30"; D.view.family = null;

  console.log("doctrine smoke ok");
}());
