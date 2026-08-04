# 便携运行环境

- CPython 3.10.11（Windows x64）
- Pillow 12.0.0
- NumPy 1.26.4
- 工程核心模块路径已写入 `python/python310._pth`
- `python/Lib/site-packages/sitecustomize.py` 统一将控制台输出设为 UTF-8

入口 BAT 会直接调用 `python/python.exe`，不会读取用户系统中安装的 Python、pip、conda 或
micromamba 环境。Python 自身许可证位于 `python/LICENSE.txt`；第三方包的许可证和元数据保留在
`python/Lib/site-packages` 对应目录中。

RenderDoc/qrenderdoc 和 Blender 属于独立桌面软件，不包含在此 Python 运行环境中。需要相关阶段时，
用户仍需分别安装它们。
