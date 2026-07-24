"""Support Desk — THE WIRES (outbound-signal gates + the webhook uplink).

The Queue-Config "wires" panel writes one SdSetting row
(key='queue_notifications', value={'assign_email': bool, 'breach_warning': bool,
'webhook_url': str}) that historically NOTHING read — the toggles were dead
switches and the webhook URL never fired. This module makes the wires real:

  • ``allows(db, event)``   — per-event delivery gate consulted by all three
    notify wrappers (tickets.dispatch_safe / tickets_self._dispatch_safe /
    _common._notify_safe). Cutting "assignment pings" or "breach warnings" on
    the panel now silences those wires desk-wide.
  • ``post_webhook(...)``   — fire-and-forget POST of a Slack-compatible payload
    ({"text": ...}) for lifecycle events whenever a webhook URL is wired. Runs
    on a daemon thread with a short timeout, never raises, and dedupes
    (event, ticket) bursts for 30s so a multi-recipient fan-out posts ONCE.
  • ``send_test(url)``      — synchronous probe behind the panel's TEST
    TRANSMISSION button (superuser route in ops.py).

Config reads go through a tiny module cache (15s TTL): the wrappers sit on hot
paths and the StaticPool single connection makes every extra query count.
``upsert_setting`` calls ``invalidate_cache()`` so panel saves apply instantly.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

from app.models.support_desk.ops import SdSetting
from app.models.support_desk.constants import (
    EVT_TICKET_CREATED, EVT_TICKET_ASSIGNED, EVT_TICKET_ESCALATED,
    EVT_TICKET_RESOLVED, EVT_TICKET_SLA_BREACH, EVT_PIR_PUBLISHED,
    EVT_INCIDENT_ROLES_ASSIGNED, EVT_INCIDENT_DECISION, EVT_INCIDENT_IMPACT,
    EVT_INCIDENT_CADENCE, EVT_INCIDENT_DECLARED, EVT_INCIDENT_MI_PROPOSED,
    EVT_INCIDENT_STATUS_UPDATE, EVT_PIR_SUBMITTED, EVT_PIR_APPROVED,
    EVT_PIR_REJECTED, EVT_INCIDENT_SEV_CHANGED,
    EVT_RCA_FILED, EVT_RCA_VALIDATED,
)

# Which panel switch gates which event's per-user delivery.
_GATED = {
    EVT_TICKET_ASSIGNED: "assign_email",
    EVT_TICKET_SLA_BREACH: "breach_warning",
}
# Lifecycle events mirrored to the external uplink when a URL is wired. Live
# incident-command events ride the same wire — an outage bridge (Slack webhook)
# hears declares, roster changes, decisions, impact stamps, cadence changes and
# stakeholder broadcasts, plus the PIR review trail. post_webhook's 30s
# (event, ticket) dedupe collapses multi-recipient fan-outs to one post.
WEBHOOK_EVENTS = {
    EVT_TICKET_CREATED, EVT_TICKET_ASSIGNED, EVT_TICKET_ESCALATED,
    EVT_TICKET_RESOLVED, EVT_TICKET_SLA_BREACH, EVT_PIR_PUBLISHED,
    EVT_INCIDENT_ROLES_ASSIGNED, EVT_INCIDENT_DECISION, EVT_INCIDENT_IMPACT,
    EVT_INCIDENT_CADENCE, EVT_INCIDENT_DECLARED, EVT_INCIDENT_MI_PROPOSED,
    EVT_INCIDENT_STATUS_UPDATE, EVT_PIR_SUBMITTED, EVT_PIR_APPROVED,
    EVT_PIR_REJECTED,
    # Severity reclassification is command-relevant — the bridge hears it. The sibling
    # EVT_INCIDENT_TASK_ASSIGNED is a personal ping and deliberately NOT mirrored here.
    EVT_INCIDENT_SEV_CHANGED,
    # RCA governance beats ride the uplink; EVT_RCA_RETURNED is a personal
    # "your filing came back" ping and deliberately NOT mirrored (same precedent).
    EVT_RCA_FILED, EVT_RCA_VALIDATED,
}

_DEFAULTS = {"assign_email": True, "breach_warning": True, "webhook_url": ""}
_CACHE_TTL = 15.0
_DEDUPE_SECS = 30.0
_cache: dict = {"at": 0.0, "cfg": None}
_recent: dict = {}   # (event, ticket_id) -> monotonic ts — webhook burst dedupe


def config(db) -> dict:
    """The wires config with defaults filled — cached for _CACHE_TTL seconds."""
    now = time.monotonic()
    if _cache["cfg"] is not None and now - _cache["at"] < _CACHE_TTL:
        return _cache["cfg"]
    cfg = dict(_DEFAULTS)
    try:
        row = db.query(SdSetting).filter(SdSetting.key == "queue_notifications").first()
        if row and isinstance(row.value, dict):
            for k in cfg:
                if k in row.value and row.value[k] is not None:
                    cfg[k] = row.value[k]
    except Exception:
        pass
    _cache["at"] = now
    _cache["cfg"] = cfg
    return cfg


def invalidate_cache() -> None:
    _cache["at"] = 0.0
    _cache["cfg"] = None


def allows(db, event: str) -> bool:
    """False only when the panel explicitly cut this event's wire. Fail-open —
    a broken settings read must never silence the desk."""
    key = _GATED.get(event)
    if not key:
        return True
    try:
        return bool(config(db).get(key, True))
    except Exception:
        return True


def _post(url: str, text: str) -> None:
    try:
        req = urllib.request.Request(
            url, data=json.dumps({"text": text}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=4).read()
    except Exception:
        pass   # the uplink is best-effort by design


def post_webhook(db, event: str, ticket, title: str) -> None:
    """Mirror ``event`` to the wired webhook (if any). Never raises. Posts at
    most once per (event, ticket) per 30s — dispatch fan-outs to several
    recipients collapse to a single transmission."""
    try:
        if event not in WEBHOOK_EVENTS:
            return
        url = (config(db).get("webhook_url") or "").strip()
        if not url.startswith(("http://", "https://")):
            return
        key = (event, str(getattr(ticket, "id", "")))
        now = time.monotonic()
        for stale in [k for k, ts in _recent.items() if now - ts > _DEDUPE_SECS]:
            _recent.pop(stale, None)
        if key in _recent:
            return
        _recent[key] = now
        label = event.replace("SUPPORT_", "").replace("_", " ")
        num = getattr(ticket, "ticket_number", "") or ""
        text = f"[{label}] {title}" + (f" · {num}" if num and num not in (title or "") else "")
        threading.Thread(target=_post, args=(url, text), daemon=True).start()
    except Exception:
        pass


def send_test(url: str) -> tuple[bool, str]:
    """Synchronous test transmission for the panel's TEST button."""
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return False, "URL must start with http:// or https://"
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps({"text": "\U0001F4E1 FourConnect Support Desk — test transmission from the Uplink Array"}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=6) as r:
            return True, f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, str(e)[:180] or "unreachable"
