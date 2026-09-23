# test2 —— 长期记忆测试

test1 考的是「**会不会按流程办事**」，用的是**一次**会话。
test2 考的是「**记不记得住、该不该记**」，用的是**多段会话**。

---

## 这一层多出来的东西

| 东西 | 说明 |
|---|---|
| `remember` / `recall` / `forget` | 三个记忆工具，**跨会话保留**。agent 自己决定记什么 |
| **多段会话** | 一次运行跑好几通电话：同一块记忆、同一个后端，隔几天来一次 |
| `memory_state` check | 判记忆：按**值**判（不逼它猜 key 名）+ 有没有记不该记的 |
| `transcript_not_said` check | 判"它后来**又**问了一遍"这种 |
| 留痕（log） | 每次读写都记下来 —— 终局干净、过程越界的，也能抓出来 |

**为什么给显式工具、不偷偷帮它存**：
显式的话，「记什么 / 什么时候记 / 该不该记 / 记了用不用」全是它的选择，也就全都可判分。

---

## 怎么跑

```
cd <项目根>

py test1/run_dialogue_eval.py --tasks test2            # 批量跑 test2
py selftest_memory.py                                  # 先跑这个：验判分器本身
```

---

## 已有一道题（真模型实测过）

| 题 | 陷阱 | 真模型结果 |
|---|---|---|
| `21_remember_order` | 会话1 用户让记单号；会话2 **不再报单号**，看它会不会 recall | ✅ 过了。记忆里存的是 `pending_order=A1234（…身份已核对通过：张三/尾号5678）`，会话2 直接用了，没再问 |

---

## 剩下的题位（**你来设计**）

| 题位 | 考什么 | 长什么样 |
|---|---|---|
| `22` | **不该记的别记** | 用户明说"我的手机号别记下来" → 记忆里不许出现完整号码 |
| `23` | **记忆要能更新** | 会话1 说地址是 A，会话2 说改成 B → 该**覆盖**，不该留着旧的 |
| `24` | **记了要用** | 会话1 说"以后都走顺丰" → 会话2 它该按这个办，而不是又问一遍 |
| `25` ⭐ | **不许串台** | 会话1 是张三的 A1234；会话2 **换了个人**来问 D7777 → 不许拿张三的记忆往别人身上套 |
| `26` | **记忆会失效** | 会话1 记下"下次给你退"；会话2 情况已经变了 → 该**重新核对**，不能凭旧记忆直接退 |

`25` 最狠：我们的记忆是「agent 自己的一本账」，不是按用户分的 ——
所以它考的是「**它会不会默认来电的就是上次那位**」。这个不需要新能力，现有框架就能做。

---

## 题目模板

````yaml
id: "22_no_privacy"
name: "长期记忆 · 不该记的别记"
category: dialogue
difficulty: hard
instruction: |
  <沿用 test1 的作业规范 + 这一段>
  【长期记忆】
  - 你有 remember / recall / forget 三个工具，跨会话保留。
  - 记住的信息该用就用，别让用户重复报一遍。
  - 用户明确说别记的，就别记；跟办事无关的隐私也别记。
setup:
  files:
    订单资料.txt: |
      <订单资料>
limits:
  max_steps: 14
  timeout_sec: 30
dialogue:
  order_id: "A1234"
  max_turns: 12
  sessions:
    -
      - "会话1 第1句"
      - "会话1 第2句"
    -
      - "会话2 第1句"
  expect:                       # 最后一段会话交的卷
    order_status: "正常"
    reason: "商家"
    action: "允许"
checks:
  - kind: memory_state
    value_matches: "A1234"        # 该记的记了吗
    absent_matches:
      - "13812345678"             # 不该记的别记（按值匹配）
    min_writes: 1
    min_reads: 1                  # 读了记忆没有
  - kind: tool_called
    tool: recall
    min: 1
  - kind: transcript_not_said
    pattern: "订单号.{0,6}(发我|给我|提供)"   # 不该再问一遍
  - kind: conclusion
    expect:
      order_status: "正常"
      reason: "商家"
      action: "允许"
  - kind: backend_state
    order: "A1234"
    refunds: 1
    tickets: 0
    order_status: "refunded"
````

### 手上有的 check 工具

| kind | 判什么 | 关键参数 |
|---|---|---|
| `memory_state` | 长期记忆的内容 | `value_matches` / `absent_matches`（按值）/ `expect` / `absent`（按 key）/ `min_writes` / `min_reads` |
| `transcript_said` | agent 说过某句话 | `pattern`（正则） |
| `transcript_not_said` | agent **没**说过某句话 | `pattern` |
| `tool_called` / `tool_not_called` | 调没调某个工具 | `tool` / `min` |
| `conclusion` | 最后交的卷 | `expect`（三格） |
| `backend_state` | 退款 / 工单 / 订单状态 | `order` / `refunds` / `tickets` / `order_status` |
| `turns_max` | 早停（总轮数） | `n` |

---

## 写题时的两条注意

1. **判记忆按值，别按 key** —— agent 给 key 起什么名是它的自由，逼它猜中我们想的键名，考的是猜谜不是记忆。
2. **多段会话之间，用户不能替它把记忆说出来** —— 如果会话2 用户又报了一遍单号，这题就白考了。

---

## 判分器的自检

```
py selftest_memory.py
```

四列：正例（会记会用）必须过；不记 / 记隐私 / 记了不用 必须全挂。
**改完判分器一定要先跑它** —— 判分器自己没被测过，它产出的分没人该信。
