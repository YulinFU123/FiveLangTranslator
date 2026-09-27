from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, asdict


@dataclass(slots=True)
class VulkanDevice:
    name: str
    device_type: str = "unknown"
    vendor: str = ""
    api_version: str = ""
    amd_candidate: bool = False


@dataclass(slots=True)
class VulkanReport:
    available: bool
    sdk_present: bool
    loader_present: bool
    devices: list[VulkanDevice]
    details: str = ""

    def to_dict(self): return {**asdict(self), "devices": [asdict(item) for item in self.devices]}


def probe_vulkan() -> VulkanReport:
    tool = shutil.which("vulkaninfo") or shutil.which("vulkaninfo.exe")
    sdk = bool(os.environ.get("VULKAN_SDK"))
    loader = bool(shutil.which("vulkan-1.dll")) or sdk
    if not tool:
        return VulkanReport(loader or sdk, sdk, loader, [], "未找到 vulkaninfo；可安装 Vulkan SDK 获取设备详情")
    try:
        completed = subprocess.run([tool, "--summary"], capture_output=True, text=True, errors="replace", timeout=15, creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)))
        output = completed.stdout + "\n" + completed.stderr
        names = re.findall(r"deviceName\s*=\s*(.+)", output)
        devices = [VulkanDevice(name.strip(), amd_candidate=any(token in name.casefold() for token in ("amd", "radeon"))) for name in names]
        return VulkanReport(completed.returncode == 0, sdk, loader, devices, output[-2000:])
    except Exception as exc:
        return VulkanReport(False, sdk, loader, [], str(exc))
