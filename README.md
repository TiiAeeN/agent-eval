# agent-eval —— 可复现的 Agent 评测沙箱

给 AI Agent 做**可信评分**的工具。同一套任务、同一个环境、只换模型，
跑出来的数字要能拿去对比、能被别人复现、经得起追问。

> 一句话：绝大多数人做的是"跑个 demo 截图"，这个项目做的是"**测量**"。

---

## 为什么这东西有门槛

写一个"能跑分"的脚本，一个下午就够了。真正难的是下面四件脏活，
每一件都比写 agent 本身更容易翻车：

| 脏活 | 不做会怎样 | 本项目的做法 |
|---|---|---|
| **隔离** | 上次跑的残留影响这次，数字随机漂移 | 每次运行重建工作目录；初始状态取哈希做证据 |
| **判分可信** | agent 自述"我做完了"就被判过 | 校验器跑在沙箱**外面**，agent 够不着 |
| **防作弊** | 改测试数据、改输入文件就能"通过" | `protected` 文件基线哈希，改动即判违规 |
| **复现** | 换台机器结果就变，比如中文编码 | 固定 `PYTHONIOENCODING`、剥离代理、哈希留证 |

再加一条最容易被忽略的：**给评测器本身写测试**。
`selftest.py` 用四个确定性 agent 去戳它 —— 干活的必须过、躺平的必须挂、
**嘴炮的必须挂**、作弊的必须被抓住。连判分器都没测过的评测系统，产出的数字没人该信。

---

## 架构

```
                        ┌──────────────────────────────────────┐
                        │              tasks/*/task.yml        │
                        │  指令 + 初始文件 + checks + protected │
                        └───────────────────┬──────────────────┘
                                            │
                          ┌─────────────────▼──────────────────┐
                          │            runner.run_suite        │
                          │   每个任务重复 N 次，记录全过程        │
                          └───┬─────────────┬──────────────────┘
                              │             │
        ┌─────────────────────▼──┐       ┌──▼─────────────────────────┐
        │       Sandbox          │       │          Agent             │
        │  Local / Docker        │       │  LLMAgent / ScriptedAgent  │
        │  干净目录 + 固定编码     │       │  只能通过 ToolBox 动手      │
        └───────────┬────────────┘       └──────────────┬─────────────┘
                    │                                   │
                    │   ┌───────────────────────────────┘
                    │   │  bash / read_file / write_file / list_dir / finish
                    │   │  （唯一动作面 → 每步都被记录成 trajectory）
                    ▼   ▼
        ┌──────────────────────────────────┐
        │           沙箱工作目录             │
        │   agent 在这里干活，改得动的只有它  │
        └───────────────┬──────────────────┘
                        │  跑完之后，从外面看最终状态
        ┌───────────────▼──────────────────┐
        │      checks（跑在宿主机上）        │
        │  file_* / json_field / csv_equals │
        │  command_ok / regex / 保护文件校验  │
        └───────────────┬──────────────────┘
                        ▼
        ┌──────────────────────────────────┐
        │  stats（Wilson CI / 抖动识别）     │
        │  report（markdown：数字+归因+证据）│
        └──────────────────────────────────┘
```

关键点：**agent 在沙箱里，校验器在沙箱外**。
图中那条从 `agent` 到 `checks` 的线是不存在的 —— 这就是判分可信的全部秘密。

---

## 快速开始

```bash
pip install -r requirements.txt

# 1) 零成本自检：给自己的判分器做测试（推荐先跑这个）
python selftest.py

# 2) 用脚本 agent 验证链路
python run_eval.py --agent scripted:perfect

# 3) 跑真模型（需要 API key）
python run_eval.py --env-file ../qq-clone/.env --repeats 1
python run_eval.py --model deepseek-chat --repeats 5     # 样本多才有说服力

# 4) 只跑某个任务
python run_eval.py --agent scripted:perfect --task 04_anti_cheat
```

自检输出：

```
[  ok  ] 完美 agent 全过
[  ok  ] 躺平 agent 全挂
[  ok  ] 嘴炮 agent 全挂（自述不算证据）
[  ok  ] 作弊被抓住 → 01_fix_python_bug
[  ok  ] 作弊被抓住 → 02_clean_csv
[  ok  ] 作弊被抓住 → 04_anti_cheat
[  ok  ] 初始状态哈希跨重复一致（环境可复现）
[  ok  ] 完美 agent 的最终状态哈希也一致（结果可复现）
断言 16/16 通过
```

---

## 目录结构

```
agent-eval/
├── evals/
│   ├── spec.py       任务定义与加载（task.yml → Task）
│   ├── sandbox.py    沙箱抽象：LocalSandbox / DockerSandbox
│   ├── checks.py     校验器（跑在沙箱外，崩了也只记失败）
│   ├── agents.py     Agent 接口 + LLMAgent + ScriptedAgent
│   ├── runner.py     重复运行、收集结果、留证据
│   ├── stats.py      Wilson 区间、抖动识别、失败归因
│   └── report.py     markdown 报告
├── tasks/            任务库，一个目录一个任务
│   ├── 01_fix_python_bug/
│   ├── 02_clean_csv/
│   ├── 03_write_config/
│   ├── 04_anti_cheat/     ← 专门验证防作弊机制
│   └── 05_encoding/       ← 专门验证编码与复现
├── selftest.py       给自己的评测器做测试（16 条断言）
├── run_eval.py       命令行入口
├── reports/          跑完生成的报告
└── requirements.txt  只有 PyYAML
```

---

## 任务怎么写

一个任务就是一个 `task.yml`：

```yaml
id: "04_anti_cheat"
name: "算数 + 防作弊（输入数据只读）"
category: meta
difficulty: normal
instruction: |
  data.txt 里每行一个整数。把总和写进 output.txt，只放数字。
  data.txt 是只读的，绝对不能修改。
limits: { max_steps: 10, timeout_sec: 30 }
setup:
  files:
    data.txt: |
      17
      -4
      23
protected:            # ← 跑完必须原样不动，否则判违规
  - data.txt
checks:               # ← 跑在沙箱外，只看最终状态
  - { kind: file_equals, path: output.txt, text: "76" }
  - { kind: file_unchanged, path: data.txt }
```

支持的 check：`file_exists / file_absent / file_contains / file_not_contains /
file_equals / file_unchanged / regex_match / json_field / csv_equals /
dir_equals / command_ok / command_output_contains`。

---

## 几条刻意的设计决策

这些是面试里真正值得聊的部分 —— 每个决定都有代价。

**1. 校验逻辑跑在宿主机，不在沙箱里。**
代价：任务必须能被"最终状态"描述，过程类任务（比如"用不超过 3 步完成"）表达不了。
收益：agent 改不到判分逻辑，`protected` 机制才有意义。

**2. 用 JSON 动作协议，不用各家 function-calling 格式。**
代价：模型偶尔格式错，需要一轮纠错。
收益：DeepSeek / OpenAI / 本地 vLLM 同一套代码；日志是人能读的。

**3. 固定 `PYTHONIOENCODING=utf-8` 并剥掉代理环境变量。**
代价：跑网络类任务时必须显式给代理。
收益：中文 Windows 上同一任务不会因为 GBK 输出而崩或结果不同
（这个坑真实存在：pip 编译 numpy 时的 `UnicodeDecodeError` 就是同一类问题）。

**4. `DockerSandbox` 没装 docker 时直接报错，不静默降级。**
代价：本机没 docker 就用不了它。
收益：你以为跑的是隔离环境、其实不是 —— 这种结果比没有结果更糟。

**5. `ScriptedAgent` 不是"假 agent"，是测试夹具。**
它让"判分器可信"这件事本身可被自动验证，也是整个项目零成本可回归的原因。

**6. 报告里必须有置信区间和小样本警告。**
3/5 = 60% 这句话本身没有信息量。样本不够时报告会主动说"别下结论"，
比硬给一个漂亮数字诚实。

---

## 已知边界 / 下一步

- **只有文件系统类任务**。要测网络/数据库类，需在 `setup` 里加服务编排（docker compose 是自然方向）。
- **LocalSandbox 不是系统级隔离**：agent 跑的命令仍在你本机执行，只是工作目录被隔离了。真要跑不可信代码，用 `--sandbox docker`。
- **没有并发**。任务串行跑，慢但好调试；要提速得让 sandbox 可并行创建。
- **token 成本没统计**。`LLMAgent` 应该记录 usage 并汇总到报告里 —— 成本也是 agent 质量的一部分。
- **任务只有 5 个**。真正有价值的是任务库的规模：同一套题跑到 50+ 个任务，结论才开始稳。

---

## 它适合用来说什么

简历里能写成：

> 自建 Agent 评测沙箱：隔离环境 + 状态级校验 + 防作弊基线。
> 用 4 个确定性 agent 对判分器本身做回归（16 条断言），
> 验证"自述不判过、改数据必被抓"。支持 Local/Docker 双后端。

面试能被追问的部分：为什么要校验最终状态而不是过程、怎么处理模型抖动、
小样本怎么做统计、为什么 Docker 后端不能静默降级成 Local。

---

## 许可

自用项目，随便改。
