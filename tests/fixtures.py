"""Deterministic cassette bodies for the AC suite (the §3 replay channel content).

These are crafted upstream payloads: offseason, DST edge dates, postponed/doubleheader,
XSS feed, duplicate story across feeds, malformed responses. Kept as constants so every
run is bit-identical.
"""

# --- NHL ------------------------------------------------------------------------------
NHL_OFFSEASON = b"""{"clubTimezone":"US/Eastern","games":[
  {"startTimeUTC":"2026-04-10T23:00:00Z","gameState":"OFF","gameScheduleState":"OK",
   "awayTeam":{"abbrev":"NYR","score":3},"homeTeam":{"abbrev":"BOS","score":2}}
]}"""

# A game on a June (EDT) date and one on a January (EST) date — for the DST test.
# Both are FUTURE relative to the test clock (2099-06-17) so they land as "next game".
NHL_EDT = b"""{"clubTimezone":"US/Eastern","games":[
  {"startTimeUTC":"2099-06-25T23:00:00Z","gameState":"FUT","gameScheduleState":"OK",
   "awayTeam":{"abbrev":"NYR"},"homeTeam":{"abbrev":"BOS"}}
]}"""
NHL_EST = b"""{"clubTimezone":"US/Eastern","games":[
  {"startTimeUTC":"2100-01-15T23:00:00Z","gameState":"FUT","gameScheduleState":"OK",
   "awayTeam":{"abbrev":"NYR"},"homeTeam":{"abbrev":"PHI"}}
]}"""

# --- MLB ------------------------------------------------------------------------------
MLB_UPCOMING = b"""{"dates":[{"date":"2099-06-18","games":[
  {"gameDate":"2099-06-18T23:10:00Z","status":{"abstractGameState":"Preview","detailedState":"Scheduled"},
   "teams":{"away":{"team":{"name":"New York Mets"}},"home":{"team":{"name":"Atlanta Braves"}}}}
]}]}"""

MLB_EMPTY = b"""{"dates":[]}"""

# A postponed game + a same-day doubleheader (two future games) -> "next" = earliest.
MLB_POSTPONED_DH = b"""{"dates":[
  {"date":"2099-06-18","games":[
    {"gameDate":"2099-06-18T23:10:00Z","status":{"abstractGameState":"Preview","detailedState":"Postponed"},
     "teams":{"away":{"team":{"name":"New York Mets"}},"home":{"team":{"name":"Atlanta Braves"}}}}
  ]},
  {"date":"2099-06-20","games":[
    {"gameDate":"2099-06-20T17:10:00Z","status":{"abstractGameState":"Preview","detailedState":"Scheduled"},
     "teams":{"away":{"team":{"name":"New York Mets"}},"home":{"team":{"name":"Miami Marlins"}}}},
    {"gameDate":"2099-06-20T21:10:00Z","status":{"abstractGameState":"Preview","detailedState":"Scheduled"},
     "teams":{"away":{"team":{"name":"New York Mets"}},"home":{"team":{"name":"Miami Marlins"}}}}
  ]}
]}"""

# --- RSS ------------------------------------------------------------------------------
METS_RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Mets sign reliever</title><link>https://www.mlb.com/mets/news/reliever</link>
<pubDate>Wed, 17 Jun 2099 10:00:00 GMT</pubDate></item></channel></rss>"""

# Google News item: " - Publisher" suffix, rss/articles redirect link, <source> tag.
GOOGLE_RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Big Story Happens - The Associated Press</title>
<link>https://news.google.com/rss/articles/AAA?oc=5</link>
<pubDate>Wed, 17 Jun 2099 12:00:00 GMT</pubDate>
<source url="https://apnews.com">The Associated Press</source></item>
</channel></rss>"""

# Same story as GOOGLE_RSS but via a different publisher/link (a duplicate to be collapsed),
# plus a second item with NO pubDate (must badge "time unknown" and sort last).
GOOGLE_RSS_DUP = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Big Story Happens - Reuters</title>
<link>https://news.google.com/rss/articles/BBB?oc=5</link>
<pubDate>Wed, 17 Jun 2099 11:00:00 GMT</pubDate>
<source url="https://reuters.com">Reuters</source></item>
<item><title>Later Untimed Item - Reuters</title>
<link>https://news.google.com/rss/articles/CCC?oc=5</link>
<source url="https://reuters.com">Reuters</source></item>
</channel></rss>"""

# A feed item carrying an XSS payload in the title and a javascript: link.
XSS_RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>&lt;script&gt;alert(1)&lt;/script&gt; pwn &lt;img onerror=alert(2) src=x&gt;</title>
<link>javascript:alert(3)</link>
<pubDate>Wed, 17 Jun 2099 09:00:00 GMT</pubDate></item></channel></rss>"""

# --- Markets --------------------------------------------------------------------------
STOOQ_DJI = b"Symbol,Date,Time,Open,High,Low,Close,Volume\n^DJI,2099-06-17,22:00:00,38000,38250,37900,38150,0\n"
STOOQ_ND = b"Symbol,Date,Time,Open,High,Low,Close,Volume\n^XYZ,N/D,N/D,N/D,N/D,N/D,N/D,N/D\n"
YAHOO_AAPL = b"""{"chart":{"error":null,"result":[{"meta":{"regularMarketPrice":201.5,"previousClose":200.0,"regularMarketTime":4084992000}}]}}"""
