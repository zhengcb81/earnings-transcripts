# 实际交付包隔离验收操作

这些命令用于实施阶段的测试临时目标。本轮规划未执行。`$laneRoot`为卡内新工区，`$scratch`为本次新建的临时目录；运行前保存初始清单。禁止省略destination而使用全局默认目标。

## CWP：现有setuptools wheel

```powershell
python -m pip wheel --no-index --no-deps --no-build-isolation --wheel-dir "$scratch/wheels" "$laneRoot"
python -m venv "$scratch/venv"
$builtWheels = @(Get-ChildItem -LiteralPath "$scratch/wheels" -Filter 'company_wiki-*.whl')
if ($builtWheels.Count -ne 1) { throw 'Expected one wheel in the new test build directory' }
& "$scratch/venv/Scripts/python.exe" -m pip install --no-index --find-links "$wheelhouse" $builtWheels[0].FullName
```

本机wheelhouse与build依赖需事先可用；缺失记录环境问题，不启动联网安装或把它算产品RED。安装必须使用刚生成wheel的确切路径，不能按同名同版本从共享wheelhouse选到旧包。对该wheel路径加`[catalog]`等声明extra另建环境或明确安装该extra，不拿base缺optional SDK冒产品失败。以非仓库cwd运行11个console入口及processed/statement公开module请求；注入临时配置，加载包内schema/profile，禁止源码PYTHONPATH。新venv只供此测试，结束清理。

## FF：canonical skill installer

在FF lane目录运行：

```powershell
python tools/sync_installs_b3.py --install --dest "$scratch/skills" --json
python tools/sync_installs_b3.py --check --dest "$scratch/skills" --json
```

实际安装根是`$scratch/skills/filing-fetch`。该工具没有`--canonical`，canonical由工具自身所在工区决定；不能从主仓脚本运行却声称测了lane。后续进程从安装根的scripts公开入口启动，cwd为空目录，只给fixture command与测试配置。

## RF：canonical runtime installer

```powershell
python tools/sync_installations.py --canonical "$laneRoot" --destination "$scratch/skills" --apply --json
python tools/sync_installations.py --canonical "$laneRoot" --destination "$scratch/skills" --json
```

实际安装根是`$scratch/skills/revenue-forecast`。该脚本默认目标可指向全局skills，所以必须显式`--destination`；第二条不带`--apply`的命令是只读check，RF工具没有`--check`开关，不能照搬FF的参数。第二次相同同步应无新增写入。保留用户配置的测试副本；不借安装去覆盖生产材料或四项assurance WIP。

## ET与SID：现有脚本runtime，不新造发布系统

两仓本轮未见wheel/setup/安装器。将本仓活动CLI的传递依赖列成显式runtime manifest，再从Git当前候选逐项复制到临时artifact；复制的是可执行代码和必要非秘密资源，不是整仓/测试/原件/输出/.env。manifest与复制文件SHA记录在本卡交接。

- ET至少包含transcript_tool.py、transcript_api.py、transcript_extraction.py、retrieval_runtime.py、retrieval_worker.py、retrieval_budget.py、legacy_projection_comparison.py及本卡新纯合同/settings/protocol；如验证standalone batch，加入scraper及其实际依赖。完整闭包由作者根据当前imports核实，不能把这个最小列举当已穷尽。空cwd用临时artifact内transcript_tool.py绝对路径执行，模块从artifact解析。
- SID以artifact/src保留现有包结构，复制公共CLI/API/adapter/domain/budget/identity等传递依赖。在临时venv或测试launcher只把artifact根加入import path，空cwd执行`-m src.company_wiki_adapter_cli`。允许artifact自身的import路径，禁止指向原checkout或邻仓；不要求新增wheel。
- 安装阶段有provider依赖不意味着纯contracts/supervisor可以import这些实现。依赖方向测试另用import-refusing probe，生产代码不含该guard。

本文件仅规定可复现命令与交付形态，不修改现有生产安装/模型/费用政策，不在每次commit自动部署或测试。
