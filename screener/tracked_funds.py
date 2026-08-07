"""
Hedge funds / institutional managers whose 13F-HR (quarterly holdings report) is
worth surfacing in the digest.

Important caveat on scope: this is *not* a licensed, verified "top 500 hedge funds
by AUM" ranking -- no such live, current ranking is available to build this from
(Institutional Investor's Hedge Fund 500, HFR, and Preqin's rankings are paywalled
subscription data, and AUM changes every reporting cycle). What's here instead is a
curated list of ~140 well-known large multi-strategy/generalist managers plus funds
specifically known for special-situations/event-driven/distressed/activist strategies
(the ones most relevant to this screener's purpose) -- compiled from public knowledge
and, critically, every single CIK below was verified live against SEC's own
company-search API (browse-edgar, cross-checked against data.sec.gov/submissions for
ambiguous multi-entity names), not guessed from memory. See README for how to re-verify
or extend.

Known gaps (name resolved to nothing under any spelling tried, or the fund appears not
to file 13F-HR at all -- e.g. short-sellers like Muddy Waters/Hindenburg/Kerrisdale
generally don't, since 13F only covers long equity positions): Balyasny Asset
Management, Systematica Investments, Perceptive Advisors, Blackwells Capital, Fir Tree
Partners (pre-2021 name; re-added under its current name "Fir Tree Capital Management"),
GAMCO Asset Management (re-added as "GAMCO Investors"), Vintage Capital Management, Q
Investments, Zeff Capital, Whitehaven Group. Add them (with verified CIKs) if you want
them tracked -- this dict is the only thing to edit.

Format: CIK (as a plain int-string, no leading zeros -- matches how models.py normalizes
ciks from EFTS hits) -> canonical fund name, for display purposes only. classify.py
checks hit CIKs against this dict's keys; it does not use the values at all.
"""

TRACKED_FUND_CIKS: dict[str, str] = {
    # --- Large multi-strategy / generalist ------------------------------------
    "1350694": "Bridgewater Associates, LP",
    "1037389": "Renaissance Technologies LLC",
    "1273087": "Millennium Management LLC",
    "1179392": "Two Sigma Investments, LP",
    "1423053": "Citadel Advisors LLC",
    "1637460": "Man Group plc",
    "1603466": "Point72 Asset Management, L.P.",
    "1736225": "ExodusPoint Capital Management, LP",
    "1454027": "Verition Fund Management LLC",
    "1665241": "Schonfeld Strategic Advisors LLC",
    "1167557": "AQR Capital Management LLC",
    "1318757": "Marshall Wace, LLP",
    "1325091": "Marshall Wace North America L.P.",
    "937617": "Davidson Kempner Capital Management LLC",
    "1415453": "Brevan Howard Asset Management LLP",
    "1351450": "Capula Investment Management LLP",
    "1009268": "D. E. Shaw & Co, L.P.",
    "1380393": "Fortress Investment Group LLC",
    "1315421": "Graham Capital Management, L.P.",
    "1612063": "Winton Group Ltd",
    "1450698": "Jane Street Capital, LLC",
    "1765924": "Susquehanna International Group, LLP",
    "1103804": "Viking Global Investors LP",
    "1135730": "Coatue Management LLC",
    "1167483": "Tiger Global Management LLC",
    "1061165": "Lone Pine Capital LLC",
    "1747057": "D1 Capital Partners L.P.",

    # --- Credit / distressed / special situations -----------------------------
    "909661": "Farallon Capital Management, L.L.C.",
    "1480532": "York Capital Management Global Advisors, LLC",
    "1525907": "Cerberus Capital Management, L.P.",
    "1300714": "Anchorage Capital Group, L.L.C.",
    "1218199": "King Street Capital Management, L.P.",
    "1011443": "HBK Investments L P",
    "1278951": "GoldenTree Asset Management LP",
    "949509": "Oaktree Capital Management LP",
    "937789": "Angelo Gordon & Co LP",
    "1169161": "Silver Point Capital L.P.",
    "1074034": "Canyon Capital Advisors LLC",
    "1281084": "Monarch Alternative Capital LP",
    "1633312": "Man Investment Partners (US) LP",  # f/k/a Bardin Hill / Halcyon Capital Mgmt
    "1509842": "PointState Capital LP",
    "1389507": "Discovery Capital Management, LLC",
    "1655183": "Mudrick Capital Management, L.P.",
    "1050417": "Contrarian Capital Management, L.L.C.",
    "1362948": "Aurelius Capital Management, LP",
    "1727012": "Diameter Capital Partners LP",
    "1358253": "TIG Advisors, LLC",
    "1541996": "Marcato Capital Management LP",
    "1313756": "Owl Creek Asset Management, L.P.",
    "1166564": "Cyrus Capital Partners, L.P.",
    "1737513": "Redwood Capital Management Holdings, LP",
    "1056491": "Fir Tree Capital Management LP",
    "1257391": "Whitebox Advisors LLC",
    "1054587": "Sculptor Capital LP",  # f/k/a Och-Ziff Capital Management
    "1730145": "Voss Capital, LP",

    # --- Event-driven / merger-arb / activist ----------------------------------
    "1336528": "Pershing Square Capital Management, L.P.",
    "1040273": "Third Point LLC",
    "1412093": "Icahn Capital LP",
    "1517137": "Starboard Value LP",
    "1159159": "JANA Partners LLC",
    "1351069": "ValueAct Capital Management, L.P.",
    "1535472": "Corvex Management LP",
    "1582090": "Sachem Head Capital Management LP",
    "1665590": "Engine Capital Management, LP",
    "1560207": "Legion Partners Asset Management, LLC",
    "1559771": "Engaged Capital LLC",
    "887762": "Barington Companies Management, LLC",
    "1058854": "Cannell Capital LLC",
    "1535392": "Mangrove Partners IM, LLC",
    "1786767": "Impactive Capital LP",
    "1885245": "Politan Capital Management LP",
    "1929389": "Irenic Capital Management LP",
    "1581079": "Anson Advisors Inc.",
    "1399386": "Bandera Partners LLC",
    "807249": "GAMCO Investors, Inc. et al",
    "1443689": "Senator Investment Group LP",
    "1731579": "Spruce Point Capital Management, LLC",
    "1755368": "Saddle Point Management, L.P.",
    "1219602": "Crescendo Partners II LP",
    "1251567": "Wynnefield Capital Inc",
    "1050154": "RCG Holdings LLC",  # f/k/a Ramius Capital
    "1085393": "Basswood Capital Management, L.L.C.",
    "1430308": "FrontFour Capital Group LLC",
    "1345471": "Trian Fund Management, L.P.",
    "1047644": "Relational Investors LLC",
    "1345523": "Riley Investment Management LLC",
    "1557543": "North Tide Capital, LLC",
    "1279150": "Scopia Capital Management LP",
    "1444043": "Camber Capital Management LP",
    "1595880": "Junto Capital Management LP",
    "1531612": "Cove Street Capital, LLC",
    "1316729": "Osmium Partners, LLC",
    "1538653": "Prescott General Partners LLC",
    "1687509": "Rubric Capital Management LP",
    "1577524": "Sarissa Capital Management LP",
    "1383838": "Shah Capital Management",
    "1452857": "Steel Partners Holdings L.P.",
    "1409888": "Standard General L.P.",
    "1370422": "Chapman Capital L.L.C.",
    "1836192": "Ancora Alternatives LLC",
    "1035674": "Paulson & Co. Inc.",

    # --- Long/short equity & other notable large managers ----------------------
    "1006438": "Appaloosa Management LP",
    "1277742": "MHR Fund Management LLC",
    "1582271": "Roystone Capital Management LP",
    "1325256": "Blue Harbour Group, L.P.",
    "1079563": "Highfields Capital Management LP",
    "1439289": "Toscafund Asset Management LLP",
    "1581811": "Egerton Capital (UK) LLP",
    "1608485": "Lansdowne Partners (UK) LLP",
    "1002858": "Third Avenue Management LLC",
    "872573": "Caxton Associates LP",
    "924178": "Moore Capital Management LLC",
    "923093": "Tudor Investment Corp et al",
    "1564702": "PDT Partners, LLC",
    "1263508": "Baker Bros. Advisors LP",
    "1346824": "RA Capital Management, L.P.",
    "1009258": "Deerfield Management Company, L.P.",
    "1055951": "OrbiMed Advisors LLC",
    "1165408": "Adage Capital Partners GP, L.L.C.",
    "1517857": "Soroban Capital Partners LP",
    "1628110": "Melvin Capital Management LP",
    "1569064": "Suvretta Capital Management, LLC",
    "1611613": "Hitchwood Capital Management LP",
    "1365341": "Cevian Capital II GP LTD",
    "1134119": "Clinton Group Inc",
    "1138995": "Glenview Capital Management, LLC",
    "1079114": "Greenlight Capital Inc",
    "1054420": "Baupost Group LLC",
    "1279891": "Wolverine Asset Management LLC",
    "1294571": "Millburn Ridgefield LLC",
    "919185": "Highbridge Capital Management LLC",
    "1744347": "Vident Advisory, LLC",
    "1317338": "Avenue Capital Management II, L.P.",
}
