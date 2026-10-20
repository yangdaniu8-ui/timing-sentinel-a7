import argparse
from dataclasses import replace
import json
from pathlib import Path
import time
import sys
from app.models import Config
from app.services.capture_reader import read_capture
from app.services.device_client import DeviceClient
from app.services.record_store import export_csv, export_json, load_record, save_record
from transport.mock import MockTransport


def demo(scenario="normal", seed=1, config=None):
    transport = MockTransport()
    client = DeviceClient(transport)
    try:
        client.connect()
        config = config or replace(Config(), min_ticks=25000 if scenario == "early" else 0)
        client.configure(config)
        client.arm()
        deadline = time.monotonic() + 3
        while not client.status()["ready"]:
            if time.monotonic() > deadline:
                raise TimeoutError("READY timeout")
            time.sleep(0.005)
        transport.stimulate(scenario, seed)
        while client.status()["state"] != "FROZEN":
            if time.monotonic() > deadline:
                raise TimeoutError("Freeze timeout")
            time.sleep(0.01)
        capture = read_capture(client)
        return capture
    finally:
        transport.close()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Timing Sentinel A7 mock CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("demo")
    run.add_argument("--scenario", choices=("normal", "early", "late", "missing"), default="normal")
    run.add_argument("--seed", type=int, default=1)
    run.add_argument("--output", type=Path, required=True)
    check = sub.add_parser("inspect")
    check.add_argument("record", type=Path)
    args = parser.parse_args()
    if args.command == "demo":
        capture = demo(args.scenario, args.seed)
        save_record(capture, args.output)
        export_json(capture, args.output.with_suffix(".json"))
        export_csv(capture, args.output.with_suffix(".csv"))
    else:
        capture = load_record(args.record)
    print(json.dumps({"origin": capture.metadata["origin"], "result": capture.metadata["result"],
                      "session": capture.metadata["session_id"], "samples": len(capture.samples),
                      "events": len(capture.events) // 16, "complete": capture.complete,
                      "observation": capture.metadata["observation"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
