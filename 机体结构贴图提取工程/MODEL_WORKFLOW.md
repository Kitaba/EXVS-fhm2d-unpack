# EXVS 模型完整工作流

完整中文指南：[`_internal/docs/EXVS_COMPLETE_MODEL_WORKFLOW_ZH.md`](./_internal/docs/EXVS_COMPLETE_MODEL_WORKFLOW_ZH.md)

统一命令入口：[`exvs_workflow.py`](./exvs_workflow.py)

```text
exvs_workflow.py direct     已知 FHM2D：拆贴图、拆模型、生成 Blender 工程
exvs_workflow.py renderdoc  RenderDoc 证据：反向定位源包并生成工程
exvs_workflow.py status     检查可编辑 PNG 的修改与合法性
exvs_workflow.py repack     将纹理工程重新构建为 FHM2D
exvs_workflow.py inspect    检查工作流完成标志
```

请先阅读完整指南中的能力边界和备份要求。当前支持纹理替换回包；Blender 网格重新编码为游戏
HSEM/LEKS 尚未完成。
