# EXVS 模型自动提取与 Blender 工程生成

流水线假设目标机体使用已确认的 EXVS 六贴图 PBR Shader：

```text
BaseColorMap / NormalMap / MetallicMap /
RoughnessMap / AmbientOcclusionMap / EmissiveMap
```

## 第一步：在 RenderDoc 中一键采集

用 qrenderdoc 打开一帧包含完整机体的 `.rdc`，进入 `Window -> Python Shell`，执行：

```python
EXVS_AUTO_CAPTURE_CONFIG = {
    "capture_root": r"E:\rendercapture\my_model_capture"
}
exec(open(
    r'D:\BaiduNetdiskDownload\ib1.0.4\EXVS_Texture_Toolkit\_internal\renderdoc\exvs_auto_capture.py',
    encoding='utf-8'
).read())
```

该命令自动完成：

1. 扫描帧内所有 DrawCall；
2. 用六个 PBR 绑定识别机体绘制；
3. 轻量读取世界矩阵、纹理 ResourceId、VB/IB 和 Shader；
4. 按空间距离和纹理资源范围划分不同机体；
5. 自动选择每组索引数最多的 DrawCall 作为 anchor；
6. 导出分组内所有 VB、IB、Constant Buffer 和 DDS；
7. 导出 Shader、材质参数、Sampler 和 D3D12 Pipeline State。

默认分组参数：

```python
EXVS_AUTO_CAPTURE_CONFIG = {
    "capture_root": r"E:\rendercapture\my_model_capture",
    "spatial_threshold": 15.0,
    "resource_gap": 512,
    "min_total_indices": 300,
}
```

完成标志：

```text
EXVS auto capture complete: ...\automation\renderdoc_capture_complete.json
```

## 第二步：自动定位 FHM2D 并构建全部机体

在工具目录运行：

```powershell
& 'C:\Users\hujinchang\AppData\Local\micromamba\micromamba.exe' run `
  -p 'D:\mambaenv\manga-translator' python `
  .\_internal\core\exvs_model_pipeline.py `
  'E:\rendercapture\my_model_capture' `
  --fhm-root 'D:\BaiduNetdiskDownload\ib1.0.4\data\x64\dplcache_release' `
  --workers 8
```

流水线自动：

1. 一次扫描所有候选 FHM2D；
2. 按运行时 DDS 的压缩数据哈希和 payload offset 精确匹配；
3. 要求每个机体默认达到 100% 贴图覆盖；
4. 解码各 DrawCall 的顶点、法线、UV 和索引；
5. 使用世界矩阵恢复零件关系；
6. 生成 world OBJ 和 anchor-local OBJ；
7. 提取完整 mip DDS 和可编辑 PNG；
8. 建立 ResourceId、Shader 语义和嵌入贴图名称映射；
9. 读取 EmissiveScale、ShadowReceiver 等材质常量；
10. 为每台机体生成 Blender 工程清单和脚本。

如果已知源包并且只构建一个分组，可跳过全库扫描：

```powershell
& 'C:\Users\hujinchang\AppData\Local\micromamba\micromamba.exe' run `
  -p 'D:\mambaenv\manga-translator' python `
  .\_internal\core\exvs_model_pipeline.py `
  'E:\rendercapture\my_model_capture' `
  --group 1 `
  --package 'D:\path\known.fhm2d'
```

## 第三步：Blender 4.5

每个模型目录都会生成：

```text
model_projects\model_001_E1234\run_in_blender.py
```

在一个空白 Blender 文件的 Scripting 工作区执行：

```python
exec(open(
    r'E:\rendercapture\my_model_capture\model_projects\model_001_E1234\run_in_blender.py',
    encoding='utf-8'
).read())
```

脚本自动导入 OBJ、创建全部 PBR 材质、连接六类贴图、设置通道/色彩空间/发光/背面剔除、打包资源并保存 `.blend`。

## 输出结构

```text
my_model_capture\
├─ batch_export\
├─ render_state\
├─ automation\
│  ├─ scan_manifest.json
│  ├─ model_groups.json
│  └─ renderdoc_capture_complete.json
└─ model_projects\
   ├─ runtime_match\
   ├─ model_001_E...\
   │  ├─ parts\
   │  ├─ assembly\
   │  ├─ texture_project\
   │  ├─ blender_project.json
   │  ├─ model_project.json
   │  └─ run_in_blender.py
   └─ pipeline_summary.json
```

## 当前边界

- 自动化恢复静态网格、UV、法线、零件相对位置和材质。
- 当前 DrawCall 不含骨骼索引、权重和骨骼矩阵，因此不会生成骨骼动画。
- Blender 使用 Principled BSDF 近似游戏的延迟渲染；最终光照、曝光和 Tone Mapping 仍需单独追踪。
- 默认开启 DirectX 法线绿色通道反转；如果凹凸方向相反，在 `blender_project.json` 中将 `invert_normal_green` 改为 `false`。
