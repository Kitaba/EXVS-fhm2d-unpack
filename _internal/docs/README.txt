EXVSIB 便携纹理工具
====================

放置要求
--------
将整个 EXVSIB_Texture_Toolkit 文件夹放到任意 vsac*_Release.exe 的同级目录。
不要只移动其中的 BAT。

首次使用
--------
1. 双击“启动解包工具.bat”。
2. 在页面中执行“一键建立完整立绘库”。
3. 等待扫描、提取、目录、验证和人物映射全部完成。
4. 双击“启动立绘查看器.bat”。

目录
----
_internal     程序、嵌入式 Python、Python 包和 Texconv。
workspace     提取的 PNG、映射、替换图片、日志和后续构建数据。

重要说明
--------
- 原始 data\x64\dplcache_release 下的 FHM2D 只读，不会被覆盖。
- vsac29 使用内置人物映射数据库，不会重复执行坐标分析。
- 其他纹理版本只有在数据库签名不匹配时才自动重新分析。
- 完整 workspace 约需 5 GiB 或更多空间。
- 立绘查看器上传的修改图保存在：
  workspace\asset-mapping\replacements
- 整个工具文件夹可以迁移；工作区使用相对位置。
- 服务仅监听 127.0.0.1，不要修改为 0.0.0.0 暴露到网络。

运行环境
--------
Python 3.10.11 x64 embeddable
Pillow 12.0.0
NumPy 1.26.4
Microsoft DirectXTex Texconv
