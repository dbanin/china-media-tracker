# Changelog

Every change to the ruleset version is recorded here with what it altered,
because reclassification changes historical numbers and that must be traceable.
Code changes that do not alter classification are not listed.

## Ruleset 2026.10.1 (2026-10-12)

An outside review of the methodology, and the audits it prompted, found state text the rules could not
see: a diplomat list six weeks out of date, diplomatic titles read wherever they stood on the page,
official sourcing in most languages that no trigger read, agency copy republished without its credit
line, and stored bodies that were not the article at all. Every change below, the diplomat list
included, carries this one version. Articles labelled under 2026.09.9 are reclassified forward at 2,000
per hourly run as usual (classify_rules.RECLASSIFY_PER_RUN). The model prompt changes the same day
(prompt 2026-10-12, the last two items), and the labels made under the older prompt are judged again.
Counts are on the local snapshot of 12 October; the bodies held there grew from 6,218 to 7,089 as the
day's fetches ran, so each count names the set it was measured on.

- The diplomat list (pipeline/diplomats.yaml) is part of the ruleset, because a name on it decides a
  state origin label. It still named Xiao Qian as ambassador to Australia six weeks after Liu Jinsong
  took up the post on 2026-08-24, and Liu signed an op-ed in the Australian Financial Review on
  2026-09-16. Liu Jinsong is added, with the consuls-general Wang Yu (Sydney), Fang Xinwen (Melbourne),
  Sun Zhaoyang (Brisbane), Fu Lihua (Perth, who signed an article in The West Australian on 2026-10-01)
  and Li Dong (Adelaide), and the two acting heads named on the Perth and Adelaide consulate sites in
  October. Xiao Qian stays listed as former ambassador: former post-holders are always kept, because a
  piece signed while in post is state origin whenever it is collected, and matching reads the name,
  never the post. The ambassador was then checked against the embassy website for each of the 23
  countries with the most active monitored outlets (the top 20, with four countries tied at 14 for the
  last place). Six posts had changed hands: Hou Yue (Italy, credentials presented 2026-09-29), Lu Shan
  (Poland), Jiang Xuebin (Turkey), Wan Li (Tunisia) and Chen Feng (Romania, with the charge d'affaires
  Zhou Xiang, who headed the embassy from late August) are added, and Dai Bing, listed as ambassador to
  Bulgaria, is ambassador to the Republic of Korea, where his predecessor Xing Haiming is added as
  former. Sun Linjiang, Liu Shaobin, Wu Ken, Lu Kang, Fan Xianrong and Zhang Run (Mexico) are marked
  former, and Deng Li is recorded as ambassador to France. None of the 315,773 stored author fields
  carries a name added in these two steps, and the new byline check below finds no hit in the 6,218
  bodies then held.
- Entries that came in with the same refresh, or had gone stale, were checked against mission websites
  and the foreign ministry. Zhang Ming (head of the mission to the EU and then SCO secretary-general,
  never ambassador to Russia), Chen Dong (Central African Republic, not France), Chen Xu (Geneva, not
  Israel), Wei Hongtian (Grenada and Somalia, not Nigeria) and Fei Shengchao (Belgium, not Italy) now
  carry the posts they held. Names listed twice each turned out to be one person and are now one entry
  carrying the current post and the former ones with a source: Wang Lutong, Hua Chunying, Jia Guide
  (permanent representative at Geneva, formerly ambassador to Italy and Peru), Cai Run (EU mission,
  formerly Israel and Portugal), Xiao Junzheng (Israel, formerly Greece), Zhang Yiming (formerly UAE and
  Namibia), and Zhang Run and Wang Wei, whose second entries (Ireland, Mozambique) named posts neither
  ever held. Han Chunlin is ambassador to Kazakhstan and former ambassador to Romania. He Xiangdong is
  former ambassador to Ireland and his successor since November 2024, Zhao Xiyuan, is added; Han Jing
  is ambassador to Zambia, as listed, and was ambassador to Suriname until 2024, where Lin Ji succeeded
  him and is added; Zhang Wei is ambassador to Benin, never to Bulgaria, where Dai Qingli is ambassador
  and is added; Guo Shaochun is consul-general in Los Angeles and was ambassador to Zimbabwe from 2019 to
  2022, never to Ireland; Wei Xiaodong is confirmed as consul-general in Istanbul and added; Fang Qiu,
  ambassador to Greece since November 2024, is added; and Zeng Jixin, ambassador to the United Arab
  Emirates since the spring (foreign ministry notice of 2026-04-21), is added. Removed, because no record
  shows them in any Chinese diplomatic post: Wang Xiaotao (first head of the foreign aid agency, never a
  diplomat), Ren Li and Wang Xiaodong (nor among the ambassadors to Hungary or Turkey), and Wang Liyong
  (listed as ambassador to Greece). None of the four removed names appears in a stored author field or
  in the 7,061 bodies then held.
- The list was then rebuilt against the foreign ministry's mission pages and each mission's own website
  on 2026-10-12, and every entry checked carries the URL that confirmed it. It names the head of mission
  in 178 of the 182 countries with an active monitored outlet and a Chinese mission (the other 53 of the
  235 are territories, or have no relations with China), with the consuls-general in the ten countries
  with the most active outlets. 146 names are added, among them six charges d'affaires who run embassies
  without an ambassador in post or who sign articles in the local press (Yemen, Mongolia, Finland,
  Sudan, Georgia and, before Zhou Qian arrived, the Czech Republic), and 35 entries are corrected: most
  are ambassadors since succeeded and now marked former, eight are marked former but unverified because
  no record of the post was found and the post now has another holder, four were listed in a post no
  record shows them holding (Wang Xuefeng is ambassador to Denmark, Yu Bo to Honduras, Zhang Jianwei to
  Thailand and Chen Chuandong to Lebanon), and two, Zhang Jianguo and Ji Guangbin, appear in no Chinese
  diplomatic post and are kept for the owner to check. Lithuania (a charge d'affaires office with no
  named head), Eritrea, Liechtenstein and Kiribati remain without a current head. Chen Li, consul-general
  in New York, is listed because a listed name no longer matches inside a hyphenated or longer name
  (below), so the Guardian's Taipei reporter Yu-chen Li, in three stored author fields, is not taken for
  him. Of the 203,394 stored author fields that are not empty, four carry a name the rebuild added:
  Zeng Jixin's op-ed in The National on 2026-09-15, labelled independent journalism under 2026.09.7,
  whose page label reads "Zeng Jixin is the Chinese ambassador to the UAE", so it is state origin by
  diplomat_list_author once its body is fetched again at reclassification (the body is no longer held;
  if it cannot be fetched the old label stands); a Le Renouveau piece by Zhu Kewei, ambassador to
  Burundi, already state origin by its author field title; and the two pieces by Zhang Yang and Kang Yan
  whose outcome the next item gives. 32 of the 7,061 bodies then held contain a newly listed name, none
  in a byline, and none changes label.
- diplomat_list_author, the strong signature for a listed name in the author field, now fires only when
  a diplomatic title describes that person and makes them China's diplomat. The title must stand in the
  author field itself, on the byline line, in a bio line that names the author or speaks of "the
  writer", on a title line of its own directly under the name, or next to the name in an opinion or
  commentary section. A title elsewhere in the head, tail or page labels no longer counts, so a namesake
  whose piece mentions an ambassador stays a weak candidate for the model. The title phrase must say
  Chinese, China's, of China or PRC, or the same in another language the test reads, or the line must
  name a Chinese embassy or consulate; China as the host country never counts, so "Li Dong, Ambassador
  of Singapore" and Singapore's ambassador to China stay weak candidates. A title in the author field
  that names no country, as in Sada Elbalad's "Liao Liqiang; Ambassador", counts only when the page
  labels or the opening of the body name China, and a title that names no sender and has China as its
  host ("Ambassador to China") never takes China from the page. A sentence with a speech or action verb
  (said, told, noted, according to and their equivalents) is reported speech, not a byline, so "Wang Yu,
  Chinese Ambassador to Australia, said on Monday" in a namesake reporter's story does not fire; a bio
  that begins with the name must begin its line and stand among the first short lines of the body or as
  its last line, followed at most by two short lines such as a disclaimer, so "Xiao Qian is the Chinese
  Ambassador to Australia." alone at the end still fires; in an opinion section "Chinese Ambassador Wang
  Yu" counts only on a byline or author line of its own; and the author box form "Wang Yu | Chinese
  Ambassador to Australia", which matched nothing before, now fires. A bio line with no verb counts as one
  that speaks of the writer, on a line of its own in the head, tail or page labels, when the writer word
  is followed by a dash, a colon or a comma and then only a title phrase ("Autor: ambasador ChRL w
  Polsce", "Penulis, Duta Besar China untuk Indonesia", "Tác giả: Đại sứ Trung Quốc tại Việt Nam", "The
  writer, Chinese Ambassador to Australia"), in Turkish with the copula as a suffix on the title ("Yazar,
  Çin'in Ankara Büyükelçisidir"), and in Hungarian when China stands as the possessor right after "A
  szerző" and the title carries its possessive ending (nagykövete, főkonzulja, ügyvivője), so "A szerző
  Kína budapesti nagykövete" fires and "A szerző Magyarország pekingi nagykövete" does not. A title on
  its own line directly under the name, as Expresso das Ilhas prints "Zhang Yang" over "Embaixador da
  República Popular da China em Cabo Verde" in its page labels, counts when that line is a title phrase
  and not a headline or a sentence ("Chinese ambassador Liu Jinsong visits Adelaide" under a namesake's
  byline does not). When the author field is empty, a listed name on a byline line at the top of the
  body with China's diplomatic title on the same line ("By Liu Jinsong, Chinese Ambassador to
  Australia"), which matched nothing because diplomat_byline_head needs the title before the country
  word, now fires the signature too; a listed name in a body byline with no such title fires nothing. A
  listed name no longer matches inside a hyphenated or longer name. Of the five state origin labels the
  diplomat list carried, four keep their label on the text still held, and the fifth, a La Jornada op-ed
  by Chen Daojiang whose body is no longer held, is decided again when it is reclassified, as is the
  Sada Elbalad article, whose stored page labels do not name China (its stored summary, which opens with
  Xi Jinping as "Chinese President", suggests the body will keep the label). The Zhang Yang op-ed in
  Expresso das Ilhas, labelled independent journalism by the residual rule, is state origin on its stored
  page labels once its body is fetched again (if it cannot be fetched the old label stands). The Kang Yan
  piece in Delo stays a candidate for the model, because its stored page labels give only the section
  (Mnenja) and its title "veleposlanica Kitajske" is in the summary, which matching does not read. No
  other of the 13 stored author fields that carry a listed name, no stored page label and none of the
  6,855 to 6,857 bodies held changes outcome, and none of the 1,970 stored Hungarian rows carries a
  listed name in its author field.
- The title test reads ambassador, consul general and charge d'affaires in Polish, Czech, Slovak,
  Slovenian, Croatian, Serbian, Hungarian, Romanian, Dutch, Swedish, Danish, Norwegian, Finnish, Turkish,
  Indonesian, Malay, Vietnamese and Swahili (veleposlanica, ambasador, velvyslanec, nagykövet,
  ambassadør, suurlähettiläs, büyükelçi, duta besar, đại sứ, balozi, never the embassy), with the China
  forms that make the person China's diplomat (Kitajske, Chin, ChRL, Číny, Kína, the Kínai
  Népköztársaság or KNK, Kinas, Kiinan, Çin'in, and the Kenyan Swahili Uchina), the opinion section names
  of those languages (Mnenja, Mišljenja, Opinie, Názory, Vélemény, Debatt, Mielipide, Görüş, Opini, Ý
  kiến, Maoni), and the host country wherever each language puts it, before the title as well as after
  it: "ambasador Polski w Chinach", "Türkiye'nin Çin Büyükelçisi", "Magyarország kínai nagykövete",
  "Suomen Kiinan-suurlähettiläs", "Danmarks Kina-ambassadør", "Balozi wa Tanzania katika China" and
  "Duta Besar RI di Tiongkok" do not make a listed name China's diplomat, while "Çin'in Ankara
  Büyükelçisi", "Çin Halk Cumhuriyeti'nin Ankara Büyükelçisi" and "Balozi wa Uchina nchini Kenya" do.
  Titles in the scripts of the languages with at least five active outlets (Arabic and Persian, Cyrillic
  for Russian, Ukrainian, Bulgarian, Serbian and Macedonian, Korean, Greek, Hebrew, Japanese, Thai, Hindi
  and Khmer) count as the Latin ones do, with the host country (after в, لدى, در, στην, ב, ประจำ or
  ប្រចាំ, before में or 주재, after 駐) never counting as China, and fourteen entries also carry the name
  as printed in those scripts (16 forms, from the embassy site's page in that language for Russia,
  Ukraine, Kazakhstan, Kyrgyzstan, Bulgaria, Korea, Japan, Egypt and Yemen, and from a news report in
  that language for Thailand, Israel, Greece, Syria and the former ambassador to Korea), so "Чжан
  Ханьхуэй, Посол Китая в России" or "لياو ليتشيانغ، سفير الصين لدى مصر" fires diplomat_list_author
  while "посол России в Китае" does not. One stored row changes: an Argumenty i Fakty op-ed signed
  "Чжан Ханьхуэй", labelled independent journalism by the residual rule, becomes a candidate for the
  model (its body is no longer held, so it is fetched again when reclassified).
- The byline signature diplomat_byline_head took any China word within 80 characters after the title, so
  "By Li Dong, Ambassador of Singapore to China" fired it with China only as the host country, and the
  author field signature diplomat_title_author_field did the same with "Shen Bo, Nederlands ambassadeur
  in China", "Deutschlands China-Botschafterin" and "Li Dong; Ambassador to China". Both now count a
  match only when its title phrase makes the person China's diplomat by the test above, or, in the author
  field, names no country on a line that names a Chinese embassy or consulate, so "Ambassador of China to
  Kenya", "Embajador de China en México", "Ambassadeur de Chine en France", "Ambasciatore della
  Repubblica Popolare Cinese in Italia", "Xiao Qian, Chinese Ambassador to Australia" and "Botschafter
  der Volksrepublik China in Deutschland" still fire. The only stored match of each keeps its label: a
  Tirana Times op-ed by the ambassador to Albania, and the Le Renouveau piece by Zhu Kewei.
- The China Radio International credit, and the same name in the state entity test, now read
  CRIOnline, CRIONLINE, CRIENGLISH and CRI English, the forms CRI uses for itself as a release issuer and
  on its former English site, and China Plus, the brand of its English service, in a credit line or
  dateline only, because "China Plus One" is a supply chain phrase. The acronym must be upper case,
  because the old case insensitive pattern also matched "Cri Cri online" in a Mexican listing and "CRI
  online" in an Italian Red Cross notice (both now regression cases, neither a stored row), and "CRI
  Online" or "CRI English" labels an article only in a credit line, a dateline, the author field, or in
  credit form in the first 400 or last 600 characters (before a colon, alone on a line, after a
  copyright mark), so a story that mentions an interview with CRI Online goes to the model. The CGTN
  credit line, dateline, closing line and author field read CGTN Radio, the name CRI's English site
  (english.cri.cn) now carries, and CGTN Digital, CGTN Africa, CGTN America, CGTN Europe and CGTN
  Français, where "Source: CGTN Radio" matched nothing before because the name had to stand alone. CRI's
  Chinese names 中国国际广播电台 and 国际在线 (the second only before punctuation or a source word such as
  消息 or 报道, because 国际在线教育 is international online education), its Japanese name 中国国際放送局 and
  its Korean name 중국국제방송 (only before a particle or a non Hangul character, so 중국국제방송장비전시회, a
  broadcasting equipment fair, does not count) count in the state entity test that two weak signals
  need and as a citation that sends an article to the model, as 中央广播电视总台 already did, and in a
  source line in Chinese script (来源, 來源, 稿源 and the other markers) when the name ends the line, so
  "来源：国际在线教育" does not. The credit lines of CGTN, Xinhua, China Daily, the Global Times, People's
  Daily, China News Service, CRI and China Media Group take a full width colon and a trailing date, as in
  "Source：CGTN 2026-09-06" on english.sdchina.com, while a line that goes on as a sentence after the name
  still does not count. Four stored rows change, all on title and summary alone: a CRI release carried by
  Antara that the rules had labelled independent journalism becomes state origin, two Utusan copies the
  model had already labelled state origin now reach that label by rule, and one PR Newswire release is
  still with the model. All four open "A news report from CRIOnline:", so the later narrowing changes
  none of them, and no stored row carries any of the CJK names.
- Seven West Media (7NEWS, PerthNow, The West) names an article's source (AAP, AP, Reuters, CNN, NBC, PA,
  Deutsche Presse Agentur, BANG, or a house desk such as 7NEWS Sport) only in the "source" field of the
  article's own object in the data the page carries, never in the extracted body, and bylines wire copy
  "Digital Staff" or "Staff Writers", so syndicated Xinhua copy there would have carried no credit for
  any signature to read. The fetch stage now reads that field (extract.embedded_source), only from the
  object whose id is the article's c-ID, so photo credits ("AP, Xinhua" on a related card's picture) and
  related cards' sources are never taken, and adds it to the page labels as "source: AAP"; when the
  byline is a desk the source is credited beside it ("Staff Writers; Reuters"), or stands alone when
  there is no byline, where the author scoped Xinhua signatures already read it. A new signature,
  state_outlet_source_label, reads that page label line and labels state origin an article credited to
  Xinhua, CGTN, China Daily, the Global Times, People's Daily, China News Service or China Radio
  International, as the whole source or one item of a source list, so a named writer with source Xinhua
  is labelled as a desk byline already was. All 817 Seven West pages in the Australian audit carry the
  field, 76 name an outside source, and 19 of those have a desk byline that gains the credit. None of the
  90,970 stored page labels carries a state source line, and the 32 Seven West pages among the 4,019
  saved pages credit only AAP, NBC, NewsWire and the publisher's own names, so no stored article changes
  label. Rows fetched earlier keep their stored labels and author until fetched again. The extraction
  version does not change, because no body changes.
- Official sourcing triggers, which send an article to the model, read far more. Statements by Chinese
  ministries and state bodies with no spokesperson named ("China's commerce ministry said in a
  statement") matched no trigger: new state body triggers in English, Italian, French, Spanish,
  Portuguese, German, Russian, Arabic, Korean, Japanese, Chinese, Indonesian and Vietnamese read the
  commerce, defence, foreign, public security and other ministries, the State Council, the NDRC, the
  statistics bureau, customs, police and provincial governments when China is attached to the name of
  the body and a reporting verb stands in the same sentence (so "Taiwan's defense ministry said it had
  tracked around 35 Chinese warplanes" does not fire), and a police statement released on Weibo, WeChat
  or Douyin counts too. On the stored titles, summaries and the 6,226 bodies held for classified articles
  this sends 339 more articles to the model, 124 of them on foreign ministry statements, and stops 3,
  about $3.36 at one cent a batched call. Generic attributions to the Chinese state with a reporting verb
  ("Chinese authorities said 16 people were killed", "China said Tuesday", "Beijing confirms", "the
  Chinese government said", "Chinese officials told", "according to Chinese authorities") and their forms
  in Italian, French, Spanish, Portuguese, German, Russian, Arabic, Korean, Japanese, Indonesian, Malay
  and Vietnamese now count, with China or Beijing only as the subject, never after a preposition ("held
  in China says", "the US ambassador to China said"), as part of a name ("MGM China reported", "China
  coach says") or before a verb that does not report ("Chinese authorities have been criticised"). On the
  same set this sends 271 more (159 in English, 31 Portuguese, 29 Spanish, 13 French, 13 Arabic, 9
  Indonesian, 6 Russian, 4 Korean, 3 Italian, 3 Vietnamese, 1 Japanese), about $2.71; 39 of a sample of
  40 read were a real attribution to the Chinese state, and the fortieth ("las autoridades de China
  declararon el estado de alerta") no longer fires. Five more triggers read Chinese state outlets
  (Синхуа, Сіньхуа, Sinchua, Hszinhua, the Global Times in Cyrillic, Persian and Korean, and CCTV beside
  a state broadcaster word), the foreign ministry spokesperson (with the spokespersons' names in
  Cyrillic, Greek, Arabic, Persian, Devanagari and Thai), Chinese embassies and consulates, Chinese state
  media, and statements by Chinese ministries, state bodies and police, in Croatian, Bosnian, Serbian,
  Macedonian, Bulgarian, Ukrainian, Greek, Polish, Czech, Slovak, Hungarian, Romanian, Turkish, Thai,
  Persian, Hindi, Nepali and Hebrew, and Korean 중국대사관 and 관영매체 written without a space, each only
  when China is part of the phrase, so "Амбасадор Кине" (the ambassador), an embassy in China, "Çin
  Dışişleri Bakanı" (the minister) and another country's state agency do not fire. This sends 290 more,
  265 of them labelled by the rules until now (203 discovered in the last 30 days), and stops none,
  about $2.65; 24 of a read-through of 30 cite a Chinese official source or state media. The state media
  trigger for languages without a trigger of their own read the bare phrases "państwowe media", "státní
  média", "állami média", "κρατικά μέσα", "devlet medyası", "관영 매체", "国営メディア" and "государственные
  СМИ" with no China in them, against the rule the English trigger follows. They now count only with
  China, Beijing or a Chinese state outlet in the same sentence (in Korean also 中, the headline
  abbreviation), so "íránská státní média", "az orosz állami média" and a Cambodian "관영 매체" no longer
  fire, and the forms with China attached that state_media_reported_more already reads (Romanian,
  Turkish, Thai, Hindi and Korean) are no longer listed twice. Of the 119 classified articles it fired on
  in their titles, summaries and the 6,218 bodies held, 113 still fire it, one is still sent to the
  model by another trigger, and five lose the route: the Cambodian story and four that meant China's
  state media with China named only in an earlier sentence (Korean, Czech, Polish and Turkish, one of
  them labelled unchecked state sourcing by the model). Picture credits in the forms "(KCNA/Handout via
  Xinhua)", "(Wang Gang/China News Service/VCG via Getty Images)" and "( Reuters: China Daily )" are
  removed before matching, while text credits such as "(AFP/Xinhua)" stay. No rules decision of state
  origin changes from any trigger.
- Uncredited copies of state origin items now go to the model. Agency copy republished without its
  credit line names no credit, state entity or official source, so the rules labelled it independent
  journalism or not relevant without a model call; the recall audit's describe found 153 such articles
  whose headline or feed summary nearly repeats a state origin item, about 49 of them Italpress items at
  Quotidiano.net, Libero and Il Tempo that Italpress itself ran stamped "(XINHUA/ITALPRESS)", and 35 press
  releases at CNA and The Straits Times whose copies at the Laotian Times, PR Newswire or CNA were
  labelled state origin. When an article would be labelled independent journalism or not relevant by
  rules, the rules stage (classify_rules.classify_article) compares it with the current state origin
  items of other outlets, using the recall audit's measures, moved to pipeline/lookalike.py and shared by
  both: headline word sets at a Jaccard similarity of 0.6 or more, the feed summary's five word shingles
  at a containment of 0.5 or more, read without the boilerplate the item's feed repeats
  (pipeline/boilerplate.py), and the body's MinHash fingerprint from pipeline/body_dups.py at that
  module's own threshold, never a body it marked as page template text. A match goes to the model as a
  state origin candidate, with the matched article's id, outlet, headline, similarity and method recorded
  in llm_trigger under "lookalike", and the model, and the second rater of the stability check, is shown
  that item's headline and outlet as context, never the article's own outlet or country. The window runs
  eleven days either side (lookalike.WINDOW_BEFORE_DAYS and WINDOW_AFTER_DAYS), not ten before and two
  after as first written: the recall audit found 17 lookalikes whose credited item came 2.9 to 20.5 days
  later, 13 of them judged by hand to be the same item (GlobeNewswire releases on the Postmedia sites that
  Antara reran eight days later, Xinhua copy that IOL, Sidwaya and La Republica ran days after the
  uncredited copy), and across all 132 cross-outlet pairs of state origin items with matching headlines
  the second copy came within 10.2 days of the first, while a twelve day pair of Djokovic China Open
  stories falls outside. The window is measured between publication times when both items have a
  credible one (not after discovery, not before 2026-09-03, and not more than 14 days before discovery
  unless a backfill read brought the item in), else between discoveries, so an item found late no longer
  moves its pair. When two credited copies are equally similar, the route and the recall audit name the
  earliest one, then the lower article id; before, the pick followed Python's per process string hash
  seed, and the same Straits Times release was matched to CNA in one run and PR Newswire in the next. The
  credited copy is often labelled after the uncredited one (67 of the first 131 matches), so each rules
  stage also sweeps the state origin labels made in the last 48 hours (LOOKALIKE_SWEEP_HOURS) against the
  rules labels discovered up to the window's width before them, however old those labels are. On the 12
  October database the route sends 145 articles from the backlog (131 at the first window, 118 by
  headline and 13 by summary, and 14 more at the wider one; bodies could not be compared there, as almost
  none are stored locally), about $1.45 at 1.0 cent a batched call, and about three to four a day after
  that, roughly a dollar a month. A state origin label the model gives such an article is stored with its
  own route, uncredited_copy (classify_rules.model_route), rather than unattributed, which is left for
  model labels no signature or match stands behind; ensure_routes moves older such labels at export, and
  the route is listed in meta.routes and route_totals, the route select, a fifth Start here route card
  ("Uncredited copy") and the methodology prose. The interface no longer says that every article waiting
  for the model carries official Chinese sourcing: the waiting line holds official sourcing, a weak
  signature, or a near copy of a credited state item.
- Bodies that are not the article are no longer classified. The body near-duplicate pass flagged 50
  template bodies in the local snapshot (one text stored under several headlines of one outlet), and a
  scan of the 6,855 stored bodies found the same failures without a template: a bot or script challenge
  served with status 200 (Le Monde's "Client Challenge" page, every one of its 103 stored bodies at 286
  characters; DIGITIMES "Access denied"), soft paywalls the detector missed (Asahi Shimbun teasers,
  DIGITIMES "requires paid subscription" teasers, the NHK ONE registration and terms notice in front of
  NHK News), pages the browser builds from data so the served document held only menus (the New Straits
  Times Press sites NST and Harian Metro), the wrong block of the page (Afghanistan International's
  related article card carrying another story; the Lowy Interpreter's standfirst in place of its prose
  blocks), a loading notice (the Lebanese National News Agency) and an injected spam block
  (BusinessWorld). Labels made on them rested on the headline. Extraction now prunes related article
  cards, reads the NST template's embedded article unless it is marked premium or the page carries any
  paywall marker, joins the Lowy prose blocks, and tries trafilatura's precision mode whenever a body has
  under 600 characters of running text (pipeline/extract.py, with golden fixtures). Paywall detection
  adds the Asahi, DIGITIMES and NHK markers and a stated truncation ("残り886文字"). A challenge page, or a
  body of 200 characters or more with under 150 characters of running text (lines of seven words or
  more, or 25 characters in a script without spaces; on the snapshot nothing real fell below 150), is
  recorded at fetch as failed with an "unreadable:" reason and is not retried. A consent or cookie wall
  served in place of the article is failed the same way at any length, as "unreadable: consent wall",
  when the page title is a consent dialog's or consent platform text (Seznam, Google's "Before you
  continue", Didomi, OneTrust, Quantcast, Sourcepoint, Funding Choices, Taboola) fills at least half the
  body, and the body carries under half of the headline's words; consent is never given. The classify
  stage applies the same tests to stored bodies, re-reads the saved page with the repaired extractor
  first, and also fails a template body whose text carries under half of the words of the headlines it
  was stored under (body_dups.template_unreadable; on the snapshot every extraction failure was under 32
  percent and every real follow up story 58 percent or more). Such an article loses its current labels,
  model labels included, and drops out of every count as unreadable; a re-fetch that serves the same
  unreadable text as the stored body withdraws the label too. In the local snapshot 39 current labels go
  (24 independent journalism, 14 not about China, 1 model independent journalism): 19 become paywalled
  and 20 failed. 22 bodies are read again from their saved pages; there 7 not about China labels become
  independent journalism and one independent journalism label goes to the model. The consent wall test
  fails only the 122 Novinky.cz and Seznam Zprávy articles, whose stored bodies were all Seznam's 1,091
  character wall, withdrawing 121 not about China labels and 1 independent journalism label. Video only
  pages (France 24 video, Il Tempo and Libero "pillole") still carry a headline and a few lines and are
  left for review.
- Two templates are read again under extraction version 2026.10.1 (config.EXTRACT_VERSION, now recorded
  on every stored body as articles.extract_version; extract.EXTRACTION_CHANGES lists the templates whose
  reading changed). The same Chinese state paid releases, distributed on PR Newswire between 24 and 30
  September, were state origin at Channel NewsAsia, Utusan and PR Newswire and independent journalism by
  rules at The Straits Times and Antara English, because the extracted body never held the credit. The
  Straits Times paid press release section builds each release in the browser from a React Router data
  payload, so all 410 stored bodies from the section ran 205 to 424 characters of disclaimer and
  headline; at Antara the opening paragraph, the one carrying the "(ANTARA/PRNewswire)" stamp, is bare
  text ahead of the first line break, and extraction dropped it on every Antara English article (20 of
  22 checked among its 144 stored bodies). pipeline/extract.py now reads the release from the Straits
  Times payload (only on that route, and never on a page carrying any paywall marker), with its headline
  above it as the other outlets print it, and keeps Antara's opening paragraph. A reclassification used
  to reuse the stored body; classify_rules.ensure_body now discards a body of a listed template stored
  under an older version and extracts it again from the page saved at fetch time when that is on disk,
  or else fetches it again by the ordinary fetch path (robots.txt honoured, three seconds per domain, the
  project user agent, paywalled pages left alone). An article that cannot be fetched again keeps its
  label and is not labelled on the stub. Reclassification takes these articles whatever the ruleset of
  their label, and a model label is replaced only when the rules now read state origin, as before. On
  pages fetched again for all 100 rows labelled independent journalism by rules at the two (32 Straits
  Times paid releases, 68 Antara English articles), two Xinhua Silk Road releases (Mazu culture, the
  Changsha tourism conference) carry the headline issuer pattern and are state origin by rules, as their
  Channel NewsAsia and PR Newswire copies are; 61 more show their wire stamp and go to the model like
  their copies elsewhere, among them the Hangzhou Investment Promotion Bureau release at both outlets and
  the Shaanxi International Communication Center film festival release, whose issuers no signature
  names, and 16 of the 61 have a copy the model judged state origin at another outlet; the other 37,
  Antara's own reporting, show no signature and stay where they were. A model label kept over a
  refreshed body that differs from the stub it was made on (unreadable, an older extraction, or at most
  half as long) is marked body_refreshed and queued for the model again, its old label current until the
  new one arrives: up to 52 labels (9 Straits Times, 43 Antara English), 6 of them certain from saved
  pages. Other Straits Times sections and Indonesian Antara read the same under both versions and are
  untouched. Copies of one item that differ only in what the outlet itself printed are not reconciled:
  each copy is judged on its own text, and 21 title-linked groups (44 rows) still hold a copy with a
  strong credit beside a copy with none, 18 of the groups Xinhua video items Italpress distributes that
  Il Tempo, Libero and Quotidiano.net run with an Italpress credit only.
- The model prompt now carries a date, PROMPT_VERSION 2026-10-12 in pipeline/classify_llm.py, and every
  model label records it in its model version, for example "claude-sonnet-5 (prompt 2026-10-12)"; a
  session label names it the same way or counts as older. Three changes share it: the reworded state
  origin definition (see Definitions below), the sentence saying that state text republished without its
  credit line is still state origin when the article is that text with no substantive alteration, and
  the factual-claim rule for unchecked state sourcing. The codebook counted "a ministry spokesperson is
  quoted and nothing checks the claim" as unverified relay, so every "Beijing said" story qualified,
  including one that only reports a stated position, and relay ran high, most of all in wire-heavy
  markets. An article is now unchecked state sourcing only when its central content is a factual claim
  attributed to a Chinese official or state source (an assertion about events, numbers or actions) passed
  on without independent confirmation; reporting a stated position, policy, warning, threat, opinion or
  denial is independent journalism even without outside comment. The prompt gives two contrasting
  examples (a commerce ministry export figure is relay, a foreign ministry warning to Manila is not), and
  its worked example of a reported espionage denial, which had taught relay, now teaches independent
  journalism. Checking a claim means checking that claim itself: when a central death toll, figure or
  event account rests only on a Chinese official or state source, unrelated context or background
  elsewhere in the article does not make it independent journalism, a case the old prompt let through
  twice in an audit of Australian wire copy. The new generic attribution trigger above sends exactly
  these articles to the model, which decides under this rule.
- Current model labels made under an older prompt (all 9,781 in the local snapshot, 8,921 calls after
  near-duplicate copying, about 89 dollars at 1.0 cent a batched call) are judged again through the API
  with what new articles leave of each run's calls, never more than TRACKER_LLM_RELABEL_SHARE (default
  0.5) of the day's binding cap, paced over the day. Each keeps its old label until the new one is
  stored, so no article is left unlabelled, and a relabel that fails keeps its label and is not retried.
  The queue works in category order rather than oldest label first (classify_llm.RELABEL_PRIORITY,
  relabel_candidates), oldest first within each tier: unchecked state sourcing labels first, because the
  factual-claim rule changes that figure most; then labels kept over a refreshed body (body_refreshed,
  above), whatever their prompt; then independent journalism labels where the model recorded official
  Chinese sourcing (a sourcing trigger in llm_trigger or a name in china_sources_cited) and no
  independent confirmation, the ones the old rule may have under-called; then state origin; then the
  rest. The 1,715 unchecked state sourcing labels (1,626 calls once near-duplicates are collapsed) take
  about 13 days at 130 relabel calls a day, the room 340 new articles leave under a 470 call cap, and
  about 7 days at the full half share of 235; oldest first, the last of them would have waited about 69
  days. Days whose labels still carry an older prompt get the same grey timeline mark as labels on an
  older ruleset. The unchecked state sourcing and independent journalism figures will move as the
  relabelling proceeds.

## Gate 2026.10.1 (2026-10-12)

The gate decides what enters the corpus, not how an article is labelled, and gate decisions are
recorded per item and never revisited, so this applies only to items discovered after today. Measured
first on the 2026-10-12 snapshot: of 68,676 gate relevant items classified in the last 30 days, 33,485
(48.8 percent) were labelled not relevant by the rules, but 20,213 of those came from gate exempt section
feeds that never met the keyword gate (among them the 7NEWS "sponsored" feed, which carries sport and
lifestyle: 716 items, 2 relevant; it is removed and its items corrected, see the collector entries
below). Of 45,382 items the keyword gate itself admitted, 13,272 (29.2 percent) were not relevant, spread
across ordinary news paths and single mentions of the country name in Korean, Spanish, Thai and
Vietnamese news, where over everything collected a single summary term also admitted 164 state origin
and 103 unchecked state sourcing labels, so it cannot be demoted everywhere.

- In a sports section, read from the item's URL path (sport, sports, deportes, esportes, tenis, sukan,
  olahraga and the other segments under sports_sections in keywords.yaml; the article's own slug is not
  read), one China term found only in the feed summary no longer admits the item: it needs a term in the
  headline, two distinct terms, or a state media name such as Xinhua or CGTN, and a state origin
  signature still admits it as before. A rejected item carries "sports_section:uncorroborated" among its
  gate terms. Replayed over every item collected so far (2026-09-03 to 2026-10-12), the change would have
  kept out 1,344 items, 1,125 of them not relevant and 219 independent journalism labels, all but one
  from the residual rule (tournament reports from Beijing and Shanghai, Asian Games results naming the
  Chinese team), and no state origin or unchecked state sourcing label. Lifestyle, travel and
  entertainment paths are left alone, because a Xinhua sourced bridge feature in a lifestyle magazine is
  unchecked state sourcing and would have been lost.
- The keyword terms are matched on the feed summary without the boilerplate its feed repeats in most of
  its items (pipeline/boilerplate.py, such as Ghana News Agency's promotional wrapper), while a state
  origin signature is still read in the whole summary. Replayed over the 29,773 items of the last 30 days
  on feeds with boilerplate, no gate decision changed (one item's list of matched terms did). The section
  feed correction (pipeline/section_retag.py), the language correction (pipeline/language_retag.py) and
  the fixture dry run (pipeline/dryrun.py) run the gate the way discovery does, without the feed's
  boilerplate and with the item's url, so they decide the same way on the same item.
- An item that carries a title and nothing else, as the entries of a Google News sitemap do, is also
  admitted by a separate title_only list in keywords.yaml (Chinese cities and districts, fairs such as
  the Canton Fair and CAEXPO, state initiatives, state media and provincial issuer brands such as
  iChongqing and SZMG, Chinese companies whose names alone were at least as often China coverage as not,
  and Spanish, Portuguese and Serbian forms), recorded as "title_only:<term>" among the gate terms. On
  stored titles it raised title-only recall of PR Newswire's state origin releases from 103 to 145 of 210
  (49 to 69 percent) and of its China items from 108 to 150 of 223 while admitting 3 more of its 229
  items labelled not relevant, and raised Telegraf from 35 to 38 of 39, Rio Times from 23 to 25 of 49 and
  Clarín from 82 to 83 of 94 with no item labelled not relevant. On the saved sitemaps it adds about 8 to
  25 fetches a day, all at PR Newswire. The generated methodology has a paragraph on the list: what it
  holds, that it applies to news sitemap items only because they carry no summary, how companies were
  chosen, and its measured effect.
- The main Serbian list now also carries the other cases of Kina, kineski, Peking and Tajvan in Latin and
  Cyrillic (Kinu, Kinom, kineske, kineskog, kineskom, Кине, кинеског and the like) and Xi Jinping and Mao
  as Serbian writes them (Si Đinping, Mao Cedunga, Си Ђинпинг), with Kinu, Kinom and their Cyrillic forms
  counted only when capitalised because "u kinu" is "at the cinema", while Pekinga, Šangaju and
  Kineskinja stay out because on stored items they were mostly the Beijing and Shanghai tennis
  tournaments and athletes. Replayed over the stored Serbian items the additions would have admitted 8
  more, 1 labelled China related, 1 not relevant and 6 never labelled.
- The Croatian and Bosnian lists now carry the same case forms, with Xi Jinping and Mao as each writes
  them (Si Đinping, Mao Ce-tung, Mao Zedonga) and, for Bosnian, the Cyrillic forms, under the same cinema
  guard and with the same tennis and athlete forms left out. Replayed over the stored Croatian and
  Bosnian items they would have admitted 10 more, 1 labelled not relevant and 9 never labelled.

## Data schema 12 (2026-10-12)

Underlying items now count each item once per window and scope, and the day's other changes add export
fields and database tables. No label changes come from the schema itself.

- Underlying items, the number of distinct pieces behind a count of placements, were distinct within one
  day and one country only and then summed: over the days of a window in the page, and across countries
  for the world totals in latest.json. A story placed on three days of a 30 day window counted as three
  underlying items, and one carried in two countries as two in a world figure. Each day of the daily
  files now carries underlying_items, the item ids behind its state origin and unchecked state sourcing
  counts per country (about 34 KB on September's 2.2 MB file), and the page counts the distinct ids over
  whatever window, day and Distribution setting it shows; latest.json's uniqA and uniqAB do the same for
  its all time and 30 day blocks and, in its totals, across countries. Title groups are joined through
  body links (pipeline/body_dups.py) for these figures, which can only fall. Items are still counted per
  label, and a day's own figure in the daily CSV is unchanged. Data from an older export carries no ids,
  and the page then says its underlying items are summed day by day. On the local snapshot of 12
  October, the 30 days ending 11 October: Italy 297 to 292, the world 1,163 to 1,155, the worldwide wires
  164 and Indonesia 105 unchanged; the snapshot holds no body links, which are where most cross country
  duplicates are found.
- New export fields: meta.llm_prompt_version and labels_on_older_prompt (in meta, on each day of the
  daily files and in global_series.json, for the grey timeline mark); meta.relay_human_study,
  relay_provisional_reason and relay_publishable for the hand coding study, and
  relay_reliability_history for a stability check made under an older prompt, with the check's
  prompt_version; meta.candidate_recall, the latest recall audit of the candidate gate; meta.themes_changes,
  the dated themes versions; the uncredited_copy route in meta.routes and route_totals, where every route
  now appears, at zero until something reaches it; in outlets.json, leaning_source_weak and
  leaning_second_source, and the ownership group state_funded_international; missed_estimate_since beside
  the missed item estimate; and the counts of relayed and shared outlets (relay_outlets, shared_outlets).
- New database tables: body_fingerprints and body_links (body near-duplicates), feed_boilerplate (per
  feed summary boilerplate, kept a day), recall_audits, aap_state (where the AAP listing read stopped),
  junk_discovery (the ledger of discoveries taken off for junk feeds), feed_poll_log (each poll of a feed
  both collectors poll), seen_urls (the addresses of pruned items, so they are not counted again), and
  schema_migrations (one-time migration steps); on the relay's database,
  relay_changes and relay_ack. New columns: articles.audit_keep, articles.day_of_record and
  articles.extract_version, classifications.body_refreshed, feed_polls.collector (part of the key) and
  feed_polls.estimator. Every column is added by a one-time migration step, so an older database opens
  as before.

## Publication: a human coding study decides (2026-10-12)

An outside review pointed out that the reliability study measured stability, not correctness: the
automatic study is the same model (claude-sonnet-5) re-judging its own sample with the same prompt
(classify_llm.request_params, only the model name overridden, no temperature set, so the API default
applies), and a passing rerun (kappa 0.62 on 9 October) had replaced the provisional marking with
published counts. The owner has reversed the earlier decision to run without a human in the loop: he and
two research assistants hand code a fixed sample, and unchecked state sourcing is published only when
Cohen's kappa between their codes and the model on unchecked state sourcing versus independent
journalism is at least 0.6 (config.KAPPA_WARNING_THRESHOLD), with at least 10 articles on each side,
overall; a language with at least 20 items in that comparison and a lower kappa is withheld on its own.
Until then the counts are provisional, with the reason in meta.json (relay_provisional_reason,
relay_human_study). pipeline.agreement sample now draws the hand coding sample, seeded and stratified by
language and, within each language, by the model's current category: about 200 articles for each
language with at least 300 model labelled articles, from articles with an extracted body. The sample
drawn on 12 October (data/reviews/human_sample_2026-10-12.csv) holds 1,400 articles, 200 each in
English, Spanish, Malay, Korean, Arabic, French and Portuguese, with no model label in the sheet;
data/reviews/CODING_GUIDE.md tells coders how to decide and fill it, and compute now stores the per
language results that the export's language veto reads, which it previously left out. The model study
is still run and recorded as a stability check: it never makes counts publishable, a passing one leaves
them provisional, and a current one below 0.6 withholds them. Because labels from the older prompt are
being judged again, pairing a first label from that prompt with a re-judgement under the current one
would measure the prompt change and call it stability, so pipeline/second_rater.py draws only first
labels made under the current prompt, and the automatic study waits, as "waiting_for_labels" in the
study state, until RELIABILITY_MIN_POOL such labels have a body on disk; a forced study waits too. A
study batch keeps the prompt its requests carried, pairs only first labels made under that same prompt
(one judged again while the batch ran drops out), and records the prompt in the study's details; a new
prompt is a reason for a new study, like a new model. The export treats a check made under an older or
unknown prompt as history: it moves from relay_reliability to relay_reliability_history, withholds no
counts and vetoes no language. A check stored before studies recorded their prompt is dated by when it
was computed, so the 9 October rerun is now history and the site shows no current stability figure until
the next study. METHODOLOGY.md (pipeline/methodology.py) gains a stability paragraph from
meta.relay_reliability_history giving the old check's date, sample size and kappa, saying it ran under
an earlier version of the classifier's instructions and is kept as history, withholding nothing, and
saying from meta.relay_study when the next check runs; the provisional counts paragraph points to a check
"described below" only when one is rendered, and the withheld languages sentence names unchecked state
sourcing counts as its subject instead of a dangling "They". On the site, the notes and the Stability
check row of the method facts carry one sentence from TrackerCompute.stabilityStatusText, and the
Reliability study technical note, the unchecked state sourcing FAQ answer and definition in
docs/content.json and the method note in docs/index.html say that hand coding alone decides
publication. No ruleset, gate or category change.

## Definitions: state origin is authored or commissioned, then run unaltered (2026-10-12)

The state origin definition said the text was written by an entity of the Chinese state and that nobody
at the receiving publication formed an editorial view, and two of its own examples failed it: the
Corriere della Sera placements were written by the publisher's own studio, CairoRcs, to a Xinhua
commission, and an opinion editor chose to run every ambassador's op-ed. The definition now reads, as
the original study's codebook does, text authored or commissioned by a Chinese state entity and
published without substantive editorial alteration; deciding to run a piece, or placing it on an
opinion page, is not an alteration. Rewritten in the model prompt's state origin paragraph and its
"Answer A only when" line (pipeline/classify_llm.py, part of prompt 2026-10-12), the exported category
text that feeds the methodology file (pipeline/export.py), the site's category definition
(docs/content.json) and the method prose (docs/index.html). The four routes already match the wording:
a wire credit and a distribution stamp carry state authored text, a sponsored or partner disclosure
needs a named state entity as the sponsor, and a diplomatic byline is state authored. No ruleset, gate
or category count change; METHODOLOGY.md picks up the wording at the next export.

## Model stage: session labels name the prompt they were made under (2026-10-12)

Labels made in an interactive session must now carry model_version "claude-sonnet-5 (session, prompt
<PROMPT_VERSION>)", today "claude-sonnet-5 (session, prompt 2026-10-12)". A new command, `python -m
pipeline.labels export-prompt --out <dir>`, writes SYSTEM_PROMPT.md and OUTPUT_SCHEMA.json from
classify_llm as the code holds them, each headed with the prompt version, a sha256 of its content and
the model_version the labels must carry, so a session pass is never run on a stale copy of the prompt;
data/labels/README.md says the labelling agents must be given these two exports. The ingest
(pipeline.labels) counts as older_prompt and skips, never fatally, a session label made under an older
prompt or naming none, since it would only be queued for the API again; the article stays in the queue
for a label under the current prompt. A line whose model_version does not name the session is
malformed, so a session label can no longer pass for an API label ("claude-sonnet-5 (prompt
2026-10-12)"). The committed files from 30 September to 4 October name no prompt and are skipped on
every ingest, which changes nothing in the database: all their labels were applied at the time. No
ruleset, prompt or category change.

## Audits: what the candidate gate and the relevance gate miss (2026-10-12)

The model reads an article only when a weak state origin signature or an official sourcing trigger
fires (classify_rules.classify_article; llm_triggers in pipeline/signatures.yaml), and agency copy run
without a credit line names none of these. On the 12 October snapshot 69,504 articles were labelled
independent journalism or not relevant by rules and never reached the model, against 9,781 the model
judged, and 153 of them repeat the headline or feed summary of a state origin item another outlet
carried with its credit (the lookalike route of ruleset 2026.10.1 now sends such copies to the model).
New pipeline/recall_audit.py: `sample` draws a seeded sample of those articles, stratified by language,
with a tenth of the draws on the lookalikes and the outlets with a wire copy habit (named, news
agencies, five or more state origin labels, or three or more lookalikes) oversampled threefold, and
writes a CSV with no rule label to data/reviews/ beside a key that freezes the strata and the rule
labels; `compute` weights the coders' labels back to the population and stores the share placed in
state origin or unchecked state sourcing, with a Wilson interval on the effective sample size, per
language and overall, in a new recall_audits table, which the export reads as meta.candidate_recall.
METHODOLOGY.md and the README say the gate cannot see uncredited relay and give the latest miss rate.
The first sample, 300 articles, is data/reviews/recall_audit_2026-10-12.csv; the rate is not yet
measured. AsiaOne, which the sample names for oversampling, was registered the same day.

Items the relevance gate rejects are still pruned after three days, but a random 2 percent of them
(config.GATE_AUDIT_KEEP_SHARE) is now kept for good, so the gate's recall can be audited for any past
period instead of only the last three days. Whether an item is kept is decided by a salted hash of its
url_hash (store.gate_audit_keep): reproducible, the same on the relay and the hosted side, and blind to
outlet, country and language. The decision is recorded in a new articles.audit_keep column, added to
older databases on open and backfilled for the rejections still stored (4,434 of 217,678 on the
2026-10-12 snapshot, 2.0 percent); prune_gated_out decides any undecided rejection before it deletes and
never deletes a kept one, and rows with a planted byline flag stay exempt as before. Kept rows keep every
field. pipeline/gate_audit.py `sample` now draws only from the kept set, takes --since and --until,
stratifies by language with a per-language floor, and writes each row's stratum size and weight;
`compute` reports the weighted rate next to the raw one. The database grows by about 1,700 rows, roughly
1.8 MB, a day, some 50 to 55 MB a month. No ruleset, gate or category change.

## Relay: outlets refused by the runner move to the relay, with their refused articles (2026-10-12)

Pearls and Irritations, which has run signed pieces by Chinese embassy officials, refused the hosted
runner on all 45 China-relevant articles it found there (HTTP 403), and article 3945 could not be
classified again because its re-fetch was refused (refetch_failed:http_403). Every active outlet with
most of its China-relevant articles of the last 30 days refused by the runner (HTTP 403 or 429) was
requested once from the owner's machine with the project user agent and the robots.txt check, through
the pipeline's own fetch and extraction: 22 returned the article and move to the self-hosted collector,
each with a dated note giving the evidence (Pearls and Irritations, Reporter.al, Semanario Universidad,
El Confidencial, Daily Graphic, The Standard Gambia, Kandit News, Linkiesta, 38 North, Yabiladi, Telma,
ION News, The Vibes, One PNG, Ndarinfo, Suriname Herald, North Press Agency, Avesta, iWitness News, El
Nacional, Al Sahwa, News Diggers). 50 outlets refused that request as well and stay where they are, among
them TASS, Kyunghyang Shinmun, Bloomberg, NDTV and the New York Times; the seven relay outlets in the same
position (The Washington Times, Times of Israel, Davar, Behadrei Haredim, Khmer Times, UPI, Tempo) are
refused from both places. Until now the relay fetched only what its own feeds discovered, so an article
the runner had found and been refused stayed failed once it left the feed. The relay now reads the
machine's copy of the main database, read only, and queues those articles for its own outlets, at most
20 per outlet per run and once each, and not for an outlet whose latest fetch on the relay was refused
too (pipeline/relay.py seed_refused). The bundle carries articles fetched in its seven days as well as
those discovered in them; on ingest a failed row moves on to fetched without keeping its old failure,
and a labelled article whose re-fetch was refused gets its body back with its label and status
untouched, so it can be classified again. The relay also no longer re-admits an item the section feed
correction gated out from its older copy. No ruleset, gate or category change; collection only.

## Relay: the bundle carries what changed, and no checkout keeps old bundles (2026-10-12)

The relay branch always held one parentless commit, so the repository on GitHub never grew, but the
owner's checkout did: the bundle was committed in it, and each force push left the old bundle in the
reflog of origin/relay, 230 bundles and 667 MiB by 2026-10-12. The commit is now made and pushed from a
throwaway repository (pipeline/relay.py push_bundle), so nothing stays behind. With the shared polling of
148 outlets the hourly bundle would have grown from 3.98 MB to about 9.3 MB, nearly all of it rows the
runner already held, sent again every hour for seven days. After it saves its database the runner now
force-pushes an acknowledgement to the relay-ack branch (python -m pipeline.relay ack): the build time of
the latest bundle it took in with no gap before it. The next bundle carries only the article rows and
shared-feed polls changed since an hour before that, read from a change log that triggers keep on the
relay's database (relay_changes); the small per feed and per day records still cover the whole window,
and a bundle opens with a record saying which kind it is. A runner whose database went back to an older
cache or the snapshot sees the gap, keeps its own acknowledgement and sends it back, so the next bundle
fills the gap; with no acknowledgement, or one older than seven days, the bundle is whole as before. Two
things ingest never used stay home: entry dates of unsaturated polls, which only rejudging a saturated
poll reads, and gated-out rows of shared outlets older than ingest accepts. On a simulation of the relay
database with the shared outlets added, a whole bundle is 7.1 MB instead of 9.3 MB, and an hourly bundle
with the acknowledgement one to two hours old is 0.6 to 1.0 MB. No ruleset, gate or category change;
collection only.

## Collector: short feeds polled by both collectors (2026-10-12)

Every active feed was measured on the feed_polls ledger from 12 September: 1,159 of 2,139 polled feeds
return 20 items or fewer, and 221 of those (218 outlets, all on the hosted collector) came back as a full
window with nothing seen before in more than a tenth of their polls, together 11,867 of 41,663 polls and
an estimated 331 thousand missed items (under the estimator of the time; see the next entry), of which an
estimated 4,800 would have been China coverage. The hosted runs that actually fired were a median 2.7
hours apart (3.6 hours in the last week, against a cron of every hour), so dropped runs, not small feeds
alone, drive most of it. The thirteen worst feeds were requested once each with the project user agent
after the robots.txt check: none honours a count or page size parameter, and no same scope larger RSS
feed was found. An outlet can now carry collector: both. The runner and the relay on the owner's machine
then both poll its feeds, and its article pages are still fetched once, by the runner. The relay's items
reach the main database through the bundle and are deduplicated by URL, so each article is inserted
once, counted in discovery once and linked to near-duplicate headlines once. The runner keeps its own
feed health for these outlets. Every poll of a shared feed is logged with the collector that made it
(feed_poll_log), and a poll counts as saturated only when its window misses the previous poll by either
collector; a relay poll is judged against the runner's polls when its bundle is ingested, and a runner
poll that a relay poll reached the bundle after is judged again. 148 outlets moved to both: the 150
outlets behind the 152 short feeds the audit expected to fall under a tenth of polls saturated, less four
that are now relayed only, plus PR Newswire and GlobeNewswire; the registry counts 149 since Yemen Press
Agency's Arabic feed became an outlet of its own. That adds about 208 feed requests an hour to the relay
and no page fetches. The site and the methodology count the relay's own outlets and the shared ones
apart, badge a shared outlet "also relayed", and when the relay falls silent call only its own outlets
incomplete, saying that the shared ones fall back to the runner's hourly polls. Collection only; no
ruleset, gate or category change.

## Collector: feed saturation measured for the relayed feeds, and guarded against batch publishers (2026-10-12)

The relay recorded every feed poll in its own database, but its bundle carried only feed health, so
feed saturation and the missed-items estimate were never measured on the hosted side for the outlets it
collects: on the 12 October snapshot 28 of their feeds had any poll rows, and those only from the runner.
Each bundle now carries the relay's per feed and day poll rows for its seven day window, and feed_polls
records which collector made each poll (collector, part of the key, so a feed polled by both keeps one
row each; rows already stored are attributed to the collector that owns the database). The hosted side
replaces its copy of a relay row with the day's running total rather than adding to it, so a bundle sent
twice counts each poll once, and never touches the runner's own rows. On the snapshot this adds 792
feed-days, 7,127 polls and 297 saturated polls; meta.feed_saturation and the country saturation warnings
include them, and the section feed flooding guard reads the larger single collector's ledger so an item
new to both is not counted twice. The estimate of items lost between polls was the window's own
publishing rate times the gap since the previous successful poll, and a publisher that replaces a short
feed wholesale once a day made every batch look saturated with a window a few minutes wide: NewsMada
delivered 118 new items in 30 days and was booked 7,953 missed, and five such feeds about 15,000 between
them. The rate is now taken over at least two hours, or the feed's median window span over the last
three days when that is shorter (never under a quarter hour); one poll's estimate is at most twice the
new items the feed delivered per day over the previous fourteen days, scaled for gaps over a day; and a
feed polled at least three times a day that delivers no more than two and a half windows a day is a
batch publisher, whose burst polls count only whole batches that fell between polls. Replayed on the
three and a half days whose every item is still on record, the estimate falls from 57,651 to 34,028;
extrapolated to the ledger since 15 September it falls from 624,924 to about 406,000, the five batch
feeds from 15,423 to 1,121, and the estimated missed China items from 8,859 to about 5,400. Ledger rows
already written keep the old figures, but every row now names the estimator that made it
(feed_polls.estimator, config.MISSED_ESTIMATOR_VERSION 2026-10-12; v1 for older rows), and the site, the
daily files, the country warnings and outlets.json report the estimate only from rows of the current
estimator, marked "estimated since" the first day it ran, while polls and full windows still count every
poll on record. Collection diagnostics only; no ruleset, gate or category change.

## Collector: Google News sitemaps beside short RSS feeds (2026-10-12)

Some short RSS feeds overflow between polls at a busy site: at peak the ten items of the Rio Times feed
spanned 0.19 hours and Clarín's 0.34, and PR Newswire's twenty item list lost an estimated 431 China items
in 30 days, about 103 of them state origin. A feed of type news_sitemap (registry field news_sitemaps,
pipeline/news_sitemap.py) now reads the Google News sitemap a site lists in robots.txt, which carries
every article of the last two days with its title and publication date. It reads a urlset or a sitemap
index (at most the three most recently modified child files, one level), under the same robots.txt
check, per domain pacing and user agent as every other request. Each sitemap is polled as an extra
editorial feed after the outlet's RSS, which stays. It keeps entries up to 48 hours old, a day inside the
three day retention of rejected items, so nothing is counted twice. Items already known are skipped
before the keyword gate. New items pass the gate on their title alone, because a sitemap has no summary,
with the title_only terms of Gate 2026.10.1: with them a title alone passes 69 percent of PR Newswire's
past state origin items (49 percent on the main lists only), 25 of 49 Rio Times China items, and 38 of 39
at Telegraf and 83 of 94 at Clarín. An item the gate turned down on its title is readmitted if an RSS
feed later brings a summary that passes. A sitemap item with the same path and title as one already
stored for the outlet on another host, as with Telegraf's www and politika hosts, is not inserted. When
the sitemap reaches back to the previous poll, a saturated RSS poll's missed estimate is scaled down by
the share of the RSS window that the sitemap also lists. Each sitemap URL was requested once with the
project user agent. Registered: PR Newswire (prnewswire.com's whole English release list, issuers
worldwide included; 31 of the 36 RSS entries in its window were on it, and the items keep the outlet id
us_prnewswire in the worldwide distribution stratum), Rio Times, Clarín, Telegraf (its sitemap leaves out
the ona. and biznis. subdomains, which the RSS still covers) and AsiaOne. Daily Times answered 403 and is
not registered. Expected cost at the current cadence: about 1,700 sitemap requests a month and roughly
300 to 850 extra article fetches before the title_only list, which adds about 8 to 25 a day at PR
Newswire, because only items whose titles pass the gate are fetched. Collection only; no ruleset or
category change.

## Collector: the AAP release pages of ten ACM mastheads are read (2026-10-12)

The Canberra Times, Newcastle Herald, Illawarra Mercury, The Examiner and the six ACM mastheads added
today each carry a "Press Releases from AAP" page, where paid releases from Xinhua Silk Road, People's
Daily, CGTN, Global Times, CMG and provincial bodies reach Australian readers (49 from 2026-09-03 to
2026-10-12, found by hand). The page is an empty shell filled in the browser by AAP's aaprwidget.js, so it
was recorded as unreadable and never polled. A new section type, aap_widget (pipeline/section_pages.py),
reads the widget's own source, the AAP release listing at newsroom.aap.com.au, under the usual robots.txt
check, user agent and one request every three seconds. The first read goes back to 2026-09-03 (146
pages, 1,459 releases, all 49 among them). The releases count as each masthead's output, like any press
release section, and AAP is not registered as a worldwide distribution wire. Each masthead keeps its own
copy, linked by its title to the other copies as one underlying item, and arrives tagged as the outlet's
press release section. Because only about a quarter of AAP releases name a China term (352 of 1,459) and
all 49 state releases do, the keyword gate applies to these items, unlike other section feeds.
Australia's discovered totals rise by about 37 items a day per masthead; the backfill counts on each
release's publication day (see the backfill entry). Discovery reads the listing once before any worker
starts (section_pages.aap_preread, called from fetch_feeds.run), because reading it at the first
masthead's request, under a lock, held about ten of the sixteen discovery workers through the first
run's backfill of roughly 8.5 minutes; the mastheads take their copies from what was read. The read may
use at most 30 percent of the time the stage has left (AAP_PREREAD_SHARE). A read cut short there, or at
the 200 page cap, keeps what it reached, and the page it stopped on, the oldest release it reached and
the date it was reading back to are stored in a new aap_state table once a masthead has been polled with
the read; the next run reads the new releases from page 1, then resumes at the stored page and reads on
to the stored date, stepping back toward page 1 if withdrawn releases have moved the stretch up. A page
that fails still fails the read it belongs to, so the next run reads that stretch again. Each run reads
back to the earliest successful poll among the mastheads polled in the last seven days
(AAP_RECENT_DAYS), less six hours, so a run stopped at its deadline after some mastheads leaves the next
read covering the rest, while a masthead failing for longer no longer sets the date. Each masthead keeps
its own row for a release, at the release's page with a "masthead" parameter, so the fetch stage used to
download the same page once per masthead (about 3,500 requests for the backfill and 90 a day after, all
to one host). pipeline.fetch_articles now maps a URL to the page it copies (FETCH_KEY_RULES, with the AAP
release service as the only rule): the first row of a key is fetched, and its siblings, moved up behind
it in the queue, take its saved body and fetch outcome without a request, after a check that the body
file on disk still matches its hash. Only a saved body is lent, so one bad response is not copied to
every masthead, and each row is still classified on its own. No category, ruleset or gate term change;
collection only.

## Section discovery: plural section names, and special readers kept on a re-probe (2026-10-12)

The section probe matched "Business Wire" only in the singular, so AsiaOne's old "Business Wires" section
(/business-wires) did not count as a press release section, and "Advertorials", "Partner Contents",
"Paid Posts", "Branded Contents" and "Brand Studios" slipped the same way. The press release pattern now
takes the plural and the spaced forms of Business Wire, PR Newswire, GlobeNewswire, EIN Presswire and
Accesswire, plus "Press Wire" and "PR Wire"; the sponsored and partner patterns take the plurals of their
English names. A wire still needs its PR qualifier: a bare "Newswire", "Wires" or "Newswires" is an
agency desk or an outlet's name (newswire.lk), and "Business", "Newsletter" and "Partners" alone stay
editorial. A desk crediting a sponsor and a site-wide feed are still never sections. `discover_sections
apply` rebuilt every section feed from the probe file and would have turned the ten aap_widget sections
back into type page, which reads nothing on those pages. It now keeps any section feed whose type is
neither rss nor page exactly as registered, whether the new probe finds the same section, misses it or
cannot reach the site, and keeps that section's record (and its readable flag) in release_sections. The
probe recognises a section page that embeds newsroom.aap.com.au/static/aaprwidget.js and records it as
aap_widget without looking for a feed on it, so another ACM masthead added later is registered on the
right reader. No section already recorded changes kind; no ruleset, gate or category change.

## Collector: items from the seven removed section feeds face the keyword gate (2026-10-12)

Seven feeds registered as press release, sponsored or partner sections were removed on 2026-10-12: the
five desks and site-wide feeds of the entry further down (Index.hr's sport desk, the Mirror's rss.xml, LIGA.net's
/ua/all/rss.xml, Novaya Gazeta Europe's /feed/rss and Asia-Plus's asiaplus.news/feed/), the Japan Times
top stories feed and the 7NEWS "Sponsored" feed (next entries). Every item they brought in had passed
without the keyword gate and still carried a section arrival tag. Each export and each relay run now
drops that tag and runs the gate on the stored title and summary (pipeline/section_retag.py), as for any
editorial feed item, without the feed's summary boilerplate and with the item's url, as discovery reads
it. Asia-Plus's Russian site feed is now polled by tj_asiaplus, the Russian outlet split from Asia-Plus
English, so the 409 items the section poll stored under the English outlet first move once to
tj_asiaplus with their outlet-day discovery counts and the feed's poll records, so none is counted under
Asia-Plus English and none twice, and are then gated in Russian there (13 stay relevant on the local
database). For the first six feeds, 8,502 articles on the local database: 42 stay relevant, keep their
labels and now count as arriving by an editorial feed; the other 8,460 become gated out, which takes them
out of every count, and their 8,250 current labels (8,239 not about China, 11 independent journalism, no
state origin or unchecked state sourcing) are withdrawn and kept. The 7NEWS feed's 716 items all fail the
gate, and their labels (714 not about China, 2 independent journalism) are withdrawn and kept. fail_reason
records why, and each day's gate relevant discovery count drops by one per item. Pruning never deletes a
gated-out row that holds a label or a review. The relay no longer re-admits a corrected item from its
older copy. The correction is idempotent. Over the first six feeds, section arrival counts for relevant
articles fall from 8,772 to 4,175 sponsored, 3,826 to 672 partner and 15,174 to 14,423 press release,
and the 7NEWS items leave the sponsored count as well. All seven removed feeds are recorded with their
reasons as rejections on their sections in release_sections, the one list both section discovery and
the correction read, so a later probe or apply never registers them again, nor the 7NEWS topic page, the
Index.hr sport desk or the single Japan Times release they came from. No ruleset, signature or category
change.

## Registry: 7NEWS "Sponsored" section feed removed (2026-10-12)

7NEWS was registered with a sponsored section at /lifestyle/sponsored, polled on /lifestyle/sponsored/feed,
because the homepage links that page as "Sponsored". The page is the site's dormant "Your Feed" topic
(described as deals, products and offers), which the site also attaches to sport and 7you stories, so its
feed is the latest stories across desks: requested once with the project user agent after the robots.txt
check, it returned 100 items, 63 from /sport/ and 23 from /7you/, and every card on the page is marked as
not sponsored. Over 30 days it brought in 716 items through the section's gate exemption with the term
section:sponsored; 714 were labelled not about China (714 of 7NEWS's 727 such labels) and 2 independent
journalism. The feed is removed and the section kept in release_sections as unreadable, the outlet and its
editorial feed /rss unchanged. /sponsored and /brandedcontent return 404, and no other sponsored, partner
or branded content section was found on the homepage, so nothing replaces it. PerthNow and The West
Australian, on the same platform, register no section feed and link no such section. No ruleset, gate or
category change; collection only.

## Registry: Japan Times release and sponsored sections corrected (2026-10-12)

The Japan Times was registered with a partner section at /comfacts/348, which is a single press release
(GAKU naming the paper a media partner), polled on /feed/topstories/, the "Today's Top Stories" feed every
page declares as its alternate. That feed has been frozen since February 2025: its five items are news
stories from 2023 to 2025, which entered the database through the section's gate exemption with the term
section:partner. The page belongs to the paper's news release section, /comfacts ("News releases, powered
by Comfacts"), whose own feed /comfacts/feed/ returned 30 releases, and the paper's sponsored content
lives apart on info.japantimes.co.jp ("Special supplements: sponsored contents planned and edited by JT
Media Enterprise Division"), whose feed /cms/feed/ returned 10 items. Both were requested once with the
project user agent after the robots.txt check, and both now replace the wrong entry, as press release
and sponsored sections. No China Daily, China Watch or Xinhua placement was found in either section or
among the paper's stored articles; the sponsored archive holds one 2022 country report on the Greater
Bay Area. No ruleset, gate or category change; collection only.

## Registry and collector: feeds read in the wrong language, and feeds that served junk (2026-10-12)

A script count over the titles of the last 30 days (share of Cyrillic, Arabic, Han, Hangul, Thai,
Devanagari and Thaana letters, and a stopword check for the Latin script languages) found outlets whose
feeds publish in another language than the one registered, so the keyword gate, the residual rules and
theme tagging read them on the wrong terms. Asia-Plus English had carried the Russian site's feed, so
"Таджикистан и Китай намерены расширить сотрудничество" was read on English terms. Corrected in
sources/outlets.yaml, each outlet's notes saying how: Haqqin.az is Russian, not Azerbaijani; Tunisie Focus
French, not Arabic; Goobjoog Somali, PSM News Dhivehi, Mimi Mefo Info French and NewsMada Malagasy. The
Arabic feeds of North Press Agency and Yemen Press Agency, Monaco Tribune's English feed and Asia-Plus's
Russian feed become outlets of their own language. Hankyoreh English now polls its English edition feed,
since its old feed serves the Korean site. Telex English is inactive because its feed served the
Hungarian site and no English feed could be confirmed. Stored articles keep the language they were
discovered with, and reclassification never revisits a language, so a new correction
(pipeline/language_retag.py) runs at the start of every discover stage. It sets each article's language
to its feed's and clears its theme tags. It withdraws the residual rules labels made in the wrong
language and keeps them, 64 on the local database (51 not about China, 13 independent journalism), and
the rules stage judges those articles again. It also reruns the gate, as the collector reads it, on
gated-out items still stored, which re-admits 6 (3 Korean, 2 Hungarian, 1 Arabic); items the section
feed correction gated out are left to it, since it already gated them in the feed's language. 1,440
articles in 14 outlets change language, including 35 older items from The National and the Oman Observer
stored as Arabic before those outlets were registered as English. Model labels and state origin labels
from a signature are not touched. The same title count found seven feeds that no longer carry their
outlet's news: Les Nouvelles Calédoniennes now polls its site's own /rss.xml, since its FeedBurner feed
serves Vietnamese shoe advertisements, and Centrafrique Presse, La Voix de Djibouti, Guatevisión, ORTC,
BelaPAN and the Pacific Islands News Association are inactive, because their feeds serve gambling posts,
advertorials, machine-generated titles or a "No Feed Access" placeholder and no clean feed could be
found on their sites. Deactivation withdraws no stored label, so a second correction
(pipeline/junk_feeds.py, run at the start of every discover stage and every export) sets aside every
item the seven feeds brought in from the first day their stored titles were junk (2026-09-03, or
2026-09-24 for the three outlets added that week), which each outlet's new junk_feed entry records. Its
labels are withdrawn and kept as not current, among them the one label taken from these items, a
Centrafrique Presse gambling post counted as independent journalism. On the local database that is 334
stored items, none ever requeued or pruned, and 2,127 discoveries taken off the per day denominators
(BelaPAN 1,163, Centrafrique Presse 505, Guatevisión 121, La Voix de Djibouti and ORTC 120 each, Les
Nouvelles Calédoniennes 60, the Pacific Islands News Association 38), recorded in a ledger
(junk_discovery) so that a rerun or a relay bundle sent again takes nothing off twice. No ruleset,
signature, keyword or category change.

## Collection: pruned items are not counted again (2026-10-12)

The address of every pruned item is now remembered (store.seen_urls, keyed on the first 15 hex digits of
its url_hash), for SEEN_URL_RETENTION_DAYS (120) from its latest sighting, or SEEN_URL_UNSEEN_DAYS (14)
for one no feed lists again, so a feed that keeps listing an item no longer brings it back and counts it
among the items screened after every prune. Such repeats were 8.2 percent of the discoveries of
2026-10-01 to 2026-10-11, at least 123,682 since collection began and near 8 percent by extrapolation,
so discovery denominators fall by about 8 percent from the day this runs. The days before it are left
as counted, because the pruned addresses that would show which items came back are gone; the export
dates the change (meta.rediscovery.fixed_from, the day after the first run that remembers pruned
addresses), the site notes it under the running total, and a share taken across that date compares a
denominator with repeats against one without. On the relay, items
handed off and pruned are remembered the same way. Collection only; no ruleset, gate or category change.

## Collection: backfilled items count on their publication day (2026-10-12)

Every figure still counts an item on the UTC day it was discovered, with one exception decided at insert
time and stored in a new articles.day_of_record column. An item brought in by a backfill read counts on
its publication day when it was discovered more than BACKFILL_LAG_HOURS (36) after it was published and
was published on or after COLLECTION_START (2026-09-03). Its discovery is recorded on the same day, so
the count and its "% of all its news" denominator move together, and Australia's timeline does not show
five weeks of AAP releases (about 1,459 for each of ten mastheads) on one day. A feed's first successful
poll ever, and its first after more than BACKFILL_GAP_DAYS (3) without one, is a backfill read too, so a
new outlet or section (38 feeds of 28 outlets at the next run, 21 of them Australian) or a feed back from
an outage (18 feeds) does not put its older items on the day it was read. The rule was not applied to
every item: measured on the 2026-10-12 database, it would have moved 1,473 of 80,272 classified articles
(1.8 percent, 23 percent in Kazakhstan and 18 in Australia), and the denominators of days whose rejected
items are already pruned could not have moved with them. No existing row changes day. Items backfilled
onto days before the Gate 2026.09.6 and 2026.09.7 marks were admitted by the current gate. The
methodology says so under What the numbers mean, beside the discovery day rule, with the lag, the gap
and the start date read from the configuration. No ruleset, gate or category change.

## Collector: feed summary boilerplate found per feed (2026-10-12)

Some feeds wrap every item's summary in the same text. Ghana News Agency's world feed puts "Stay
up-to-date on the latest news from Ghana and the world" before the lede and "Get the full content now"
after it, so any two of its items shared about 55 percent of their summary shingles whatever they
reported, and the recall audit counted four of its articles as lookalikes of another GNA item. A new
module, pipeline/boilerplate.py, finds such text per feed from the data: in a feed's items discovered in
the 30 days before its latest one (at most 200, and only when at least 10 carry a summary), every five
word shingle found in more than half of the summaries is boilerplate, and those words are removed before
the summary is read. The result is kept per feed in a feed_boilerplate table for a day. On the
2026-10-12 snapshot 368 of the 2,161 feeds active in the last 30 days have some; the worst are members
only and log in notices (Budapest Times, News Diggers), placeholder Lorem Ipsum text (NBS Uganda,
Guatevision), a podcast feed's fixed line (Sveriges Radio), Post-Courier and GNA's wrappers, and most of
the rest are "The post ... appeared first on" lines. The lookalike route and the recall audit compare
summaries without it: the audit's lookalikes go from 153 to 149 (summary matches from 17 to 13), the
four dropped being exactly the GNA same outlet pairs, and the route's matches over the same population
are unchanged at 145. The keyword gate reads it the same way (Gate 2026.10.1). Body near duplicate
linking never reads summaries and is unaffected. No category change.

## Underlying items: near-duplicates linked on body text as well as headline (2026-10-12)

Title matching (pipeline/fetch_feeds.py, word overlap of 0.7 within a country over three days) missed
syndicated copy under a rewritten headline and never looked across countries. The fetch stage now
fingerprints every saved body (a 64 value one permutation MinHash of five word shingles, five character
shingles for scripts without spaces, pure Python, about a millisecond a body) into a new
body_fingerprints table, and links articles from different outlets, in any countries, discovered within
seven days of each other, whose signatures estimate at least half their shingles shared, into a new
body_links table (pipeline/body_dups.py). Same outlet pairs stay with title matching, and a body matching
its own outlet's text under another headline is treated as page template and never linked (50 such
bodies locally, mostly paywall stubs and extraction failures). On the 6,855 bodies stored locally the
pass linked 124 pairs title matching had left apart, 104 across countries (AP, Reuters, AFP and EFE copy
under new headlines, Media OutReach and GlobeNewswire releases, republished commentary, and one Xi'an
International Communication Center piece run by outlets in Kyrgyzstan and Uzbekistan); none of a hand
checked sample of 30 joined two different items, while pairs between 0.3 and 0.5 began to join
different versions or different stories about one event. articles.dup_group_id is unchanged and stays
the only group the model's label is copied along, so no category count moves. The export joins title
groups through body links for the underlying item figures (uniqA, uniqAB and the syndicated badge), which
can only fall. Measured while underlying items were still counted per country and day, the links moved 3
independent journalism items and 1 not relevant item, none in state origin or unchecked state sourcing,
because most links cross countries; since Data schema 12 counts each item once per window and across
countries, links across days and countries count too. Translations are not matched and no machine
translation is attempted; the methodology file and the README say so. Stored bodies are fingerprinted by
the fetch stage for up to three minutes a run until none is left, or at once with python -m
pipeline.body_dups backfill. No ruleset, gate or category change.

## Crawler: robots.txt Crawl-delay honoured (2026-10-12)

The crawler read robots.txt as RFC 9309 defines it, which has no Crawl-delay, so a site asking for one
was still fetched every three seconds; AsiaOne asks for 60. pipeline/robots.py now reads Crawl-delay from
the same group as the Allow and Disallow rules (the group naming the crawler, else "*"), and the one
rate limiter, pipeline.fetch_articles._rate_wait, paces each domain at the larger of three seconds and
that delay, capped at ten minutes with a log line above the cap. Article pages, feeds, section pages,
section discovery and the relay all pace through it. Fetching robots.txt once for each of the 1,996
domains behind the active outlets (1,869 serve one) found 147 that set a Crawl-delay for us, none in a
group naming the crawler: 39 at three seconds or less, 68 above three and up to ten, 24 up to 30, 11 up
to 60 and 5 at ten minutes or more (one asks for 1,200 seconds and is paced at 600). The 108 domains above
three seconds carry 135 feeds of 100 active outlets and 8,497 gate relevant articles over the last 60
days, most of them at ten seconds (Mirror, SCMP, CNA, The Straits Times). So that a slow domain does not
hold up the run, a worker sleeping through a long delay hands its slot to the next domain, an article
whose slot would fall past the deadline stays queued for the next run without spending an attempt, a
failing slow site is retried on a later run rather than within this one, and a feed host with a long
delay gets its first feed every run and further feeds only while they fit in ten minutes, taking turns by
the hour. Classification is unchanged.

## Store: one-time migration steps run once per database (2026-10-12)

pipeline/store._migrate seeded daily_discovery and daily_outlet_discovery from the articles table
whenever they were empty, on every new connection, and the other data backfills in it (audit_keep
marking, the per outlet-day gate_relevant backfill, llm_usage pricing, the feed_polls rebuild) ran
whenever their column was missing. The fetch workers each open their own connection, so several
connections opened together on an unmigrated database all found the step undone and ran it: the seeds
failed on the primary key and the column steps on "duplicate column name". Each of these steps now checks
and runs under BEGIN IMMEDIATE and is recorded in a new schema_migrations table in the same transaction,
so exactly one connection runs it and the rest wait for the lock and skip it. Every column the migration
adds (day_of_record and extract_version among them) is added the same way. On a new database the seeds
find no articles and are recorded as done, so they never later turn articles stored without
record_discovery into discovery counts. The runner's database already has its discovery tables filled
and its columns added, so nothing is recounted; each step is recorded on its first connection after this
change. No ruleset, gate or category change.

## Registry: AsiaOne registered (2026-10-12)

AsiaOne (Singapore) is registered as sg_asiaone, because the owner's paper found it had the highest
unchecked state sourcing of the outlets it read, with agency copy run without a credit line, and it was
not in the registry at all. Its China, Asia and World section feeds (asiaone.com/rss-feed/63335, /4 and
/5) were each requested once with the project user agent through pipeline.feeds_util.fetch_feed, after
the robots.txt check, and each returned 10 items; the News feed (/rss-feed/1) returns 404. They are
polled by the hosted collector, at the 60 second Crawl-delay AsiaOne asks for, and its Google News
sitemap is read beside them. No press release, sponsored or partner section was found on the homepage
under the discovery rules, and the old /business-wires section returns 404. Ownership is private: SPH
Media has been a minority shareholder since a December 2022 private buyout, so the leaning is unassessed
rather than copied from The Straits Times. The recall audit already names sg_asiaone for oversampling, so
its items are now drawn more often. No ruleset, gate or category change; collection only.

## Registry: Australian Chinese-language outlets registered inactive (2026-10-12)

The Chinese language rule says outlets that publish primarily in Chinese are registered inactive with a
note so the exclusion is visible, but no Australian one was registered. Seven are now, all `active:
false` with the rule's standard inactive_reason: Sydney Today (今日悉尼), 1688.com.au and its Daily Chinese
Herald (澳洲日报), the Australian Chinese Daily (澳洲新報), Vision China Times (看中國), Australian Chinese
News (大华时代, Melbourne), ABC Chinese and SBS Chinese (the Mandarin service, which au_sbs's note already
said was excluded). Each note gives the source that confirms the outlet is Australian and writes in
Chinese. Only Vision China Times shows a feed; for the others the schema's one required URL is the
homepage or section page, and the note says so. The pages of Sydney Today, 1688 and the Australian
Chinese Daily answered a bot challenge, so their language was confirmed from published research rather
than the site. No article page was requested. Australia's active outlet count does not change (30), and
it now has 48 registered outlets; no ruleset, gate or category change.

## Registry: Le Monde, Novinky.cz and Seznam Zprávy article pages are unreadable by automated clients (2026-10-12)

Every stored Le Monde body is its 286 character "Client Challenge" bot page (103 of 103), and every
stored Novinky.cz and Seznam Zprávy body is the same 1,091 character Seznam consent wall ("Nastavení
souhlasu s personalizací", 61 of 61 each), all served with HTTP 200 in place of the article; ruleset
2026.10.1 now fails such bodies as unreadable. Le Monde's robots.txt allows the article paths for the
project user agent, but the comment at its head says robots and other automatic browsing of the site
are prohibited and asks for a licence, so no article was requested from the owner's machine and the
outlet was not moved to the self-hosted collector. Novinky.cz and Seznam Zprávy allow the paths; two
articles of each, requested once from the owner's machine with the project user agent, returned the same
wall, so the self-hosted collector would not help. No challenge is solved and no consent is accepted
automatically: each outlet's notes record the finding, the outlets stay active on the hosted collector
for their feeds, and licensed or publisher granted access is the only route to their article text. No
ruleset, gate or category change.

## Registry and site: international broadcasters, ownership sources and the US based leaning rater (2026-10-12)

Government funded broadcasters aimed at audiences abroad get their own ownership value,
state_funded_international, instead of counting as public service beside the BBC at home, ABC or CBC:
Radio Free Asia (five services), RFE/RL (Georgian, Armenian, Turkmen, Belarusian and Current Time),
Deutsche Welle (three feeds), RFI and France 24 (five feeds), the BBC World Service language services
(Brasil, Hindi, Persian, Burmese), KBS World and RNZ Pacific, 24 outlets, 19 of them active. Public
service goes from 75 to 56 active outlets. State run outlets without editorial independence, such as RT
and Sputnik, stay under state. The schema, the research merge, the export's ownership groups and the
"Who publishes it" chart (a fifth ownership colour, validated on adjacent pairs against the card
surface) carry the new group. Three ownership records now rest on primary sources. Radio Free Asia's
source is the U.S. Agency for Global Media's firewall page, which cites the statute (22 U.S.C. 6204(b))
protecting the professional independence of the networks it funds, instead of a US based rating site.
TRT World (inactive) is recorded as state: since 2018 TRT is governed by Presidential Decree No. 4, which
replaced the organ articles of Law 2954, the President appoints its board and director general, its
foreign broadcasts are tasked with promoting the state, and nothing protects its editorial decisions
from the government, whatever its legal label as an impartial public entity says. Radio Taiwan
International is recorded as state as well: its Establishment Act makes it a foundation budgeted and
supervised by the Ministry of Culture, which selects its board, and the Act has no editorial
independence provision, so it does not meet the definition of state_funded_international. Each entry's
note gives the reason. Media Bias/Fact Check, which rates on a United States spectrum, was the only
source for 239 leanings outside the United States (229 active). No documented rule asks for two sources,
so they are kept, but the export now marks them leaning_source_weak, the chart's table gives each leaning
group's count of them with a caution, and the note under the chart names where every leaning comes from.
A new optional leaning_second_source that is not the same rater lifts the mark. No ruleset, gate or
category change.

## Site: the combined state-linked measure and the per million people basis are removed (2026-10-12)

The State-linked articles measure added state origin, unchecked state sourcing and the candidates
carrying official Chinese sourcing that are still waiting for the model, so one figure mixed judged
counts with an unjudged queue whose size varies by country. It is gone from the Measure buttons above
the map, the theme counter and the leaning section, from the ranked list, the legend, the sparkline, the
worldwide line and the country panel, and from both CSV exports (the view export loses its target and
target_in_published_items columns, the daily export its target_in_published_items column). A stale
selection of it falls back to state origin. The waiting candidates stay visible only as their own
figure, labelled as not a finding: the country panel's verification pending row and outlet column, the
dotted line in the panel chart, the notes strip, official_sourcing_pending in the view export and
awaiting_model in the daily export. The data files are unchanged; the theme counts keep their combined
slot, which no measure reads. The method text already called articles per million residents the least
defensible denominator, and a figure that cannot be defended is not shown: the Per million people button
is gone from both Basis rows, above the map and above the ranked list, with its four metrics, its grid
entries, its legend unit, its not-on-the-scale line, the population line in the tooltip, the 100,000
resident floor and the worldwide stratum's "not applicable" legend line (docs/compute.js, docs/app.js,
docs/index.html). A saved or stale selection of it falls back to Number of articles in the same measure.
The method prose (docs/index.html, pipeline/methodology.py, README.md) says in one sentence that no per
capita figure is shown because population is a poor denominator for what outlets publish, and the
worldwide stratum note (docs/content.json) no longer mentions a per million value. Population stays in
the data files: sources/population.json's note, the Population figures row of the method page, the
denominator passages in the method page, METHODOLOGY.md's template and the README, and the comment over
the basis grid in compute.js now say that the export carries each country's resident population in
latest.json, that it reaches the page only in the population column of the CSV of the current view,
which keeps its place, and that no figure is divided by it. Three functions nothing called any more are
removed (relayStudyText and distributionName in docs/app.js, plantedPlural in docs/compute.js), and the
comment on the theme counts in pipeline/export.py describes the second slot as a combined count kept so
the file layout does not change. No ruleset, gate or category count change; METHODOLOGY.md picks up the
wording at the next export.

## Site: phone layout, complete days, whole-word quotes and labels in their own case (2026-10-12)

The site no longer scrolls sideways on a phone: example quotes show a bare link as its host
(globenewswire.com rather than a long attachment URL), and quotes, headlines, outlet names, notes and
tooltips wrap at 320 to 375 pixels, as do the example and theme card grids, the route picker, the
timeline controls, the theme counter's count line and the leaning table. The top country card, its share
and the findings sentence count the 30 complete days the map and ranked list open on, so on 2026-10-12
Italy reads 423 articles (32 percent of the world's state origin, 37 percent of the country outlets'
under that toggle) instead of 436, which had included the partial export day. The all time cards and the
"What we found" summary end on the same last complete day as the map's All time view (state origin 1,627
rather than 1,642 on 2026-10-12, matching the map under Both, Country outlets and Worldwide wires), the
map tooltip's top carrier and route count the same 30 complete days as the figure beside them, and
Articles screened is labelled as the collector's running total. Example quotes no longer start or end
inside a word ("ttend the summit", "una quo"): the export drops a word the rules window cut in half and
marks each cut with an ellipsis, cutting on characters in Chinese, Japanese and Thai, and the highlighted
phrase still lines up; the page no longer trims such a quote a second time, while quotes from older data
are trimmed as before. The rules store each matched span widened to the nearest whole word on both sides
(at most 20 more characters, and unchanged in Chinese, Japanese and Thai) with an ellipsis where text was
left out, so new quotes keep every word. The map legend printed the All China coverage unit as "china
articles per active monitored outlet", because it lowered the first letter of every unit, and the same
blanket lowering turned "Signed by a Chinese diplomat", the themes "Taiwan and Hong Kong", "Chinese
domestic politics" and "Belt and Road" and "% of its China coverage" into lower case mid-sentence. A new
helper, lowerFirst in docs/compute.js, lowers only the first letter of a sentence case label, and never
when the label opens with China, Chinese, a place such as Taiwan or Hong Kong, a country name or an
acronym; every mid-sentence label in docs/app.js goes through it, and tests/frontend_smoke.js checks the
unit for every measure and basis. The map's cap, the 95th percentile of every country's value on every
date of the timeline, and the theme counter's own cap are fixed across the dates shown but computed in
the browser from the published data, so each daily export recomputes them and a shade can stand for a
different number from one week to the next. The map key, both theme counter keys, the Color scale
paragraph and the methodology now say so in the same words, and the method text also says that past 60
days the cap is pooled over 60 evenly spaced dates. No cap is shipped in the export, since one computed
at export time from the same files would be the number the browser already computes; holding one cap
across updates is a baseline choice left to the owner. No figure or classification changes beyond the
complete-day windows.

## Site and methodology: themes versions marked (2026-10-12)

Themes versions now carry dates, as ruleset and gate changes do, and the theme timeline marks each one
with a green dash-dot line whose tooltip names the version and what changed. The history is kept next to
the code that reads it (HISTORY in pipeline/themes.py, exported as meta.themes_changes): 2026.09.3 on
2026-09-14, when the counter was introduced and read only the first 800 characters of each body, and
2026.09.4 on 2026-09-15, when it began reading the whole body. A version bump in themes.yaml without a
dated entry fails the tests. The themes paragraph in METHODOLOGY.md named only the current version;
pipeline/methodology.py now writes one sentence from meta.themes_changes listing each version with the
day it took effect and what changed, and says that every export re-tags each article whose tags were
made under another version, so the published series has no break at a version change and the timeline
marks record when the method changed. An export without themes_changes says that no earlier version is
recorded. No ruleset, gate, category or themes change.

## Documentation: README figures and stale references brought up to date (2026-10-12)

The README still gave figures from earlier days as if they were current. The registry expansion increment
now says its counts (1,469 active outlets in 233 countries, 10 Chinese-language exclusions) were true at
the time and adds the 12 October registry from pipeline.registry (2,128 outlets, 1,703 active across 235
countries and territories in 68 languages plus the two press release wires, about 310 inactive for
blocking the crawler, 19 Chinese-language outlets excluded, seven of them Australian, and 17 gap
entries), the last also stated under the Chinese language rule. The cost paragraph claimed a full 600
call day cost two to four dollars; it now describes the monthly budget (TRACKER_LLM_MONTHLY_BUDGET_USD,
110 dollars) and the second daily cap it sets, and the measured price of a call from recorded tokens,
about two cents at full price and one through the Batches API, at which the budget binds before the
ceiling (235 synchronous or 470 batched calls a day on 12 October). The theme counter reads term lists in
English plus eighteen other languages, not fifteen, follows the Distribution toggle as well as Measure
and Window, and leaves about one in ten China articles with no theme, not a quarter, mostly in
Bulgarian, Ukrainian and Greek, which have no list of their own. The ruleset carry forward takes up to
2,000 articles a run, not a few hundred. The registry unevenness paragraph lists the three measures and
four bases the site has after today's removals, names the Distribution toggle and gives the current
spread (44 outlets in the United States against a median of 6, 16 single-outlet countries). The workflow
variables list gains the monthly budget and session grace variables, the API key is marked as added since
the 2026-09-04 setup, the 900 pixel map breakpoint is marked as of the time (720 now), and AsiaOne is
described as registered the same day as the recall snapshot. The comments on the relay metric in
docs/compute.js and on RELAY_PROVISIONAL_DISPLAY in pipeline/config.py, and the README paragraph on
unchecked state sourcing, no longer say that any study clears or settles the counts: only hand coding
publishes them, a hand coding kappa or a current stability check below the threshold withholds them, and
a passing stability check leaves them provisional. The README gives the interface's names for the four
categories and says that unverified relay is the prompt's internal name. No ruleset, gate, category or
data change.

## Registry: wider Australian coverage (2026-10-12)

Australia goes from 20 to 30 active outlets and from 23 to 41 registered. Every candidate feed was
requested once with the project user agent through pipeline.feeds_util.fetch_feed, after the
robots.txt check and at most one request per domain every three seconds. Added and polled by the
hosted collector: The Spectator Australia, The Saturday Paper, Inside Story and six ACM regional
mastheads (Bendigo Advertiser, The Courier, The Border Mail, The Standard, The Daily Advertiser, The
Advocate). The Australian Financial Review is reactivated on /rss/feed.xml (its paywalled articles are
recorded as paywalled, as for any outlet), and ABC News gains its Just In feed. Registered inactive so
the gap is visible: The New Daily, InDaily, Michael West Media and East Asia Forum, whose feeds return
403 from the owner's machine as well, and news.com.au, Sky News Australia, The Daily Telegraph, The
Courier-Mail and The Advertiser, whose robots.txt (like The Australian's and the Herald Sun's, retested
the same day) disallows every crawler not on a named list, so they were not requested. AAP, 9News and
10 News publish no public feed found. No ruleset, gate or category change; collection only.

## Collector: five registered section feeds were desks or site-wide feeds (2026-10-12)

Five section feeds in sources/outlets.yaml were never sections. Index.hr's sport desk was registered as
sponsored because its header reads "Sport sponsored by" (3,650 items, 2 relevant). The other four are
site-wide feeds that a section page declared as its alternate: the Mirror's rss.xml as partner (3,070
items, 13 relevant), LIGA.net's /ua/all/rss.xml as press release (721, 9), Novaya Gazeta Europe's
/feed/rss as sponsored (498, 9) and Asia-Plus's asiaplus.news/feed/ as sponsored (398, 2). Nearly every item
from them carried a section arrival tag and covered every desk. They are removed, the outlets and their
editorial feeds stay, each outlet's notes say why, and release_sections keeps the section with feed null
and readable false. Discovery now reads only the words before a sponsor credit ("Sport sponsored by",
"Weather presented by") as the section name, never takes a feed whose path is only feed words and a
language edition (/rss.xml, /feed, /feed/rss, /ua/all/rss.xml, /rss) as a section's feed, and applies
both rules again to earlier probe results. Articles already collected keep their rows, tags and
verdicts. The registry's section feed total in the export falls from 136 to 131. No category, article
count, ruleset or gate change; collection only.

## Collector: section feeds that flood the queue lose the gate exemption (2026-10-12)

Press release, sponsored and partner section feeds skip the keyword gate because that is where
paid placements sit. Discovery sometimes registers a whole desk as a section (Index.hr's sport
page, whose header reads "Sport sponsored by", admitted 3,650 items in 30 days with 2 relevant;
ten such feeds were a sixth of all fetches). A section feed whose resolved items over the trailing
30 days exceed 500 with under 2 percent classified anything other than not relevant now has the
keyword gate applied again until it quietens or earns the exemption back (fetch_feeds
section_feed_flooding; the discover counts gain section_feeds_guarded). Pending items are never
read as irrelevance. No category, count, ruleset, gate or schema version change.

## Distribution toggle: country outlets and worldwide wires apart (2026-10-11)

The map card gains a Distribution toggle beside Measure, with Both (the default, today's figures),
Country outlets and Worldwide wires. It drives every figure that sums across countries: the map, the
ranked list and the legend line, the timeline sparkline, the "What we found" cards, the theme
counter's world totals, the panel's world table and both CSV exports, which gain a distribution
column. Under Country outlets the worldwide stratum leaves every sum; under Worldwide wires only it
remains, the map is left unfilled with a caption saying the wires are not drawn by country, and the
top country card becomes the wires' own 30 day count. Outlets monitored and the leaning chart count
newsrooms only and never include the wires. Opening a row the mode hides, from the search, the list
or a link, puts the toggle back to Both. The export writes, beside each world figure, the stratum's
own part: a per day `wld` block in global_series.json and `state_origin_wires`, `unchecked_wires`,
`independent_wires` and `china_total_wires` under meta.findings.all_time, plus a `last_30d` block
with the world's and the wires' state origin; files without them read as zero wires. Schema version
11. No count changes: interface and export fields only. No category, ruleset or gate change.

## Worldwide distribution stratum for the press release wires (2026-10-11)

Press release distribution wires (PR Newswire, GlobeNewswire) were counted on their own and appeared
in no total: in the last 30 days that left 179 state origin releases off the map and the country
list. They are now counted as a stratum of their own, "Worldwide distribution", under the code WLD in
latest.json, the daily files, outlets.json (the wires carry country WLD in the export only) and
articles/WLD.json. Country counts are unchanged: the stratum is never attributed to the United States
and never added into any country's numbers. World totals, the global series and the findings totals
now include the worldwide stratum; the top country card never names it. The site lists it in the
ranked list and the country search, adds one line under the map legend, and says in the state origin
card how many arrived through the wires. meta.distribution_wires keeps its old name and equals the
stratum. Schema version 10. No category, ruleset or gate change.

## Stress test before the 12 October presentation (2026-10-11)

Two audits of the 8 and 9 October changes, and the fixes. The recurring bylines diagnostic no longer
leaves the command line (`python -m pipeline.planted recurring`): on the site it listed syndicated
agency reporters under a heading about invented journalists, so planted.json drops it and the section
shows only matches against the published watchlist. The daily model cap no longer discounts batched
calls twice: llm_cost.measured_per_call now measures the full price of a call from the stored token
counts and the batch discount is applied once, and days with an uncollected batch, and today, are left
out of the measurement; the live cap of about 900 calls a day was roughly double what 110 dollars buys.
The persona matcher splits multi-author fields on punctuation and "and" before normalising, tries the
reversed "Last, First" order, strips honorifics, accepts a byline word only at the start of a line or
after "written", and matches front names as whole phrases, so "una declaracion de Alice Johnson" and
"National Social Research Center" no longer flag. The scan compiles one alternation for all front names
and domains, stops at the stage deadline and checkpoints every commit; load_personas validates types,
ids and operation references and warns instead of failing; the export never stops on a bad watchlist;
flags_total gains articles_total. On the site, each render step is isolated so one failure cannot skip
the rest or bind every control twice, a missing planted.json says so instead of reading as a finding,
the Start here and FAQ text describe the reliability check as done, and theme data tolerates nulls.
No category, count, ruleset, gate or schema version change.

## Planted bylines: matches against personas named in published investigations (2026-10-08)

A new side channel, pipeline/planted.py, reads sources/personas.yaml (personas and false front
organisations named in published investigations of planted contributions, each entry cited to a
public report) and flags articles whose author field equals a listed name, whose body opens with a
byline naming one, or whose title or body cites a listed front or one of its domains. Names are
compared after normalisation (casefold, diacritics stripped, punctuation dropped, middle initials
optional) and a single word never matches. The author field is checked on every discovered item,
including those the relevance gate rejected, because a planted byline need not be about China; a
flagged item is exempt from the gated out pruning so the row stays readable. Flags live in a new
planted_flags table, are never deleted, and are written at the end of the discover and fetch
stages and before the export; a failure there is recorded in the stage's counts and never stops
a run. The export writes docs/data/planted.json (operations, personas, fronts, flags newest
first and capped at 500, counts by country and outlet, and a diagnostic of bylines that recur
across at least three outlets and two countries in 30 days, with generic bylines, outlet and wire
names and pure wire copy left out) and meta.json gains a planted block. A flag means the byline
matches a persona named in a published investigation, cited there; the tracker cannot prove a
byline is fictitious, the recurring table is for human review, and absence of a match proves
nothing. No category, count or ruleset change: no flagged article is folded into state origin,
unchecked state sourcing or independent journalism, and no published figure moves. Schema
version 9 for the new file and meta block only.

## Model stage: budget sized for the API to judge every waiting article (2026-10-08)

The default monthly budget (TRACKER_LLM_MONTHLY_BUDGET_USD) rises from 30 to 110 dollars and the
session hold (TRACKER_SESSION_GRACE_DAYS) defaults to 0 instead of 3, so every article the rules
cannot settle is offered to the API on the day it is discovered. 110 dollars covers about 340 calls a
day at the measured cost of a call, the intake seen in September across both routes. The session
route stays as a fallback for when the budget is spent, and data/labels/api_first.jsonl still puts
the articles it cannot fetch first. The daily cap now divides the budget by the measured average
cost of a call over the current and previous month once at least 500 recorded calls exist
(llm_cost.measured_per_call), instead of always by the full price constant, which would have
allowed only about 200 calls a day; the cap reports per_call_measured. No ruleset, gate or schema
version change: what a label means is unchanged; only how many articles the API takes, and when.

## Model stage: API budget kept for what the session route cannot reach (2026-10-04)

Since 2026-09-30 waiting articles are also judged by the session route (pipeline/labels.py),
which refetches their text on the owner's machine at no API cost. Some sites refuse that fetch
(HTTP 403, robots.txt, paywall, gone), so those articles can only be judged by the API from the
runner's own copy of the text. The API budget is now kept for them. A new committed list,
data/labels/api_first.jsonl (one object per line: url_hash, reason, listed_at, source), names
them; pipeline.labels.load_api_first reads it, skipping and counting a malformed line, and the
labels ingest never reads it as a labels file. classify_llm.run now splits the waiting articles
after the near-duplicate collapse into two pools: the listed ones, drawn first, and the others
discovered more than LLM_SESSION_GRACE_DAYS days ago (env TRACKER_SESSION_GRACE_DAYS, default 3;
0 turns the hold off), drawn with what budget remains, each with the existing stratified draw.
Younger articles are held for the session and not offered to the API on that run; being held is
not a ceiling event. The stage's counts gain api_first_eligible, api_first_sent, grace_eligible,
grace_sent and held_for_session (sent is calls made, or requests submitted in batch mode).
With no list and every article past the grace period the stage draws exactly as before.
METHODOLOGY.md says so in the model stage section. No ruleset, gate or schema version change:
what a label means is unchanged; only which articles the API takes, and when, changes.

## Model stage: session labels imported by the runner (2026-09-30)

The monthly API budget ran out on 2026-09-16 and 4,585 articles are waiting for the verification
judgement. They may now be judged by the same model, with the identical system prompt and the
headline and body only (never the outlet, the country or the URL), through an interactive session
on the owner's machine at no API cost. The session's answers are committed as data/labels/*.jsonl,
one JSON object per line, and the new pipeline/labels.py imports them on the hosted runner at the
start of every run, right after the relay bundle (workflow step "Ingest session labels, if any").
A label is applied only to an article still waiting for the model with no current classification,
through classify_llm.store_result, the call the API path uses, so the label row, the route of a
state origin label, the article status and the near-duplicate copies come out exactly as after an
API reply. Everything else is counted and skipped, so the files can be ingested any number of
times without inserting anything twice; each ingest records a run_log row (stage labels_ingest).
These labels carry a model version that names the session, for example "claude-sonnet-5
(session)", keep their own JSON line as the raw response, count as model labels in every figure
and are sampled by the reliability study like any other. meta.json gains session_labels (total and
by model version), a part of llm_labels_total rather than an addition to it, and METHODOLOGY.md
says how these labels are produced. No ruleset or schema version change: what a label means is
unchanged, and the new meta key is additive.

## Export: atomic file writes (2026-09-30)

A local preview loaded docs/data/latest.json while an export was still writing it, got a
half-written file, and rendered the whole map as not monitored. pipeline/export.py now writes
every generated file (the docs/data JSON, the per-country article files and the audit jsonl
lines) to a temp file in the same directory and renames it into place, so a page loaded mid
export always reads either the old file or the new one, never a partial one. No label or figure
changes.

## Crawler: robots.txt read as RFC 9309 defines it (2026-09-28)

The weekly feed check on 2026-09-28 reported 47 feeds blocked by robots.txt. Thirteen of them,
at eight outlets (iDNES, Lidovky, Kloop, Kommersant and its English edition, Lenta, Sveriges
Radio and teleSUR), are in fact open to crawlers. urllib.robotparser applies the first matching
rule in file order where the standard applies the longest match, so "Disallow: /" followed by
"Allow: /rss.aspx" closed the feed the publisher had opened; it has no "*" or "$" wildcards; and
it drops a "?" from a rule, so "Disallow: /?" became "Disallow: /" and closed Lenta entirely. The
crawler also required both its own group and the "*" group to allow a URL, where the standard has
a group naming the crawler replace the "*" group. pipeline/robots.py now does all four the way the
standard says. The other 34 feeds really do disallow crawlers and stay blocked. All eight outlets
had stayed active in the registry, so their per outlet denominators counted outlets that were
contributing nothing; they contribute again from the next run. No label meaning changes.

## Ruleset 2026.09.9 (2026-09-25)

A budget diagnosis of September's model spend, run after the $30/month cap
was hit on 2026-09-16, traced most of the waste to two mechanisms rather than
to genuine judgement calls.

- classify_article no longer routes a gate-exempt section item (press_release,
  sponsored or partner; fetch_feeds.py skips the keyword gate for these by
  design) to the model on a weak signature alone. It now also requires the
  item to clear the same body-level China relevance test the residual C
  versus not_relevant rule already used (body_relevance, backed by
  gate.count). Strong signatures still give Category A outright, with no
  model call, exactly as before; an official sourcing trigger still routes to
  the model on its own. Of the 3,921 model calls made in September, 1,119
  (28.5%) carried a section tag, all reached by this same weak-signature
  path, and 1,059 of those (94.6%) came back not_relevant, 77.6% of every
  wasted call this period. Article bodies are not retained past
  classification, so whether the new test would actually have failed on each
  of those 1,059 could not be confirmed against September's stored data; on
  the working assumption behind this change, that a not_relevant model
  verdict on a section item usually means its body never substantively named
  China either, roughly 1,059 calls would have been avoided. The other 60
  section-tagged, weak-signature calls this period labelled A (59) or C (1);
  these are the change's exposure, an upper bound rather than a measured
  loss, since a genuine Chinese state placement names a China entity in its
  own body almost by definition and should still clear the relevance test and
  reach the model as before. Both figures come from the stored gate_terms and
  llm_trigger of every current September classification.
- Two exclusions (the existing signatures.yaml exclusions mechanism) strip
  recurring, content-independent site furniture that was extracted into the
  article body and read as a state-linked sponsorship signal: nst_recirc_boilerplate
  for New Straits Times (my_nst), where a "Branded Content" label inside a
  fixed five-line recirculation widget fired all five language variants of
  the sponsored_placement group at once on unrelated sports and entertainment
  stories, and bharian_nav_menu_boilerplate for Berita Harian (my_bharian),
  where the "Iklan Web" item in the site's own navigation menu fired the
  sponsored_asia weak signature the same way. In September, my_nst made 49
  model calls that carried only this span (48 not_relevant, 1 C) and
  my_bharian made 51 (47 not_relevant, 2 B, 2 C); both exclusions were built
  directly from those calls' stored llm_trigger spans. All 100 calls would
  now be kept out of the model, since neither outlet's items carried any
  other signature or trigger. The 5 that had reached B or C (1 at my_nst, 4
  at my_bharian) would be relabelled not_relevant by rule on the next
  reclassification; the model's own reasoning on all 5 already described the
  scraped body as site navigation with no actual article content, so this is
  a loss of a low-confidence default judgement, not of a substantiated label.
  Each regex matches the whole recurring block, not the bare disclosure word,
  so it does not touch a genuine per-article sponsorship disclosure. A third
  outlet named in the same diagnosis, Utusan Malaysia (my_utusan), was
  checked and excluded from this change: its wire_stamp_alone hits (58 of its
  59 September model calls) are each a distinct PRNewswire release from a
  different company in its press-release section, not a repeating span, so
  there is no literal boilerplate to exclude there; that outlet's waste is
  addressed by the section change above instead.
- Combined, the two changes would have kept roughly 1,159 of September's
  3,921 model calls (about 30%) out of the model: an estimated 1,059 from the
  section change plus a fully measured 100 from the two exclusions, against
  an upper bound of 65 at-risk labels (60 unmeasured from the section change,
  expected to be far fewer in practice, plus 5 measured B or C labels from
  the exclusions).

## Ruleset 2026.09.8 (2026-09-22)

An audit of the whole instrument found that most state origin labels rested on
patterns that could not carry them, and that the labels were concentrated in a
handful of outlets because of how those patterns were written rather than
because of anything in the world. Relabelling changes historical numbers.

- The three distribution stamp patterns paired the carrier's own name with any
  state media mention within 3,000 characters, across paragraphs. A carrier
  names itself in every one of its own datelines, so the test collapsed to
  "does this outlet mention Chinese state media at all". An agency report about
  Chinese disinformation was labelled state origin. The state entity must now
  stand where a credit stands: in the release's own issuer line within 150
  characters of the wire dash, in a source trailer anchored to the start of a
  line, or in a slash credit inside the stamp. A new pattern keeps the genuine
  case where the release headline names the issuer above the wire dateline.
  Measured before the change: 231 of Italy's 242 labels and 118 of the United
  States' 122 came through these patterns from one carrier each.
- china_media_group_credit no longer accepts the bare acronym. CMG is the
  Toronto ticker of Computer Modelling Group Ltd, whose buyback releases carry
  "news release" within a few words of it, and three Canadian articles about
  that company were labelled Chinese state origin, 19 percent of the country's
  count.
- The state entity test that lets two weak signals become a label no longer
  accepts topic words. Silk Road, Belt and Road, Chinese government, State
  Council and Information Office are subjects, not issuers, and a travel
  advertorial naming the Silk Road plus two sponsorship phrases produced a
  confidence 1.0 label with no model call.
- Native script credit lines added for Greek, Hebrew, Persian, Turkish,
  Vietnamese, Russian, Arabic, Korean, Japanese and Chinese. The ruleset carried
  58 Latin spellings of Xinhua and none in the first five, against 12 active
  Greek outlets, 11 Persian and 10 Hebrew, so state origin was structurally
  undetectable outside Latin script and every comparison between countries was
  confounded with script. Each name counts only where a credit stands: after a
  source marker, alone in the author field, alone on a closing line, or in a
  wire dateline. A bare mention in the body is a citation and goes to the model,
  as the Latin spellings already did.
- A native script photo credit exclusion, so a picture credit is not read as a
  text credit in those scripts.

## Gate 2026.09.7 (2026-09-22)

The gate decides what enters the corpus, not how an article is labelled, and
gate decisions are recorded per item and never revisited, so this applies only
to items discovered after today.

- Korean is now matched as a substring, like the other languages that attach
  grammar to the noun. Korean particles join the noun with no space, so a word
  boundary missed every inflected mention: the bare form matched while "of
  China", "China and Taiwan" and "in Beijing" did not. Korean is the third
  largest source of unchecked state sourcing labels, so those counts were a
  floor. Two Korean terms are now corroborating rather than admitting, because
  they are homographs: Xinjiang is also the word for a kidney, and the term for
  the Communist Party sits inside the word for heavy industry.
- Latin script terms keep their word boundary and their case rule in every
  language, including the ones matched as substrings. They did not before, so
  "Xi" matched inside "Taxi", "BRI" inside "British" and "PLA" inside "display"
  in Thai, Japanese, Lao, Khmer and Burmese items.
- Croatian, Serbian and Bosnian "Kina", "Kine" and "Kini" now require a capital
  first letter. They are China and also the genitive and plural of the word for
  a cinema, so lowercase forms admitted film listings: 86 percent of the
  Croatian corpus turned out not relevant. An all capitals headline still
  matches, and the country is a proper noun, so real uses are unaffected.
- The residual relevance rule counts one stretch of text once. The term list
  overlaps itself, so "Xi Jinping" counted as both "Xi" and "Xi Jinping" and a
  headline naming one Chinese person cleared the "a term in the headline and at
  least two occurrences" branch on its own, and was published as independent
  journalism.

## Data schema 8 (2026-09-28)

Three new generated pieces for a front end aimed at an educated newcomer. No
label changes, and every figure below reuses the existing rollups and
definitions: discovery day attribution, distribution wires excluded from
every country figure, paywalled items uncounted, unchecked state sourcing
gated by relay_visible and relay_provisional exactly as before.

- meta.findings, a small summary the front end turns into a "what we found"
  box: since when the data runs, the all time state origin, unchecked state
  sourcing and independent totals with the state share of China coverage, the
  country carrying the most state origin in the last 30 days with the outlet
  and route that carried most of it, and state origin output by the
  publishing outlet's ownership, all time and active outlets only. Unchecked
  state sourcing is null in this box whenever it may not be shown, the same
  gate the rest of the interface already applies.
- docs/data/examples.json, up to six recent worked examples each for state
  origin and, when it may be shown, unchecked state sourcing: headline,
  outlet, country, url, discovery date, route, and the short matched span
  that produced the label, cut to at most 25 words, with the character
  offsets of the matched phrase inside it so the front end can highlight it.
  Examples are varied by country and outlet, most recent first, current
  ruleset labels only, distribution wires excluded.
- latest.json carries top_carrier and top_route per country for the last 30
  days of state origin, so a map tooltip can say what carried the country's
  number.

## Data schema 7 (2026-09-22)

Changes to what is counted and where, from the same audit. Anyone comparing
figures across this date needs all of these.

- Every figure is now attributed to the UTC day the item was discovered.
  Numerators used the published date where a feed gave one while every
  denominator used discovery, so shares were computed against a denominator
  that did not contain the same articles: on 1,033 of 2,857 country days the
  numerator exceeded its own denominator. It also manufactured 14 days of
  history before collection began, a published timeline starting 2026-08-20
  with real article counts and no discovery at all, which read as a ramp up
  that never happened. Published dates are kept on every article record.
- Global press release distribution services are a new registry tier and are
  counted as their own stratum, never inside a country. A worldwide release
  service is not a national newsroom and its releases are not placements in the
  market it is registered in. This moves 122 state origin labels out of the
  United States, whose count goes from 122 to 0, and the world total from 658
  to 536. The releases are still collected, classified and reported, under
  distribution_wires in meta.json.
- State origin with no signature at all is reported as its own stratum rather
  than as a row in the route table. It is the model asserting state origin
  where the deterministic layer found nothing, which is a different kind of
  evidence. It was 37 percent of the total.
- Figures containing unchecked state sourcing are omitted from latest.json when
  that label may not be shown, because a share published beside the state origin
  count and the China total gives the withheld number back by subtraction.
- A country whose unjudged backlog rivals its judged articles carries a warning
  that it is not comparable with countries whose backlog is smaller.
- The audit trail is one file per day instead of one per month. A month in one
  file meant a fresh 39.5 MB blob in every export commit, twice a day.
- METHODOLOGY.md no longer claims the counts rest on hand coding when the study
  behind them is a model rerun, and no longer claims the recorded sampling
  fractions can be used to reweight counts. They cannot.

## Data schema 6 (2026-09-20)

No label changes. Unverified relay can now be mapped on its own, beside state
origin, and the way its counts are released changed, which anyone reading the
CSV exports needs to know.

- Before any reliability study has run, relay counts are shown as provisional
  instead of withheld: one model's judgement, marked as unchecked everywhere it
  appears. In the CSV exports the unverified_relay columns are filled where they
  used to be blank, and relay_status reads provisional. A new column,
  unverified_relay_underlying_items, counts distinct items behind the placements.
- Provisional means not checked yet, never checked and failed. Once a study
  reports, the counts are published if kappa on the relay versus independent
  journalism judgement is at least 0.6 and withheld if it is not.
- The study runs by itself: the same model re-judges a stratified sample of 250
  labelled articles through the Batches API, at most once a calendar month, when
  the monthly model budget can pay for it. Its pairs are kept in
  data/export/reliability/.
- Daily files carry tb (relay among the items the top outlets published), so
  relay has a share of monitored output, and theme counts carry a fourth value
  for relay. meta.json gains relay_provisional and relay_study.
- Fixed on the way: recording a second rater study raised after its calls had
  been paid for, and the per language result of a model study was stored under
  a key the export never read, so a language could not be withheld on it.

## Ruleset 2026.09.7 (2026-09-15)

Every one of the 229 published state origin labels was read, not sampled. Six
were wrong, 2.6 percent, and all six are the same kind of mistake: an outlet's
own coverage of Chinese state activity treated as Chinese state text. That is
the distinction the instrument exists to draw, so the correction makes the
headline number smaller and more defensible.

Four rested on a cooperation phrase sitting near a Chinese entity: a Bahraini
culture authority hosting a Chinese show "in cooperation with the Chinese
embassy", a Senegalese report on a panel CGTN organised, a Sierra Leonean report
on a Chinese medical team "in partnership with" a local clinic, a Syrian
business story citing Xinhua. Those phrases are now removed from the paired
sponsored patterns and from the bare Arabic, Indonesian and Russian patterns,
which require a real disclosure term instead, and sponsored_prose_phrases_head_tail
is retired. One rested on xinhua_writer_byline matching "According to a report by
Xinhua reporter" mid sentence in a locally bylined article; that pattern is now
anchored to a byline position. One rested on a diplomat name inside an author
field that held navigation text; author fields are now cleaned in extraction,
which is where the defect was, and backfilled over stored rows.

Five further labels were correct but misrouted: Antara republishing Xinhua copy
in Indonesian, recorded as a sponsored disclosure because no credit pattern
matched a co-credit byline. Two additive patterns, xinhua_co_credit_author and
xinhua_translator_credit, move them to wire credit, so the route table stops
overstating sponsored placements by five.

immediapress_with_state_entity and wire_stamp_with_state_entity are untouched.
They carry 73 labels, the Italpress "(XINHUA/ITALPRESS)" syndication and the PR
Newswire releases sourced to Xinhua and Global Times, and those are exactly what
the paired patterns are for.

Expect state origin to fall from 229 to about 223 as articles are reclassified
forward at 2,000 per hourly run. The mixed ruleset marks stay on the timeline
until that finishes, which is the honest reading: labels made under different
rules should not be read as one series.

## Ruleset 2026.09.6 (2026-09-15)

Bare sponsored disclosure patterns are now scoped to the article instead of the
whole page. The eleven weak sponsored_* patterns move from any_with_labels to a
new byline_head scope (author field, headline, first 400 characters of the
body), sponsored_prose_phrases_head_tail moves to a head_tail scope that does
not read page labels, and cairorcs_studio moves to labels_head_tail. Every
pattern that pairs a disclosure with a named Chinese state entity is unchanged
and still reads page chrome, because there the entity names who paid.

Page labels capture site furniture, so the unscoped patterns fired on
navigation strips, ad slots and menu entries rather than on a disclosure
attached to the article: a staff bylined New Straits Times sports report with a
"Branded Content" item in its "What To Read Next" strip, a Focus war report
beside an "(Anzeige) Solaranlagen" ad slot, a site-wide "Sponsored Content" menu
entry. Measured on the model queue, 459 of 2,586 waiting articles, 18 percent,
were queued this way.

Measured effect over 673 articles: 194 fired before, 75 still fire, 119 stop.
None of the 119 had "Advertorial" or "Xinhua" in the author field, and all 52
that did still fire, so signed advertorials and wire bylines are untouched. In
that set no article moved into or out of state origin; the changes are
candidates that are no longer sent to the model. Two fixtures were added from
real articles, and articles labelled under 2026.09.5 are reclassified forward
during the hourly runs, 2,000 per run.

## Gate 2026.09.6 (2026-09-15)

Made after a methodology review. The signature list is unchanged, so the
ruleset version stays 2026.09.5 and no article is relabelled; these change what
reaches classification and what the interface may publish. The gate carries its
own version because it decides what enters the corpus, and that boundary moved
today.

Gate decisions are recorded per item and never revisited, so this gate applies
to items discovered from 2026-09-15 onward. Items the old gate rejected are not
re-examined, and those rejected more than three days ago are already pruned.

What re-examining them would recover was measured rather than assumed. Running
the new gate over the 151,367 rejections still stored admits 7: all of them on
the new province and city terms (Jiangxi, Hengqin, Shanxi, Guangdong, Dalian),
none on the state origin signature exemption, and two of the seven are the
Dalian Commodity Exchange in palm oil reports, which the residual relevance rule
drops afterwards. A re-gate pass is therefore not worth the run time. That
figure bounds only what is recoverable from rejections still stored: it cannot
speak for items already pruned, or for feed entries that rolled off a feed
before any poll saw them, so it is not evidence that the earlier gate lost
nothing.

The relevance gate now carries every Chinese province, autonomous region and
the largest cities, and an item whose title or summary already fires a state
origin decision passes whatever its keywords say. In three days of rejected
items, province-only headlines such as a Jiangxi cooperation anniversary and a
Dalian APEC meeting were discarded before any signature ran. Outlets in
Singapore and Malaysia no longer pass on phrases about their own Chinese
communities (home_phrases in pipeline/keywords.yaml). Items from an outlet's
press release, sponsored or partner section skip the gate.

When the daily model call ceiling binds, the articles sent are a stratified
random draw across countries with the allocation recorded per day, instead of
the oldest first.

Every state origin label now stores its route (the signature group that
established it) and every article its arrival (editorial feed or a release
section). Existing labels are filled at the next export. Every feed poll records
whether its window reached the previous poll, with an estimate of missed items,
and the relay collector's hourly passes travel in its bundle.

The interface shows unverified relay as not yet measured until the model has
run, withholds relay counts until kappa on the relay versus independent
journalism distinction reaches the threshold, defaults to state origin only,
renames the share of all published items to a share of monitored output with a
floor of five outlets, fixes the color scale across dates, and adds a no data
state for languages without a keyword list.

Themes version 2026.09.4 tags on the whole body instead of the first 800
characters; every article is re-tagged at the next export.

## Ruleset 2026.09.5 (2026-09-14)

A bare "CGTN" line now counts as a credit only in the last 600 characters
of the body (cgtn_bare_line_tail). Under 2026.09.4 the same line anywhere in
the body was a strong credit, and an Indian Express explainer that surveys
Chinese outlets under the headings "Xinhua", "CGTN" and "Global Times" was
labelled state origin. Credit lines of the form "Source: CGTN" and the
dateline form are unchanged. Articles labelled under 2026.09.4 are
reclassified forward during the hourly runs.

## Ruleset 2026.09.4 (2026-09-11)

Two weak signatures from different groups now give a state origin label
only when a Chinese state entity is named somewhere in the article or its
page chrome: a state media outlet, an embassy or consulate, a ministry, a
provincial or municipal government, a party body, or a state cultural or
tourism agency. Under 2026.09.3 a Chery car advertorial in a Pakistani
outlet became state origin from an ad label plus a "not edited by the
publisher" note, with no state entity anywhere in it. Such pieces now go to
the model as candidates. Strong signatures are unchanged. Articles labelled
under 2026.09.3 are reclassified forward during the hourly runs.

## Ruleset 2026.09.3 (2026-09-05)

Parenthetical credits (cgtn_credit, china_daily_credit, global_times_credit,
peoples_daily_credit, china_news_service_credit, cri_credit) now count only in
the dateline form at the start of a line, "BEIJING (CGTN) --". Under 2026.09.2
the prose "China Global Television Network (CGTN) released a video" gave a
state origin label to a report about CGTN.

A new exclusion, syndication_disclaimer, removes wire boilerplate such as
"Except for the headline, this article has not been edited by ..." before
the sponsored placement patterns run, so agency copy in Indian outlets is no
longer routed as a sponsorship candidate.

Three official sourcing triggers were narrowed. cites_cctv requires
broadcaster or China context in the same sentence on either side of the
acronym, because "Detectives have reviewed CCTV" was routing crime stories
as candidates. mofa_spokesperson and state_media_reported_en require a
Chinese entity in the same sentence, because "Foreign Ministry spokeswoman
Maria Zakharova" and "according to state media" about the Korean Central
News Agency were treated as Chinese sourcing.

The residual rule that separates independent journalism from not relevant
now counts occurrences as well as distinct terms: an article is independent
journalism when the body has at least three distinct China terms, or at
least five occurrences, or a China term in the headline and at least two
occurrences. Under 2026.09.2 a report that said "China" twenty times and
nothing else scored one distinct term and was labelled not relevant.

The relevance gate changed alongside, without a version of its own because
gate decisions are recorded per item and never revisited: plain "Sino" is
now the prefix form "Sino-" only, since it matched the Spanish and Italian
conjunction; TikTok, ByteDance, Huawei, BYD, Alibaba, Tencent, Sinopec,
Mainland and CCTV count only with a second term; and outlets in Hong Kong,
Macau and Taiwan no longer pass the gate on the name of their own territory.

Articles classified under 2026.09.2 are reclassified forward during the
hourly runs; earlier rows remain in the database marked not current.

## Ruleset 2026.09.2 (2026-09-03)

Added a third signature strength, hint, that never contributes to a Category A
decision and only routes the article to the LLM as an A candidate. The press
release wire stamp pattern (wire_stamp_alone) moved from weak to hint. Under
2026.09.1 a company release carried by PR Newswire in a partner outlet whose
page chrome says "Advertorial" counted as two weak signatures from two groups
and became Category A, which wrongly labelled Huawei and vivo product
launches as state origin. Under 2026.09.2 such items go to the LLM, which
must find a Chinese state issuer before answering A.

The diplomat title patterns on the head and tail of the body
(diplomat_title_head, diplomat_title_tail) moved from strong to weak. A
news report that opens by naming "Wu Jie, ambassadeur de Chine en Côte
d'Ivoire" matched the head pattern and became Category A although a
journalist wrote it. Signed pieces still become A by rules through the
author field (diplomat_title_author_field, diplomat_list_author) and
through the new explicit byline pattern diplomat_byline_head; everything
else goes to the LLM. Articles classified
under 2026.09.1 were reclassified forward; the earlier rows remain in the
database marked not current.

## Ruleset 2026.09.1 (2026-09-02)

Initial ruleset. Signature groups: credit and dateline, distribution stamps,
sponsored placement disclosures per language, authored by state. See
pipeline/signatures.yaml for the patterns and tests/fixtures for the evidence
each pattern was checked against.
