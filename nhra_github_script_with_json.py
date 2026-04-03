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


def parse_event_label_parts(label: str):
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

        page.locator("input[type='submit']").first.click()
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(1000)
        return

    raise RuntimeError(f"Could not activate event: {event.label}")


def parse_int_cell(text: str) -> Optional[int]:
    text = text.strip().replace(",", "")
    if text in {"", "-", "N/A"}:
        return None
    m = re.search(r"-?\d+", text)
    return int(m.group()) if m else None


def extract_all_class_statuses(html: str) -> list[ClassStatus]:
    soup = BeautifulSoup(html, "lxml")
    results = []

    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 5:
                continue

            texts = [" ".join(c.get_text(" ", strip=True).split()) for c in cells]
            category = texts[1].strip()

            if not category or category.lower() in {"category", "event total"}:
                continue

            quota = parse_int_cell(texts[2])
            entries = parse_int_cell(texts[3])
            percent_full = texts[4].strip() or None

            if quota is None or entries is None:
                continue

            results.append(
                ClassStatus(category, entries, quota, percent_full)
            )

    return results


def write_json(events_payload):
    payload = {
        "last_checked": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "events": events_payload,
    }

    JSON_OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    JSON_OUTPUT_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    log(f"Updated JSON: {JSON_OUTPUT_FILE}")


def run():
    json_events = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.goto(BASE_URL)
        page.wait_for_timeout(1200)

        events = extract_events(page)
        future_events = [e for e in events if is_future_or_today(e.event_date)]

        log(f"{len(future_events)} future events found")

        for event in future_events:
            try:
                page.goto(BASE_URL)
                page.wait_for_timeout(800)

                choose_event(page, event)
                html = page.content()

                classes = extract_all_class_statuses(html)
                if not classes:
                    log(f"Skipping {event.label}")
                    continue

                date_text, location, name = parse_event_label_parts(event.label)

                json_events.append({
                    "id": event.value,
                    "name": name,
                    "date": date_text,
                    "location": location,
                    "classes": [
                        {
                            "name": c.label,
                            "quota": c.quota,
                            "entries": c.entries,
                            "percent_full": c.percent_full
                        }
                        for c in classes
                    ]
                })

                log(f"Parsed {event.label}")

            except Exception as e:
                log(f"[ERROR] {event.label}: {e}")

        browser.close()

    write_json(json_events)


if __name__ == "__main__":
    run()
