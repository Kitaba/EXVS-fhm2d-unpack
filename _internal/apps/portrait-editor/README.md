# EXVSIB 立绘编辑器

> 目前只有解包功能是比较完整可用的。
> 立绘预览、图片替换和 FHM2D 反向回包仍在持续整理。
> 其他功能和实验性代码请当作代码参考使用。

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
http://127.0.0.1:17865
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

上传前先在右侧选择明确的主体或表情状态。支持 PNG、JPEG、WEBP、BMP 和 DDS。
服务端会自动解码为 RGBA PNG；尺寸不一致时按比例缩放并使用透明像素补齐到目标尺寸。

仍需注意：

- 不要改变人物在画布中的位置或透明插槽；
- JPEG 没有 Alpha，导入时会补充不透明 Alpha；
- DDS 只作为输入格式，回包时仍使用原 FHM2D 纹理的格式和 mipmap 元数据；
- 如果出现 `payload size mismatch`，应检查原纹理格式、mipmap、数组层数和切片结构。

页面上传成功后，修改图使用映射中的 `replacement_path` 保存，因此可以直接交给
后续 DDS 转换和 FHM2D 回封流程。

## 回包边界

当前回包流程面向已经进入 `vsac29` 人物映射数据库的图片 FHM2D。已覆盖 BC7、RGBA8、混合格式、已识别尾部块和批量索引表变体；但目前完成的是结构级验证和代表性完整流程测试，并非每个包都已经逐个进行游戏内验证。

未分类的音频、特效、杂项或结构扫描报告标记为错误的 FHM2D，不应直接使用人物图片回包流程。部署前请先备份原始文件，并优先只测试一个包。

## 服务参数

默认只监听本机：

```powershell
python .\patch-edit\portrait-editor\server.py --host 127.0.0.1 --port 17865
```

该服务没有账户或网络访问控制，不应使用 `0.0.0.0` 暴露到不可信网络。
