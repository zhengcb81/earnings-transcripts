# ROOT ET测试依赖收尾：独立、断网、无密钥

状态：**PASS，源码冻结，等待ROOT正常提交/推送**。此节点仅修改 tests/test_common.py 和 tests/test_scraper.py；生产*.py/config、原228项和10gold对应源码完全不动。

## 为什么是测试依赖失真

原cfg的base_dir=.依赖运行cwd和真实transcripts目录，干净checkout可失败，换cwd还可能读另一个项目的数据湖。工厂测试没有给key却要求真实LLM可用。Google测试调用真实HTTP，但available失败就不做任何输出断言而成功空过。旧实际基线与交付均3FAIL+1SKIP；这些原日志继续保留，没有被忽略或变成“本轮全绿”的一部分。

首个新RED分别在CWP和ET cwd复现cfg外指、以及fake available=False后0次translate仍成功；没有网络调用。原两测试Git字节也已封存。

## 改法和更强断言

- scraper cfg使用独占tmp_path，通过真实render_original生成3份正确头部TXT（MSFT两期、SNOW一期）；原company/key/transcripts路径open/iterdir/glob访问会立即使测试失败。已存在sample/sentinel在teardown验证字节不变。
- 不再因本机没原件跳过；真实读取头部、原文、Characters/source和两个季度summary/非零字符/URL。缺文件测试也位于owned目录。
- common cfg基目录同样Temp，模板配置原样。四组虚构JSON key验证实际factory优先级，只有availability边界mock，不构建LLMclient、不请求任何供应商。
- 整个translator测试类断网。Google真实包装器使用受控fake backend，构造、available、translate输出和精确两次调用均必断言，不再按网络状态空过。

## 结果与分母

**99 PASS /3.07s，0SKIP** = 两完整文件65项 +此前冻结ROOT30项 +作者纯行为4项。原四个风险selector展开后 **7 PASS /1.28s，0SKIP**，从另一cwd验证。Ruff(--no-cache)/diffcheck通过。

首轮窄回归98PASS/1测试期望错误与6PASS/1跨cwd错误、Ruff原未用导入都保留first收据；新加强断言错误地把头部后2空行算进Characters，实际renderer正文82字符，现按真实正文strip头部分隔空白比较，未修改生产解析/渲染。

全部Temp删除、sentinel保留、源/配置SHA守护通过。0API/网络/费用，无Git/安装/生产写。没有重复228大套件；原228/10gold的源码SHA已再次核对相同。

## 实际CI范围

对 examined HEAD 的git ls-tree确认没有.github/workflows、requirements*/pyproject或tests/conftest；不是稀疏检出误判。本项目此HEAD无可引用的远端CI job，不宣称不存在的CI成功，也不加一层门禁。ROOT正常提交/推送，附实际本地测试收据即可。

精确最终两文件SHA、原RED、新分母、清理及归档逐字节读回结果见 root-reception-test-hygiene.json；旧测试和节点收据见同名receipts.zip。
