# EXVSIB 立绘编辑器

本地编辑器直接读取：

```text
patch-edit/asset-mapping
patch-edit/all-textures
```

修改图写入：

```text
patch-edit/asset-mapping/replacements
```

## 启动

双击：

```text
start-editor.bat
```

或者在游戏根目录运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\patch-edit\portrait-editor\start-editor.ps1
```

浏览器打开：

```text
http://127.0.0.1:8765
```

不要直接双击 `index.html`。页面需要本地服务读取映射、原图并保存替换文件。

## 功能

- 按局外领航员、局内领航员、战斗人员立绘分类浏览。
- 按 package 或 group 搜索，筛选已修改对象。
- 按映射坐标组合主体、眼睛、嘴部或其他表情状态。
- 在同一表情族中切换所有状态。
- 在“修改后”和“原始”预览间切换。
- 缩放、适合窗口和导出当前组合预览。
- 为主体或任意表情状态选择替换 PNG。
- 删除单张替换图并恢复原图。
- 重新扫描在页面外手动放入的替换文件。

## 替换约束

上传前先在右侧选择明确的主体或表情状态。文件必须满足：

- PNG；
- RGBA 模式；
- 与目标图层宽高完全一致；
- 不移动人物、画布或透明插槽；
- 使用 straight alpha。

服务端不会自动缩放或转换模式。校验失败时不会写入替换目录。

页面上传成功后，修改图使用映射中的 `replacement_path` 保存，因此可以直接交给
后续 DDS 转换和 FHM2D 回封流程。

## 服务参数

默认只监听本机：

```powershell
python .\patch-edit\portrait-editor\server.py --host 127.0.0.1 --port 8765
```

该服务没有账户或网络访问控制，不应使用 `0.0.0.0` 暴露到不可信网络。
