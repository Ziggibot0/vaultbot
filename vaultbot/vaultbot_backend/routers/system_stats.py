"""Hardware resource stats endpoints, extracted from routers/system.py.

Polled by the plugin every 3s for the resource strip (CPU/RAM/GPU/NPU).
Uses psutil for CPU/RAM/disk/net, optional libraries for GPU/NPU.
Every field is best-effort: if a library isn't installed or hardware
isn't present, the field is None and the frontend silently omits it.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

router = APIRouter()


def _cpu_stats() -> dict[str, Any]:
    """CPU utilization via psutil."""
    try:
        import psutil

        return {
            "percent": round(psutil.cpu_percent(interval=None), 1),
            "cores": psutil.cpu_count(logical=True) or 0,
            "per_core": [
                round(p, 1) for p in psutil.cpu_percent(interval=None, percpu=True)
            ]
            if psutil.cpu_count()
            else [],
        }
    except Exception:  # noqa: BLE001
        return {"percent": 0, "cores": 0, "per_core": []}


def _ram_stats() -> dict[str, Any]:
    """Memory usage via psutil."""
    try:
        import psutil

        vm = psutil.virtual_memory()
        return {
            "used_gb": round(vm.used / 1_073_741_824, 1),
            "total_gb": round(vm.total / 1_073_741_824, 1),
            "percent": round(vm.percent, 1),
        }
    except Exception:  # noqa: BLE001
        return {"used_gb": 0, "total_gb": 0, "percent": 0}


def _gpu_stats() -> dict[str, Any] | None:
    """GPU utilization + VRAM + temperature.

    Tries four methods in order:
    1. Windows Performance Counters (works for AMD/NVIDIA iGPUs + dGPUs)
    2. NVIDIA via pynvml (if installed)
    3. AMD via pyadl (if installed)
    4. WMI fallback (name + total VRAM only, no utilization)

    Returns None only if no GPU is detectable at all. The Windows
    Performance Counter path is the most reliable on Windows because
    it doesn't require any vendor-specific library — it uses the OS's
    own GPU telemetry which works for integrated and discrete GPUs alike.
    """
    gpu_name = None
    gpu_vram_total = None

    # CREATE_NO_WINDOW: prevents PowerShell subprocess from popping up
    # a visible console window on every poll. Without this, the 3-second
    # polling from the frontend spawns 3 PowerShell windows every 3
    # seconds, making Obsidian unusable.
    import sys

    _no_window = 0
    if sys.platform == "win32":
        _no_window = 0x08000000  # CREATE_NO_WINDOW

    # ── Get the GPU name + total VRAM via WMI (always works on Windows)
    try:
        import json as _json
        import subprocess

        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_VideoController | "
                "Where-Object { $_.Name -and $_.Name -notmatch 'Microsoft Basic' } | "
                "Select-Object -First 1 Name, AdapterRAM | ConvertTo-Json",
            ],
            capture_output=True,
            timeout=5,
            encoding="utf-8",
            errors="replace",
            creationflags=_no_window,
        )
        if result.returncode == 0 and result.stdout.strip():
            data = _json.loads(result.stdout.strip())
            if isinstance(data, list):
                data = data[0] if data else {}
            gpu_name = data.get("Name", "")
            gpu_vram_total = data.get("AdapterRAM", 0)
            if gpu_vram_total:
                gpu_vram_total = round(gpu_vram_total / 1_073_741_824, 1)
    except Exception:  # noqa: BLE001
        pass

    # ── Windows Performance Counters for real-time utilization + VRAM
    # This works for AMD, NVIDIA, and Intel GPUs on Windows 10+ without
    # any vendor-specific library. It's the same data Task Manager uses.
    try:
        import subprocess as _sp

        # GPU utilization: sum all active engine utilizations.
        # Each engine reports its own percentage; the total GPU usage is
        # the max across all engines (not the sum — one engine at 50% +
        # another at 30% means the GPU is at 50% busy, not 80%).
        util_cmd = (
            "$ErrorActionPreference='SilentlyContinue';"
            "$u = Get-Counter '\\GPU Engine(*)\\Utilization Percentage';"
            "$max = ($u.CounterSamples | Measure-Object CookedValue -Maximum).Maximum;"
            "Write-Output $max"
        )
        result = _sp.run(
            ["powershell", "-NoProfile", "-Command", util_cmd],
            capture_output=True,
            timeout=5,
            encoding="utf-8",
            errors="replace",
            creationflags=_no_window,
        )
        gpu_util = None
        if result.returncode == 0 and result.stdout.strip():
            try:
                gpu_util = round(float(result.stdout.strip()), 1)
            except (ValueError, TypeError):
                pass

        # GPU memory (dedicated + shared VRAM usage).
        # For iGPUs, "Dedicated Usage" includes system RAM allocated to
        # the GPU, so dedicated+shared is the real "GPU memory used".
        # For dGPUs, dedicated is VRAM and shared is minimal.
        vram_cmd = (
            "$ErrorActionPreference='SilentlyContinue';"
            "$d = Get-Counter '\\GPU Adapter Memory(*)\\Dedicated Usage';"
            "$dSum = ($d.CounterSamples | Measure-Object CookedValue -Sum).Sum;"
            "$s = Get-Counter '\\GPU Adapter Memory(*)\\Shared Usage';"
            "$sSum = ($s.CounterSamples | Measure-Object CookedValue -Sum).Sum;"
            "Write-Output ($dSum + $sSum)"
        )
        result = _sp.run(
            ["powershell", "-NoProfile", "-Command", vram_cmd],
            capture_output=True,
            timeout=5,
            encoding="utf-8",
            errors="replace",
            creationflags=_no_window,
        )
        gpu_vram_used = None
        if result.returncode == 0 and result.stdout.strip():
            try:
                vram_bytes = float(result.stdout.strip())
                gpu_vram_used = round(vram_bytes / 1_073_741_824, 1)
            except (ValueError, TypeError):
                pass

        if gpu_name or gpu_util is not None:
            # For iGPUs (shared system RAM), the WMI AdapterRAM is just
            # the small dedicated segment. Use system RAM total as the
            # "pool" if the GPU name suggests an integrated GPU.
            vram_total = gpu_vram_total
            if gpu_name and any(
                kw in gpu_name.lower()
                for kw in (
                    "radeon",
                    "iris",
                    "uhd",
                    "hd graphics",
                    "integrated",
                    "amd radeon(tm)",
                )
            ):
                try:
                    import psutil as _ps

                    vram_total = round(_ps.virtual_memory().total / 1_073_741_824, 1)
                except Exception:  # noqa: BLE001
                    pass
                # For iGPUs, vram_used can exceed vram_total (shared mem
                # is allocated dynamically from system RAM). Cap the
                # reported used at the total so the meter doesn't break.
                if (
                    gpu_vram_used is not None
                    and vram_total
                    and gpu_vram_used > vram_total
                ):
                    gpu_vram_used = vram_total
            return {
                "name": gpu_name or "GPU",
                "utilization_percent": gpu_util,
                "vram_used_gb": gpu_vram_used,
                "vram_total_gb": vram_total,
                "temperature_c": None,  # no cross-vendor temp via perf counters
            }
    except Exception:  # noqa: BLE001
        pass

    # ── NVIDIA via pynvml (fallback if perf counters failed)
    try:
        import pynvml

        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        util = pynvml.nvmlDeviceGetUtilizationRates(handle)
        mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
        try:
            temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
        except Exception:  # noqa: BLE001
            temp = None
        try:
            name = pynvml.nvmlDeviceGetName(handle)
            if isinstance(name, bytes):
                name = name.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            name = gpu_name or "NVIDIA GPU"
        return {
            "name": name,
            "utilization_percent": util.gpu,
            "vram_used_gb": round(mem.used / 1_073_741_824, 1),
            "vram_total_gb": round(mem.total / 1_073_741_824, 1),
            "temperature_c": temp,
        }
    except Exception:  # noqa: BLE001 — pynvml not installed or no NVIDIA GPU
        pass

    # ── If we got a name from WMI but no utilization, return what we have
    if gpu_name:
        return {
            "name": gpu_name,
            "utilization_percent": None,
            "vram_used_gb": None,
            "vram_total_gb": gpu_vram_total,
            "temperature_c": None,
        }

    return None


def _npu_stats() -> dict[str, Any] | None:
    """NPU utilization if detectable.

    AMD Ryzen AI NPUs and Intel NPUs don't have a standard Python API yet.
    We try Windows Performance Counters via subprocess; if unavailable,
    return None. This field is aspirational — the frontend will omit it
    gracefully until a reliable cross-vendor NPU library exists.
    """
    return None


def _disk_io() -> dict[str, Any]:
    """Disk read/write rates (MB/s) computed as delta since last call."""
    try:
        import time as _time

        import psutil

        counters = psutil.disk_io_counters()
        if not counters:
            return {"read_mb_s": 0, "write_mb_s": 0}
        now = _time.monotonic()
        if not hasattr(_disk_io, "_prev"):
            _disk_io._prev = (now, counters.read_bytes, counters.write_bytes)
            return {"read_mb_s": 0, "write_mb_s": 0}
        dt = now - _disk_io._prev[0]
        if dt < 0.1:
            dt = 0.1
        read_rate = round((counters.read_bytes - _disk_io._prev[1]) / 1_048_576 / dt, 1)
        write_rate = round(
            (counters.write_bytes - _disk_io._prev[2]) / 1_048_576 / dt, 1
        )
        _disk_io._prev = (now, counters.read_bytes, counters.write_bytes)
        return {"read_mb_s": max(0, read_rate), "write_mb_s": max(0, write_rate)}
    except Exception:  # noqa: BLE001
        return {"read_mb_s": 0, "write_mb_s": 0}


def _net_io() -> dict[str, Any]:
    """Network send/recv rates (KB/s) computed as delta since last call."""
    try:
        import time as _time

        import psutil

        counters = psutil.net_io_counters()
        if not counters:
            return {"send_kb_s": 0, "recv_kb_s": 0}
        now = _time.monotonic()
        if not hasattr(_net_io, "_prev"):
            _net_io._prev = (now, counters.bytes_sent, counters.bytes_recv)
            return {"send_kb_s": 0, "recv_kb_s": 0}
        dt = now - _net_io._prev[0]
        if dt < 0.1:
            dt = 0.1
        send_rate = round((counters.bytes_sent - _net_io._prev[1]) / 1024 / dt, 1)
        recv_rate = round((counters.bytes_recv - _net_io._prev[2]) / 1024 / dt, 1)
        _net_io._prev = (now, counters.bytes_sent, counters.bytes_recv)
        return {"send_kb_s": max(0, send_rate), "recv_kb_s": max(0, recv_rate)}
    except Exception:  # noqa: BLE001
        return {"send_kb_s": 0, "recv_kb_s": 0}


@router.get("/system/stats")
async def system_stats() -> dict[str, Any]:
    """Real-time hardware resource snapshot for the plugin's resource strip.

    Polled every 3 seconds by the frontend. Returns CPU, RAM, GPU, NPU,
    disk, and network stats. Every field is best-effort: None values are
    silently omitted by the frontend. Never raises — a stats failure
    never blocks the UI.

    The first call to ``psutil.cpu_percent(interval=None)`` returns 0
    (it needs a baseline), so we pre-seed it with a quick 0.1s sample on
    the first call to avoid a "0% CPU" flash.
    """
    try:
        import psutil

        # Seed cpu_percent so the first poll has a real value.
        if not hasattr(system_stats, "_cpu_seeded"):
            psutil.cpu_percent(interval=0.1)
            system_stats._cpu_seeded = True
    except Exception:  # noqa: BLE001
        pass

    return {
        "cpu": _cpu_stats(),
        "ram": _ram_stats(),
        "gpu": _gpu_stats(),
        "npu": _npu_stats(),
        "disk": _disk_io(),
        "net": _net_io(),
    }
