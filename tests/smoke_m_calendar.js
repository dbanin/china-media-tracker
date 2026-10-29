/* Smoke test for docs/m_calendar.js (the propaganda calendar). Run with: node tests/smoke_m_calendar.js */
(function () {
  var M = require("../docs/measures.js");
  var C = require("../docs/m_calendar.js");
  function assert(cond, msg) { if (!cond) { console.error("FAIL: " + msg); process.exit(1); } }
  function comp(state, extra) {
    var t = {state: state, window_days: 6, comparison_days: 20, observed: 40, expected: 30, ratio: null, ci_low: null, ci_high: null,
             p_shift: null, label_mix_days: 0, mentions: null, countries: []};
    for (var k in (extra || {})) t[k] = extra[k];
    return t;
  }
  var prov = {status: "provisional", label: "Propaganda calendar", check: {state: "none", n: 0, coded: 0}};
  var data = {compiled_on: "2026-10-28", lead_days: 3, lag_days: 3, normal_days: 4, calendar_days_in_windows: 0.42,
    windows: [
      {id: "golden", name: "National Day <b>and</b> Golden Week", kind: "retrospective", state: "tested", start: "2026-10-01", end: "2026-10-07",
       window_start: "2026-09-28", window_end: "2026-10-10", status: "fixed", source_url: "https://example.org/a", excluded: {ruleset_change: 2},
       comparisons: {normal: comp("too_few_days", {window_days: 4, comparison_days: 3}),
                     other: comp("tested", {ratio: 1.6, ci_low: 1.2, ci_high: 2.1, mentions: {items: 40, mentioning: 5, comparison_share: 0.01}})}},
      {id: "march", name: "Long March", kind: "retrospective", state: "too_few_days", start: "2026-10-22", end: "2026-10-22",
       window_start: "2026-10-19", window_end: "2026-10-25", status: "fixed", source_url: "javascript:alert(1)", excluded: {labelling_incomplete: 3},
       comparisons: {normal: comp("too_few_days", {window_days: 0}), other: comp("too_few_days", {window_days: 0})}},
      {id: "ciie", name: "CIIE", kind: "forward", state: "upcoming", start: "2026-11-05", end: "2026-11-10",
       window_start: "2026-11-02", window_end: "2026-11-13", status: "announced", source_url: "https://example.org/c"}
    ],
    undated: [{id: "pc", name: "21st Party Congress", expected_period: "autumn 2027"}]};

  assert(C.ID === "calendar" && M.get("calendar"), "registered");
  assert(M.SLOTS.calendar === "findings-calendar", "its slot");
  assert(M.SECTIONS.some(function (s) { return s.id === "findings-calendar-wrap" && s.measures.indexOf("calendar") !== -1; }), "its section");

  var L = C.lists(data);
  assert(L.retro.length === 2 && L.forward.length === 0 && L.upcoming.length === 1, "retrospective and forward apart");
  assert(C.verdict(data.windows[0].comparisons.other) === "raised" && C.verdict(comp("tested", {ci_low: 0.8, ci_high: 1.3})) === "no clear difference", "verdicts");
  assert(C.verdict(comp("tested", {ci_low: 0.5, ci_high: 0.9})) === "lower", "a lower window");
  assert(C.compText(data.windows[1].comparisons.normal, "normal days").indexOf("too few days") === 0, "too few days");

  var slot = {hidden: true, innerHTML: ""};
  var api = {id: "calendar", slot: slot, section: null, meta: {}, names: {}, esc: M.esc, M: M};
  M.get("calendar").render(prov, data, api);
  var h = slot.innerHTML;
  assert(slot.hidden === false, "shown when provisional");
  assert(h.indexOf('aria-label="Propaganda calendar (provisional)"') !== -1 && h.indexOf("m-chip-provisional") !== -1, "provisional heading and chip");
  assert(h.indexOf("retrospective") !== -1 && h.indexOf("fixed in advance") !== -1, "both kinds labelled");
  assert(h.indexOf("1.60 times other days") !== -1 && h.indexOf("cal-verdict") !== -1, "the ratio and verdict");
  assert(h.indexOf("5 of 40 state origin headlines") !== -1, "the mention share");
  assert(h.indexOf("Days left out: 2 for a ruleset change") !== -1, "excluded days named");
  assert(h.indexOf("<b>and</b>") === -1 && h.indexOf("&lt;b&gt;") !== -1, "names are escaped");
  assert(h.indexOf("javascript:") === -1, "only https sources are linked");
  assert(h.indexOf("Window 2 Nov to 13 Nov, dates announced") !== -1, "upcoming windows");
  assert(h.indexOf("21st Party Congress (autumn 2027)") !== -1, "undated events");
  assert(h.indexOf("Timing, not cause") !== -1, "the caveat");
  assert(h.indexOf("Too few days to test: 0 usable window days, 20 normal and 20 other comparison days") !== -1, "an untested window in one line");
  assert(!/[–—]/.test(h), "no dashes");

  M.get("calendar").render({status: "off"}, data, api);
  assert(slot.hidden === true && slot.innerHTML === "", "hidden when off");
  M.get("calendar").render(prov, null, api);
  assert(slot.innerHTML.indexOf("did not load") !== -1, "a missing file says so");
  console.log("ok m_calendar");
}());
