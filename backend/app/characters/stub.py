"""Rule-based character responder (spec §38 Phase 3: "static/stubbed responses").

Used when no LLM is configured and as the safe fallback when the validator
rejects every LLM attempt. Deliberately simple and deliberately in character:
Carlos is vague until asked precisely, Maya manages, Priya coaches.
"""

from __future__ import annotations

import re


def _has(text: str, *words: str) -> bool:
    t = text.lower()
    return any(w in t for w in words)


class StubResponder:
    def reply(self, character_id: str, text: str, history_len: int = 0) -> str:
        fn = getattr(self, f"_{character_id}", None)
        return fn(text, history_len) if fn else "Sorry, who is this?"

    def system(self, character_id: str, intent: str) -> str:
        return SYSTEM_LINES.get(character_id, {}).get(intent, "")

    # ------------------------------------------------------------------ maya
    def _maya(self, text: str, n: int) -> str:
        if _has(text, "carrier", "wan", "isp", "circuit", "provider"):
            return "My first guess is the carrier again, honestly. Last two Austin incidents were circuit problems. But don't take my word for it, confirm it."
        if _has(text, "what changed", "anything change", "maintenance", "tuesday", "change log", "change window"):
            return "Priya ran a maintenance window Tuesday night. I don't know the details; the change log will. Check it before you touch anything."
        if _has(text, "what command", "which command", "how do i", "how should i", "what should i run", "what should i check"):
            return "I'm not going to drive from here. Tell me what you've looked at and what it showed, and what you plan to do next."
        if _has(text, "escalat", "priya", "help from", "senior"):
            return "If you need Priya, loop her in. That's fine at SEV-2. Just keep owning the ticket and keep me posted."
        if _has(text, "resolved", "fixed", "restored", "back up", "working again"):
            return "Good. Confirm with Carlos that users are actually working, update the ticket, and I'll want a postmortem before end of day."
        if _has(text, "impact", "blast radius", "how many", "who is affected"):
            return "As far as I know it's the whole Austin office and only Austin. Help desk has the numbers. Is anything else affected?"
        if _has(text, "sev", "severity", "cadence", "how often"):
            return "It's a SEV-2. Update me every 30 minutes, more if something changes. The incident guide on the wiki has the details."
        if _has(text, "root cause", "cause", "why", "what's wrong", "what is wrong"):
            return "I don't know the cause. That's what I need you to find out. What's the current impact and what's your next step?"
        if _has(text, "status", "update", "progress"):
            return "Thanks. Who's impacted right now, what have you ruled out, and what's your next step?"
        return "Noted. Keep me posted. What's the impact right now and what are you doing next?"

    # ---------------------------------------------------------------- carlos
    def _carlos(self, text: str, n: int) -> str:
        if _has(text, "external", "google", "outside", "public", "youtube", "internet site", "reach the web", "browse"):
            return "Oh good question, let me check... ok I just had Dana in Austin try google.com and a couple other sites, they load fine. It's the internal stuff, like the ticketing portal and the file share, those just spin forever."
        if _has(text, "what did they try", "what exactly", "which app", "which system", "what are they trying", "error"):
            return "Mostly the ticketing portal and the shared drive. They said it just spins and then times out, no error page or anything. One person said email still works but she's on the phone app so who knows."
        if _has(text, "how many", "everyone", "all users", "everybody", "verified", "sure"):
            return "I've had maybe a dozen calls and everyone who called has the same thing. I've been assuming it's everyone but honestly I haven't gone desk to desk. Want me to?"
        if _has(text, "when", "what time", "start", "since"):
            return "First call came in around 8:50 this morning, then a bunch more after 9. So it started this morning I think? Nobody called yesterday."
        if _has(text, "ip", "gateway", "ping", "traceroute", "dns", "ospf", "route", "vlan", "config"):
            return "Uh, I don't really know that stuff. I can ask a user to run something if you tell me exactly what to type?"
        if _has(text, "thanks", "thank you", "resolved", "fixed", "working"):
            return "Awesome, I'll let the Austin folks know. Let me know if you want me to confirm with a couple of them."
        return "Yeah so the internet's down in Austin, I'm getting a lot of calls. Nobody can get to anything. Let me know what you need from me!"

    # ----------------------------------------------------------------- priya
    def _priya(self, text: str, n: int) -> str:
        if _has(text, "what command", "which command", "what should i run", "what do i run", "what should i check", "how do i"):
            return "Before we jump to commands, what do you know about where the traffic stops?"
        if _has(text, "did you change", "your change", "what did you do", "tuesday", "maintenance", "chg-88", "change window", "anything change"):
            return "I did Tuesday's window, CHG-8816 and CHG-8817. Routine cleanup, both verified after. Nothing that would affect Austin users. It's all in the change log if you want the details."
        if _has(text, "neighbor", "adjacency", "full"):
            return "Full tells you the two routers are talking to each other. What does it tell you about what they're actually exchanging?"
        if _has(text, "routing table", "show ip route", "routes", "prefix", "route"):
            return "Which side did you look at? Connectivity is bidirectional. A path that only works one way is a broken path."
        if _has(text, "ping", "traceroute", "unreachable", "timeout", "times out"):
            return "A failed ping tells you the path is broken somewhere. It doesn't tell you which direction. What would distinguish those?"
        if _has(text, "carrier", "wan", "circuit", "link down", "interface"):
            return "Is the transit link actually down? You have monitoring and the routers themselves. Don't guess when you can look."
        if _has(text, "acl", "ssh", "access-list", "management"):
            return "Think about what an ACL on the management plane would and wouldn't affect. Does it match the symptom you're seeing?"
        if _has(text, "found it", "fixed", "root cause", "resolved"):
            return "Ok. Before you call it done, verify from the user side and make sure you didn't move the problem somewhere else."
        return "What have you ruled out so far, and what evidence rules it out?"


SYSTEM_LINES = {
    "maya": {
        "request_status_update": "Haven't heard from you in a bit. Where are we on INC-1042?",
        "cadence_missed": "We're past the 30-minute SEV-2 update window. I need a status on Austin, even if it's 'still investigating'.",
        "manager_notices_new_outage": "We just lost more of the Austin site on the board. Did you make a change?",
        "request_postmortem": "Monitoring shows Austin back. Nice work. Please update the ticket with the resolution and get me a postmortem: impact, timeline, root cause, fix, and what stops it happening again.",
    },
    "carlos": {
        "users_confirm_restored": "Just heard from two people in Austin, portal's loading again. Thanks!",
    },
}


def mentioned(text: str) -> list[str]:
    return [m.lower() for m in re.findall(r"@(maya|carlos|priya)\b", text, re.IGNORECASE)]
