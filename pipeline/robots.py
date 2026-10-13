"""robots.txt matching as RFC 9309 defines it.

urllib.robotparser, used here until ruleset 2026.09.10, departs from the standard in the two ways
that matter for news feeds:
  - it applies the first rule that matches in file order, where the standard applies the most
    specific (longest) match, so "Disallow: /" followed by "Allow: /rss.aspx" blocked the feed a
    publisher had explicitly opened (iDNES, Lidovky);
  - it has no wildcards, so "*" and a closing "$" in a path were read as literal characters.
The crawler also used to require both its own group and the "*" group to allow a URL. Under the
standard a group naming the crawler replaces the "*" group; it does not stack on top of it.

The result is still a crawler that does what each site asks, now read the way the site means it.

Crawl-delay is not in RFC 9309, but it is widely used and a site that sets it is asking to be
fetched more slowly (AsiaOne asks for 60 seconds). It is read from the same group the Allow and
Disallow rules come from: the groups naming the crawler, else the "*" groups. A Crawl-delay line
does not end a group, so the grouping of Allow and Disallow rules is unchanged; where a "*" record
holding only a Crawl-delay runs straight into a record naming the crawler, the two share one group
and the crawler takes the delay, which errs on the slow side. pipeline.fetch_articles paces each
domain at the larger of its own minimum and this delay.
"""
import re
from typing import List, Optional, Tuple
from urllib.parse import urlsplit

Rule = Tuple[bool, str]          # (allow, path pattern)


class Robots:
    def __init__(self, groups: List[Tuple[List[str], List[Rule]]], delays: Optional[List[Optional[float]]] = None):
        self.groups = groups
        # Crawl-delay seconds per group, aligned with groups; None where the group sets none.
        self.delays = list(delays) if delays is not None else [None] * len(groups)

    def crawl_delay(self, token: str) -> Optional[float]:
        """Crawl-delay in seconds for the crawler, chosen like rules_for: the groups naming it, else
        the "*" groups. The largest value when several apply; None when the chosen groups set none."""
        token = token.lower()
        named = [d for (agents, _), d in zip(self.groups, self.delays) if any(a != "*" and a == token for a in agents)]
        if not named:
            named = [d for (agents, _), d in zip(self.groups, self.delays) if "*" in agents]
        values = [d for d in named if d is not None]
        return max(values) if values else None

    def rules_for(self, token: str) -> Optional[List[Rule]]:
        """Rules of every group naming the crawler, merged; else of every "*" group; else None."""
        token = token.lower()
        named = [rules for agents, rules in self.groups if any(a != "*" and a == token for a in agents)]
        if named:
            return [r for rules in named for r in rules]
        star = [rules for agents, rules in self.groups if "*" in agents]
        if star:
            return [r for rules in star for r in rules]
        return None

    def can_fetch(self, token: str, url: str) -> bool:
        parts = urlsplit(url)
        path = (parts.path or "/") + ("?" + parts.query if parts.query else "")
        if path == "/robots.txt":
            return True
        rules = self.rules_for(token)
        if not rules:
            return True
        best_len, best_allow = -1, True
        for allow, pattern in rules:
            if not _matches(pattern, path):
                continue
            n = len(pattern)
            # The longest match wins; on a tie the less restrictive rule, Allow, wins.
            if n > best_len or (n == best_len and allow):
                best_len, best_allow = n, allow
        return best_allow


def parse(lines: List[str]) -> Robots:
    groups: List[Tuple[List[str], List[Rule]]] = []
    delays: List[Optional[float]] = []
    agents: List[str] = []
    rules: List[Rule] = []
    delay: Optional[float] = None
    in_rules = False
    for raw in lines:
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        field, value = line.split(":", 1)
        field, value = field.strip().lower(), value.strip()
        if field == "user-agent":
            if in_rules:
                groups.append((agents, rules))
                delays.append(delay)
                agents, rules, delay, in_rules = [], [], None, False
            agents.append(value.lower())
        elif field == "crawl-delay":
            seconds = _seconds(value)
            if agents and seconds is not None:  # outside any group it applies to no one
                delay = seconds if delay is None else max(delay, seconds)
        elif field in ("allow", "disallow"):
            if not agents:
                continue                      # a rule before any user-agent line belongs to no group
            in_rules = True
            if value:                         # an empty Disallow allows everything: no rule
                rules.append((field == "allow", value))
    if agents:
        groups.append((agents, rules))
        delays.append(delay)
    return Robots(groups, delays)


def _seconds(value: str) -> Optional[float]:
    """A Crawl-delay value in seconds: the leading number ("10", "0.5", "10 seconds"), else None."""
    m = re.match(r"\d+(?:\.\d+)?", value.strip())
    return float(m.group(0)) if m else None


_cache = {}


def _matches(pattern: str, path: str) -> bool:
    rx = _cache.get(pattern)
    if rx is None:
        anchored = pattern.endswith("$")
        body = pattern[:-1] if anchored else pattern
        rx = re.compile("".join(".*" if ch == "*" else re.escape(ch) for ch in body) + ("$" if anchored else ""))
        _cache[pattern] = rx
    return rx.match(path) is not None
