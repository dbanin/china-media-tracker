/* Smoke test for the preliminary figures banner (C.preliminaryBanner). Run with: node tests/smoke_preliminary.js,
   or through tests/test_preliminary_banner.py, which falls back to JavaScriptCore where node is missing. */
var C = require("../docs/compute.js");
function assert(cond, msg) { if (!cond) { console.error("FAIL: " + msg); process.exit(1); } }
var B = {
  heading: "Preliminary figures", heading_em: "Preliminary", lead: "L.", lead_settled: "LS.", evidence: "E.", evidence_model_only_checked: "EM.",
  apart: {both: "AB.", model_only: "AM.", relay: "AR."}, withheld: {model_only: "WM.", relay: "WR."},
  checked: {none: "CN.", none_published: "CNP.", model_only: "CM.", relay: "CR.", both: "CB."}, link_text: "How", link_href: "#method"
};
/* No content block, or no meta at all. */
assert(C.preliminaryBanner(null, {}) === null && C.preliminaryBanner({}, {}) === null, "no content, no banner");
var b = C.preliminaryBanner(B, null);
assert(b.text === "L. E. AB. CN." && b.status.relay === "provisional" && b.status.modelOnly === "provisional", "nothing known: provisional, unchecked");
assert(b.heading === "Preliminary figures" && b.headingEm === "Preliminary" && b.linkHref === "#method" && b.linkText === "How", "heading and link");
/* Today's meta: no study has reported, model_only_state_origin a bare count. */
var today = {relay_measured: true, relay_publishable: false, relay_provisional: true, model_only_state_origin: 1123,
             relay_human_study: {passed: false, state: "waiting_for_labels"}};
assert(C.preliminaryBanner(B, today).text === "L. E. AB. CN.", "today: both provisional, nothing checked");
/* The key the export is adding, absent or an object with a pending status. */
assert(C.preliminaryBanner(B, Object.assign({}, today, {model_only_state_origin: undefined})).text === "L. E. AB. CN.", "absent key");
assert(C.preliminaryBanner(B, Object.assign({}, today, {model_only_state_origin: {count: 1123, status: "provisional"}})).text === "L. E. AB. CN.", "object, provisional");
/* The hand coding study of unchecked state sourcing passed: the last sentence must not say nothing was checked. */
var relayPassed = Object.assign({}, today, {relay_publishable: true, relay_provisional: false, relay_human_study: {passed: true, state: "passed", kappa_bc: 0.74}});
b = C.preliminaryBanner(B, relayPassed);
assert(b.text === "L. E. AM. CR." && b.status.relay === "passed", "relay passed: " + b.text);
assert(b.sentences[b.sentences.length - 1] !== B.checked.none, "a passed study is never called unchecked");
assert(C.preliminaryBanner(B, Object.assign({}, today, {relay_human_study: {state: "passed"}})).status.relay === "passed", "state alone is enough");
/* The model only check passed, by status or by flag. */
b = C.preliminaryBanner(B, Object.assign({}, today, {model_only_state_origin: {count: 1123, status: "passed"}}));
assert(b.text === "L. EM. AR. CM.", "model only passed: " + b.text);
assert(C.preliminaryBanner(B, Object.assign({}, today, {model_only_state_origin: {passed: true}})).status.modelOnly === "passed", "passed flag");
assert(C.preliminaryBanner(B, Object.assign({}, today, {model_only_state_origin: {status: "PASSED"}})).status.modelOnly === "passed", "status case");
/* Both passed: no provisional sentence, the settled lead. */
b = C.preliminaryBanner(B, Object.assign({}, relayPassed, {model_only_state_origin: {status: "passed"}}));
assert(b.text === "LS. EM. CB.", "both passed: " + b.text);
/* A failed study withholds; nothing is then called provisional, and nothing shown has passed. */
b = C.preliminaryBanner(B, Object.assign({}, today, {relay_provisional: false, relay_human_study: {passed: false, state: "failed"}}));
assert(b.text === "L. E. AM. WR. CNP." && b.status.relay === "withheld", "relay failed: " + b.text);
b = C.preliminaryBanner(B, Object.assign({}, today, {relay_provisional: false, relay_human_study: {state: "waiting_for_labels"}}));
assert(b.status.relay === "withheld", "withheld by the export's own flags");
b = C.preliminaryBanner(B, Object.assign({}, relayPassed, {model_only_state_origin: {status: "failed"}}));
assert(b.text === "LS. E. WM. CR.", "model only failed, relay passed: " + b.text);
/* Missing optional keys fall back rather than print undefined. */
b = C.preliminaryBanner({lead: "L.", evidence: "E."}, relayPassed);
assert(b.text === "L. E." && b.linkHref === "#method" && b.text.indexOf("undefined") === -1, "sparse content");
console.log("preliminary smoke ok");
