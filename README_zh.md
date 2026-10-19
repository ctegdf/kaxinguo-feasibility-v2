# kaxinguo_feasibility_v1

一套冻结的数据集与协议，用于比较两种 LLM 机器人任务规划架构——**BT**（一体式，一次性输出完整行为树）
与 **STEP**（交互式，逐步决策）——在 **10 个目标 / 48 个场景** 上的表现，并附带一套可用来对接
你自己的模型端点、真正跑一遍评测并汇总结果的新工具。

English version: [README.md](README.md).

## 这是什么

`kaxinguo_feasibility_v1` 是一个手工编写的家庭机器人规划基准：给定语义地图、自然语言指令，以及一组
物理技能（`MOVE_TO`、`OBSERVE`、`FOLLOW`、`SAY`），LLM 需要生成一个能够正确依据尚未观察到的场景事实
分支的计划。两种架构在**同样的** 48 个场景上被比较：

- **BT（一体式）**——模型只被调用一次，必须一次性输出完整行为树，把所有尚未发生的观察结果都表达为
  条件分支；执行器只负责解释这棵树，本版本不重规划。
- **STEP（交互式）**——模型在每个动作/ASK 完成后被再次调用，每次只输出一个决策（`CALL` 一个技能、
  `ASK` 用户，或 `FINISH`）。

本仓库包含：

- 冻结的数据集与协议：`数据集及参考计划/`（场景、参考计划、验证/oracle 数据）与
  `提示词与接口配置/`（系统提示词、JSON Schema、共享语义上下文、发布配置）。
- 协议实现代码，原始内部发布与本次打包共用同一份：`运行代码与说明/{adapters,protocol,
  observation_render,generate_schemas,build_reference_plans,verify_release}.py`。
- **为本次公开打包新增的工具**，用于真正针对一个模型跑协议：`运行代码与说明/live_runner.py`
  （针对 OpenAI 兼容端点或 mock 端点运行 episode）、`运行代码与说明/mock_server.py`
  （只提供两条固定的、手工编写的演示回复——不依赖任何模型或网络）、以及
  `运行代码与说明/summarize_results.py`（把一个目录下的运行轨迹汇总成 CSV 表格）。

## 范围与未验证事项——下结论之前请先读这一节

本仓库自带的离线回归检查（`运行代码与说明/verify_release.py`）只验证协议实现与手工编写的 fixture
之间是否自洽。根据已提交的 `运行代码与说明/verification_report.json`：

```
"scope": "FROZEN_PROTOCOL_AND_HAND_AUTHORED_FIXTURE_REGRESSION"
"unique_goals": 10, "scenarios": 48
"reference_data_cases_passed": 48
"step_adapter_replay_cases_passed": 48
"hand_authored_bt_replay_cases_passed": 48
"model_called": false
"model_planning_accuracy": null
"vllm_backend_schema_acceptance_tested": false
"real_robot_or_vision_tested": false
"general_free_text_semantic_scorer_implemented": false
```

直白地说：

- **已验证**：协议的结构/分支规则，以及 48 个手工编写的 fixture，彼此内部一致，能通过 adapter 与
  解释器正确回放。这是针对作者自己编写的 fixture 的**回归检查**，不是模型评测。
- **本仓库没有任何内容验证过的事项**：没有任何真实 LLM 被调用过（`model_called: false`）；
  没有涉及任何真实机器人或视觉系统——每个场景的感知都来自一个合成的语义 oracle，而不是图像
  （`real_robot_or_vision_tested: false`）；也没有任何后端被确认真正接受这个协议所依赖的
  `structured_outputs` 请求扩展（`vllm_backend_schema_acceptance_tested: false`）。
- **这不是一个通用的自动语义评分器。** 这里没有自由文本评判器
  （`general_free_text_semantic_scorer_implemented: false`）。`summarize_results.py` 只能判断一条
  轨迹是否在结构上完成（`outcome: "FINISHED"`），**永远不会**自动把一条轨迹标记为
  `HUMAN_CONFIRMED_PASS` 或 `HUMAN_CONFIRMED_FAIL`——必须先由人工为每条轨迹填写六项布尔评审标准
  （见下文第 5 步）。单纯的 `FINISHED` 不等于语义上的通过。
- **忠实的同义改写也可能在到达人工评审之前就被判为失败。** 当协议要求某个 `ASK` 之前必须先有一次
  口头汇报时（`validation_data.json` 隐藏场景里的 `required_reports` 规则），冻结的 oracle
  检查的是"固定 `summary` 原句是否作为子串出现在已说出的话里"，而不是语义是否等价。模型如果用不同
  措辞忠实复述了同一个事实，会得到 `ENVIRONMENT_REJECTED` → `STRUCTURAL_FAIL`，完全没有机会进入
  `human_review.criteria`。这是冻结协议/oracle 本身继承下来的局限
  （`运行代码与说明/verify_release.py` 的 `SemanticOracle`，本包未对其做任何修改）；如果你在一个
  看起来推理合理的运行里看到 `QUESTION_BEFORE_REQUIRED_REPORT`，先去核对模型的原话措辞，不要急着
  认定它推理出错。

## "OpenAI 兼容"——更准确的说法

`live_runner.py` 发送的是标准 Chat Completions 请求（`messages`、`temperature`、`max_tokens`、
`stream`），但每个请求还会附带一个 `structured_outputs` 字段，携带本协议的输出 JSON Schema
（见 `adapters.py:make_request`）——这是一个 **vLLM 特有的请求扩展**，文档链接记录在
`提示词与接口配置/release_config.json` 的 `backend_request_documentation` 字段中：
https://docs.vllm.ai/en/latest/features/structured_outputs/ 。这**不属于**厂商中立的 OpenAI API
标准表面。

如果你的后端不认识 `structured_outputs`，可能出现两种情况：直接拒绝请求（响亮且安全——你会看到
`TRANSPORT_ERROR` 或 `STRUCTURAL_FAILURE`，可以主动选择退路）；或者悄悄忽略这个未知字段、不带
schema 约束地正常回答（安静，也更危险——你实际上是在跑一个不受约束的普通对话补全，却仍把它记录为
与 schema 约束运行相同的实验条件）。如果你打算对接一个非 vLLM 或非 vLLM 兼容的后端，请**在相信结果
之前**先确认它是否支持这个扩展（或等价机制），并明确记录你处于哪种情况，而不要让约束悄悄失效。

## 从零开始

### 1. 安装

```bash
python3 -m pip install -r requirements.txt
```

唯一的必需依赖是 `jsonschema==4.26.0`。`live_runner.py` 的 `--transport sdk` 路径会延迟导入
`openai` 包，但这**不是**必需依赖——默认的 `--transport http` 只用标准库（`urllib`）。

### 2. 离线协议/fixture 回归（无网络、无模型）

```bash
python3 运行代码与说明/verify_release.py
```

这会把 48 个手工编写的 fixture 重新跑一遍 adapter 与解释器。stdout 只打印上面引用的汇总数字
（成功时退出码为 0）。脚本能写进 `运行代码与说明/verification_report.json` 的 `case_results` 列表
的每一条记录都是 `PASS`——没有 `FAIL` 这个取值：一旦某条不匹配，会直接抛出未捕获的异常并立刻终止
脚本，根本不会走到"追加这条记录"那一步。如果哪里不对，应该看报错堆栈和非零退出码，而不是看报告
本身（报告只会从出问题的那条开始缺失后续记录，不会标出哪条失败）。它不调用任何模型，也不能证明
任何模型质量——只能证明协议与 fixture 彼此一致。

### 3. Mock 走查——不需要任何模型访问

`mock_server.py` 提供两条固定的、手工编写的演示回复（BT 用 `episode_001`，STEP 用 `episode_016`），
响应中明确带有 `mock_notice` 字段，确保轨迹永远不会被误认为真实模型运行。在一个终端启动：

```bash
python3 运行代码与说明/mock_server.py --bt-test-id episode_001 --step-test-id episode_016
```

然后在另一个终端，分别用每个架构对应的 test id 来跑。这里的 `--out-dir` 指向一个临时位置，而不是
默认的 `运行结果/mock_runs/`，这样你自己的运行就不会和已经提交在那里的两条示例混在一起（省略它会
发生什么，见 `运行代码与说明/各阶段预期输出.md` 第 4 阶段）：

```bash
python3 运行代码与说明/live_runner.py --mock --architecture bt --test-id episode_001 \
  --model mock-model --endpoint http://127.0.0.1:8800 --out-dir /tmp/kaxinguo_mock_walkthrough

python3 运行代码与说明/live_runner.py --mock --architecture step --test-id episode_016 \
  --model mock-model --endpoint http://127.0.0.1:8800 --out-dir /tmp/kaxinguo_mock_walkthrough
```

注意：`--architecture both` 确实可以配合单个 `--test-id` 使用，但它的意思是"用两种架构各跑一遍**同
一个**测试用例"，不是"跑两个不同的测试用例"。mock server 只把 `episode_001` 配置为 BT 演示、
`episode_016` 配置为 STEP 演示（两者 `task_id` 不同），所以要把它们配成一组必须像上面那样分两次调用。
对接真实端点时（见第 4 步），`--architecture both --test-id <id>` 才正是你想要的用法——让你的模型用
两种架构各解一遍同一个用例。

这就是你会真正得到的结果——两条真实的、已提交的示例轨迹就存放在 `运行结果/mock_runs/`，正是按上述
方式生成的：两者的 `outcome` 都是 `"FINISHED"`，BT 那条只用了 1 次模型调用（一次性出完整计划），
STEP 那条用了 6 次（每个决策一次调用，包括其中的 `ASK`），而 `human_review.criteria` 整块都是
`null`——没有任何内容被自动标记为通过或不通过。

### 4. 对接你自己的端点

把 API key 导出到 `--api-key-env` 指定的环境变量名下（默认 `OPENAI_API_KEY`）——**绝不要**写在命令行
参数里：

```bash
export OPENAI_API_KEY=sk-...

python3 运行代码与说明/live_runner.py --architecture both --test-id episode_001 \
  --model <你的模型名> --endpoint https://your-endpoint/v1
```

`--endpoint` 必须是 `http(s)://` 地址，且不含用户名密码、查询串或 fragment——除此之外都会被拒绝
（见 `completion_endpoint()`）。路径部分比较宽松：裸 host、`.../v1`、自定义路径，或者已经写全的
`.../v1/chat/completions`，都能接受；无论你写哪种，它都会被规范化成以 `/v1/chat/completions` 结尾
（已经以 `/v1` 结尾就补上 `/chat/completions`，已经以 `/chat/completions` 结尾就保持不变，否则补上
完整的 `/v1/chat/completions`）。单个 test id 的结果看起来正常之后，再跑全量：

```bash
python3 运行代码与说明/live_runner.py --architecture both --all \
  --model <你的模型名> --endpoint https://your-endpoint/v1
```

轨迹默认写到 `运行结果/live_runs/`（可用 `--out-dir` 覆盖）。

### 5. 汇总与人工评审

```bash
python3 运行代码与说明/summarize_results.py --runs-dir 运行结果/live_runs
```

`--include-mock` 默认关闭，正是为了不让 mock 走查的数据悄悄混进真实指标；只有当你确实想单独汇总
mock 走查本身时才加上它，例如：

```bash
python3 运行代码与说明/summarize_results.py --runs-dir 运行结果/mock_runs --include-mock
```

这个例子汇总的是已经提交在 `运行结果/mock_runs/` 的那两条示例轨迹（正是按第 3 步的方式生成的）。
如果你自己按第 3 步跑过、用的是它示例里的 `--out-dir /tmp/kaxinguo_mock_walkthrough`，应该把
`--runs-dir` 指向那里去汇总你自己跑出来的结果，而不是仓库自带的示例——两种情况的区别见
`运行代码与说明/STAGE_OUTPUTS.md` 第 8 阶段。

这会写出四张 CSV（`scenarios.csv`、`task_pass_rates.csv`、`costs.csv`、`goals.csv`）。
`scenarios.csv` 里的 `classification` 列取以下四个值之一：

| 取值 | 含义 |
|---|---|
| `STRUCTURAL_FAIL` | `outcome != "FINISHED"`——episode 在结构上就没有正常走到完成。 |
| `PENDING_HUMAN_REVIEW` | 结构上已完成，但 `human_review.criteria` 六项里至少有一项还没填。 |
| `HUMAN_CONFIRMED_PASS` | 结构上已完成，且人工把六项全部标为 `true`。 |
| `HUMAN_CONFIRMED_FAIL` | 结构上已完成，六项全部填写完毕，但至少有一项是 `false`。 |

在人工打开每条轨迹 JSON、手动填写 `human_review.criteria` 的六个布尔字段之前，不会有任何内容变成
`HUMAN_CONFIRMED_PASS` 或 `HUMAN_CONFIRMED_FAIL`。这六个字段是：`structure_and_arguments`、
`required_observation_evidence`、`correct_branch`、`all_required_actions`、`constraints_respected`、
`completion_at_correct_time`——对应本协议为一个场景定义的六项通过标准。整条流水线里没有任何自动
评判器。

## 仓库结构

| 路径 | 内容 |
|---|---|
| `数据集及参考计划/` | 冻结的数据集：公开测试输入、隐藏的验证/oracle 数据、手工编写的参考计划、覆盖率汇总。 |
| `提示词与接口配置/` | 冻结的协议表面：系统提示词、四种消息形态的 JSON Schema、共享语义地图/上下文、`release_config.json`。 |
| `运行代码与说明/` | 协议实现（`adapters.py`、`protocol.py`、`observation_render.py`、`generate_schemas.py`、`build_reference_plans.py`、`verify_release.py` 及其 `verification_report.json`）、新增的评测工具（`live_runner.py`、`mock_server.py`、`summarize_results.py`）、分阶段预期输出文档，以及发布清单。 |
| `运行结果/mock_runs/` | mock 走查的真实已提交示例输出——两条轨迹 JSON 加四张汇总 CSV。 |
| `.github/workflows/` | 每次 push/PR 都会跑一遍离线回归和完整 mock 走查的 CI。 |

各目录的中文名称是数据集原有的命名，本次发布原样保留；本 README 逐一解释其用途，而不建议重命名。

## 许可

本仓库采用**两套独立许可证**——只放一份根目录 `LICENSE` 会造成误导，因为代码和数据的法律内容
本就不同：

| 内容 | 许可证 | 文件 |
|---|---|---|
| `.py` 源代码（协议实现 + 评测工具） | MIT | [`LICENSE`](LICENSE) |
| 数据集、提示词、Schema、配置、文档 | CC BY 4.0 | [`LICENSE-DATA`](LICENSE-DATA) |

## 引用本次发布

见 [`CITATION.cff`](CITATION.cff)。其中 `authors` 与 `date-released` 字段为占位符，等待维护者
确定——请不要从模板中臆测出一个名字。

## 更多

- [`CONTRIBUTING.md`](CONTRIBUTING.md)——如何反馈数据集/协议问题（以新版本形式，而非就地修改）、
  如何为新增工具提交改动。
- [`CHANGELOG.md`](CHANGELOG.md)——本次打包发布版本，与冻结的 `kaxinguo_feasibility_v1` 协议/数据
  版本（两条独立的版本轴线）。
- [`运行代码与说明/各阶段预期输出.md`](运行代码与说明/各阶段预期输出.md) /
  [`运行代码与说明/STAGE_OUTPUTS.md`](运行代码与说明/STAGE_OUTPUTS.md)——上面 8 个阶段分别该期待
  什么输出，附真实引用的程序输出。
