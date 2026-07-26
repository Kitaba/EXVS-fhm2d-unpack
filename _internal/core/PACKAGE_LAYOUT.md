# all-textures 包目录布局

`replan_texture_packages.py` 使用 `texture_layout.py` 中的统一变量，将已识别和未识别包放入：

```text
all-textures/packages/
  outgame_navigator/<package>/
  ingame_navigator/<package>/
  combat_portrait/<package>/
  pending/<package>/
```

分类依据是 `asset-mapping/groups.csv` 的已验证类别，而不是仅凭文件名。当前版本统计为：

- 局外领航员：115 个包
- 局内领航员：52 个包
- 战斗人员立绘：357 个包
- 待定：1,138 个包

脚本会自动更新 `inventory/textures.csv`、`asset-mapping/groups.csv`、`layers.csv` 和组合 JSON 中的 `source_png`。执行 `--apply` 前会在 `asset-mapping/replan-backups/<时间戳>` 保存关键清单副本，并生成 `replan-manifest.json`。

预览分类而不修改文件：

```powershell
python patch\replan_texture_packages.py --all-textures <all-textures> --mapping <asset-mapping>
```

正式执行：

```powershell
python patch\replan_texture_packages.py --all-textures <all-textures> --mapping <asset-mapping> --apply
```

