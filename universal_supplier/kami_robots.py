"""RFC-style wildcard/longest-rule robots evaluation for the KAMI audit.

urllib.robotparser does not implement '*' and '$' rules used by stanki.ru.
Unrecognized/malformed directives fail closed; no UA impersonation.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit


class KamiRobots:
    def __init__(self, text: str, agent: str = "UniversalSupplier-KamiAudit"):
        groups = []
        agents, directives = [], []
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            name, value = (s.strip() for s in line.split(":", 1))
            name = name.lower()
            if name == "user-agent":
                if directives:
                    groups.append((agents, directives))
                    agents, directives = [], []
                agents.append(value.lower())
            elif agents:
                directives.append((name, value))
        if agents:
            groups.append((agents, directives))
        applicable = [(max((len(a) for a in names if a != "*" and a in agent.lower()), default=0),
                       names, rules) for names, rules in groups
                      if "*" in names or any(a in agent.lower() for a in names)]
        if not applicable:
            raise ValueError("No applicable robots group: manual verification required")
        specificity = max(g[0] for g in applicable)
        self.rules = []
        self.delay = 0.0  # Caller policy supplies a conservative floor when absent.
        for score, _, rules in applicable:
            if score != specificity:
                continue
            for name, value in rules:
                if name == "crawl-delay":
                    self.delay = max(self.delay, float(value))
                elif name in {"allow", "disallow"} and value:
                    # Query chars stay literal; only '*' and final '$' have semantics.
                    pattern = "^" + ".*".join(re.escape(s) for s in value.rstrip("$").split("*"))
                    if value.endswith("$"):
                        pattern += "$"
                    self.rules.append((len(value.replace("*", "").rstrip("$")),
                                       name == "allow", re.compile(pattern)))

    def can_fetch(self, url: str) -> bool:
        parsed = urlsplit(url)
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        matched = [(size, allow) for size, allow, pattern in self.rules if pattern.search(path)]
        return max(matched, default=(0, True))[1]
