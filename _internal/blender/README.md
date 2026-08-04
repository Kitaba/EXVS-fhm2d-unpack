# Blender 4.5 自动导入

在 Blender 4.5 的 `Scripting` 工作区中新建文本，粘贴并执行：

```python
exec(open(r'D:\BaiduNetdiskDownload\ib1.0.4\EXVS_Texture_Toolkit\_internal\blender\exvs_blender_import.py', encoding='utf-8').read())
```

脚本将：

- 导入 `leos_anchor_local.obj`；
- 保持游戏 Y-up 到 Blender Z-up 的坐标转换；
- 创建 `emi / pbr1 / pbr2 / pbr3` 四套 Principled BSDF 材质；
- 连接 BaseColor、Normal、Metallic、Roughness、AO、Emissive；
- Metallic、Roughness、AO 使用 RenderDoc Shader 确认的 R 通道；
- 使用捕获中的单位 UV0 变换；
- 为 `emi` 设置捕获到的 Emissive Scale 9；
- 设置背面剔除、Standard 色彩管理并打包贴图；
- 保存为 `E:\rendercapture\leos_model\analysis_output\leos_assembly\leos_pbr.blend`。

默认启用 `INVERT_NORMAL_GREEN`。如果观察到凹凸方向相反，将脚本顶部该选项改成 `False` 后重新生成材质。

脚本不会删除当前场景中的已有对象。建议在一个空白 Blender 文件中运行，避免材质名称自动追加 `.001`。
