"""The active shopping list on a CalDAV task list (Radicale) instead of a Mealie shopping list.

The owner's Shopping list lives on Radicale (nathan/shopping) so the assistant app, Tasks.org
and this planner all see one list. Each item is a VTODO; this module hands the rest of the
planner the same item dicts Mealie's shopping-list API returns, so shopping_sync and the web
routes work unchanged:

    id        "dav-" + the file name's stem (Mealie ids are bare hex, so the two never collide)
    note      SUMMARY, the text people see everywhere, quantity included ("2 lbs chicken")
    quantity  0, because the quantity is already in the note
    checked   STATUS:COMPLETED
    labelId   the Mealie label whose name is in CATEGORIES (the aisle)
    extras    {"is_synced": True} for items this planner made (UID ends @mealie-planner);
              anything typed in the app, Tasks.org or by hand here is manual and is never
              deleted by a sync or a wipe.

Staples stay a Mealie list: they are a catalog to pick from, not something you shop off.

Env: SHOPPING_CALDAV_URL (the collection, e.g. https://dav.cosmoslab.dev/nathan/shopping/),
CALDAV_USER, CALDAV_PASSWORD.
"""
from __future__ import annotations

import base64
import datetime as dt
import os
import re
import urllib.error
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urljoin

PREFIX = "dav-"
SYNCED = "@mealie-planner"
NS = {"d": "DAV:", "c": "urn:ietf:params:xml:ns:caldav"}
_UNITS = re.compile(r"^(lbs?|oz|cups?|cans?|cloves?|tsps?|tbsps?|g|kg|ml|l|packages?|bags?|pieces?|slices?|"
                    r"bunch(es)?|heads?|jars?|bottles?|boxes?|dozen|pints?|quarts?|gallons?)\b", re.I)


def is_dav_id(item_id: str) -> bool:
    return str(item_id).startswith(PREFIX)


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _unesc(s: str) -> str:
    return re.sub(r"\\([\\;,nN])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), s)


def first_category(raw: str) -> str:
    """The first of a CATEGORIES list. Only an unescaped comma separates, so an aisle named
    "Baking, Spices" survives."""
    return _unesc(re.split(r"(?<!\\),", raw)[0]).strip()


def _fold(line: str) -> str:
    out, b = [], line.encode()
    while len(b) > 75:
        cut = 75
        while cut and (b[cut] & 0xC0) == 0x80:   # never split a UTF-8 character
            cut -= 1
        out.append(b[:cut].decode())
        b = b" " + b[cut:]
    out.append(b.decode())
    return "\r\n".join(out)


def _qty(q) -> str:
    try:
        q = float(q or 0)
    except (TypeError, ValueError):
        return ""
    return "" if q <= 0 else (str(int(q)) if q == int(q) else f"{q:g}")


def summary(note: str, quantity) -> str:
    """What the list shows: the quantity in front unless the note already starts with a number,
    and a bare 1 only when a unit follows ("1 lb beef", but "bananas" not "1 bananas")."""
    note = re.sub(r"\s+", " ", note or "").strip()
    q = _qty(quantity)
    if not q or re.match(r"^[\d½¼¾⅓⅔]", note) or (q == "1" and not _UNITS.match(note)):
        return note
    return f"{q} {note}"


class RadicaleList:
    def __init__(self, url: str, user: str, password: str, labels: Callable[[], List[Dict[str, Any]]]):
        self.url = url.rstrip("/") + "/"
        self._auth = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
        self._labels = labels

    @classmethod
    def from_env(cls, labels) -> Optional["RadicaleList"]:
        url = os.getenv("SHOPPING_CALDAV_URL", "")
        return cls(url, os.getenv("CALDAV_USER", ""), os.getenv("CALDAV_PASSWORD", ""), labels) if url else None

    # ------------------------------------------------------------------ HTTP

    def _http(self, method: str, url: str, body: Optional[str] = None, headers: Optional[dict] = None):
        h = {"Authorization": self._auth, **(headers or {})}
        if body is not None and "Content-Type" not in h:
            h["Content-Type"] = "text/calendar; charset=utf-8"
        req = urllib.request.Request(url, body.encode() if body is not None else None, h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.read().decode(errors="replace")
        except urllib.error.HTTPError as e:
            if e.code in (404, 412):
                return e.code, ""
            raise RuntimeError(f"Shopping list (CalDAV) {method}: HTTP {e.code}") from None

    def _href(self, item_id: str) -> str:
        stem = str(item_id)[len(PREFIX):]
        if not re.fullmatch(r"[A-Za-z0-9._@%+-]{1,200}", stem):
            raise ValueError(f"bad shopping item id {item_id!r}")
        return self.url + stem + ".ics"

    def _label_maps(self):
        try:
            labels = self._labels() or []
        except Exception:
            labels = []
        return ({lab["id"]: lab["name"] for lab in labels if lab.get("id")},
                {lab["name"].lower(): lab["id"] for lab in labels if lab.get("name")})

    # ------------------------------------------------------------------ read

    def _todos(self):
        body = ('<?xml version="1.0"?><c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
                '<d:prop><d:getetag/><c:calendar-data/></d:prop><c:filter><c:comp-filter name="VCALENDAR">'
                '<c:comp-filter name="VTODO"/></c:comp-filter></c:filter></c:calendar-query>')
        status, text = self._http("REPORT", self.url, body, {"Depth": "1", "Content-Type": "application/xml"})
        if status != 207:
            raise RuntimeError(f"Shopping list (CalDAV) REPORT: HTTP {status}")
        for resp in ET.fromstring(text).findall("d:response", NS):
            href = urljoin(self.url, resp.findtext("d:href", "", NS))
            ics = resp.findtext(".//c:calendar-data", "", NS)
            if ics and href.endswith(".ics"):
                yield href, ics

    @staticmethod
    def _props(ics: str) -> Dict[str, str]:
        lines = re.sub(r"\r?\n[ \t]", "", ics).splitlines()
        props, inside, nested = {}, False, 0
        for line in lines:
            up = line.upper()
            if up == "BEGIN:VTODO":
                inside = True
            elif not inside:
                continue
            elif up == "END:VTODO":
                break
            elif up.startswith("BEGIN:"):   # a VALARM inside: its properties are not the item's
                nested += 1
            elif up.startswith("END:"):
                nested -= 1
            elif not nested and ":" in line:
                name, value = line.split(":", 1)
                props.setdefault(name.split(";")[0].upper(), value)
        return props

    def items(self) -> List[Dict[str, Any]]:
        by_id, by_name = self._label_maps()
        out = []
        for href, ics in self._todos():
            p = self._props(ics)
            cat = first_category(p.get("CATEGORIES", ""))
            label_id = by_name.get(cat.lower())
            status = p.get("STATUS", "").upper()
            out.append({
                "id": PREFIX + href.rsplit("/", 1)[1][:-4],
                "note": _unesc(p.get("SUMMARY", "")),
                "quantity": 0,
                "checked": status == "COMPLETED" or "COMPLETED" in p,
                "labelId": label_id,
                "label": {"name": cat} if cat else None,
                "position": 0,
                "extras": {"is_synced": True} if p.get("UID", "").endswith(SYNCED) else {},
            })
        return out

    # ------------------------------------------------------------------ write

    def _ics(self, uid: str, item: Dict[str, Any], label_names: Dict[str, str], created: str) -> str:
        now = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        cat = label_names.get(item.get("labelId") or "", "")
        lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//cosmoslab mealie-planner//EN", "BEGIN:VTODO",
                 f"UID:{uid}", f"DTSTAMP:{now}", f"CREATED:{created or now}", f"LAST-MODIFIED:{now}",
                 f"SUMMARY:{_esc(summary(item.get('note', ''), item.get('quantity')))}"]
        lines += [f"CATEGORIES:{_esc(cat)}"] if cat else []
        lines += ["STATUS:COMPLETED", f"COMPLETED:{now}", "PERCENT-COMPLETE:100"] if item.get("checked") \
            else ["STATUS:NEEDS-ACTION"]
        lines += ["END:VTODO", "END:VCALENDAR"]
        return "\r\n".join(_fold(x) for x in lines) + "\r\n"

    def add(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        names, _ = self._label_maps()
        made = []
        for item in items:
            stem = uuid.uuid4().hex
            synced = (item.get("extras") or {}).get("is_synced")
            uid = stem + (SYNCED if synced else "@mealie-planner-manual")
            status, _ = self._http("PUT", self.url + stem + ".ics", self._ics(uid, item, names, ""),
                                   {"If-None-Match": "*"})
            if status not in (200, 201, 204):
                raise RuntimeError(f"Shopping list (CalDAV) PUT: HTTP {status}")
            made.append({**item, "id": PREFIX + stem})
        return made

    def update(self, items: List[Dict[str, Any]]) -> None:
        """Rewrite each item from the dict, keeping its UID (so who made it never changes)."""
        names, _ = self._label_maps()
        for item in items:
            href = self._href(item["id"])
            status, old = self._http("GET", href)
            if status == 404:
                continue   # ticked off and cleared elsewhere meanwhile
            p = self._props(old)
            uid = p.get("UID") or (href.rsplit("/", 1)[1][:-4] + "@mealie-planner-manual")
            if not item.get("labelId") and p.get("CATEGORIES"):   # keep an aisle set elsewhere
                _, by_name = self._label_maps()
                item = {**item, "labelId": by_name.get(first_category(p["CATEGORIES"]).lower())}
            self._http("PUT", href, self._ics(uid, item, names, p.get("CREATED", "")))

    def delete(self, ids: List[str]) -> None:
        for item_id in ids:
            self._http("DELETE", self._href(item_id))

    def clear_synced(self) -> int:
        """Remove what this planner put on the list; items people added stay."""
        ids = [i["id"] for i in self.items() if i["extras"].get("is_synced")]
        self.delete(ids)
        return len(ids)
