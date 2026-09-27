from app.hardware.vulkan import VulkanReport


def test_vulkan_report_serializes_devices():
    report = VulkanReport(False, False, False, [], "not installed")
    payload = report.to_dict()
    assert payload["available"] is False
    assert payload["devices"] == []
