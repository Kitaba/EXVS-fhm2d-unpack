# RenderDoc 批量模型数据导出

脚本适配当前安装的 RenderDoc 1.45。它遍历捕获中的 Draw Call，按索引数预筛选，再用 Pixel Shader 的六个资源名识别 EXVS PBR 模型材质。扫描结果写入一个 JSON；导出模式会自动保存顶点流、索引流、常量缓冲和纹理 Mip 0。

## 第一次：只扫描

1. 用 qrenderdoc 打开 `.rdc`，等待回放完成。
2. 打开 `Window -> Python Shell`。
3. 保持 `exvs_batch_export.py` 顶部的 `SCAN_ONLY = True`。
4. 在 Python Shell 执行：

```python
exec(open(r'D:\BaiduNetdiskDownload\ib1.0.4\EXVS_Texture_Toolkit\_internal\renderdoc\exvs_batch_export.py', encoding='utf-8').read())
```

查看 `E:\rendercapture\leos_model\batch_export\draw_manifest.json`。其中 `matches_exvs_pbr6=true` 的事件是绑定了 BaseColor、Normal、Metallic、Roughness、AO、Emissive 六类资源的模型 Draw Call。

## 第二次：批量导出

把脚本顶部改为：

```python
SCAN_ONLY = False
EVENT_IDS = []
```

再次执行同一行命令。每个命中的事件会生成 `E<事件号>` 目录：

- `event.json`：Draw Call、输入布局、资源绑定及各文件关系；
- `vb*.bin`：原始顶点缓冲，POSITION/NORMAL/TANGENT 通常位于 slot 0，UV 通常位于 slot 1；
- `ib*.bin`：当前 Draw Call 实际使用的索引范围；
- `cb*.bin`：VS/PS 常量缓冲，包括 world matrix 和 UV transform；
- `t*.dds`：Pixel Shader 纹理的 Mip 0。

若只想验证 EID 5694，先设为 `EVENT_IDS = [5694]`。确认输出正确后再清空列表批量运行。

## 数据关系

`index buffer -> vertex buffer POSITION/TEXCOORD -> world matrix -> Draw Call texture bindings`

这条关系链能恢复：每个三角形使用图集的哪一块、三角形在模型局部空间的位置、经过每 Draw Call 世界矩阵后的渲染位置，以及该批三角形对应的材质纹理。骨骼动画模型还需要一并解析 blend indices/weights 和骨骼矩阵缓冲；脚本先保留所有绑定顶点流和 VS 常量缓冲，避免在规律确认前丢字段。
