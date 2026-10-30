"""Run the Lucky Rooms demo: python dev.py [--lan [--ip ADDRESS]]."""

import argparse
import ctypes
import ipaddress
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parent
WINDOWS = os.name == "nt"
NETWORK_QUERY = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$rows = @(foreach ($adapter in (Get-NetAdapter -Physical)) {
    if ($adapter.Status -ne 'Up' -or -not $adapter.HardwareInterface -or
        $adapter.PhysicalMediaType -notin @('802.3', 'Native 802.11', 'Wireless LAN')) { continue }
    $config = Get-NetIPConfiguration -InterfaceIndex $adapter.InterfaceIndex
    $interface = Get-NetIPInterface -AddressFamily IPv4 -InterfaceIndex $adapter.InterfaceIndex
    $routes = @(Get-NetRoute -AddressFamily IPv4 -InterfaceIndex $adapter.InterfaceIndex `
        -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue)
    $metric = $null
    if ($routes.Count -gt 0) {
        $metric = ($routes | Measure-Object RouteMetric -Minimum).Minimum + $interface.InterfaceMetric
    }
    foreach ($address in $config.IPv4Address) {
        if ($address.AddressState -ne 'Preferred') { continue }
        [PSCustomObject]@{ name = $adapter.Name; ip = $address.IPAddress; metric = $metric }
    }
})
ConvertTo-Json -InputObject $rows -Compress
"""


class LauncherError(Exception):
    pass


def usable_ipv4(value):
    try:
        address = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError as exc:
        raise ValueError("Enter an IPv4 address, for example 192.168.1.100.") from exc
    if (address.is_loopback or address.is_link_local or address.is_multicast
            or address.is_reserved or int(address) < 0x01000000):
        raise ValueError(f"{value} is not a usable LAN IPv4 address.")
    return str(address)


def choose_lan_address(rows):
    candidates = {}
    for row in rows:
        try:
            address = usable_ipv4(row["ip"])
        except ValueError:
            continue
        candidates[(row["name"], address)] = row
    candidates = list(candidates.values())
    if not candidates:
        raise LauncherError("No active physical Wi-Fi/Ethernet IPv4 address found. "
                            "Connect to your LAN or use --lan --ip ADDRESS.")
    routed = [row for row in candidates if row["metric"] is not None]
    if routed:
        best = min(row["metric"] for row in routed)
        candidates = [row for row in routed if row["metric"] == best]
    if len(candidates) != 1:
        choices = ", ".join(f"{row['name']}: {row['ip']}" for row in candidates)
        raise LauncherError(f"LAN address is ambiguous ({choices}). Use --lan --ip ADDRESS.")
    return candidates[0]["ip"], candidates[0]["name"]


def detect_lan_address():
    if not WINDOWS:
        raise LauncherError("Automatic LAN detection supports Windows. Use --lan --ip ADDRESS.")
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", NETWORK_QUERY],
            capture_output=True, encoding="utf-8", errors="replace", timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode:
            raise LauncherError(f"Windows LAN detection failed: {result.stderr.strip()}\n"
                                "Use --lan --ip ADDRESS.")
        return choose_lan_address(json.loads(result.stdout))
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError) as exc:
        raise LauncherError(f"Could not read Windows LAN configuration: {exc}. "
                            "Use --lan --ip ADDRESS.") from exc


class WindowsJob:
    """Keep the launched trees together, including npm's cmd/node descendants."""

    def __init__(self):
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [("ProcessTime", ctypes.c_int64), ("JobTime", ctypes.c_int64),
                        ("Flags", wintypes.DWORD), ("MinWorkingSet", ctypes.c_size_t),
                        ("MaxWorkingSet", ctypes.c_size_t), ("ActiveProcesses", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("Priority", wintypes.DWORD),
                        ("Scheduling", wintypes.DWORD)]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("Basic", BasicLimits), ("IoCounters", ctypes.c_uint64 * 6),
                        ("ProcessMemory", ctypes.c_size_t), ("JobMemory", ctypes.c_size_t),
                        ("PeakProcessMemory", ctypes.c_size_t), ("PeakJobMemory", ctypes.c_size_t)]

        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                       ctypes.c_void_p, wintypes.DWORD]
        self.kernel.SetInformationJobObject.restype = wintypes.BOOL
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.Basic.Flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def add(self, process):
        if not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def check_dependencies():
    python = ROOT / ".venv" / ("Scripts/python.exe" if WINDOWS else "bin/python")
    if not python.is_file():
        raise LauncherError("Missing .venv Python. Follow the one-time installation steps in README.md.")
    npm = shutil.which("npm.cmd" if WINDOWS else "npm")
    if not npm or not shutil.which("node"):
        raise LauncherError("Node.js/npm is missing from PATH. Install the version listed in README.md.")
    if not (ROOT / "frontend/node_modules/vite/bin/vite.js").is_file():
        raise LauncherError("Frontend dependencies are missing. Run npm.cmd ci in frontend (see README.md).")
    result = subprocess.run(
        [str(python), "-c", "import uvicorn, fastapi, socketio, httpx, dotenv, websockets"],
        cwd=ROOT, capture_output=True, text=True, timeout=20,
    )
    if result.returncode:
        raise LauncherError("Python dependencies could not be imported. Install requirements.txt "
                            f"into .venv (see README.md).\n{result.stderr.strip()}")
    return str(python), npm


def check_port(host, port, name):
    for family, socktype, protocol, _, address in socket.getaddrinfo(
            host, port, type=socket.SOCK_STREAM):
        with socket.socket(family, socktype, protocol) as probe:
            if WINDOWS:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                probe.bind(address)
            except OSError as exc:
                raise LauncherError(f"{name} cannot bind {host}:{port}: {exc}. "
                                    "The port may be occupied. Stop its owner yourself and retry; "
                                    "this launcher will not stop unrelated applications.") from exc


def check_running(processes):
    for name, process in processes:
        code = process.poll()
        if code is not None:
            raise LauncherError(f"{name} exited with code {code}. See its output above.")


def wait_ready(name, url, processes, headers=None):
    # Ignore desktop HTTP proxy settings for these local/LAN probes.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + 30
    last_error = "no response"
    while time.monotonic() < deadline:
        check_running(processes)
        try:
            request = urllib.request.Request(url, headers=headers or {})
            with opener.open(request, timeout=1) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = str(exc)
        time.sleep(0.2)
    raise LauncherError(f"{name} was not ready at {url} within 30 seconds: {last_error}")


def stop_services(processes, job):
    if processes:
        print("\nStopping services...", flush=True)
    for _, process in reversed(processes):
        if process.poll() is None:
            try:
                if WINDOWS:
                    process.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    os.killpg(process.pid, signal.SIGTERM)
            except OSError:
                pass
    deadline = time.monotonic() + 4
    for _, process in processes:
        try:
            process.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass
    if job:
        # Also kills descendants whose npm/cmd parent has already exited.
        job.close()
    elif not WINDOWS:
        for _, process in processes:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    for _, process in processes:
        process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lan", action="store_true", help="Expose frontend and backend on your LAN")
    parser.add_argument("--ip", help="Override LAN IPv4 detection (requires --lan)")
    args = parser.parse_args()
    if args.ip and not args.lan:
        parser.error("--ip requires --lan")
    if args.ip:
        try:
            args.ip = usable_ipv4(args.ip)
        except ValueError as exc:
            parser.error(str(exc))

    processes = []
    job = None
    try:
        python, npm = check_dependencies()
        address, network = "localhost", None
        if args.lan:
            address, network = (args.ip, "Manual override") if args.ip else detect_lan_address()
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                try:
                    probe.bind((address, 0))
                except OSError as exc:
                    raise LauncherError(f"LAN address {address} is not available on this computer. "
                                        "Use --lan --ip with its Wi-Fi/Ethernet IPv4 address.") from exc
        frontend_url = f"http://{address}:5173"
        api_url = f"http://{address}:8000"
        backend_host = "0.0.0.0" if args.lan else "127.0.0.1"
        frontend_host = "0.0.0.0" if args.lan else "localhost"
        environment = os.environ.copy()
        environment.update(FRONTEND_ORIGIN=frontend_url, VITE_API_BASE_URL=api_url,
                           MOTHER_SERVER_BASE_URL="http://127.0.0.1:8001", PYTHONUNBUFFERED="1")
        # SERVICE_SECRET remains inherited, or is loaded from root .env by both apps.
        services = [
            ("Mother Server", "127.0.0.1", 8001, ROOT,
             [python, "-m", "uvicorn", "mock_mother.main:app", "--host", "127.0.0.1", "--port", "8001"],
             "http://127.0.0.1:8001/docs", None),
            ("Game Backend", backend_host, 8000, ROOT,
             [python, "-m", "uvicorn", "backend.main:app", "--host", backend_host, "--port", "8000"],
             f"{api_url}/me", {"Authorization": "Bearer demo-alice"}),
            ("Frontend", frontend_host, 5173, ROOT / "frontend",
             [npm, "run", "dev", "--", "--host", frontend_host, "--port", "5173", "--strictPort"],
             frontend_url, None),
        ]
        print(f"\nLUCKY ROOMS - {'LAN' if args.lan else 'LOCAL'} MODE\n", flush=True)
        if network:
            print(f"Network: {network}\n{'Selected' if args.ip else 'Detected'} IP: {address}\n", flush=True)
        for name, host, port, *_ in services:
            check_port(host, port, name)
            print(f"{name + ':':15} {host}:{port}", flush=True)
        if args.lan:
            print("\nIf other devices cannot connect, allow Python/Node (TCP 8000/5173) "
                  "through Windows Firewall on your trusted private network. "
                  "Guest Wi-Fi client isolation can also block access.", flush=True)
        job = WindowsJob() if WINDOWS else None
        for name, _, _, cwd, command, url, headers in services:
            print(f"\nStarting {name}...", flush=True)
            # Prevent Ctrl+C arriving between Popen and registration for cleanup.
            old_handler = signal.signal(signal.SIGINT, signal.SIG_IGN)
            try:
                process = subprocess.Popen(
                    command, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if WINDOWS else 0,
                    start_new_session=not WINDOWS,
                )
                processes.append((name, process))
                if job:
                    try:
                        job.add(process)
                    except OSError:
                        process.kill()
                        process.wait()
                        raise
            finally:
                signal.signal(signal.SIGINT, old_handler)
            wait_ready(name, url, processes, headers)
        check_running(processes)
        audience = "any device connected to this network" if args.lan else "this computer"
        print(f"\nReady!\n\nOpen on {audience}:\n\n{frontend_url}\n\n"
              "Press Ctrl+C to stop all services.\n", flush=True)
        while True:
            check_running(processes)
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 0
    except (LauncherError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"\nLauncher error: {exc}", file=sys.stderr, flush=True)
        return 1
    finally:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        stop_services(processes, job)


if __name__ == "__main__":
    sys.exit(main())
