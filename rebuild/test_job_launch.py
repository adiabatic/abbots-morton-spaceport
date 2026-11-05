import io
import plistlib
import re
import subprocess
import sys
from datetime import datetime

import pytest

from rebuild.tools import job_launch


def test_the_plist_runs_the_command_under_caffeinate_with_only_the_whitelisted_environment(tmp_path):
    log = tmp_path / "logs" / "series.log"
    log.parent.mkdir()
    log.write_text("an earlier run\n")
    environ = {
        "HOME": "/Users/someone",
        "USER": "someone",
        "LOGNAME": "someone",
        "LANG": "en_US.UTF-8",
        "TMPDIR": "/var/folders/xy/T/",
        "PATH": "/opt/bin:/usr/bin:/bin",
        "UV_CACHE_DIR": "/checkout/.uv-cache",
        "AMS_CYCLE_RUN": "fault-kit",
        "AMS_KERNEL_THREADS": "4",
        "GITHUB_TOKEN": "secret",
        "SHELL": "/bin/zsh",
        "XAMS_LOOKALIKE": "1",
    }
    command = ["uv", "run", "python", "-m", "rebuild.tools.memory_series"]

    job = job_launch.write_job(command, kind="memory-series", log=log, root=tmp_path, environ=environ)

    root = tmp_path.resolve()
    assert re.fullmatch(
        rf"local\.ams\.memory-series\.{job_launch.checkout_digest(root)}\.\d{{8}}T\d{{6}}Z", job.label
    )
    assert job.plist == root / "var" / "jobs" / f"{job.label}.plist"
    assert plistlib.loads(job.plist.read_bytes()) == {
        "Label": job.label,
        "ProgramArguments": ["/usr/bin/caffeinate", "-i", *command],
        "WorkingDirectory": str(root),
        "EnvironmentVariables": {
            name: environ[name]
            for name in (
                "HOME",
                "USER",
                "LOGNAME",
                "LANG",
                "TMPDIR",
                "PATH",
                "UV_CACHE_DIR",
                "AMS_CYCLE_RUN",
                "AMS_KERNEL_THREADS",
            )
        },
        "RunAtLoad": True,
        "KeepAlive": False,
        "AbandonProcessGroup": True,
        "ProcessType": "Interactive",
        "LimitLoadToSessionType": ["Aqua", "Background"],
        "StandardOutPath": str(log.resolve()),
        "StandardErrorPath": str(log.resolve()),
    }
    assert log.read_bytes() == b""


def test_a_second_launch_in_the_same_second_takes_a_label_of_its_own(tmp_path, monkeypatch):
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 5, 21, 6, 55, tzinfo=tz)

    monkeypatch.setattr(job_launch, "datetime", Frozen)
    jobs = [
        job_launch.write_job(["true"], kind="fault-kit", log=tmp_path / f"{n}.log", root=tmp_path, environ={})
        for n in range(3)
    ]

    stem = f"local.ams.fault-kit.{job_launch.checkout_digest(tmp_path)}.20261105T210655Z"
    assert [job.label for job in jobs] == [stem, f"{stem}-2", f"{stem}-3"]
    assert sorted(path.name for path in (tmp_path / "var" / "jobs").iterdir()) == sorted(
        f"{job.label}.plist" for job in jobs
    )


@pytest.mark.skipif(sys.platform != "darwin", reason="launchd is macOS's")
def test_a_trivial_job_runs_under_launchd_and_its_exit_status_comes_back(tmp_path):
    out = io.BytesIO()

    status = job_launch.launch(
        ["/bin/sh", "-c", "echo out; echo err >&2; exit 7"],
        kind="test",
        log=tmp_path / "job.log",
        root=tmp_path,
        environ={"PATH": "/usr/bin:/bin"},
        out=out,
    )

    assert status == 7
    assert sorted(out.getvalue().splitlines()) == [b"err", b"out"]
    assert list((tmp_path / "var" / "jobs").iterdir()) == []
    loaded = subprocess.run(["/bin/launchctl", "list"], capture_output=True, text=True, check=True).stdout
    assert f"local.ams.test.{job_launch.checkout_digest(tmp_path)}." not in loaded
