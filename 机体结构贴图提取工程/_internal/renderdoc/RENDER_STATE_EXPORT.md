# EXVS Render State 自动采集

该流程在现有模型批量导出的基础上，补充 Shader、Shader Reflection、Constant Buffer 解析值、Sampler、Pipeline State 和 Render Target 信息。现有的 `exvs_batch_export.py` 不需要修改。

## 在 RenderDoc 1.45 中运行

1. 使用 qrenderdoc 打开包含 Leos 模型的 `.rdc`。
2. 等待捕获回放完成。
3. 打开 `Window -> Python Shell`。
4. 执行：

```python
exec(open(r'D:\BaiduNetdiskDownload\ib1.0.4\EXVS_Texture_Toolkit\_internal\renderdoc\exvs_render_state_export.py', encoding='utf-8').read())
```

脚本默认采集以下事件：

```text
5528, 5601, 5681, 5689, 5694, 5707, 5934, 5947
```

输出位于：

```text
E:\rendercapture\leos_model\render_state
```

成功结束时 Python Shell 会显示：

```text
EXVS render state complete: E:\rendercapture\leos_model\render_state\render_state_manifest.json
```

每个事件的 `recoverable errors` 表示某个可选字段在当前 RenderDoc API 中不可用。脚本会继续保存其他数据。具体错误记录在对应的 `render_state.json`，不应仅凭错误计数判断整个事件失败。

## 输出内容

```text
render_state\
├─ render_state_manifest.json
├─ shaders\
│  ├─ ShaderStage.Vertex_*.json
│  ├─ ShaderStage.Vertex_*.txt
│  ├─ ShaderStage.Pixel_*.json
│  └─ ShaderStage.Pixel_*.txt
├─ E5528\
│  ├─ render_state.json
│  └─ cbuffers\
│     └─ *.bin
└─ ...
```

`render_state.json` 同时包含：

- VS/PS Shader 和反射文件引用；
- SRV、UAV、Sampler 绑定；
- Constant Buffer 原始字节、反射布局、解析变量值；
- 原始缓冲区的 float4、int4、uint4 视图；
- Rasterizer、Depth/Stencil、Blend、Viewport 和 Scissor；
- Color/Depth Render Target 及资源描述。

## 离线生成材质签名

RenderDoc 导出完成后，在项目目录运行：

```powershell
& '.\_runtime\python\python.exe' '.\_internal\core\renderdoc_render_state_summarize.py'
```

生成：

```text
E:\rendercapture\leos_model\render_state\material_signatures.json
```

材质签名使用以下数据计算：

- Pixel Shader ResourceId；
- 六张 PBR 纹理 ResourceId；
- `nuUVTransformCBuffer`；
- `UpdatePerObject`；
- `vsngCharaGBufferControl`；
- Sampler；
- Rasterizer、Depth/Stencil 和 Blend State。

完全一致的 DrawCall 会自动归入同一个材质状态组。

## 兼容与保真策略

- Constant Buffer 始终优先保存原始 `.bin`；变量解析失败不会丢失数据。
- D3D12 Root Constants 会尝试通过 RenderDoc 的变量接口解析，即使没有普通 Buffer ResourceId。
- Shader 和反射文件按 ResourceId 去重。
- RenderDoc 版本缺少单项 API 时，只在 JSON 中记录该项错误。
- 当前脚本只采集角色材质 DrawCall。GBuffer 到 Lighting/Tone Mapping 的资源依赖追踪将作为独立脚本实现。
