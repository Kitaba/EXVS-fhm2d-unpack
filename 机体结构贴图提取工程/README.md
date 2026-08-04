# 机体结构贴图提取工程

这是从主工具集中隔离出来的机体模型、结构、贴图和渲染逆向工程。目录内的 Python、RenderDoc、
Blender runtime、texconv、测试和文档均为独立副本。运行本工程不会修改上级目录中的旧脚本。

本工程自带 `_runtime/python/python.exe` 和所需 Python 包。用户不需要安装 Python、pip、conda 或
micromamba。不要用系统 Python 运行这些脚本。

首次复制或解压后，可双击 `检查便携运行环境.bat`。它会检查便携 Python、Pillow、核心模块、中文
控制台以及统一工作流入口。运行环境版本见 [`_runtime/RUNTIME_INFO.md`](./_runtime/RUNTIME_INFO.md)。

便携包解决的是命令行 Python 及其包依赖。RenderDoc/qrenderdoc 和 Blender 仍是独立桌面软件：只有
执行捕获导出或 Blender 导入阶段时才需要用户安装对应软件。

## 入口

- 本地中文四步向导：双击 [`启动工作流帮助.bat`](./启动工作流帮助.bat)。网页依次说明 RenderDoc
  捕获导出、FHM2D 自动匹配、Blender 导入编辑和贴图回包；直接拆包与实验性全量扫描不放在主向导中。
- 完整指南：[`_internal/docs/EXVS_COMPLETE_MODEL_WORKFLOW_ZH.md`](./_internal/docs/EXVS_COMPLETE_MODEL_WORKFLOW_ZH.md)
- 统一命令：[`exvs_workflow.py`](./exvs_workflow.py)
- 快速说明：[`MODEL_WORKFLOW.md`](./MODEL_WORKFLOW.md)
- 实验性批量入口：[`一键提取全部模型与贴图（简陋不完整）.bat`](./一键提取全部模型与贴图（简陋不完整）.bat)

## 简易一键提取入口（结果不完整）

双击 `一键提取全部模型与贴图（简陋不完整）.bat`，工具会使用默认路径扫描全部 FHM2D，尽可能提取当前能识别的
模型、包内贴图和 Blender 工程。默认输出到 `workspace/全部模型与贴图/`。

这个入口是方便普通用户尝试的实验性批处理，不代表完整资源枚举：

- 只识别当前已知的 LEKS/HSEM/MODL 模型组合；
- 只提取当前支持的 46XT 贴图记录和 GPU 格式；
- 未知索引布局、跨包资源、纯特效材质和新格式可能缺失；
- 部分包会只有模型、只有贴图或使用回退材质；
- 生成 Blender 工程不等于支持模型网格回包；
- 单个包失败不会阻止其他包继续处理。

命令行可指定其他游戏目录或输出目录：

```powershell
& '.\_runtime\python\python.exe' `
  '.\extract_all_models_textures.py' `
  --source 'E:\game\ob\25\data\x64\dplcache_release' `
  --output 'E:\work\EXVS_批量提取'
```

## 快速检查

双击 `启动工作流帮助.bat`，或在本目录执行：

```powershell
& '.\_runtime\python\python.exe' '.\exvs_workflow.py' --help
```

## 隔离规则

- 不从上级 `_internal` 导入模块；
- qrenderdoc 默认调用本目录 `_internal/renderdoc`；
- Blender 清单引用本目录 `_internal/blender`；
- texconv 使用本目录 `_internal/core/tools/texconv.exe`；
- 命令行程序使用本目录 `_runtime/python/python.exe`；
- 新的工作输出请放在本目录 `workspace/` 或用户明确指定的位置；
- 原始游戏 FHM2D 始终只读，回包输出写入单独目录。

当前支持纹理替换回包。修改后的 Blender 网格重新编码为 HSEM/LEKS 尚未实现。
