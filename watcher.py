#!/usr/bin/env python3
"""Injective Endpoint Watcher

Lightweight monitoring for publicly documented Injective endpoints.
Generates JSON, CSV and Markdown reports, plus a simple diff against the
previous run when available.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DEFAULT_TARGETS = "targets.txt"
DEFAULT_OUTPUT_DIR = "output"
DEFAULT_TIMEOUT = 10.0
DEFAULT_RETRIES = 1
DEFAULT_USER_AGENT = "injective-endpoint-watcher/1.0"


@dataclass
class Target:
    name: str
    url: str
    category: str = "general"
    note: str = ""


@dataclass
class Result:
    name: str
    url: str
    category: str
    note: str
    timestamp_utc: str
    status_code: int | None
    latency_ms: float | None
    content_length: int | None
    content_type: str | None
    final_url: str | None
    ok: bool
    error: str | None


@dataclass
class DiffEntry:
    name: str
    url: str
    previous_status: int | None
    current_status: int | None
    previous_latency_ms: float | None
    current_latency_ms: float | None
    status_changed: bool
    latency_delta_ms: float | None
    previous_error: str | None
    current_error: str | None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_stamp(dt: datetime | None = None) -> str:
    return (dt or utc_now()).strftime("%Y%m%dT%H%M%SZ")


def iso_utc(dt: datetime | None = None) -> str:
    return (dt or utc_now()).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def ensure_output_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def parse_targets_file(path: Path) -> list[Target]:
    """Parse targets.txt.

    Accepted formats per non-comment line:
    - https://example.com
    - name|https://example.com
    - name|https://example.com|category
    - name|https://example.com|category|note
    """
    targets: list[Target] = []
    if not path.exists():
        raise FileNotFoundError(f"Targets file not found: {path}")

    for line_no, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if "|" not in line:
            targets.append(Target(name=f"target-{len(targets)+1}", url=line))
            continue

        parts = [part.strip() for part in line.split("|")]
        if len(parts) < 2:
            raise ValueError(f"Invalid target on line {line_no}: {raw_line}")

        name = parts[0] or f"target-{len(targets)+1}"
        url = parts[1]
        category = parts[2] if len(parts) >= 3 and parts[2] else "general"
        note = parts[3] if len(parts) >= 4 else ""
        targets.append(Target(name=name, url=url, category=category, note=note))

    if not targets:
        raise ValueError(f"No valid targets found in {path}")
    return targets


def build_session(user_agent: str, retries: int) -> requests.Session:
    session = requests.Session()
    retry_strategy = Retry(
        total=retries,
        connect=retries,
        read=retries,
        status=retries,
        backoff_factor=0.4,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "HEAD"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": user_agent})
    return session


def probe_target(session: requests.Session, target: Target, timeout: float) -> Result:
    started = time.perf_counter()
    timestamp = iso_utc()
    try:
        response = session.get(target.url, timeout=timeout, allow_redirects=True)
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        content_type = response.headers.get("Content-Type")
        content_length_header = response.headers.get("Content-Length")
        if content_length_header and content_length_header.isdigit():
            content_length = int(content_length_header)
        else:
            content_length = len(response.content)

        return Result(
            name=target.name,
            url=target.url,
            category=target.category,
            note=target.note,
            timestamp_utc=timestamp,
            status_code=response.status_code,
            latency_ms=latency_ms,
            content_length=content_length,
            content_type=content_type,
            final_url=str(response.url),
            ok=200 <= response.status_code < 400,
            error=None,
        )
    except Exception as exc:  # noqa: BLE001
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        return Result(
            name=target.name,
            url=target.url,
            category=target.category,
            note=target.note,
            timestamp_utc=timestamp,
            status_code=None,
            latency_ms=latency_ms,
            content_length=None,
            content_type=None,
            final_url=None,
            ok=False,
            error=str(exc),
        )


def summarize(results: list[Result]) -> dict[str, Any]:
    total = len(results)
    successes = sum(1 for item in results if item.ok)
    failures = total - successes
    latencies = [item.latency_ms for item in results if item.latency_ms is not None]
    by_category: dict[str, int] = {}
    for item in results:
        by_category[item.category] = by_category.get(item.category, 0) + 1

    return {
        "generated_at_utc": iso_utc(),
        "total_targets": total,
        "successful_targets": successes,
        "failed_targets": failures,
        "success_rate": round((successes / total) * 100, 2) if total else 0,
        "latency": {
            "min_ms": min(latencies) if latencies else None,
            "max_ms": max(latencies) if latencies else None,
            "avg_ms": round(statistics.mean(latencies), 2) if latencies else None,
            "median_ms": round(statistics.median(latencies), 2) if latencies else None,
        },
        "by_category": by_category,
    }


def load_previous_results(latest_json_path: Path) -> list[Result] | None:
    if not latest_json_path.exists():
        return None
    payload = json.loads(latest_json_path.read_text(encoding="utf-8"))
    items = payload.get("results", [])
    return [Result(**item) for item in items]


def build_diff(previous: list[Result] | None, current: list[Result]) -> list[DiffEntry]:
    if not previous:
        return []

    previous_by_url = {item.url: item for item in previous}
    diff_entries: list[DiffEntry] = []
    for current_item in current:
        previous_item = previous_by_url.get(current_item.url)
        if not previous_item:
            continue

        status_changed = previous_item.status_code != current_item.status_code
        latency_delta = None
        if previous_item.latency_ms is not None and current_item.latency_ms is not None:
            latency_delta = round(current_item.latency_ms - previous_item.latency_ms, 2)

        diff_entries.append(
            DiffEntry(
                name=current_item.name,
                url=current_item.url,
                previous_status=previous_item.status_code,
                current_status=current_item.status_code,
                previous_latency_ms=previous_item.latency_ms,
                current_latency_ms=current_item.latency_ms,
                status_changed=status_changed,
                latency_delta_ms=latency_delta,
                previous_error=previous_item.error,
                current_error=current_item.error,
            )
        )
    return diff_entries


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_csv(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    rows = list(rows)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def markdown_summary_block(summary: dict[str, Any]) -> list[str]:
    latency = summary["latency"]
    lines = [
        "## Summary",
        "",
        f"- Generated at: `{summary['generated_at_utc']}`",
        f"- Total targets: **{summary['total_targets']}**",
        f"- Successful targets: **{summary['successful_targets']}**",
        f"- Failed targets: **{summary['failed_targets']}**",
        f"- Success rate: **{summary['success_rate']}%**",
        f"- Min latency: `{latency['min_ms']}` ms",
        f"- Median latency: `{latency['median_ms']}` ms",
        f"- Avg latency: `{latency['avg_ms']}` ms",
        f"- Max latency: `{latency['max_ms']}` ms",
        "",
    ]
    return lines


def write_markdown_report(path: Path, summary: dict[str, Any], results: list[Result], diff_entries: list[DiffEntry]) -> None:
    lines: list[str] = ["# Injective Endpoint Watcher Report", ""]
    lines.extend(markdown_summary_block(summary))

    lines.extend(
        [
            "## Results",
            "",
            "| Name | Category | Status | Latency (ms) | Size | Content-Type | Final URL | Error |",
            "|---|---|---:|---:|---:|---|---|---|",
        ]
    )

    for result in results:
        lines.append(
            "| {name} | {category} | {status} | {latency} | {size} | {ctype} | {final_url} | {error} |".format(
                name=result.name,
                category=result.category,
                status=result.status_code if result.status_code is not None else "-",
                latency=result.latency_ms if result.latency_ms is not None else "-",
                size=result.content_length if result.content_length is not None else "-",
                ctype=(result.content_type or "-").replace("|", "/"),
                final_url=result.final_url or "-",
                error=(result.error or "-").replace("|", "/"),
            )
        )

    if diff_entries:
        lines.extend(
            [
                "",
                "## Diff vs previous run",
                "",
                "| Name | Previous Status | Current Status | Status Changed | Previous Latency | Current Latency | Delta (ms) |",
                "|---|---:|---:|---|---:|---:|---:|",
            ]
        )
        for entry in diff_entries:
            lines.append(
                f"| {entry.name} | {entry.previous_status or '-'} | {entry.current_status or '-'} | {entry.status_changed} | {entry.previous_latency_ms or '-'} | {entry.current_latency_ms or '-'} | {entry.latency_delta_ms or '-'} |"
            )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def update_latest_copy(source: Path, latest_name: str) -> None:
    latest_path = source.parent / latest_name
    shutil.copyfile(source, latest_path)


def run(targets_file: Path, output_dir: Path, timeout: float, retries: int, user_agent: str) -> int:
    ensure_output_dir(output_dir)
    latest_json_path = output_dir / "latest_report.json"
    previous_results = load_previous_results(latest_json_path)

    targets = parse_targets_file(targets_file)
    session = build_session(user_agent=user_agent, retries=retries)
    try:
        results = [probe_target(session, target, timeout=timeout) for target in targets]
    finally:
        session.close()

    summary = summarize(results)
    diff_entries = build_diff(previous_results, results)

    stamp = utc_stamp()
    json_path = output_dir / f"report_{stamp}.json"
    csv_path = output_dir / f"report_{stamp}.csv"
    md_path = output_dir / f"report_{stamp}.md"
    diff_json_path = output_dir / f"diff_{stamp}.json"

    json_payload = {
        "meta": {
            "tool": "injective-endpoint-watcher",
            "version": "1.0.0",
            "generated_at_utc": iso_utc(),
            "targets_file": str(targets_file),
            "timeout_seconds": timeout,
            "retries": retries,
        },
        "summary": summary,
        "results": [asdict(item) for item in results],
    }
    write_json(json_path, json_payload)
    write_csv(csv_path, [asdict(item) for item in results])
    write_markdown_report(md_path, summary, results, diff_entries)
    write_json(diff_json_path, {"generated_at_utc": iso_utc(), "diff": [asdict(item) for item in diff_entries]})

    update_latest_copy(json_path, "latest_report.json")
    update_latest_copy(csv_path, "latest_report.csv")
    update_latest_copy(md_path, "latest_report.md")
    update_latest_copy(diff_json_path, "latest_diff.json")

    print(f"Checked {len(results)} targets.")
    print(f"Markdown report: {md_path}")
    print(f"JSON report: {json_path}")
    print(f"CSV report: {csv_path}")
    print(f"Diff report: {diff_json_path}")
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Monitor publicly documented Injective endpoints.")
    parser.add_argument("--targets", default=DEFAULT_TARGETS, help="Path to targets file.")
    parser.add_argument("--output", default=DEFAULT_OUTPUT_DIR, help="Directory for reports.")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="HTTP timeout in seconds.")
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES, help="HTTP retries per target.")
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT, help="Custom User-Agent header.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        return run(
            targets_file=Path(args.targets),
            output_dir=Path(args.output),
            timeout=args.timeout,
            retries=args.retries,
            user_agent=args.user_agent,
        )
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
