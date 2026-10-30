/* Smoke test for the pure helpers docs/app.js exposes on window.TrackerApp.helpers (country search ranking,
   the stale data notice, the tracker:select view fields, month keys, value formatting, cell ink).
   Needs d3, compute.js and app.js loaded first with a document that never finishes loading, so the page
   itself never initialises: tests/test_frontend_interface.py builds that under jsc (or node). */
(function () {
  var H = window.TrackerApp && window.TrackerApp.helpers;
  function assert(cond, msg) { if (!cond) throw new Error("FAIL: " + msg); }
  assert(H, "TrackerApp.helpers is exposed");

  /* Country search: an exact ISO code or alias first, then names that start with the query. */
  var names = {USA: "United States", AUS: "Australia", GBR: "United Kingdom", UKR: "Ukraine", NLD: "Netherlands", CHN: "China",
               CHL: "Chile", TCD: "Chad", BLR: "Belarus", DEU: "Germany", NER: "Niger", NGA: "Nigeria", KOR: "South Korea", PRK: "North Korea",
               DZA: "Algeria", CYP: "Cyprus"};
  var entries = Object.keys(names).map(function (iso) { return H.searchEntry(iso, names[iso], ""); })
    .sort(function (x, y) { return x.name.localeCompare(y.name); });
  function first(q) { var m = H.rankCountryMatches(entries, H.foldText(q.trim())); return m.length ? m[0].iso : null; }
  assert(first("us") === "USA", "us opens the United States, not Australia: " + first("us"));
  assert(first("US") === "USA" && first("U.S.") === "USA" && first("usa") === "USA" && first("united states") === "USA", "US, U.S., usa, united states");
  assert(first("uk") === "GBR", "uk opens the United Kingdom, not Ukraine: " + first("uk"));
  var uk = H.rankCountryMatches(entries, "uk").map(function (e) { return e.iso; });
  assert(uk[0] === "GBR" && uk[1] === "UKR", "Ukraine follows the United Kingdom for uk: " + uk.join(","));
  assert(first("holland") === "NLD" && first("Holland") === "NLD", "Holland is the Netherlands");
  assert(first("PRC") === "CHN" && first("chn") === "CHN", "PRC and CHN are China");
  assert(first("ger") === "DEU", "a name that starts with the query beats one that contains it: " + first("ger"));
  assert(first("aus") === "AUS" && first("aust") === "AUS", "aus is Australia");
  assert(first("korea") === "PRK" && H.rankCountryMatches(entries, "korea").length === 2, "both Koreas, alphabetical");
  assert(H.rankCountryMatches(entries, "").length === entries.length, "an empty query lists every country");
  assert(H.rankCountryMatches(entries, "zzz").length === 0, "no match");

  /* Stale data and mixed exports. */
  var now = Date.parse("2026-10-17T12:00:00Z");
  var meta = function (h, extra) { var m = {generated_at: new Date(now - h * 36e5).toISOString().replace(".000Z", "+00:00")}; for (var k in (extra || {})) m[k] = extra[k]; return m; };
  assert(H.staleLines(meta(10), [], now).length === 0, "ten hours old is not stale");
  var l30 = H.staleLines(meta(30), [], now);
  assert(l30.length === 1 && /30 hours ago/.test(l30[0]) && /out of date/.test(l30[0]), "thirty hours old is stale: " + l30);
  assert(/3 days ago/.test(H.staleLines(meta(75), [], now)[0]), "days past 48 hours");
  assert(H.staleLines(meta(75, {stale_after_hours: 96}), [], now).length === 0, "an export's own stale_after_hours replaces 24");
  assert(H.staleLines(null, [], now).length === 0 && H.staleLines({}, [], now).length === 0, "no meta, no notice");
  var m0 = meta(2), t0 = m0.generated_at, plus = function (s) { return new Date(Date.parse(t0) + s * 1000).toISOString(); };
  var gens = [{url: "data/meta.json", at: t0, schema: 13}, {url: "data/latest.json", at: plus(20), schema: 13}, {url: "data/outlets.json", at: plus(-5), schema: null}];
  assert(H.staleLines(m0, gens, now).length === 0, "files of one export, seconds apart, are one generation");
  var mixed = H.staleLines(m0, gens.concat([{url: "data/spikes.json", at: plus(-26 * 3600), schema: 13}]), now);
  assert(mixed.length === 1 && /different exports/.test(mixed[0]) && /spikes\.json/.test(mixed[0]) && !/latest\.json/.test(mixed[0]), "a file a day older is named: " + mixed);
  var schemas = H.staleLines(m0, gens.concat([{url: "data/daily/2026-10.json", at: plus(30), schema: 12}]), now);
  assert(schemas.length === 1 && /schema versions 12 and 13/.test(schemas[0]), "mixed schema versions: " + schemas);

  /* tracker:select view fields: an unusual day opens as state origin, That day, Both. */
  var v = H.viewFields({iso: "AUS", day: "2026-10-02", measure: "a", window: 1, distribution: "both"});
  assert(v.measure === "a" && v.window === 1 && v.distribution === "both", "spike fields");
  v = H.viewFields({measure: "state_origin", window: "that_day", distribution: "country"});
  assert(v.measure === "a" && v.window === 1 && v.distribution === "country", "named forms");
  v = H.viewFields({measure: "china", window: "total"});
  assert(v.measure === "china" && v.window === "all" && v.distribution === null, "total");
  v = H.viewFields({measure: "nope", window: 99, distribution: "everywhere"});
  assert(v.measure === null && v.window === null && v.distribution === null, "unknown values are ignored");
  v = H.viewFields({iso: "AUS", day: "2026-10-02"});
  assert(v.measure === null && v.window === null && v.distribution === null, "the old {iso, day} event changes no control");

  /* Month keys from global_series.json, in order, once each. */
  var mk = H.monthKeys([{date: "2026-10-02"}, {date: "2026-09-30"}, {date: "2026-10-01"}, {date: "2026-08-15"}, null, {}]);
  assert(mk.join(",") === "2026-08,2026-09,2026-10", "month keys: " + mk);

  /* % of all its news reads per 1,000 items, never a 0.0% that hides a real value. */
  assert(H.valueText(0.00014, "share_of_output_a") === "0.14 per 1,000", H.valueText(0.00014, "share_of_output_a"));
  assert(H.valueText(0.025, "share_of_output_a") === "25 per 1,000", H.valueText(0.025, "share_of_output_a"));
  assert(H.valueText(0, "share_of_output_a") === "0 per 1,000" && H.valueText(null, "share_of_output_china") === "n/a", "zero and n/a");
  assert(H.valueText(0.25, "share_a") === "25.0%" && H.valueText(12, "count_a") === "12", "other metrics unchanged");

  /* Cell ink: dark from Lab lightness 55, so the L 62 step (light ink at 2.6:1 before) takes dark ink. */
  assert(H.inkOn("rgb(230, 114, 91)") === "#0c0d0f", "L 62 step takes dark ink");
  assert(H.inkOn("rgb(61, 3, 6)") === "#f3ede2" && H.inkOn("rgb(158, 15, 23)") === "#f3ede2", "darker steps keep light ink");
  assert(H.inkOn("rgb(210, 58, 46)") === "#ffffff", "the middle red, under 4.5:1 with cream or dark ink, takes white");
  /* State origin by issuer: state-owned enterprises and SAR government bodies apart, private releases as their own count. */
  var it = {by_type: {state_media: {A: 900}, state_owned_enterprise: {A: 41, B: 0}, sar_government: {A: 7}, private_release: {C: 30, not_relevant: 12}, none: {C: 5}}, unrecorded: 1200};
  var line = H.issuerLineText(it);
  assert(/41 are releases of state-owned enterprises/.test(line) && /7 come from Hong Kong or Macao SAR/.test(line), "issuer line: " + line);
  assert(/42 releases from private issuers/.test(line) && /1,200 labels were made before the issuer rule/.test(line), "private and unrecorded: " + line);
  assert(H.issuerLineText(undefined) === "" && H.issuerLineText({}) === "" && H.issuerLineText({by_type: {}, unrecorded: 0}) === "", "older data shows no line");
  assert(/State-owned enterprise/.test(H.issuerBadge("state_owned_enterprise")) && H.issuerBadge("none") === "" && H.issuerBadge(undefined) === "", "issuer badge");
  /* An excluded language is not read at all: its own sentence, never the keyword list one. */
  var ln = H.languageNotes({language_support: "partial", languages: ["en"], excluded_languages: {zh: 3}});
  assert(ln.length === 1 && /registered but not monitored \(project rule\)/.test(ln[0]) && !/Keyword lists/.test(ln[0]), "excluded language: " + ln);
  assert(H.languageNotes({language_support: "partial", languages: ["en"], excluded_languages: ["zh"]}).length === 1, "a list works too");
  var kp = H.languageNotes({language_support: "partial", languages: ["en", "sw"], excluded_languages: {}});
  assert(kp.length === 1 && /Keyword lists cover only some/.test(kp[0]), "genuinely partial keyword coverage keeps its note");
  assert(H.languageNotes({language_support: "partial", languages: ["en", "sw"]}).length === 1, "older data without the field");
  assert(H.languageNotes({language_support: "full", languages: ["en"]}).length === 0, "full support, no note");
  /* Header links (site QA 2026-10-29): a link whose section is missing or hidden is hidden with it. */
  var shownIds = {"map-card": 1, themes: 1, method: 1};
  var nh = H.navHidden(["#map-card", "#copies-map", "#themes", "#nowhere", "javascript:alert(1)", "", "#method"], function (id) { return !!shownIds[id]; });
  assert(nh.join(",") === "false,true,false,true,true,true,false", "nav links follow their sections: " + nh);
  assert(H.navHidden(null, function () { return true; }).length === 0, "no links, nothing to do");
  console.log("app smoke ok");
})();
