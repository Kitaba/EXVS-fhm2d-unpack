# 任意文件夹 FHM2D 重压缩

`fhm2d_folder_repack.py` 可以处理一个编辑项目目录，也可以递归处理一个包含多个 `project.json` 的目录。

## 单个项目

```powershell
python patch\fhm2d_folder_repack.py D:\work\0x00806795 -o D:\work\repacked --texconv EXVS_Texture_Toolkit\_internal\core\tools\texconv.exe
```

项目目录必须保留 `project.json`、`png_edit`、`dds` 和原始项目记录。用户只需要编辑 `png_edit` 中的 PNG；脚本会按照原始纹理的尺寸、格式、mipmap 和嵌入索引重新编码并重建对应的 FHM2D。

## 批量目录

```powershell
python patch\fhm2d_folder_repack.py D:\work\projects -o D:\work\repacked --texconv EXVS_Texture_Toolkit\_internal\core\tools\texconv.exe
```

输出文件名来自每个项目的 `source_name`。默认输出到输入目录下的 `repacked`，已有输出不会被覆盖，确认后可追加 `--force`。

## 便携版

```powershell
EXVS_Texture_Toolkit\_internal\python\python.exe EXVS_Texture_Toolkit\_internal\core\fhm2d_folder_repack.py <项目目录> -o <输出目录> --texconv EXVS_Texture_Toolkit\_internal\core\tools\texconv.exe
```

该工具只负责重建 FHM2D，不会自动覆盖游戏目录。部署前应先检查输出文件，并使用立绘查看器的“备份并部署”。

