"""Rehearse real Prometheus -> Alertmanager -> local HTTP delivery, without sending messages."""

import json
import subprocess
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]


def main():
    project = "resumeagent-notifications-" + uuid.uuid4().hex[:12]
    compose = ["docker", "compose", "-p", project, "-f", str(ROOT / "docker-compose.notifications-test.yml")]
    report = {"scope": "isolated-local-webhook", "started_at": datetime.now(UTC).isoformat(), "passed": False}
    evidence = ROOT / "backend/.test-work/operations" / project
    evidence.mkdir(parents=True)
    opener = build_opener(ProxyHandler({}))

    def call(*args, **kwargs):
        return subprocess.run([*compose, *args], cwd=ROOT, check=True, **kwargs)

    try:
        call("up", "-d", "--quiet-pull", timeout=180)
        port = call("port", "receiver", "8000", capture_output=True, text=True).stdout.strip()
        base = "http://" + port

        def request(path, body=None):
            data = None if body is None else json.dumps(body).encode()
            req = Request(base + path, data=data, headers={"Content-Type": "application/json"})
            with opener.open(req, timeout=3) as response:
                return json.load(response)

        def wait_for(read, accept, timeout):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    value = read()
                    if accept(value):
                        return value
                except (OSError, URLError):
                    pass
                time.sleep(1)
            raise RuntimeError("Notification acceptance deadline exceeded")

        wait_for(lambda: request("/evidence"), lambda _: True, 30)
        # Wait longer than the production database rule's 30s hold period.
        healthy_until = time.monotonic() + 35
        while time.monotonic() < healthy_until:
            if request("/evidence")["deliveries"]:
                raise RuntimeError("Unexpected notification while healthy")
            time.sleep(1)
        report["healthy_silent"] = True
        request("/control", {"database_up": False})

        def received(status):
            return any(item["status"] == status and any(
                alert["alertname"] == "PlatformDatabaseUnavailable" and alert["status"] == status
                for alert in item["alerts"]
            ) for item in request("/evidence")["deliveries"])

        wait_for(lambda: received("firing"), bool, 90)
        report["firing_received"] = True
        request("/control", {"database_up": True})
        wait_for(lambda: received("resolved"), bool, 90)
        report.update(resolved_received=True, passed=True, **request("/evidence"))
    except Exception as exc:
        report["error_type"] = type(exc).__name__
        raise
    finally:
        try:
            with (evidence / "containers.log").open("w", encoding="utf-8") as log:
                call("logs", "--no-color", stdout=log, stderr=subprocess.STDOUT)
        finally:
            try:
                call("down", "-v", "--remove-orphans", timeout=60)
                report["cleanup_passed"] = True
            except Exception:
                report.update(cleanup_passed=False, passed=False)
                raise
            finally:
                report["finished_at"] = datetime.now(UTC).isoformat()
                (evidence / "notifications.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
                print(f"Notification evidence: {evidence / 'notifications.json'}")


if __name__ == "__main__":
    main()
