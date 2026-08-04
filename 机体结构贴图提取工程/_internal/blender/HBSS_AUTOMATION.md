# HBSS 模型到 Blender 4.5

## 总装原则

Blender 工程把资源分为两个互不混淆的根节点：

- `EXVS_ASSEMBLY_ROOT`：机体本体、左右手、明确属于机体结构的翼部；
- `EXVS_STANDALONE_ROOT`：枪械、军刀、浮游组件、形态辅助网格和特效等独立组件。

机体结构件使用主体 LEKS 的 model-space bind 矩阵定位。独立组件保留自己的局部坐标，
不会自动挂到手腕或机体骨骼上，也不会为了视觉效果猜测位置。

Collection 结构：

- `EXVS/Base`：机体主体；
- `EXVS/Base/Attachments`：已经由 LEKS 挂点定位的机体结构件；
- `EXVS/Hands/<状态>/<Left|Right>`：左右手型，默认仅显示 NGR；
- `EXVS/Standalone/Components`：独立武器和其他组件，默认隐藏；
- `EXVS/Standalone/Forms`：形态或辅助网格，默认隐藏。

## 单个包导入

每个成功导出的包都有：

```text
packages/<包名>/blender/blender_project.json
packages/<包名>/blender/run_in_blender.py
packages/<包名>/import_complete_to_blender.py
```

在 Blender 4.5 的 Scripting 工作区打开并运行 `run_in_blender.py`。脚本会导入 OBJ、建立
材质、创建两个根节点、应用机体挂点矩阵，并按清单保存 `.blend`。

包根目录的 `import_complete_to_blender.py` 是相同功能的便捷入口。每个
`models/<序号_结构名>/` 下还会生成 `import_this_structure_to_blender.py`，只导入当前结构，
适合独立检查武器、手型或其他组件。单结构脚本会把 `.blend` 保存到自身目录，不会覆盖
整包的总装 `.blend`。

对象上会保存以下属性：

- `exvs_part`：资源名称；
- `exvs_part_kind`：`body`、`hand`、`body_attachment` 或 `standalone`；
- `exvs_placement_mode`：`model_space`、`bone_bind` 或 `standalone_local`；
- `exvs_placement_resolved`：机体总装定位是否已确认；
- `exvs_attachment_bone`：存在明确机体挂点时的骨骼名称。

## 全量重新生成 Blender 清单

修改分类或组合规则后，不必重新解包模型：

```powershell
& '.\_runtime\python\python.exe' `
  '.\_internal\core\hbss_rebuild_blender_projects.py' `
  'E:\game\ob\25\model_extract_all_v2' --workers 8
```

汇总写入 `E:\game\ob\25\model_extract_all_v2\assembly_manifest.json`。

## 贴图与材质

当包目录存在 `textures_extracted/<包名>/textures.csv` 时，工程生成器会自动绑定该纹理项目。
Blender 优先读取 `png_edit` 中的可编辑 PNG；PNG 不存在时直接读取提取出的 DDS。材质脚本会自动
建立 Base Color、Normal、Metallic、Roughness、AO 和 Emissive 节点，并沿用已恢复的渲染参数。

需要一次完成“提取贴图 + 生成全部 Blender 入口”时，在重建命令末尾加入
`--extract-textures`。该操作会输出大量 DDS，因此默认不会自动启用：

```powershell
& '.\_runtime\python\python.exe' `
  '.\_internal\core\hbss_rebuild_blender_projects.py' `
  'E:\game\ob\25\model_extract_all_v2' --workers 8 --extract-textures
```
