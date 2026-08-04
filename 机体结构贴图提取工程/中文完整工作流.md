# EXVS 机体捕获、反向定位、拆包、Blender 与纹理回包完整工作流

本文面向希望自行处理新机体的用户。目标是让用户只负责在游戏中把目标机体完整显示并生成一帧
RenderDoc 捕获，后续使用脚本完成证据导出、源包定位、模型与贴图拆包、Blender 工程生成、纹理修改和
FHM2D 回包。

> “截图”必须是 RenderDoc 的 `.rdc` 帧捕获。普通 PNG/JPG 游戏截图不包含 DrawCall、顶点缓冲、
> 纹理 ResourceId 或 Shader 状态，不能用于反向定位源数据。

## 1. 当前能力边界

| 工作 | 状态 | 说明 |
|---|---|---|
| RenderDoc 自动筛选 DrawCall | 支持 | 识别六通道 EXVS PBR 材质并按机体聚类 |
| GPU 网格与世界位置恢复 | 支持 | 导出 VB/IB、世界矩阵并组合静态网格 |
| GPU 贴图与渲染状态导出 | 支持 | DDS Mip 0、Shader、Sampler、常量缓冲 |
| 反向定位 FHM2D | 支持 | 用 GPU DDS 压缩数据哈希和 payload offset 匹配 |
| FHM2D 模型直解 | 支持 | LEKS/HSEM/MODL、OBJ、材质 section、机体结构挂点 |
| 完整 mip 贴图拆包 | 支持 | BC7/BC3/BC4/RGBA8 等已识别 46XT 格式 |
| Blender 4.5 自动工程 | 支持 | 模型组合、独立组件、PBR 节点、贴图和渲染常量 |
| PNG 修改后纹理回包 | 支持 | 保持尺寸、格式、mipmap、嵌入索引和非纹理数据 |
| Blender 网格重新编码为 HSEM | **未支持** | 目前不能把任意修改后的 OBJ/Blend 自动回写为游戏模型 |
| 骨骼动画完整恢复 | 部分支持 | 支持 LEKS 层级和 bind 矩阵；未覆盖全部动画数据 |

当前可靠的替换闭环是“纹理修改 → FHM2D 回包”。模型拆包与 Blender 编辑适合研究、展示、导出和
格式逆向；模型二进制回包仍需未来补齐 HSEM 顶点布局、骨骼权重和资源大小重建。

## 2. 环境准备

工具目录：

```text
D:\BaiduNetdiskDownload\ib1.0.4\EXVS_Texture_Toolkit\机体结构贴图提取工程
```

命令行 Python 必须使用：

```powershell
& '.\_runtime\python\python.exe' <脚本和参数>
```

还需要：

- RenderDoc/qrenderdoc（当前脚本已按 RenderDoc 1.45 验证）；
- Blender 4.5；
- `_internal/core/tools/texconv.exe`，用于 DDS 与可编辑 PNG 互转；
- 游戏的 `dplcache_release` 原始目录；
- 足够磁盘空间。建议每个项目至少预留源 FHM2D 大小的 3 倍。

始终保留原始 FHM2D 和 `.rdc`。不要直接覆盖游戏目录中的唯一副本。

### 实验性简易入口

不进行 RenderDoc 精确定位、只想尽可能扫描全部资源时，可以双击工程根目录的
`一键提取全部模型与贴图.bat`。它会批量筛选当前能识别的模型包、提取包内纹理并生成 Blender
入口。该模式结果明确是不完整的：未知模型布局、新纹理格式、跨包关系和部分特效材质可能缺失。
精确处理单台机体仍应采用下文的 RenderDoc 证据工作流。

## 3. 在游戏中捕获目标机体

1. 让目标机体完整进入画面，避免菜单、其他机体和大面积粒子特效遮挡。
2. 尽量使用静止姿态，保证主体、背包和左右手在同一帧完成绘制。
3. 用 RenderDoc 捕获一帧并保存为 `.rdc`。
4. 用 qrenderdoc 打开该文件，等待回放完成。

若一帧中存在多台机体，脚本会按世界位置和资源范围尝试聚类；第一次使用建议只保留一台目标机体。

## 4. 在 qrenderdoc 中一键导出证据

打开 `Window -> Python Shell`，执行：

```python
EXVS_AUTO_CAPTURE_CONFIG = {
    "capture_root": r"E:\rendercapture\my_model_capture",
    "toolkit_root": r"D:\BaiduNetdiskDownload\ib1.0.4\EXVS_Texture_Toolkit\机体结构贴图提取工程",
    "spatial_threshold": 15.0,
    "resource_gap": 512,
    "min_total_indices": 300,
}
exec(open(
    r"D:\BaiduNetdiskDownload\ib1.0.4\EXVS_Texture_Toolkit\机体结构贴图提取工程\_internal\renderdoc\exvs_auto_capture.py",
    encoding="utf-8"
).read())
```

脚本依次完成：

1. 扫描全部 DrawCall；
2. 识别 BaseColor/Normal/Metallic/Roughness/AO/Emissive 六类绑定；
3. 按空间与资源关系分组；
4. 导出每个命中事件的 VB、IB、CB 和 DDS；
5. 导出 VS/PS、Sampler、Pipeline State 和材质常量。

成功标志：

```text
E:\rendercapture\my_model_capture\automation\renderdoc_capture_complete.json
```

主要输出：

```text
my_model_capture\
├─ automation\model_groups.json
├─ batch_export\E<事件号>\event.json
├─ batch_export\E<事件号>\vb*.bin / ib*.bin / t*.dds
└─ render_state\E<事件号>\render_state.json
```

## 5. 反向定位源 FHM2D 并生成工程

切换到工具目录：

```powershell
Set-Location 'D:\BaiduNetdiskDownload\ib1.0.4\EXVS_Texture_Toolkit\机体结构贴图提取工程'
```

未知源包时运行：

```powershell
& '.\_runtime\python\python.exe' '.\exvs_workflow.py' renderdoc `
  'E:\rendercapture\my_model_capture' `
  --fhm-root 'E:\game\ob\25\data\x64\dplcache_release' `
  --workers 8
```

该阶段用 RenderDoc 导出的 DDS 像素哈希和 payload offset 搜索 FHM2D，并默认要求 100% 运行时贴图
覆盖率。然后恢复网格、世界位置、完整 mip 贴图、材质语义和 Blender 工程。

若已知包，例如 `0xFC492C9B.fhm2d`：

```powershell
& '.\_runtime\python\python.exe' '.\exvs_workflow.py' renderdoc `
  'E:\rendercapture\my_model_capture' `
  --group 1 `
  --package 'E:\game\ob\25\data\x64\dplcache_release\0xFC492C9B.fhm2d'
```

## 6. 已知源包时直接拆包

源包已经确定后，无需再次使用 RenderDoc：

```powershell
& '.\_runtime\python\python.exe' '.\exvs_workflow.py' direct `
  'E:\game\ob\25\data\x64\dplcache_release\0xFC492C9B.fhm2d' `
  --output 'E:\work\0xFC492C9B'
```

该命令生成：

```text
0xFC492C9B\
├─ texture_project\0xFC492C9B\
│  ├─ project.json
│  ├─ textures.csv
│  ├─ dds\
│  └─ png_edit\
├─ models\
│  ├─ bundle_models.json
│  └─ <序号_结构名>\*.obj / mesh.hbss / skeleton.hbss / model.hbss
├─ material_relations.json
└─ blender\
   ├─ blender_project.json
   └─ run_in_blender.py
```

## 7. Blender 4.5 一键导入

在一个空白 Blender 文件中进入 `Scripting`，打开并执行：

```text
<项目>\blender\run_in_blender.py
```

脚本自动完成：

- 导入全部 OBJ；
- 将主体、左右手和明确机体结构件放入 `EXVS_ASSEMBLY_ROOT`；
- 将武器、Bit、形态辅助件和特效放入 `EXVS_STANDALONE_ROOT`；
- 使用 LEKS bind 矩阵和 Blender 坐标系转换定位手部与结构挂点；
- 建立 Principled BSDF 材质；
- 连接 Base Color、Normal、Metallic、Roughness、AO 和 Emissive；
- 设置 sRGB/Non-Color、DirectX 法线绿色通道、发光强度和颜色管理；
- Pack Resources 并保存 `.blend`。

Blender 是对游戏延迟渲染的近似。游戏的环境光、屏幕空间效果、曝光和 Tone Mapping 不保证与
Blender 完全一致。

## 8. 修改贴图

只编辑：

```text
<项目>\texture_project\<包名>\png_edit\*.png
```

必须保持：

- 宽度和高度；
- 文件名；
- Alpha 通道语义；
- 法线、金属度、粗糙度等数据贴图的通道用途。

检查修改状态：

```powershell
& '.\_runtime\python\python.exe' '.\exvs_workflow.py' status `
  'E:\work\0xFC492C9B\texture_project\0xFC492C9B'
```

## 9. 纹理回包

对单个或多个纹理工程执行：

```powershell
& '.\_runtime\python\python.exe' '.\exvs_workflow.py' repack `
  'E:\work\0xFC492C9B\texture_project' `
  --output 'E:\work\0xFC492C9B\repacked'
```

输出示例：

```text
E:\work\0xFC492C9B\repacked\0xFC492C9B.fhm2d
```

回包器会重新生成 DDS/mipmap，将纹理写回原 payload，并保持未修改资源和 FHM2D 结构。首次测试时：

1. 备份游戏原文件；
2. 只替换一个已知包；
3. 进入相同场景检查贴图、透明度、法线和发光；
4. 出现问题立即恢复备份。

## 10. 交付项目的打包

建议保留下列文件，其他大体积中间文件可按需归档：

```text
项目根目录\
├─ capture_reference.txt        # 原 .rdc 路径、游戏版本、捕获说明
├─ model_project.json / pipeline_summary.json
├─ material_relations.json
├─ blender\ 或 run_in_blender.py
├─ models\bundle_models.json
├─ texture_project\<包名>\project.json
├─ texture_project\<包名>\textures.csv
├─ texture_project\<包名>\png_edit\
└─ repacked\<包名>.fhm2d
```

`project.json`、`textures.csv` 和原始纹理工程记录不可丢失，否则无法安全回包。

## 11. 常见问题分流

### RenderDoc 只显示一个 History

History 只说明该像素最后被哪些事件写入。先定位能显示完整网格的 DrawCall，再查看 Pipeline State 的
PS Resources。自动捕获脚本会遍历整帧，不依赖手工逐个导出 History。

### 没有找到模型分组

- 降低 `min_total_indices`；
- 增大 `spatial_threshold`；
- 确认目标机体使用六通道 PBR Shader；
- 换一帧让机体完整显示。

### FHM2D 匹配覆盖率不足

- 目标贴图可能分布在多个包；
- 捕获包含了其他机体或特效；
- DDS 只有运行时变体；
- 可先指定 `--group`，或在确认后临时降低 `--min-package-coverage`。

不要在证据不足时直接回包候选文件。

### 材质显示灰色

- 查看 `blender_project.json` 的 `texture_project_dir`；
- 确认 `png_edit` 或 `dds` 文件存在；
- 查看 `unresolved_material_textures`；
- `section_XX` 通常是尚未恢复名称的占位材质，并不代表未知贴图格式。

### 手部位置异常

当前导入器会把游戏 Y-up/-Z-forward 的 LEKS 矩阵转换到 Blender Z-up/-Y-forward。旧工程需要重新
运行最新的 `run_in_blender.py`，不要沿用此前保存的错误 `.blend`。

### 包内没有纹理

纯 `eff_*` 喷口、弹头、导弹和辅助模型包可能只依赖 Shader 常量或共享特效资源。没有 46XT 记录不
等同于未知格式。

## 12. 工作流检查

查看直解项目的完成标志：

```powershell
& '.\_runtime\python\python.exe' '.\exvs_workflow.py' inspect `
  'E:\work\0xFC492C9B' --package-name 0xFC492C9B
```

任何自动化结果都应以以下证据为准：

- RenderDoc 的事件和资源绑定；
- DDS 像素哈希与 FHM2D payload offset；
- `bundle_models.json` 的解码状态；
- `textures.csv` 的尺寸、格式和 mip 数；
- 回包前后的未修改区间一致性与游戏内验证。
