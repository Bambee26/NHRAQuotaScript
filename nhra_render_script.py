#!/usr/bin/env python3
# NHRA Super Comp quota watcher (Render-ready, SMTP included)

import json, os, re, time
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from email.message import EmailMessage
import smtplib

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

BASE_URL = "https://www.nhraeventreg.com/ListEventStatus.asp"
STATE_FILE = Path("state.json")

# ===== SMTP CONFIG =====
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USERNAME = "bambeegarfield@gmail.com"
SMTP_PASSWORD = "qgso nzek pnyw cxhb"
EMAIL_FROM = "bambeegarfield@gmail.com"
EMAIL_TO = "2062900940@tmomail.net"


@dataclass
class Event:
    label: str
    value: str
    date: datetime


def send_text(msg):
    m = EmailMessage()
    m["Subject"] = "NHRA Alert"
    m["From"] = EMAIL_FROM
    m["To"] = EMAIL_TO
    m.set_content(msg)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
        s.starttls()
        s.login(SMTP_USERNAME, SMTP_PASSWORD)
        s.send_message(m)


def parse_date(label):
    m = re.match(r"(\d+/\d+/\d+)", label)
    if not m:
        return None
    return datetime.strptime(m.group(1), "%m/%d/%Y")


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(s):
    STATE_FILE.write_text(json.dumps(s))


def extract_events(page):
    soup = BeautifulSoup(page.content(), "lxml")
    events = []
    for opt in soup.find_all("option"):
        label = opt.text.strip()
        val = opt.get("value")
        d = parse_date(label)
        if d:
            events.append(Event(label, val, d))
    return events


def get_status(html):
    soup = BeautifulSoup(html, "lxml")
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) >= 5:
            name = cells[1].text.strip()
            if name.lower() == "super comp":
                quota = int(cells[2].text.strip())
                entries = int(cells[3].text.strip())
                return entries, quota
    return None


def run():
    state = load_state()

    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page()
        page.goto(BASE_URL)
        page.wait_for_timeout(1000)

        events = extract_events(page)

        for e in events:
            if e.date.date() < datetime.today().date():
                continue

            page.select_option("select", value=e.value)
            page.locator("input[type='submit']").first.click()
            page.wait_for_timeout(1000)

            result = get_status(page.content())
            if not result:
                continue

            entries, quota = result
            key = e.label

            print(e.label, entries, quota)

            if entries < quota:
                if state.get(key) != result:
                    send_text(f"{e.label}\nSuper Comp {entries}/{quota}")
                    state[key] = result
            else:
                state.pop(key, None)

        save_state(state)
        b.close()


if __name__ == "__main__":
    run()
