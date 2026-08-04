"""One-command RenderDoc capture scan, clustering, mesh export, and state export.

Run in qrenderdoc with a capture open::

    EXVS_AUTO_CAPTURE_CONFIG = {"capture_root": r"E:\rendercapture\my_capture"}
    exec(open(r"D:\BaiduNetdiskDownload\ib1.0.4\EXVS_Texture_Toolkit\_internal\renderdoc\exvs_auto_capture.py", encoding="utf-8").read())
"""

import json
import os
import shutil
import sys


_config = globals().get("EXVS_AUTO_CAPTURE_CONFIG", {})
CAPTURE_ROOT = _config.get("capture_root", r"E:\rendercapture\model_capture")
SPATIAL_THRESHOLD = float(_config.get("spatial_threshold", 15.0))
RESOURCE_GAP = int(_config.get("resource_gap", 512))
MIN_TOTAL_INDICES = int(_config.get("min_total_indices", 300))
MIN_INDEX_COUNT = int(_config.get("min_index_count", 1))

TOOLKIT_ROOT = os.path.abspath(_config.get(
    "toolkit_root", r"D:\BaiduNetdiskDownload\ib1.0.4\EXVS_Texture_Toolkit"
))
BATCH_SCRIPT = os.path.join(TOOLKIT_ROOT, "_internal", "renderdoc", "exvs_batch_export.py")
STATE_SCRIPT = os.path.join(TOOLKIT_ROOT, "_internal", "renderdoc", "exvs_render_state_export.py")
CORE_DIR = os.path.join(TOOLKIT_ROOT, "_internal", "core")
BATCH_ROOT = os.path.join(CAPTURE_ROOT, "batch_export")
AUTOMATION_ROOT = os.path.join(CAPTURE_ROOT, "automation")
STATE_ROOT = os.path.join(CAPTURE_ROOT, "render_state")


def exec_script(path):
    with open(path, "r", encoding="utf-8") as stream:
        exec(compile(stream.read(), path, "exec"), globals(), globals())


os.makedirs(AUTOMATION_ROOT, exist_ok=True)
print("EXVS auto capture phase 1/3: lightweight scan")
EXVS_BATCH_CONFIG = {
    "output_dir": BATCH_ROOT,
    "scan_only": True,
    "min_index_count": MIN_INDEX_COUNT,
    "event_ids": [],
    "save_texture_dds_mip0": False,
}
exec_script(BATCH_SCRIPT)

scan_manifest = os.path.join(BATCH_ROOT, "draw_manifest.json")
saved_scan_manifest = os.path.join(AUTOMATION_ROOT, "scan_manifest.json")
shutil.copy2(scan_manifest, saved_scan_manifest)

if CORE_DIR not in sys.path:
    sys.path.insert(0, CORE_DIR)
from renderdoc_model_cluster import build_report

groups_path = os.path.join(AUTOMATION_ROOT, "model_groups.json")
report = build_report(
    __import__("pathlib").Path(saved_scan_manifest),
    __import__("pathlib").Path(BATCH_ROOT),
    SPATIAL_THRESHOLD,
    RESOURCE_GAP,
    MIN_TOTAL_INDICES,
)
with open(groups_path, "w", encoding="utf-8") as stream:
    json.dump(report, stream, ensure_ascii=False, indent=2)
events = sorted({event for group in report["groups"] for event in group["events"]})
if not events:
    raise RuntimeError("EXVS auto capture found no model groups; inspect {}".format(groups_path))
print("EXVS auto capture: groups={} selected_draws={}".format(report["group_count"], len(events)))
for group in report["groups"]:
    print("  {} anchor=E{} events={}".format(
        group["name"], group["anchor_event"], ",".join(str(item) for item in group["events"])
    ))

print("EXVS auto capture phase 2/3: full model buffer/texture export")
EXVS_BATCH_CONFIG = {
    "output_dir": BATCH_ROOT,
    "scan_only": False,
    "min_index_count": 1,
    "event_ids": events,
    "save_texture_dds_mip0": True,
}
exec_script(BATCH_SCRIPT)

print("EXVS auto capture phase 3/3: shader/material state export")
EXVS_RENDER_STATE_CONFIG = {
    "output_dir": STATE_ROOT,
    "event_ids": events,
    "export_stages": ("Vertex", "Pixel"),
    "save_shader_disassembly": True,
    "save_constant_buffer_raw": True,
}
exec_script(STATE_SCRIPT)

complete = {
    "schema": "exvs-renderdoc-auto-capture/v1",
    "capture_root": os.path.abspath(CAPTURE_ROOT),
    "batch_root": os.path.abspath(BATCH_ROOT),
    "render_state_root": os.path.abspath(STATE_ROOT),
    "model_groups": os.path.abspath(groups_path),
    "group_count": report["group_count"],
    "event_ids": events,
}
complete_path = os.path.join(AUTOMATION_ROOT, "renderdoc_capture_complete.json")
with open(complete_path, "w", encoding="utf-8") as stream:
    json.dump(complete, stream, ensure_ascii=False, indent=2)
print("EXVS auto capture complete: {}".format(complete_path))
