"""
Parse Garmin export ZIP and produce intervals.icu wellness records.

Extracts:
  - DI_CONNECT/DI-Connect-Wellness/*_sleepData.json
      → sleepSecs, sleepScore, spO2
  - DI_CONNECT/DI-Connect-Aggregator/UDSFile_*.json
      → restingHR, steps, body battery, stress
"""

from __future__ import annotations

import json
import zipfile
from typing import Iterator


def parse_sleep(zf: zipfile.ZipFile) -> dict[str, dict]:
    records: dict[str, dict] = {}
    for name in zf.namelist():
        if "sleepData" not in name or not name.endswith(".json"):
            continue
        try:
            entries = json.loads(zf.read(name))
        except Exception:
            continue
        for e in entries:
            d = e.get("calendarDate")
            if not d:
                continue
            total_sleep = (
                e.get("deepSleepSeconds", 0)
                + e.get("lightSleepSeconds", 0)
                + e.get("remSleepSeconds", 0)
            )
            rec: dict = {}
            if total_sleep:
                rec["sleepSecs"] = total_sleep
            scores = e.get("sleepScores", {})
            if scores.get("overallScore"):
                rec["sleepScore"] = scores["overallScore"]
            spo2 = e.get("spo2SleepSummary", {})
            if spo2.get("averageSPO2"):
                rec["spO2"] = round(spo2["averageSPO2"], 1)
            if rec:
                records[d] = rec
    return records


def parse_uds(zf: zipfile.ZipFile) -> dict[str, dict]:
    records: dict[str, dict] = {}
    for name in zf.namelist():
        if "UDSFile" not in name or not name.endswith(".json"):
            continue
        try:
            entries = json.loads(zf.read(name))
        except Exception:
            continue
        for e in entries:
            d = e.get("calendarDate")
            if not d:
                continue
            rec: dict = {}
            if e.get("restingHeartRate"):
                rec["restingHR"] = e["restingHeartRate"]
            if e.get("totalSteps"):
                rec["steps"] = e["totalSteps"]
            # Body battery high/low as comment
            bb = e.get("bodyBattery", {})
            stats = {s["bodyBatteryStatType"]: s["statsValue"]
                     for s in bb.get("bodyBatteryStatList", [])}
            if stats.get("HIGHEST") is not None and stats.get("LOWEST") is not None:
                rec["_bbHigh"] = stats["HIGHEST"]
                rec["_bbLow"] = stats["LOWEST"]
            # Awake stress level
            for agg in e.get("allDayStress", {}).get("aggregatorList", []):
                if agg.get("type") == "AWAKE" and agg.get("averageStressLevel") is not None:
                    rec["_stress"] = agg["averageStressLevel"]
            if rec:
                records[d] = rec
    return records


def build_wellness_records(zf: zipfile.ZipFile) -> list[dict]:
    """Return merged list of wellness dicts ready to PUT to intervals.icu."""
    sleep = parse_sleep(zf)
    uds = parse_uds(zf)
    all_dates = sorted(set(sleep) | set(uds))

    results = []
    for d in all_dates:
        s = sleep.get(d, {})
        u = uds.get(d, {})
        rec: dict = {"id": d}

        if s.get("sleepSecs"):
            rec["sleepSecs"] = s["sleepSecs"]
        if s.get("sleepScore"):
            rec["sleepScore"] = s["sleepScore"]
        if s.get("spO2"):
            rec["spO2"] = s["spO2"]
        if u.get("restingHR"):
            rec["restingHR"] = u["restingHR"]
        if u.get("steps"):
            rec["steps"] = u["steps"]

        notes = []
        if "_bbLow" in u and "_bbHigh" in u:
            notes.append(f"BB {u['_bbLow']}→{u['_bbHigh']}")
        if "_stress" in u:
            notes.append(f"stress {u['_stress']}")
        if notes:
            rec["comments"] = " | ".join(notes)

        if len(rec) > 1:
            results.append(rec)

    return results
