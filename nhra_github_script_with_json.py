#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

BASE_URL = "https://www.nhraeventreg.com/ListEventStatus.asp"
JSON_OUTPUT_FILE = Path("docs/quota_data.json")


@dataclass
class Event:
    label: str
    value: str
    event_date: date


@dataclass
class ClassStatus:
    label: str
    entries: int
    quota: int
    percent_full: Optional[str] = None


def log(msg: str) -> None:
    print(msg, flush=True)


def parse_event_date(label: str) -> Optional[date]:
    m = re.match(r"\s*(\d{1,2}/\d{1,2}/\d{2,4})\s*-\s*", label)
    if not m:
        return None
    raw = m.group(1).strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            pass
    return None


def parse_event_label_parts(label: str) -> tuple[str, str, str]:
    parts = [p.strip() for p in label.split(" - ", 2)]
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    if len(parts) == 2:
        return parts[0], "", parts[1]
    return label, "", label


def is_future_or_today(d: date) -> bool:
    return d >= datetime.today().date()


def extract_events(page) -> list[Event]:
    soup = BeautifulSoup(page.content(), "lxml")
    for sel in soup.find_all("select"):
        events = []
        for opt in sel.find_all("option"):
            label = " ".join(opt.get_text(" ", strip=True).split())
            value = (opt.get("value") or "").strip()
            d = parse_event_date(label)
            if label and value and d:
                events.append(Event(label=label, value=value, event_date=d))
        if events:
            return events
    return []


def choose_event(page, event: Event) -> None:
    soup = BeautifulSoup(page.content(), "lxml")
    for sel in soup.find_all("select"):
        values = {(opt.get("value") or "").strip() for opt in sel.find_all("option")}
        if event.value not in values:
            continue

        selector = None
        if sel.get("id"):
            selector = f"select#{sel['id']}"
        elif sel.get("name"):
            selector = f"select[name='{sel['name']}']"
        if not selector:
            continue

        page.select_option(selector, value=event.value)
        page.wait_for_timeout(500)

        for sub in [
            "input[type='submit'][name='Submit']",
            "input[type='submit'][value='Submit']",
            "input[type='submit']",
        ]:
            locator = page.locator(sub)
            if locator.count() > 0:
                locator.first.click(timeout=2500)
                page.wait_for_load_state("domcontentloaded", timeout=10000)
                page.wait_for_timeout(1000)
                return

        page.wait_for_timeout(1000)
        return

    raise RuntimeError(f"Could not activate event in page UI: {event.label}")


def parse_int_cell(text: str) -> Optional[int]:
    text = text.strip().replace(",", "")
    if text in {"", "-", "N/A"}:
        return None
    m = re.search(r"-?\d+", text)
    return int(m.group()) if m else None


def extract_all_class_statuses_from_html(html: str) -> list[ClassStatus]:
    soup = BeautifulSoup(html, "lxml")
    results: list[ClassStatus] = []

    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 5:
                continue

            texts = [" ".join(c.get_text(" ", strip=True).split()) for c in cells]

            category = texts[1].strip()
            quota = parse_int_cell(texts[2])
            entries = parse_int_cell(texts[3])
            percent_full = texts[4].strip() or None

            if not category:
                continue

            if category.lower() in {"category", "event total"}:
                continue

            if quota is None or entries is None:
                continue

            results.append(
                ClassStatus(
                    label=category,
                    entries=entries,
                    quota=quota,
                    percent_full=percent_full,
                )
            )

    seen = set()
    deduped = []
    for item in results:
        key = item.label.lower()
        if key not in seen:
            seen.add(key)
            deduped.append(item)

    return deduped


def write_json_feed(events_payload: list[dict]) -> None:
    payload = {
        "last_checked": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "generated_epoch": int(datetime.now(timezone.utc).timestamp()),
        "events": events_payload,
    }

    JSON_OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    JSON_OUTPUT_FILE.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    log(f"Wrote JSON feed to {JSON_OUTPUT_FILE}")


def run() -> None:
    json_events = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(BASE_URL)
        page.wait_for_timeout(1000)

        events = extract_events(page)
        future_events = [e for e in events if is_future_or_today(e.event_date)]

        for event in future_events:
            date_text, location_text, name_text = parse_event_label_parts(event.label)

            event_payload = {
                "id": event.value,
                "name": name_text,
                "date": date_text,
                "location": location_text,
                "classes": [],
                "has_data": False,
            }

            try:
                page.goto(BASE_URL)
                page.wait_for_timeout(500)

                choose_event(page, event)
                html = page.content()

                all_statuses = extract_all_class_statuses_from_html(html)

                log(f"[debug] {event.label} classes: {[c.label for c in all_statuses]}")

                if all_statuses:
                    event_payload["classes"] = [
                        {
                            "name": s.label,
                            "quota": s.quota,
                            "entries": s.entries,
                            "percent_full": s.percent_full,
                        }
                        for s in all_statuses
                    ]
                    event_payload["has_data"] = True
                else:
                    log(f"[info] No class data yet for {event.label}")

            except Exception as e:
                log(f"[warn] Failed for {event.label}: {e}")

            json_events.append(event_payload)

        browser.close()

    write_json_feed(json_events)


if __name__ == "__main__":
    run()
