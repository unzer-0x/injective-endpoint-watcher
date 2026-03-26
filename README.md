# Injective Endpoint Watcher

A lightweight monitoring tool for publicly documented Injective endpoints.

This repo is meant to be a small, clean technical contribution that demonstrates:
- endpoint visibility
- structured reporting
- lightweight monitoring
- attack surface awareness for public infrastructure

## Features

- Monitors a list of public endpoints from `targets.txt`
- Captures status code, latency, content type, content length, and final URL
- Exports reports to JSON, CSV, and Markdown
- Automatically compares the current run with the previous run
- Keeps `latest_*` copies for quick review

## Project structure

```text
injective-endpoint-watcher/
├── watcher.py
├── targets.txt
├── requirements.txt
├── .gitignore
├── LICENSE
├── README.md
└── output/
```

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python watcher.py
```

On Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python watcher.py
```

## Example usage

Run with defaults:

```bash
python watcher.py
```

Custom targets and output directory:

```bash
python watcher.py --targets targets.txt --output output --timeout 12 --retries 2
```

## Target format

`targets.txt` supports these formats:

```text
https://example.com
name|https://example.com
name|https://example.com|category
name|https://example.com|category|note
```

Examples:

```text
mainnet-chain-lcd|https://sentry.lcd.injective.network:443|mainnet|Public Chain LCD
mainnet-evm-rpc|https://sentry.evm-rpc.injective.network/|mainnet|Public EVM JSON-RPC
```

## Outputs

Each run creates timestamped files inside `output/`:

- `report_<timestamp>.json`
- `report_<timestamp>.csv`
- `report_<timestamp>.md`
- `diff_<timestamp>.json`

It also updates these rolling files:

- `latest_report.json`
- `latest_report.csv`
- `latest_report.md`
- `latest_diff.json`

## Notes

- Use only public, documented, or otherwise authorized endpoints.
- This tool is intentionally lightweight and read-only.
- It is designed for visibility and monitoring, not intrusive testing.

## Suggested positioning for an application

You can describe this project as:

> A lightweight monitoring and reporting tool for publicly exposed Injective-related endpoints, focused on endpoint visibility, response monitoring, and attack surface awareness.

## Next possible improvements

- HTML dashboard output
- alerting on status changes
- historical latency graphing
- endpoint tagging and filtering
- GitHub Actions scheduled runs
