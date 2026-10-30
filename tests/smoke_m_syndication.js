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
  counts: {entries: 3, monitored: 2, unmonitored: 1, predates_collection: 2, eligible: 1, compared: 1, confounded: 1,
           verified: 1, source_moved: 1, unverified: 1, unchecked: 0},
  verification: {checked_on: "2026-10-28", wording: "sources verified on 2026-10-28, not hand coded", hand_coded: false},
  timeline: [
    {id: "it_x_2026", outlet_id: "it_x", outlet_name: "Outlet <X>", country: "ITA", monitored: true, partner: "xinhua", partner_label: "Xinhua",
     kind: "mou", kind_label: "memorandum", signed_on: "2026-11-02", precision: "day", confidence: "stated", notes: "Signed in Rome.",
     predates_collection: false, comparison: "eligible", source: {url: "https://example.org/deal", title: "Deal", publisher: "Outlet X"},
     verification: {status: "verified", checked_on: "2026-10-28", page_date: "2026-11-02", note: "States it."},
     links: [{label: "Deal signed", publisher: "Outlet X", url: "https://example.org/deal"}]},
    {id: "it_y_2019", outlet_id: "it_y", outlet_name: "Outlet Y", country: "ITA", monitored: true, partner: "cmg", partner_label: "China Media Group",
     kind: "content_exchange", kind_label: "content exchange", signed_on: "2019-03", precision: "month", ended_on: "2022-06", confidence: "reported",
     notes: "", predates_collection: true, comparison: "predates_collection", source: {url: "javascript:alert(1)", title: "Bad"},
     verification: {status: "source_moved", checked_on: "2026-10-28", page_date: "2019-03", previous_url: "https://example.org/old", note: "Moved."},
     links: [{label: "Bad link", url: "javascript:alert(1)"}]},
    {id: "unmonitored_z", outlet_id: null, outlet_name: "Paper Z", country: null, monitored: false, partner: "china_daily", partner_label: "China Daily",
     kind: "supplement", kind_label: "supplement", signed_on: "2025", precision: "year", confidence: "stated", predates_collection: true,
     comparison: "unmonitored", source: {url: "https://example.org/z"}, links: [{label: "Z", url: "https://example.org/z"}],
     verification: {status: "unverified", checked_on: "2026-10-28", page_date: null, note: "Gone."}}
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
assert(h.indexOf("Provisional: sources verified on 2026-10-28, not hand coded.") !== -1, "D7 wording while provisional");
assert(S.WORDING === "sources verified on 2026-10-28, not hand coded", "fallback wording");
assert(h.indexOf("source checked 28 October 2026") !== -1 && h.indexOf("source moved, new page checked 28 October 2026") !== -1 &&
       h.indexOf("source not verified") !== -1, "verification tags per entry");
assert(h.indexOf("1 source has moved") !== -1 && h.indexOf("1 could not be verified") !== -1, "moved and unverified counts");
assert(/timing, not cause\. Most deals were signed before collection began on 3 September 2026/.test(h), "comparison intro: timing, not cause, and most predate collection");
var nov = JSON.parse(JSON.stringify(data)); delete nov.verification;
slot = {hidden: true, innerHTML: ""};
M.get("syndication").render(prov, nov, api(slot));
assert(slot.innerHTML.indexOf("sources verified on 2026-10-28, not hand coded") !== -1, "wording falls back without data.verification");
var chk = {status: "checked", label: "Syndication deals", check: {state: "passed", n: 20, coded: 20, statistic: "share", value: 0.95, threshold: 0.9}};
M.get("syndication").render(chk, data, api(slot));
assert(slot.innerHTML.indexOf("not hand coded") === -1, "no provisional wording once checked by hand");

/* No comparison yet: the timeline, and a line saying why. */
var only = JSON.parse(JSON.stringify(data)); only.comparisons = [];
slot = {hidden: true, innerHTML: ""};
M.get("syndication").render(prov, only, api(slot));
assert(slot.innerHTML.indexOf("No deal yet falls where a comparison is possible") !== -1 && slot.innerHTML.indexOf("3 September 2026") !== -1, "no comparison line");
assert(slot.innerHTML.indexOf("timing, not cause") !== -1 && slot.innerHTML.indexOf("Most deals were signed before collection began") !== -1, "no comparison line keeps timing, not cause");
/* Early in collection the eligible range is empty: no impossible "between 1 October and 30 September". */
var early = JSON.parse(JSON.stringify(only)); early.eligible_from = "2026-10-01"; early.eligible_until = "2026-09-29";
slot = {hidden: true, innerHTML: ""};
M.get("syndication").render(prov, early, api(slot));
assert(slot.innerHTML.indexOf("No deal can be compared yet") !== -1 && slot.innerHTML.indexOf("1 October 2026") !== -1 &&
       slot.innerHTML.indexOf("29 September 2026") === -1, "an empty eligible range says when the first comparison can come");
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

/* Stress test of 2026-10-29. One agreement listed for two outlets is one agreement; entries without a
   source check are marked and counted; "Most" only above half; links say they open a new tab. */
var two = JSON.parse(JSON.stringify(data));
var twin = JSON.parse(JSON.stringify(two.timeline[0])); twin.id = "it_x2_2026"; twin.outlet_name = "Outlet X English";
delete twin.verification;
two.timeline.splice(1, 0, twin);
two.counts.entries = 4; two.counts.unchecked = 1;
slot = {hidden: true, innerHTML: ""};
M.get("syndication").render(prov, two, api(slot));
h = slot.innerHTML;
assert(S.agreementCount(two.timeline) === 3, "two outlets, one agreement");
assert(h.indexOf("4 entries for 3 agreements") !== -1, "the lead counts entries and agreements: " + h.slice(0, 400));
assert(h.indexOf("2 of the 4 were signed before collection began") !== -1, "predating with its denominator");
assert(h.indexOf("source not checked yet") !== -1 && h.indexOf("1 entry has not been checked yet") !== -1, "unchecked marked and counted");
assert(h.indexOf("Most deals were signed") === -1 && h.indexOf("2 of the 4 entries were signed before collection began") !== -1, "no Most at exactly half");
assert(h.indexOf('<span class="syn-sr"> (opens in a new tab)</span></a>') !== -1, "new tab said to screen readers");
assert(h.indexOf("undefined") === -1 && h.indexOf("null") === -1, "no undefined or null in the page");
assert(S.agreementCount(data.timeline) === 3 && M.get("syndication").render, "distinct deals stay apart");

/* A schema 18 side file (before the source checks): no verification, no links, no verification counts. */
var old = JSON.parse(JSON.stringify(data));
delete old.verification;
old.counts = {entries: 3, monitored: 2, unmonitored: 1, predates_collection: 2, eligible: 1, compared: 1, confounded: 1, invalid: 0};
old.timeline.forEach(function (e) { delete e.verification; delete e.links; });
slot = {hidden: true, innerHTML: ""};
M.get("syndication").render(prov, old, api(slot));
h = slot.innerHTML;
assert(h.indexOf("syn-timeline") !== -1 && (h.match(/class="syn-entry/g) || []).length === 3, "old shape still lists the timeline");
assert(h.indexOf('href="https://example.org/deal"') !== -1 && h.indexOf("undefined") === -1, "old shape links from source");
assert(h.indexOf("source not checked yet") !== -1, "old shape entries are not claimed as checked");
assert(h.indexOf("sources verified on") === -1 && h.indexOf("the sources have not been checked yet") !== -1, "old shape: no verified wording");

/* Long names and right to left text wrap and stay escaped. */
var longy = JSON.parse(JSON.stringify(data));
longy.timeline[0].outlet_name = "Radiodiffusion Television Senegalaise " + new Array(20).join("Nationale ") + "\u0627\u0644\u062c\u0632\u064a\u0631\u0629";
slot = {hidden: true, innerHTML: ""};
M.get("syndication").render(prov, longy, api(slot));
assert(slot.innerHTML.indexOf("\u0627\u0644\u062c\u0632\u064a\u0631\u0629") !== -1, "right to left name kept");

/* Many ruleset and gate changes read as one line each, with the full list behind a details toggle. */
var many = [];
["2026-09-11", "2026-09-14", "2026-09-15"].forEach(function (d, i) { many.push({type: "ruleset_change", date: d, version: "r" + i, text: "the signature ruleset changed to r" + i + " on " + d + ", inside the window"}); });
many.push({type: "gate_change", date: "2026-09-15", version: "g1", text: "the relevance gate changed to g1 on 2026-09-15, inside the window"});
many.push({type: "zero_discovery", side: "after", value: 25, text: "the outlet discovered nothing on 25 of the 28 days after"});
var ch = S.confounderHtml(many);
assert(ch.indexOf("the signature ruleset changed 3 times inside the window, from 11 September 2026 to 15 September 2026") === 0, "grouped ruleset changes: " + ch);
assert(ch.indexOf("the relevance gate changed to g1 on 2026-09-15") !== -1 && ch.indexOf("discovered nothing on 25") !== -1, "single gate change and others kept");
assert(ch.indexOf("<summary>All 5</summary>") !== -1 && (ch.match(/<li>/g) || []).length === 5, "full list behind a toggle");
assert(S.confounderHtml([many[4]]).indexOf("<details") === -1 && S.confounderHtml([]) === "", "no toggle when nothing is grouped");

/* A long timeline shows the newest SHOWN entries, the rest behind one toggle, none lost or duplicated. */
var long = []; for (var i = 0; i < 51; i++) { var e = JSON.parse(JSON.stringify(data.timeline[0])); e.id = "e" + i; e.outlet_name = "Outlet " + i; long.push(e); }
var th = S.timelineHtml(long, api({}));
var shownPart = th.split("<details")[0];
assert((shownPart.match(/class="syn-entry/g) || []).length === S.SHOWN, "only the newest entries open");
assert((th.match(/class="syn-entry/g) || []).length === 51 && th.indexOf("Show the other " + (51 - S.SHOWN) + " entries") !== -1, "all 51 kept behind one toggle");
assert(th.indexOf('start="' + (S.SHOWN + 1) + '"') !== -1, "numbering continues inside the toggle");
assert(S.timelineHtml(long.slice(0, S.SHOWN), api({})).indexOf("<details") === -1, "no toggle for a short timeline");
assert(M.noticeLines({syndication: {status: "provisional", label: "Syndication deals"}})[0].indexOf("sources were verified, but it is not hand coded") !== -1, "D7 wording in the notes");
assert(M.noticeLines({doctrine: {status: "provisional", label: "Doctrine"}})[0].indexOf("no hand check of it has passed yet") !== -1, "other measures keep the hand check wording");

console.log("syndication smoke ok");
