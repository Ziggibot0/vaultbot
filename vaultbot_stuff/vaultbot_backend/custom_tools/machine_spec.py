"""
Agent-authored tool: machine_spec
"""

SCHEMA = {"name": "machine_spec", "description": "Report machine specs relevant to local LLM inference: CPU, RAM, GPU/iGPU status, Ollama config, loaded models, and environment variables.", "parameters": {"properties": {"ollama_host": {"description": "Ollama server host (default: http://localhost:11434)", "type": "string"}}, "required": [], "type": "object"}}

import os
import re
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any


def _run(cmd: list[str], timeout: int = 15) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, errors="replace"
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except Exception as e:
        return -1, "", str(e)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _bytes_to_gb(n: int) -> float:
    return round(n / (1024**3), 2)


def get_os_info() -> dict:
    info = {"platform": sys.platform}
    if sys.platform == "linux":
        rc, out, _ = _run(["uname", "-sr"])
        if rc == 0:
            info["kernel"] = out
        rc, out, _ = _run(["cat", "/etc/os-release"])
        if rc == 0:
            for line in out.splitlines():
                if line.startswith("PRETTY_NAME="):
                    info["distribution"] = line.split("=", 1)[1].strip('"')
                    break
    elif sys.platform == "darwin":
        rc, out, _ = _run(["uname", "-sr"])
        if rc == 0:
            info["kernel"] = out
    elif sys.platform == "win32":
        rc, out, _ = _run(["cmd", "/c", "ver"])
        if rc == 0:
            info["version_string"] = out.strip()
    return info


def get_cpu_info() -> dict:
    info = {"cores_logical": os.cpu_count()}
    if sys.platform == "linux":
        cpuinfo = Path("/proc/cpuinfo")
        if cpuinfo.exists():
            text = cpuinfo.read_text(errors="replace")
            m = re.search(r"model name\s*:\s*(.*)", text)
            if m:
                info["model"] = m.group(1).strip()
            m = re.search(r"cpu cores\s*:\s*(\d+)", text)
            if m:
                info["cores_physical"] = int(m.group(1))
            m = re.search(r"flags\s*:\s*(.*)", text)
            if m:
                flags = m.group(1).split()
                info["avx2"] = "avx2" in flags
                info["avx512"] = "avx512f" in flags
    elif sys.platform == "darwin":
        rc, out, _ = _run(["sysctl", "-n", "machdep.cpu.brand_string"])
        if rc == 0:
            info["model"] = out.strip()
        rc, out, _ = _run(["sysctl", "-n", "hw.physicalcpu"])
        if rc == 0:
            info["cores_physical"] = _safe_int(out.strip())
    return info


def get_ram_info() -> dict:
    info = {}
    if sys.platform == "linux":
        rc, out, _ = _run(["free", "-b"])
        if rc == 0:
            lines = out.splitlines()
            for line in lines:
                if line.startswith("Mem:"):
                    parts = line.split()
                    info["total_gb"] = _bytes_to_gb(_safe_int(parts[1]))
                    info["available_gb"] = _bytes_to_gb(_safe_int(parts[6]))
                    break
    elif sys.platform == "darwin":
        rc, out, _ = _run(["sysctl", "-n", "hw.memsize"])
        if rc == 0:
            info["total_gb"] = _bytes_to_gb(_safe_int(out.strip()))
        rc, out, _ = _run(["vm_stat"])
        if rc == 0:
            # Approximate free pages
            pages_free = 0
            page_size = 16384
            for line in out.splitlines():
                if "Pages free" in line or "Pages inactive" in line or "Pages speculative" in line:
                    pages_free += _safe_int(re.search(r"(\d+)", line).group(1))
            info["available_gb"] = _bytes_to_gb(pages_free * page_size)
    return info


def get_gpu_info() -> dict:
    gpus = []
    if sys.platform == "linux":
        # Try lspci first
        rc, out, _ = _run(["lspci", "-nnk"])
        if rc == 0:
            for line in out.splitlines():
                if "VGA" in line or "3D controller" in line or "Display controller" in line:
                    gpus.append({"pci_line": line.strip()})
        # ROCm / AMD GPU info
        rc, out, _ = _run(["rocminfo"])
        if rc == 0:
            gpus.append({"rocm": "available", "snippet": out[:500]})
        # Try vainfo for iGPU/VA-API
        rc, out, _ = _run(["vainfo"])
        if rc == 0:
            gpus.append({"vaapi": out[:300]})
    elif sys.platform == "darwin":
        rc, out, _ = _run(["system_profiler", "SPDisplaysDataType"])
        if rc == 0:
            gpus.append({"system_profiler": out[:800]})
    return {"gpus": gpus}


def get_ollama_info(host: str = "http://localhost:11434") -> dict:
    import json
    import urllib.request

    info = {"host": host}
    try:
        with urllib.request.urlopen(f"{host}/api/version", timeout=5) as resp:
            info["version"] = resp.read().decode().strip()
    except Exception as e:
        info["version_error"] = str(e)

    try:
        with urllib.request.urlopen(f"{host}/api/ps", timeout=5) as resp:
            data = json.loads(resp.read().decode())
            info["loaded_models"] = data.get("models", [])
    except Exception as e:
        info["loaded_models_error"] = str(e)

    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=5) as resp:
            data = json.loads(resp.read().decode())
            info["installed_models"] = [m.get("name") for m in data.get("models", [])]
    except Exception as e:
        info["installed_models_error"] = str(e)

    return info


def get_env_info() -> dict:
    keys = [
        "OLLAMA_HOST",
        "OLLAMA_KEEP_ALIVE",
        "OLLAMA_NUM_PARALLEL",
        "OLLAMA_MAX_LOADED_MODELS",
        "OLLAMA_FLASH_ATTENTION",
        "OLLAMA_CONTEXT_LENGTH",
        "OLLAMA_GPU_OVERHEAD",
        "OLLAMA_DEBUG",
        "CUDA_VISIBLE_DEVICES",
        "HIP_VISIBLE_DEVICES",
        "ROCR_VISIBLE_DEVICES",
        "GGML_CUDA_NO_PINNED",
        "GGML_CUDA_ENABLE_UNIFIED_MEMORY",
    ]
    return {k: os.environ.get(k) for k in keys if os.environ.get(k) is not None}


def run(args: dict) -> dict:
    host = args.get("ollama_host", os.environ.get("OLLAMA_HOST", "http://localhost:11434"))
    return {
        "os": get_os_info(),
        "cpu": get_cpu_info(),
        "ram": get_ram_info(),
        "gpu": get_gpu_info(),
        "ollama": get_ollama_info(host),
        "ollama_env": get_env_info(),
    }

