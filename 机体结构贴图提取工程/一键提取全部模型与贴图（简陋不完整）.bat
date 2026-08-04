@echo off
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"
echo ================================================================
echo EXVS 实验性一键提取全部模型与贴图
echo.
echo 该入口只能提取当前已识别的模型和贴图，结果并不完整。
echo 默认扫描 E:\game\ob\25\data\x64\dplcache_release
echo 默认输出到 workspace\全部模型与贴图
echo 可能耗时较长并占用大量磁盘空间。
echo ================================================================
choice /C YN /M "是否继续"
if errorlevel 2 exit /b 0
"%~dp0_runtime\python\python.exe" ".\extract_all_models_textures.py"
echo.
echo 任务结束。请查看 workspace\全部模型与贴图 下的清单。
pause
