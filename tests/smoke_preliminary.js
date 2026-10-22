/* Smoke test for the preliminary figures banner (C.preliminaryBanner). Run with: node tests/smoke_preliminary.js,
   or through tests/test_preliminary_banner.py, which falls back to JavaScriptCore where node is missing. */
var C = require("../docs/compute.js");
function assert(cond, msg) { if (!cond) { console.error("FAIL: " + msg); process.exit(1); } }
var B = {
  heading: "Preliminary figures", heading_em: "Preliminary", lead: "L.", lead_settled: "LS.", evidence: "E.", undetermined: "U.",
  apart: {relay: "AR."}, withheld: {relay: "WR."},
  checked: {none: "CN.", none_published: "CNP.", relay: "CR."}, link_text: "How", link_href: "#method"
};
/* No content block, or no meta at all. */
assert(C.preliminaryBanner(null, {}) === null && C.preliminaryBanner({}, {}) === null, "no content, no banner");
var b = C.preliminaryBanner(B, null);
assert(b.text === "L. E. U. AR. CN." && b.status.relay === "provisional", "nothing known: provisional, unchecked: " + b.text);
assert(b.heading === "Preliminary figures" && b.headingEm === "Preliminary" && b.linkHref === "#method" && b.linkText === "How", "heading and link");
/* Today's meta: no study has reported. */
var today = {relay_measured: true, relay_publishable: false, relay_provisional: true, undetermined: {count: 1253},
             relay_human_study: {passed: false, state: "waiting_for_labels"}};
assert(C.preliminaryBanner(B, today).text === "L. E. U. AR. CN.", "today: relay provisional, nothing checked");
/* Undetermined items never rejoin, so nothing in meta changes their sentence, not even an old passed status. */
assert(C.preliminaryBanner(B, Object.assign({}, today, {model_only_state_origin: {status: "passed"}})).text === "L. E. U. AR. CN.", "an old passed status is ignored");
assert(C.preliminaryBanner(B, Object.assign({}, today, {undetermined: {check: {state: "reported", confirmed_share: 1}}})).text === "L. E. U. AR. CN.", "a reported check rejoins nothing");
assert(!("modelOnly" in C.preliminaryBanner(B, today).status), "no rejoin status is read");
/* The hand coding study of unchecked state sourcing passed: the last sentence must not say nothing was checked. */
var relayPassed = Object.assign({}, today, {relay_publishable: true, relay_provisional: false, relay_human_study: {passed: true, state: "passed", kappa_bc: 0.74}});
b = C.preliminaryBanner(B, relayPassed);
assert(b.text === "LS. E. U. CR." && b.status.relay === "passed", "relay passed: " + b.text);
assert(b.sentences[b.sentences.length - 1] !== B.checked.none, "a passed study is never called unchecked");
assert(C.preliminaryBanner(B, Object.assign({}, today, {relay_human_study: {state: "passed"}})).status.relay === "passed", "state alone is enough");
/* A failed study withholds; nothing is then called provisional, and nothing shown has passed. */
b = C.preliminaryBanner(B, Object.assign({}, today, {relay_provisional: false, relay_human_study: {passed: false, state: "failed"}}));
assert(b.text === "LS. E. U. WR. CNP." && b.status.relay === "withheld", "relay failed: " + b.text);
b = C.preliminaryBanner(B, Object.assign({}, today, {relay_provisional: false, relay_human_study: {state: "waiting_for_labels"}}));
assert(b.status.relay === "withheld", "withheld by the export's own flags");
/* Missing optional keys fall back rather than print undefined. */
b = C.preliminaryBanner({lead: "L.", evidence: "E."}, relayPassed);
assert(b.text === "L. E." && b.linkHref === "#method" && b.text.indexOf("undefined") === -1, "sparse content");
console.log("preliminary smoke ok");
