/* Smoke test for docs/m_spikes.js (unusual days). Run with: node tests/smoke_m_spikes.js, or under jsc
   through scratchpad/measures/run_jsc_measures.sh. */
(function () {
  var M = require("../docs/measures.js");
  var S = require("../docs/m_spikes.js");
  function assert(cond, msg) { if (!cond) { console.error("FAIL: " + msg); process.exit(1); } }
  function spike(date, iso, status, extra) {
    var s = {date: date, country: iso, count: 9, uniq: 7, expected: 1.2, p: 1e-5, ratio: 7.5, status: status,
             descriptors: [], confounders: [], top_outlets: [], top_items: [], items: []};
    for (var k in (extra || {})) s[k] = extra[k];
    return s;
  }
  var prov = {status: "provisional", label: "Unusual days", check: {state: "none", n: 0, coded: 0}};
  var data = {last_complete_day: "2026-10-13", first_tested: "2026-09-17", tests: 5963, expected_false_flags: 0.095, spikes: [
    spike("2026-10-12", "ITA", "clean", {descriptors: ["one_item"]}),
    spike("2026-10-10", "WLD", "clean"),
    spike("2026-10-09", "MYS", "check", {confounders: [{reason: "gate_change", detail: "gate <b>moved</b>", affects: "count"},
                                                       {reason: "rediscovery", detail: "rate only", affects: "rate"}]}),
    spike("2026-10-08", "SGP", "check", {confounders: [{reason: "rediscovery", detail: "rate only", affects: "rate"}]}),
    spike("2026-09-01", "GBR", "clean")   /* older than the last 30 complete days */
  ]};

  assert(S.ID === "spikes" && M.get("spikes") && typeof M.get("spikes").sparkMarks === "function", "registered with sparkMarks");

  /* The recent lists: clean and to check apart, newest first, the last 30 complete days only. */
  var L = S.recentLists(data);
  assert(L.since === "2026-09-14" && L.last === "2026-10-13", "the 30 day window ends on the last complete day");
  assert(L.clean.length === 2 && L.clean[0].country === "ITA" && L.clean[1].country === "WLD", "clean days newest first, old ones left out");
  assert(L.check.length === 2 && L.check[0].country === "MYS", "days to check apart");
  var many = {last_complete_day: "2026-10-13", spikes: []};
  for (var i = 1; i <= 8; i++) many.spikes.push(spike("2026-10-0" + i, "C" + i, "clean"));
  var L2 = S.recentLists(many);
  assert(L2.clean.length === 5 && L2.moreClean === 3 && L2.clean[0].date === "2026-10-08", "five most recent, the rest counted");

  /* Reasons name only what touches the count; rediscovery touches the rate alone. */
  assert(S.reasonText(data.spikes[2]) === S.REASONS.gate_change, "count reasons only");
  assert(S.reasonText(data.spikes[3]) === "", "a rate only note is no reason");
  assert(S.descriptorText(data.spikes[0]) === "mostly one item", "descriptors");
  assert(S.shortDate("2026-10-02") === "2 Oct", "short dates");

  /* Rendering: provisional heading and chip, clean days, muted days with reasons, the chance line, escaping. */
  var slot = {hidden: true, innerHTML: ""};
  var api = {id: "spikes", slot: slot, section: null, meta: {}, names: {}, esc: M.esc, M: M,
             countryName: function (iso) { return {ITA: "Italy", WLD: "Worldwide distribution", MYS: "Malaysia", SGP: "Singapore"}[iso] || iso; }};
  M.get("spikes").render(prov, data, api);
  var h = slot.innerHTML;
  assert(slot.hidden === false, "shown when provisional");
  assert(h.indexOf('aria-label="Unusual days (provisional)"') !== -1 && h.indexOf("m-chip-provisional") !== -1, "heading reads provisional, with the chip");
  assert(h.indexOf("Italy") !== -1 && h.indexOf('data-iso="ITA"') !== -1 && h.indexOf('data-day="2026-10-12"') !== -1, "clean day listed with its country and day");
  assert(h.indexOf("sp-muted") !== -1 && h.indexOf("To check: " + S.REASONS.gate_change) !== -1, "a confounded day is muted with its reason");
  assert(h.indexOf("<b>moved</b>") === -1 && h.indexOf("&lt;b&gt;") !== -1, "details are escaped");
  assert(h.indexOf("Great Britain") === -1 && h.indexOf("GBR") === -1, "a day older than 30 days is not listed");
  assert(h.indexOf("5,963 country days tested") !== -1, "the number of tests and the chance line");
  assert(h.indexOf("rough guide") !== -1 && h.indexOf("under 0.1") !== -1, "the chance estimate carries its caveat");
  assert(h.indexOf("no more than 3 from any one outlet") !== -1, "the sub line says how days are counted");
  assert(h.indexOf("sp-relabel") === -1, "no relabelling note without a ruleset change");

  /* The tested count beside the placements, and the old file shape still reads. */
  assert(S.whatText(spike("2026-10-12", "ITA", "clean", {tested: 6})) ===
         "9 state origin placements (7 underlying items); 6 counted, no outlet adding more than 3, against about 1 expected", "what was tested");
  assert(S.whatText(spike("2026-10-12", "ITA", "clean")) === "9 state origin placements (7 underlying items) against about 1 expected", "an old file without tested");

  /* Days to check only because relabelling is unfinished are explained plainly. */
  var relabel = {last_complete_day: "2026-10-13", tests: 100, expected_false_flags: 0.2, spikes: [
    spike("2026-10-12", "ZAF", "check", {confounders: [{reason: "ruleset_change", detail: "ruleset r9 took effect", affects: "count", ruleset_date: "2026-10-12"}]}),
    spike("2026-10-11", "WLD", "check", {confounders: [{reason: "ruleset_change", detail: "ruleset r9 took effect", affects: "count", ruleset_date: "2026-10-12"}]})]};
  M.get("spikes").render(prov, relabel, api);
  var hr = slot.innerHTML;
  assert(hr.indexOf("sp-relabel") !== -1 && hr.indexOf("2 of these days are to check because the labelling rules changed on 12 Oct") !== -1, "why the days are to check");
  assert(hr.indexOf("To check: " + S.REASONS.ruleset_change) !== -1 && S.REASONS.ruleset_change.indexOf("relabelling") !== -1, "the reason names the relabelling");
  assert(hr.indexOf("about 0.2") !== -1 && !/[\u2013\u2014]/.test(hr), "the estimate, no dashes");
  assert(S.relabelText([]) === "" && S.relabelText([spike("2026-10-12", "ZAF", "check")]) === "", "no note without the reason");
  assert(h.indexOf("Category") === -1 && !/[\u2013\u2014]/.test(h), "no category letters and no dashes");

  /* Checked: no suffix. Off: hidden and cleared. Withheld: says so. No spikes: an empty state. */
  M.get("spikes").render({status: "checked", check: {state: "passed", n: 20, coded: 20, statistic: "precision", value: 0.9, threshold: 0.8}}, data, api);
  assert(slot.innerHTML.indexOf("Unusual days (provisional)") === -1 && slot.innerHTML.indexOf("m-chip-checked") !== -1, "checked has no suffix");
  M.get("spikes").render({status: "off"}, data, api);
  assert(slot.hidden === true && slot.innerHTML === "", "off hides and clears");
  M.get("spikes").render({status: "withheld", check: {state: "failed", n: 20, coded: 20, statistic: "precision", value: 0.6, threshold: 0.8}}, data, api);
  assert(slot.innerHTML.indexOf("withheld") !== -1 && slot.innerHTML.indexOf("Italy") === -1, "withheld shows no days");
  M.get("spikes").render(prov, {last_complete_day: "2026-10-13", tests: 10, expected_false_flags: 0, spikes: []}, api);
  assert(slot.innerHTML.indexOf("No unusual days") !== -1, "an empty list says so");
  M.get("spikes").render(prov, null, api);
  assert(slot.innerHTML.indexOf("did not load") !== -1, "a missing file says so");

  /* Timeline marks: one per day in scope, hollow when every day is to check; nothing off the timeline. */
  var days = ["2026-10-08", "2026-10-09", "2026-10-10", "2026-10-12"];
  var both = S.marksFor(data, days, "both");
  assert(both.length === 4 && both[0].day === "2026-10-08" && both[0].clean === false && both[3].clean === true, "marks for every day on the timeline");
  assert(S.marksFor(data, days, "wires").length === 1 && S.marksFor(data, days, "wires")[0].spikes[0].country === "WLD", "wires scope: the worldwide stratum only");
  assert(S.marksFor(data, days, "country").every(function (m) { return m.spikes.every(function (s) { return s.country !== "WLD"; }); }), "country scope leaves the wires out");
  var drawn = [];
  function node(tag) {
    var n = {tag: tag, attrs: {}, text: null};
    n.attr = function (k, v) { n.attrs[k] = v; return n; };
    n.append = function (t) { var c = node(t); (n.children = n.children || []).push(c); if (t !== "title") drawn.push(c); return c; };
    n.text = function (t) { n.textValue = t; return n; };
    return n;
  }
  var svg = node("svg");
  var hint = M.get("spikes").sparkMarks(prov, data, api, {svg: svg, x: function (d) { var k = days.indexOf(d); return k === -1 ? null : k * 100; }, days: days, w: 1000, h: 40});
  assert(drawn.length === 4 && drawn.every(function (n) { return n.tag === "rect" && /spike-mark/.test(n.attrs["class"]); }), "one mark per day");
  assert(/spike-check/.test(drawn[0].attrs["class"]) && !/spike-check/.test(drawn[3].attrs["class"]), "hollow when to check, filled when clean");
  assert(drawn[3].children[0].textValue.indexOf("Italy") !== -1, "each mark names its countries");
  assert(typeof hint === "string" && hint.indexOf("unusual days (provisional)") !== -1, "a hint sentence for the timeline");
  drawn = [];
  assert(M.get("spikes").sparkMarks(prov, {spikes: []}, api, {svg: node("svg"), x: function () { return 0; }, days: days}) === "" && drawn.length === 0, "no marks, no hint");
  assert(M.get("spikes").sparkMarks(prov, data, api, null) === "", "no timeline is fine");

  /* Selecting without a page does nothing and never throws. */
  var r = S.select("ITA", "2026-10-12", null);
  assert(r.day === false && r.country === false, "select needs a page");

  /* With app.js's hook on the page, select sends the event alone and leaves the controls alone; without
     it, the scrubber and the ranked list row still open the day and the country. */
  var hadCE = typeof CustomEvent, hadEv = typeof Event, G = (function () { return this; }());
  if (hadCE === "undefined") G.CustomEvent = function (type, init) { this.type = type; this.detail = init && init.detail; };
  if (hadEv === "undefined") G.Event = function (type) { this.type = type; };
  function fakeDoc() {
    var d = {sent: [], scrubbed: [], clicked: [], scrolled: 0};
    var scrub = {value: "", dispatchEvent: function (e) { d.scrubbed.push(e.type + ":" + scrub.value); }};
    var card = {scrollIntoView: function () { d.scrolled++; }};
    d.dispatchEvent = function (e) { d.sent.push(e); return true; };
    d.getElementById = function (id) { return id === "scrub" ? scrub : id === "map-card" ? card : null; };
    d.querySelector = function (q) { return /data-iso="ITA"/.test(q) ? {click: function () { d.clicked.push("ITA"); }} : null; };
    return d;
  }
  var dA = fakeDoc(), rA = S.select("WLD", "2026-10-10", dA, {selectCountry: function () {}, setDay: function () {}});
  assert(rA.event === true && dA.sent.length === 1 && dA.sent[0].type === "tracker:select", "the event is sent");
  assert(dA.sent[0].detail.iso === "WLD" && dA.sent[0].detail.day === "2026-10-10", "the event carries the country and the day");
  var det = dA.sent[0].detail;
  assert(det.measure === "a" && det.window === "day" && det.distribution === "both" && Object.keys(det).length === 5,
         "the event asks for state origin, that one day, both distributions");
  assert(JSON.stringify(S.selectDetail("ITA", "2026-10-12")) === '{"iso":"ITA","day":"2026-10-12","measure":"a","window":"day","distribution":"both"}', "the exact event shape");
  assert(dA.scrubbed.length === 0 && dA.clicked.length === 0 && dA.scrolled === 0, "with the hook the page's controls are left alone");
  var dB = fakeDoc(), rB = S.select("ITA", "2026-10-12", dB, null);
  assert(rB.event === true && dB.sent.length === 1, "without the hook the event is still sent");
  assert(rB.day === true && dB.scrubbed[0] === "input:3", "without the hook the scrubber moves to the day");
  assert(rB.country === true && dB.clicked[0] === "ITA" && dB.scrolled === 1, "without the hook the row is clicked and the map shown");
  var dC = fakeDoc(), rC = S.select("WLD", "2026-10-10", dC, null);
  assert(rC.day === true && rC.country === false, "without the hook a country missing from the list is not opened");
  /* Without the hook the measure, window and distribution buttons are pressed unless already active. */
  var dD = fakeDoc(), pressed = [];
  function btn(name, active) { return {classList: {contains: function (c) { return c === "active" && active; }}, click: function () { pressed.push(name); }}; }
  var buttons = {measure: btn("a", false), window: btn("1", false), distribution: btn("both", true)};
  var baseQS = dD.querySelector;
  dD.querySelector = function (q) {
    if (/data-measure="a"/.test(q)) return buttons.measure;
    if (/data-window="1"/.test(q)) return buttons.window;
    if (/data-distribution="both"/.test(q)) return buttons.distribution;
    return baseQS(q);
  };
  var rD = S.select("ITA", "2026-10-12", dD, null);
  assert(rD.controls === true && pressed.join(",") === "a,1", "state origin and that day are pressed, Both already active is left");
  if (hadCE === "undefined") delete G.CustomEvent;
  if (hadEv === "undefined") delete G.Event;
  assert(M.get("spikes").findingsExtra(prov, data, api) === "", "nothing in the findings box itself");

  /* What the day was about: the export's context and the researched news, or content.json's notes for a
     file exported before them. Wording is "coincides with", never a cause, with the caveat. */
  var ctxSpike = spike("2026-10-12", "ITA", "check", {
    top_outlets: [{id: "it_a", name: "Paper A", placements: 3}],
    top_items: [{item: 1, placements: 2, title: "Forum <opens>"}, {item: 2, placements: 1, title: "Second"}],
    context: {version: 1, state: {placements: 3, themes: [{id: "economy", label: "Trade and economy", n: 3}], terms: [{term: "Financial Street", n: 2, lift: 9}]},
              day_before: {date: "2026-10-11", placements: 1, themes: [{id: "other", label: "Other", n: 1}], terms: []},
              other: {articles: 12, themes: [{id: "diplomacy", label: "Diplomacy and summits", n: 4}], terms: [{term: "Hormuz", n: 3, lift: 5}]},
              shared_terms: ["Financial Street"], baseline_articles: 300},
    news: {researched: "2026-10-23", notes: [
      {scope: "china", event: "A forum opened in Beijing.", event_date: "2026-10-12", event_end: "2026-10-14", source_title: "Forum <page>",
       source_url: "https://example.org/forum", confidence: "confirmed", basis: "An item names it."},
      {scope: "bilateral", event: "Bad link.", event_date: "2026-10-12", source_title: "x", source_url: "javascript:alert(1)", confidence: "plausible", basis: "b"},
      {scope: "country", result: "no matching event found", searched: "news of the week"}]}});
  var ch = S.contextHtml(ctxSpike, api);
  assert(ch.indexOf("<details class=\"sp-ctx\">") === 0 && ch.indexOf("What the day was about") !== -1, "one disclosure per day");
  assert(ch.indexOf("What the items were about") !== -1 && ch.indexOf("Trade and economy (3)") !== -1 && ch.indexOf("Financial Street (2)") !== -1, "the items' themes and terms");
  assert(ch.indexOf("Carried by: Paper A (3)") !== -1 && ch.indexOf("Forum &lt;opens&gt; (2 placements)") !== -1, "outlets and the spike's own headlines, escaped");
  assert(ch.indexOf("The day before (11 Oct): 1 state origin placement, about Other (1)") !== -1, "the day before");
  assert(ch.indexOf("Other China coverage that day") !== -1 && ch.indexOf("12 other articles about China") !== -1 && ch.indexOf("Hormuz (3)") !== -1, "the other coverage, counts only");
  assert(ch.indexOf("Terms in both: Financial Street") !== -1, "shared terms");
  assert(ch.indexOf("In the news") !== -1 && ch.indexOf("In China, covered by the day") !== -1 && ch.indexOf("items.</span> Coincides with: A forum opened in Beijing (12 Oct to 14 Oct), a confirmed coincidence") !== -1, "coincides with, dated, with confidence");
  assert(ch.indexOf('href="https://example.org/forum"') !== -1 && ch.indexOf("Forum &lt;page&gt;") !== -1, "the source linked and escaped");
  assert(ch.indexOf("javascript:") === -1, "only https links");
  assert(ch.indexOf("In the country.</span> No matching event found. Searched: news of the week") !== -1, "nothing found is said, not guessed");
  assert(ch.indexOf("timing, not cause") !== -1 && ch.indexOf("single event that wire covered") !== -1 && ch.indexOf("Researched on 23 Oct") !== -1, "the caveat");
  assert(!/caused by|because of the/i.test(ch) && !/[\u2013\u2014]/.test(ch), "never a cause, no dashes");
  /* content.json fallback for a file without news; nothing at all for a day with neither. */
  var bare = spike("2026-10-19", "AUS", "check");
  var capi = {esc: M.esc, content: {spike_news: {caveat: "Timing, not cause.", notes: {"AUS 2026-10-19": {researched: "2026-10-23", notes: [
    {scope: "china", event: "A forum opened.", event_date: "2026-10-19", source_title: "Src", source_url: "https://example.org/a", confidence: "confirmed", basis: "Named."}]}}}}};
  assert(S.newsFor(bare, capi).notes.length === 1 && S.newsFor(bare, api) === null, "content.json notes by country and day");
  var cb = S.contextHtml(bare, capi);
  assert(cb.indexOf("Coincides with: A forum opened (19 Oct), a confirmed coincidence. Named. Source: ") !== -1 && cb.indexOf("Timing, not cause.") !== -1 && cb.indexOf("What the items were about") === -1, "news alone renders");
  assert(S.contextHtml(bare, api) === "", "nothing to show, no disclosure");
  M.get("spikes").render(prov, {last_complete_day: "2026-10-13", tests: 10, expected_false_flags: 0, spikes: [ctxSpike]}, api);
  assert(slot.innerHTML.indexOf("</button><details class=\"sp-ctx\">") !== -1, "the disclosure sits under the day's button, outside it");

  console.log("smoke m_spikes ok");
}());
