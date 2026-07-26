# FHM2D 结构批量扫描

`fhm2d_structure_scan.py` 是只读扫描工具，用来识别不同 FHM2D 包的内部结构。
它不会解包覆盖原文件，也不会修改游戏目录。

## 扫描全部游戏包

```powershell
$env:PYTHONPATH = (Resolve-Path .\patch).Path
python .\patch\fhm2d_structure_scan.py `
  .\data\x64\dplcache_release `
  --output .\patch\fhm2d_structure_scan `
  --workers 8
```

也可以只扫描一个文件：

```powershell
$env:PYTHONPATH = (Resolve-Path .\patch).Path
python .\patch\fhm2d_structure_scan.py `
  .\data\x64\dplcache_release\0x2403C3C8.fhm2d `
  --output .\patch\fhm2d_structure_scan_one
```

## 输出文件

- `summary.json`：数量统计和结构标记汇总；
- `summary.csv`：每个 FHM2D 一行，适合筛选和排序；
- `details.json`：每个文件的完整块、payload、尾部、纹理和错误信息。

## 重点字段

- `declared_payload_size` / `decoded_payload_size`：判断索引记录与解码结果是否一致；
- `trailing_magic`：尾部块的类型，例如 `LMB.`、`HBSS`；
- `payload_blocks` / `trailing_blocks`：payload 与尾部块数量；
- `texture_formats`：扫描到的 BC7 或 RGBA8 纹理；
- `flags`：特殊结构标记；
- `errors`：必须先单独适配的结构，不能直接回包；
- `warnings`：通常是非人物纹理或未识别的资源类型。

当前报告中的 `payload_size_mismatch` 只表示结构需要进一步分析，不代表原文件一定损坏。部分 FHM2D 会把尾部资源、索引块或其他资源块排列在人物纹理之外，需要按结构族分别实现回包策略。
