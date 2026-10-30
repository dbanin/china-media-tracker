/* Smoke test for docs/measures.js and every docs/m_<id>.js. Run with: node tests/measures_smoke.js
   (or under jsc through a shim that defines require, console and process). Each measure's own
   assertions go in tests/smoke_m_<id>.js, written the same way. */
var M = require("../docs/measures.js");
var IDS = ["alteration", "speed", "doctrine", "spikes", "engagement", "syndication"];
var MODS = {};
IDS.forEach(function (id) { MODS[id] = require("../docs/m_" + id + ".js"); });
function assert(cond, msg) { if (!cond) { console.error("FAIL: " + msg); process.exit(1); } }

/* Status helpers. */
assert(M.statusOf(undefined) === "off" && M.statusOf({status: "bogus"}) === "off", "missing or unknown status is off");
assert(M.isOff(null) && !M.isOff({status: "provisional"}), "isOff");
assert(M.statusChip({status: "off"}) === "" && M.statusChip(undefined) === "", "no chip when off");
var prov = {status: "provisional", label: "Alteration score", check: {state: "incomplete", n: 40, coded: 12}};
var chip = M.statusChip(prov);
assert(chip.indexOf("m-chip-provisional") !== -1 && chip.indexOf("Provisional") !== -1 && chip.indexOf("12 of 40 coded") !== -1, "provisional chip");
var chk = {status: "checked", label: "Speed", check: {state: "passed", n: 60, coded: 60, statistic: "bucket agreement", value: 0.9, threshold: 0.85, checked_on: "2026-10-20"}};
assert(M.statusChip(chk).indexOf("m-chip-checked") !== -1 && M.statusChip(chk).indexOf("0.90") !== -1, "checked chip");
var wh = {status: "withheld", label: "Doctrine terms", check: {state: "failed", n: 30, coded: 30, statistic: "precision", value: 0.7, threshold: 0.8, checked_on: "2026-10-21"}};
assert(M.statusChip(wh).indexOf("m-chip-withheld") !== -1, "withheld chip");
assert(M.provisionalSuffix(prov) === " (provisional)" && M.provisionalSuffix(wh) === " (withheld)", "suffix provisional, withheld");
assert(M.provisionalSuffix(chk) === "" && M.provisionalSuffix(undefined) === "", "no suffix when checked or off");
assert(M.statusChip({status: "provisional", label: "<b>"}).indexOf("<b>") === -1, "chip escapes");

/* hideIfOff works on any object with a hidden property. */
var sec = {hidden: false};
assert(M.hideIfOff(sec, {status: "off"}) === false && sec.hidden === true, "hidden when off");
assert(M.hideIfOff(sec, prov) === true && sec.hidden === false, "shown when provisional");
assert(M.hideIfOff(null, prov) === true, "no element is fine");

/* Sections, slots and notes. */
var blocksOff = {alteration: {status: "off"}, speed: {status: "off"}};
assert(!M.sectionVisible(M.SECTIONS[0], blocksOff) && M.sectionVisible(M.SECTIONS[0], {speed: prov}), "section shows when one measure is on");
IDS.forEach(function (id) {
  assert(M.SLOTS[id], "slot for " + id);
  assert(M.SECTIONS.some(function (s) { return s.measures.indexOf(id) !== -1; }), "section for " + id);
});
var lines = M.noticeLines({alteration: prov, speed: chk, doctrine: wh, spikes: {status: "off"}});
assert(lines.length === 2 && lines[0].indexOf("Alteration score is provisional") === 0 && lines[1].indexOf("Doctrine terms is withheld") === 0, "one note per provisional or withheld measure");
assert(M.noticeLines(undefined).length === 0, "no notes without measures");
var paras = M.methodParagraphs({alteration: Object.assign({method_text: "First.\n\nSecond."}, prov), spikes: {status: "off", method_text: "x"}});
assert(paras.length === 2 && paras[0].indexOf("Alteration score") !== -1 && paras[1].indexOf("Second.") !== -1, "method paragraphs skip off measures");
var mb = M.methodBlocks({alteration: Object.assign({method_text: "First.\n\nSecond."}, prov), spikes: {status: "off", method_text: "x"}});
assert(mb.length === 1 && mb[0].indexOf("<details") === 0 && mb[0].indexOf("Alteration score") !== -1 && (mb[0].match(/<p>/g) || []).length === 2, "one closed block per measure, off measures skipped");
assert(M.methodBlocks({}).length === 0, "no blocks without measures");
assert(M.emptyState(wh, {}, "x").indexOf("withheld") !== -1 && M.emptyState(prov, null, "the alteration").indexOf("did not load") !== -1, "empty states");

/* Every stub registers and renders off, provisional with no data, and provisional with an empty file. */
IDS.forEach(function (id) {
  assert(MODS[id].ID === id && M.get(id) && typeof M.get(id).render === "function", id + " registered");
  var slot = {hidden: false, innerHTML: "stale"};
  var api = {id: id, slot: slot, section: null, meta: {}, names: {}, esc: M.esc, countryName: function (i) { return i; }, M: M};
  M.get(id).render({status: "off"}, null, api);
  assert(slot.hidden === true && slot.innerHTML === "", id + " off hides and clears its slot");
  M.get(id).render(undefined, null, api);
  assert(slot.hidden === true, id + " with no block is off");
  M.get(id).render(prov, null, api);
  assert(slot.hidden === false && slot.innerHTML.length > 0, id + " provisional without data says so");
  M.get(id).render(prov, {}, api);
  assert(slot.hidden === false, id + " provisional with an empty file");
  M.get(id).render(wh, {}, api);
  assert(slot.innerHTML.indexOf("withheld") !== -1, id + " withheld says so");
  M.get(id).render(prov, {}, {id: id, slot: null});
});

/* renderAll isolates a failing renderer; findingsHtml and sparkAll skip off measures. */
M.register("zz_fake", {render: function () { throw new Error("boom"); }, findingsExtra: function () { return "<p>x</p>"; },
                       sparkMarks: function () { return "Marked."; }});
var failed = M.renderAll({}, {}, function (id) { return {id: id, slot: {hidden: false, innerHTML: ""}}; });
assert(failed.length === 1 && failed[0].indexOf("zz_fake") === 0, "a failing renderer is named and the rest run");
assert(M.findingsHtml({zz_fake: {status: "off"}}, {}, function () { return {}; }) === "", "no findings when off");
assert(M.findingsHtml({zz_fake: prov}, {}, function () { return {}; }) === "<p>x</p>", "findings when on");
assert(M.sparkAll({zz_fake: prov}, {}, function () { return {}; }, {}).join() === "Marked.", "spark hint when on");
assert(M.sparkAll({}, {}, function () { return {}; }, {}).length === 0, "no spark marks when off");
var threw = false;
try { M.register("bad", {}); } catch (e) { threw = true; }
assert(threw, "register refuses a spec without render");

console.log("measures smoke ok");
