#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import smtplib
from dataclasses import dataclass
from datetime import date, datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

BASE_URL = "https://www.nhraeventreg.com/ListEventStatus.asp"
STATE_FILE = Path("nhra_super_comp_quota_alert_state.json")


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


def is_future_or_today(d: date) -> bool:
    return d >= datetime.today().date()


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def smtp_config() -> dict:
    cfg = {
        "SMTP_HOST": os.environ.get("SMTP_HOST"),
        "SMTP_PORT": os.environ.get("SMTP_PORT", "587"),
        "SMTP_USERNAME": os.environ.get("SMTP_USERNAME"),
        "SMTP_PASSWORD": os.environ.get("SMTP_PASSWORD"),
        "EMAIL_FROM": os.environ.get("EMAIL_FROM"),
        "EMAIL_TO": os.environ.get("EMAIL_TO"),
    }
    missing = [k for k, v in cfg.items() if not v]
    if missing:
        raise RuntimeError(f"Missing environment variables: {', '.join(missing)}")
    return cfg


def send_text_via_smtp(subject: str, body: str) -> None:
    cfg = smtp_config()

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["EMAIL_FROM"]
    msg["To"] = cfg["EMAIL_TO"]
    msg.set_content(body)

    with smtplib.SMTP(cfg["SMTP_HOST"], int(cfg["SMTP_PORT"]), timeout=30) as smtp:
        smtp.starttls()
        smtp.login(cfg["SMTP_USERNAME"], cfg["SMTP_PASSWORD"])
        smtp.send_message(msg)


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


def extract_class_status_from_html(html: str, class_name: str) -> Optional[ClassStatus]:
    soup = BeautifulSoup(html, "lxml")
    target = class_name.strip().lower()

    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 5:
                continue

            texts = [" ".join(c.get_text(" ", strip=True).split()) for c in cells]
            category = texts[1].strip()
            if category.lower() != target:
                continue

            quota = parse_int_cell(texts[2])
            entries = parse_int_cell(texts[3])
            percent_full = texts[4].strip() or None

            if quota is None or entries is None:
                return None
            if quota < 0 or entries < 0 or quota > 500 or entries > 500:
                return None

            return ClassStatus(
                label=category,
                entries=entries,
                quota=quota,
                percent_full=percent_full,
            )

    return None


def check_once(class_name: str = "Super Comp") -> None:
    state = load_state()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1200)

        events = extract_events(page)
        future_events = [e for e in events if is_future_or_today(e.event_date)]

        log(f"Found {len(events)} event(s) on {BASE_URL}")
        log(f"Keeping {len(future_events)} future/today event(s); skipped {len(events) - len(future_events)} past event(s)")

        any_alert = False

        for event in future_events:
            try:
                page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(800)

                choose_event(page, event)
                status = extract_class_status_from_html(page.content(), class_name)

                if not status:
                    log(f"[skip] Could not parse {class_name} for {event.label}")
                    continue

                log(f"[ok] {event.label} -> {status.label}: entries={status.entries}, quota={status.quota}, full={status.percent_full or 'n/a'}")

                key = f"{event.label}|{class_name}"
                payload = {"entries": status.entries, "quota": status.quota}

                if status.entries < status.quota:
                    prev = state.get(key)
                    if prev != payload:
                        subject = f"{class_name} is Below Quota!"
                        body = (
                            f"{event.label}\n"
                            f"{class_name}: {status.entries}/{status.quota}\n"
                            f"% Full: {status.percent_full or 'n/a'}"
                        )
                        send_text_via_smtp(subject, body)
                        state[key] = payload
                        any_alert = True
                else:
                    state.pop(key, None)

            except Exception as e:
                log(f"[warn] Failed to parse {event.label}: {e}")

        save_state(state)
        browser.close()

        if not any_alert:
            log(f"No new below-quota {class_name} alerts for future events.")


if __name__ == "__main__":
    check_once()
