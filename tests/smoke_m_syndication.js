/* Smoke test for docs/m_syndication.js. Run with: node tests/smoke_m_syndication.js (or under jsc through
   scratchpad/measures/run_jsc_measures.sh). */
var M = require("../docs/measures.js");
var S = require("../docs/m_syndication.js");
function assert(cond, msg) { if (!cond) { console.error("FAIL: " + msg); process.exit(1); } }

assert(S.ID === "syndication" && M.get("syndication"), "registered");
assert(S.fmtDate("2025-12-17") === "17 December 2025" && S.fmtDate("2025-05") === "May 2025" && S.fmtDate("2019") === "2019", "dates at their precision");
assert(S.safeUrl("https://example.org/a") && !S.safeUrl("javascript:alert(1)") && !S.safeUrl("http://example.org"), "only https links");
assert(S.pts(0.052) === "+5.2 pts" && S.pts(-0.1).indexOf("10.0 pts") !== -1, "percentage points");

function api(slot) { return {id: "syndication", slot: slot, section: null, meta: {}, names: {}, esc: M.esc, countryName: function (c) { return c === "ITA" ? "Italy" : c; }, M: M}; }
var prov = {status: "provisional", label: "Syndication deals", check: {state: "none", n: 0, coded: 0}};

/* Off: hidden and empty, whatever the data. */
var slot = {hidden: false, innerHTML: "stale"};
M.get("syndication").render({status: "off"}, {timeline: [{id: "x"}]}, api(slot));
assert(slot.hidden === true && slot.innerHTML === "", "off hides and clears");

var data = {
  measure: "syndication", version: "1", collection_start: "2026-09-03", last_complete_day: "2026-12-30", window_days: 28,
  min_china_per_side: 50, eligible_from: "2026-10-01", eligible_until: "2026-12-02",
  counts: {entries: 3, monitored: 2, unmonitored: 1, predates_collection: 2, eligible: 1, compared: 1, confounded: 1},
  timeline: [
    {id: "it_x_2026", outlet_id: "it_x", outlet_name: "Outlet <X>", country: "ITA", monitored: true, partner: "xinhua", partner_label: "Xinhua",
     kind: "mou", kind_label: "memorandum", signed_on: "2026-11-02", precision: "day", confidence: "stated", notes: "Signed in Rome.",
     predates_collection: false, comparison: "eligible", source: {url: "https://example.org/deal", title: "Deal", publisher: "Outlet X"},
     links: [{label: "Deal signed", publisher: "Outlet X", url: "https://example.org/deal"}]},
    {id: "it_y_2019", outlet_id: "it_y", outlet_name: "Outlet Y", country: "ITA", monitored: true, partner: "cmg", partner_label: "China Media Group",
     kind: "content_exchange", kind_label: "content exchange", signed_on: "2019-03", precision: "month", ended_on: "2022-06", confidence: "reported",
     notes: "", predates_collection: true, comparison: "predates_collection", source: {url: "javascript:alert(1)", title: "Bad"},
     links: [{label: "Bad link", url: "javascript:alert(1)"}]},
    {id: "unmonitored_z", outlet_id: null, outlet_name: "Paper Z", country: null, monitored: false, partner: "china_daily", partner_label: "China Daily",
     kind: "supplement", kind_label: "supplement", signed_on: "2025", precision: "year", confidence: "stated", predates_collection: true,
     comparison: "unmonitored", source: {url: "https://example.org/z"}, links: [{label: "Z", url: "https://example.org/z"}]}
  ],
  comparisons: [
    {entry_id: "it_x_2026", outlet_id: "it_x", outlet_name: "Outlet <X>", country: "ITA", partner: "xinhua", kind: "mou", signed_on: "2026-11-02", precision: "day",
     before: {start: "2026-10-05", end: "2026-11-01", state_origin: 10, china: 100, sufficient: true, share: 0.1, ci: [0.0552, 0.1744]},
     after: {start: "2026-11-03", end: "2026-11-30", state_origin: 30, china: 120, sufficient: true, share: 0.25, ci: [0.1814, 0.3343]},
     sufficient: true, reason: "", difference: 0.15, difference_ci: [0.0549, 0.2421],
     confounders: [{type: "gate_change", text: "the relevance gate changed to 2026.11.1 on 2026-11-10, inside the window"}], confounded: true}
  ]
};

slot = {hidden: true, innerHTML: ""};
M.get("syndication").render(prov, data, api(slot));
var h = slot.innerHTML;
assert(slot.hidden === false, "provisional shows");
assert(h.indexOf("m-chip-provisional") !== -1, "chip");
assert(h.indexOf("syn-timeline") !== -1 && (h.match(/class="syn-entry/g) || []).length === 3, "three timeline entries");
assert(h.indexOf('href="https://example.org/deal"') !== -1 && h.indexOf('rel="noopener noreferrer"') !== -1, "source link");
assert(h.indexOf("javascript:") === -1, "unsafe link dropped");
assert(h.indexOf("Outlet &lt;X&gt;") !== -1 && h.indexOf("<X>") === -1, "names escaped");
assert(h.indexOf("Italy") !== -1, "country name");
assert(h.indexOf("2 November 2026") !== -1 && h.indexOf("March 2019") !== -1, "dates formatted");
assert(h.indexOf("before collection began") !== -1 && h.indexOf("outlet not monitored") !== -1 && h.indexOf("ended June 2022") !== -1, "tags");
assert(h.indexOf("syn-table") !== -1 && h.indexOf("10.0%") !== -1 && h.indexOf("25.0%") !== -1 && h.indexOf("+15.0 pts") !== -1, "before and after table");
assert(h.indexOf("timing, not cause") !== -1, "timing, not cause");
assert(h.indexOf("syn-confounded") !== -1 && h.indexOf("relevance gate changed") !== -1, "confounders shown");
assert(h.indexOf("(provisional)") !== -1, "provisional suffix on the comparison");

/* No comparison yet: the timeline, and a line saying why. */
var only = JSON.parse(JSON.stringify(data)); only.comparisons = [];
slot = {hidden: true, innerHTML: ""};
M.get("syndication").render(prov, only, api(slot));
assert(slot.innerHTML.indexOf("No deal yet falls where a comparison is possible") !== -1 && slot.innerHTML.indexOf("3 September 2026") !== -1, "no comparison line");
assert(slot.innerHTML.indexOf("syn-table") === -1, "no table without comparisons");

/* Insufficient side. */
var thin = JSON.parse(JSON.stringify(data));
thin.comparisons[0].after = {state_origin: 1, china: 12, sufficient: false, share: null, ci: null};
thin.comparisons[0].sufficient = false; thin.comparisons[0].difference = null; thin.comparisons[0].difference_ci = null;
thin.comparisons[0].reason = "fewer than 50 China articles after";
slot = {hidden: true, innerHTML: ""};
M.get("syndication").render(prov, thin, api(slot));
assert(slot.innerHTML.indexOf("too few") !== -1 && slot.innerHTML.indexOf("fewer than 50 China articles after") !== -1, "too few articles");

/* Withheld shows no figures; an empty file shows the empty state. */
var wh = {status: "withheld", label: "Syndication deals", check: {state: "failed", n: 20, coded: 20, statistic: "share", value: 0.8, threshold: 0.9}};
slot = {hidden: true, innerHTML: ""};
M.get("syndication").render(wh, data, api(slot));
assert(slot.innerHTML.indexOf("withheld") !== -1 && slot.innerHTML.indexOf("syn-table") === -1, "withheld hides figures");
M.get("syndication").render(prov, {}, api(slot));
assert(slot.innerHTML.indexOf("m-empty") !== -1, "empty file");

console.log("syndication smoke ok");
