"""Creators Home step 5: research, title scoring and the launch plan.

Everything here is labelled for what it is. Title scores add checks anyone can verify (length, keyword placement,
readability) to the writing model's own clarity and curiosity ratings. Upload times are general YouTube guidance for
the audience's time zone, not this channel's data. Real numbers only come from the channel's own YouTube videos, once
it is connected; no view counts are ever made up.
"""
from __future__ import annotations

import re
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from providers import ProviderError

# Audience words -> (IANA zone, how it's described). The first match wins; the default is the channel's own guess.
ZONES: list[tuple[tuple[str, ...], str, str]] = [
    (("nigeria", "lagos", "abuja", "naija", "yoruba", "igbo", "hausa"), "Africa/Lagos", "Nigeria (WAT)"),
    (("ghana", "accra"), "Africa/Accra", "Ghana (GMT)"),
    (("kenya", "nairobi", "east africa"), "Africa/Nairobi", "Kenya (EAT)"),
    (("south africa", "johannesburg", "cape town"), "Africa/Johannesburg", "South Africa (SAST)"),
    (("uk", "britain", "british", "london", "england"), "Europe/London", "United Kingdom"),
    (("india", "mumbai", "delhi"), "Asia/Kolkata", "India (IST)"),
    (("canada", "toronto"), "America/Toronto", "Canada (Eastern)"),
    (("us ", "usa", "america", "united states", "new york"), "America/New_York", "United States (Eastern)"),
]
DEFAULT_ZONE = ("Africa/Lagos", "Nigeria (WAT), the default; say where the audience lives on the channel to change it")

# Viewing on YouTube peaks in the evening on weekdays and late morning at weekends; a video published two to three
# hours before the peak is indexed and recommended by then.
SLOTS = [(3, 17, "Thursday 5 pm: indexed before the weekday evening peak (7-9 pm)"),
         (4, 17, "Friday 5 pm: before the Friday evening peak, with the weekend ahead"),
         (5, 10, "Saturday 10 am: weekend viewing starts late morning"),
         (6, 10, "Sunday 10 am: the other strong weekend slot")]


def audience_zone(*texts: str) -> tuple[str, str]:
    text = " " + " ".join(t or "" for t in texts).lower() + " "
    for words, zone, label in ZONES:
        if any(word in text for word in words):
            return zone, label
    return DEFAULT_ZONE


def upload_times(zone: str, label: str, now: datetime | None = None, count: int = 4) -> dict[str, Any]:
    """The next few good slots in the audience's local time (as UTC instants the publish step can schedule)."""
    tz = ZoneInfo(zone)
    local = (now or datetime.now(timezone.utc)).astimezone(tz)
    out = []
    for days in range(0, 15):
        day = (local + timedelta(days=days)).date()
        for weekday, hour, why in SLOTS:
            if day.weekday() != weekday:
                continue
            at = datetime(day.year, day.month, day.day, hour, tzinfo=tz)
            if at - local >= timedelta(hours=2):
                out.append({"at": at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                            "local": at.strftime("%a %d %b, %-I %p").replace("AM", "am").replace("PM", "pm"), "why": why})
        if len(out) >= count:
            break
    return {"zone": zone, "audience": label, "slots": out[:count],
            "note": "General YouTube guidance for this audience's time zone, not measured on your channel."}


CAPS = re.compile(r"\b[A-Z]{5,}\b")


def _has_keyword(title: str, keywords: list[str], within: int | None = None) -> str | None:
    lowered = title.lower()
    for keyword in keywords:
        k = keyword.lower().strip()
        if not k:
            continue
        at = lowered.find(k)
        if at < 0:  # all the keyword's main words, in any order, also counts
            words = [w for w in re.findall(r"[a-z0-9']+", k) if len(w) > 3]
            if words and all(re.search(rf"\b{re.escape(w)}", lowered) for w in words):
                at = min(lowered.find(w) for w in words)
        if at >= 0 and (within is None or at < within):
            return keyword
    return None


def _partly(title: str, keywords: list[str]) -> str | None:
    """A keyword phrase at least half of whose main words are in the title."""
    lowered = title.lower()
    for keyword in keywords:
        words = [w for w in re.findall(r"[a-z0-9']+", keyword.lower()) if len(w) > 3]
        if words and sum(bool(re.search(rf"\b{re.escape(w)}", lowered)) for w in words) * 2 >= len(words):
            return keyword
    return None


def score_title(title: str, keywords: list[str], clarity: int | None = None, curiosity: int | None = None) -> dict[str, Any]:
    """0-100. Checks: length for search and mobile, a viewer's search words early, readability, a concrete promise;
    plus the model's ratings when it gave them (otherwise the checks alone, scaled to 100)."""
    title = title.strip()
    n, parts = len(title), []
    if 30 <= n <= 60:
        parts.append(("Length", 20, 20, f"{n} characters: shows in full on phones and in search"))
    elif 60 < n <= 70:
        parts.append(("Length", 12, 20, f"{n} characters: the end may be cut on phones (60 is safer)"))
    elif n < 30:
        parts.append(("Length", 10, 20, f"{n} characters: room to say more about what the viewer gets"))
    else:
        parts.append(("Length", 4, 20, f"{n} characters: YouTube cuts it well before the end"))
    early, anywhere = _has_keyword(title, keywords, 40), _has_keyword(title, keywords)
    if early:
        parts.append(("Search words", 20, 20, f"“{early}” near the start"))
    elif anywhere:
        parts.append(("Search words", 12, 20, f"“{anywhere}” is there, but late; move it nearer the start"))
    elif (partly := _partly(title, keywords)):
        parts.append(("Search words", 8, 20, f"partly matches “{partly}”; the full phrase would rank better"))
    else:
        parts.append(("Search words", 0 if keywords else 10, 20, "none of the research keywords is in it" if keywords else "no keywords researched yet"))
    shouting = len(CAPS.findall(title)) > 1 or title.count("!") > 1
    parts.append(("Readability", 5 if shouting else 10, 10, "too many capitals or exclamation marks" if shouting else "reads cleanly"))
    concrete = bool(re.search(r"\d", title)) or bool(re.search(r"\b(how|why|what|signs?|ways?|mistakes?|steps?)\b", title, re.I))
    parts.append(("Concrete promise", 10 if concrete else 5, 10, "says what the viewer will get" if concrete else "a number or a clear promise (how, why, signs…) usually helps"))
    rated = clarity is not None and curiosity is not None
    if rated:
        parts.append(("Clarity (model's rating)", round(max(0, min(10, clarity)) * 2), 20, f"{clarity}/10"))
        parts.append(("Curiosity (model's rating)", round(max(0, min(10, curiosity)) * 2), 20, f"{curiosity}/10"))
    got, out_of = sum(p[1] for p in parts), sum(p[2] for p in parts)
    return {"title": title, "score": round(100 * got / out_of), "rated_by_model": rated,
            "parts": [{"name": a, "points": b, "max": c, "note": d} for a, b, c, d in parts]}


def parse_research(value: dict[str, Any], current_title: str = "") -> dict[str, Any]:
    """The writing model's research answer, checked and trimmed, with every title scored."""
    def strings(key: str, limit: int, size: int = 200) -> list[str]:
        items = value.get(key) or []
        return [re.sub(r"\s+", " ", str(i)).strip()[:size] for i in items if str(i).strip()][:limit] if isinstance(items, list) else []

    def rating(item: dict[str, Any], key: str) -> int | None:
        try:
            return max(1, min(10, int(item.get(key))))
        except (TypeError, ValueError):
            return None

    keywords = value.get("keywords") or {}
    primary = [re.sub(r"\s+", " ", str(k)).strip()[:80] for k in (keywords.get("primary") or []) if str(k).strip()][:3] if isinstance(keywords, dict) else []
    secondary = [re.sub(r"\s+", " ", str(k)).strip()[:80] for k in (keywords.get("secondary") or []) if str(k).strip()][:6] if isinstance(keywords, dict) else []
    angles = [{"angle": str(a.get("angle", "")).strip()[:200], "why": str(a.get("why", "")).strip()[:400]}
              for a in (value.get("angles") or []) if isinstance(a, dict) and str(a.get("angle", "")).strip()][:4]
    seen, titles = set(), []
    for item in value.get("titles") or []:
        if not isinstance(item, dict):
            continue
        title = re.sub(r"\s+", " ", str(item.get("title", ""))).strip().strip('"')[:100]
        if len(title) < 10 or title.lower() in seen:
            continue
        seen.add(title.lower())
        scored = score_title(title, primary + secondary, rating(item, "clarity"), rating(item, "curiosity"))
        titles.append(scored | {"why": str(item.get("why", "")).strip()[:300]})
    if len(titles) < 3 or not primary:
        raise ProviderError("the research came back too thin (fewer than 3 titles or no keywords); try again")
    titles.sort(key=lambda t: t["score"], reverse=True)
    current = None
    if current_title.strip():
        c = value.get("current_title") if isinstance(value.get("current_title"), dict) else {}
        current = score_title(current_title, primary + secondary, rating(c, "clarity"), rating(c, "curiosity")) | {"why": str(c.get("why", "")).strip()[:300]}
    return {"angles": angles, "audience_questions": strings("audience_questions", 8), "must_cover": strings("must_cover", 8),
            "avoid": strings("avoid", 4), "keywords": {"primary": primary, "secondary": secondary}, "titles": titles[:8],
            "current_title": current,
            "note": "From the writing model, not from YouTube search data. Facts marked (check) need a source before they go in the script."}


def research_ask(project: dict[str, Any], channel: dict[str, Any]) -> str:
    lines = [f"Channel: {channel.get('name', '')} ({channel.get('niche', '') or 'no niche given'})",
             f"Channel audience: {channel.get('audience', '') or 'not given'}",
             f"Video topic: {project['topic']}", f"Length: {project['duration_minutes']} minutes"]
    if project.get("audience"):
        lines.append(f"This video's audience: {project['audience']}")
    if project.get("style"):
        lines.append(f"Style: {project['style']}")
    if project.get("title"):
        lines.append(f"The creator's current title idea: {project['title']}")
    return "\n".join(lines) + (
        "\n\nResearch this video before it is written. Answer as JSON with exactly these keys:\n"
        "- angles: 3 different angles for this topic, each {\"angle\", \"why\"} (why this audience would click it)\n"
        "- audience_questions: 6 questions this audience actually asks about the topic\n"
        "- must_cover: 6 points the video has to cover to be genuinely useful; never invent statistics: anything that needs a "
        "source ends with \"(check)\"\n"
        "- avoid: 3 things that make videos on this topic disappoint viewers\n"
        "- keywords: {\"primary\": 3 phrases viewers would type into YouTube search, \"secondary\": 5 more}\n"
        "- titles: 8 title options, each {\"title\" (under 65 characters, no clickbait that the video can't deliver), "
        "\"clarity\": 1-10, \"curiosity\": 1-10, \"why\"}; vary them (question, number, story, how-to)\n"
        + ("- current_title: rate the creator's current title idea as {\"clarity\", \"curiosity\", \"why\"}\n" if project.get("title") else ""))


def research_notes(research: dict[str, Any] | None) -> str:
    """What the script writer is told from the research (empty when there is none)."""
    if not research:
        return ""
    lines = []
    if research.get("audience_questions"):
        lines.append("Answer these viewer questions along the way: " + "; ".join(research["audience_questions"][:6]))
    if research.get("must_cover"):
        lines.append("Make sure to cover: " + "; ".join(p.replace("(check)", "").strip() for p in research["must_cover"][:6])
                     + ". State figures only if you are sure of them; otherwise speak generally.")
    if research.get("avoid"):
        lines.append("Avoid: " + "; ".join(research["avoid"][:3]))
    return "\n".join(lines)


def readiness(project: dict[str, Any], connected: bool) -> list[dict[str, Any]]:
    """The launch checklist: each item is something anyone can check on the finished package."""
    record, stages = project["record"], project["stages"]
    seo, thumb, video = record.get("seo") or {}, record.get("thumbnail") or {}, record.get("video") or {}
    keywords = (seo.get("primary_keywords") or []) + ((record.get("research") or {}).get("keywords") or {}).get("primary", [])
    title = project.get("title", "")
    caption = thumb["options"][thumb["chosen"] - 1]["caption"] if thumb.get("chosen") else ""
    first = re.split(r"(?<=[.!?])\s", (seo.get("description") or "").strip(), maxsplit=1)[0] if seo else ""
    chapters = seo.get("chapters") or []
    tag_chars = sum(len(t) + 1 for t in seo.get("tags") or [])
    items = [
        ("title_length", "Title fits on phones (60 characters or less)", 0 < len(title) <= 60, f"{len(title)} characters"),
        ("title_keyword", "A search keyword is in the title", bool(_has_keyword(title, keywords)), ", ".join(keywords[:3]) or "no keywords yet"),
        ("description_keyword", "The description's first sentence has a keyword", bool(first and _has_keyword(first, keywords)), first[:120] or "no description yet"),
        ("thumbnail", "A thumbnail is chosen", bool(thumb.get("chosen")), caption and f"“{caption}”" or "none chosen"),
        ("thumbnail_text", "Thumbnail text adds to the title (5 words or fewer, not the title)",
         bool(caption) and len(caption.split()) <= 5 and caption.lower() not in title.lower(), caption or "—"),
        ("chapters", "Chapters start at 0:00, at least 3", len(chapters) >= 3 and chapters[0].get("time") == "0:00", f"{len(chapters)} chapters"),
        ("captions", "A captions file is ready", bool(video.get("captions_key")), "made with the video" if video.get("captions_key") else "make the video first"),
        ("tags", "Tags fit YouTube's 500 characters", 0 < tag_chars <= 500, f"{tag_chars} characters"),
        ("final_review", "The final video is approved", stages["final_review"]["state"] == "approved", stages["final_review"]["state"].replace("_", " ")),
        ("youtube", "The channel's YouTube is connected", connected, "connected" if connected else "connect it in the publishing step"),
    ]
    return [{"key": k, "label": label, "ok": bool(ok), "note": note} for k, label, ok, note in items]


def baseline(videos: list[dict[str, Any]], now: datetime | None = None) -> dict[str, Any] | None:
    """What this channel's own recent uploads did: real numbers from YouTube, the honest guide to what's likely."""
    if not videos:
        return None
    now = now or datetime.now(timezone.utc)
    views = sorted(v["views"] for v in videos)
    per_day = []
    for v in videos:
        age = max((now - datetime.fromisoformat(v["published_at"].replace("Z", "+00:00"))).total_seconds() / 86400, 1)
        per_day.append(v["views"] / age)
    q = statistics.quantiles(views, n=4) if len(views) >= 4 else [views[0], statistics.median(views), views[-1]]
    best = max(videos, key=lambda v: v["views"])
    return {"videos": len(videos), "median_views": round(statistics.median(views)), "typical_range": [round(q[0]), round(q[2])],
            "median_views_per_day": round(statistics.median(per_day), 1), "best": {"title": best["title"], "views": best["views"]},
            "note": f"Lifetime views of your last {len(videos)} uploads, from YouTube. A new video usually lands in this range; it is a guide, not a promise."}
