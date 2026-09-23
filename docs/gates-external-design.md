# 在 GitHub 之外跑完整门禁集

> 状态：**current**。第 1 刀（本地后端 + 证据包）与第 2 刀（签名、`refs/notes/gates`、`verify-external.yml` 回写 commit status）已落地；release 守卫接受外部证据、crun 后端、自动触发是后续刀，记在「不做的」。`verify-external.yml` 尚未在真实 GitHub 上跑过：`workflow_dispatch` 要求工作流先在默认分支上，首次运行在合并之后。

## 要解决的问题

维护者有时要在 GitHub 之外对某个提交跑一遍完整门禁：托管 runner 排队或宕机、想在推之前先验、或者要在别的机器上复核一次。过去的做法是从 `gates.yml` 里手抄命令逐条跑，跑的是子集，跑完也拿不出东西证明跑了什么。

本刀交付的是 `scripts/gates-external/`：

- `run.sh --sha <sha> --backend local [--jobs N] --out <dir>`：在给定提交的树上执行 `gates.yml` 每个 job 的每一条 `run:` 步骤；
- `bundle.json`：证据包，字段白名单，`complete` 可以由任何人从 git 重新算出来；
- 后端契约：「给一棵树与输入，按命令清单跑，还退出码与输出摘要」。local 是唯一实现，crun 后端只需新增一个文件。

## 为什么从 gates.yml 派生

门禁集只有一个定义，就是该提交上的 `.github/workflows/gates.yml`（它的文件头解释了为什么只能有一份）。手抄的清单一定会漂：`release.yml` 当年跑的「全套」少了 packages、playground、所有 contract，正是这么来的。`scripts/incremental-semantics-contract/sweep-plan.py` 对 incremental 家族已经这样做过，理由相同：清单每次运行时从 `gates.yml` 解析，手里不存副本。本刀把同一个做法扩到全部 job。

具体做法（`gatesplan.py`）：

1. 用 `git rev-parse <sha>:.github/workflows/gates.yml` 和 `git cat-file` 读**该提交**上的文件，不读工作树。在 X 上计划、在 Y 的检出上跑，这种错位由此排除。
2. 接受的形状是封闭的。顶层键、job 键、step 键、`${{ }}` 表达式都有白名单；job 的 `if:` 只接受下一节列出的三种形状，step 的 `if:` 只接受 `steps.<前面的 id>.outputs.<键> == '<字面量>'`，env 里的表达式只接受 `runner.temp`、`needs.<所需 gate job>.result`、`steps.<前面的 id>.outputs.<键>`。白名单外的东西一律在任何 job 开始前拒绝。原因：不认识的构造在本地怎么执行只能靠猜，猜错了就是静默地跑了另一套门禁。
3. 与 `sweep-plan.py` 一样做一次独立的行扫描交叉核对：YAML 遍历找到的 `run:` 步骤数必须等于逐行数出的 `run:` 键数，plan job 的行不计（d9b10e62 上 39 个 job、169 个 run 步骤；cdeb40ca 上 40 个 job，除去 plan 是 39 个 gate job、173 个 run 步骤）。

### plan job（#168 的 PR 分层）

#168 之后 `gates.yml` 先跑一个 `plan` job，按 PR 的 diff 决定哪些 gate job 要跑；每个 gate job `needs: [plan]`，`if:` 是「plan 说 `all`，或者 plan 的列表里有我」。plan job 不是门禁：它不判断树的任何性质，只负责给 PR 减负。外部运行按定义就是全集，所以：

- plan job 不执行，它的 run 行也不进 `complete` 的多重集；
- 替换表记一行 `plan -> external-all`，读者看得见这一步被换成了「全部都跑」；
- gate job 对 plan 输出的条件视为成立，`needs:` 里的 `plan` 从调度依赖里去掉。

「视为成立」只在条件正是 #168 写下的接线时才诚实。所以条件去掉 `${{ }}`、压缩空白后，必须逐字等于下面之一（`{job}` 代入该 job 自己的 id）：

| 形状 | 条件 | 另外要求 |
|---|---|---|
| plan-selected | `needs.plan.outputs.all == 'true' \|\| contains(fromJSON(needs.plan.outputs.jobs), '{job}')` | `needs` 恰好是 `[plan]` |
| plan-selected-always | `always() && needs.plan.result == 'success' && (…同上…)` | `needs` 含 plan 与至少一个 gate job |
| legacy-always | `always()` | #168 之前的形状，`needs` 里要有 gate job |

名字写成别的 job、多一个子句、只看 `all`、不 `needs: plan`、plan job 自己有 `needs:` 或 `if:`、plan 的 outputs 不是 `all`/`jobs`，都拒绝，各有自测负控。

`mutant-shards-complete` 在 #168 里还读 `${{ needs.<分片>.result }}`、用 step `id` 与 `GITHUB_OUTPUT` 算出本次跑了哪些分片家族，再用 step `if:` 决定是否汇总。这些按 GitHub 的语义求值：`needs.<id>.result` 是本次运行里那个 job 的真实结果（`success`/`failure`；`--only` 没选中的记 `skipped`，与 GitHub 相同）。step 条件为假时该步跳过，跳过的 run 步骤记 `executed=false`，`complete` 因此为假。外部全量运行里所有分片都跑了，条件为真，汇总照常执行。
4. 不复用 `sweep-plan.py` 的解析函数。它读工作树里的 `gates.yml`、只认 incremental 家族，而且会把含 `&&`、`|` 的块当错误拒掉；全量 job 里这类块很多（`wasm-target` 的 wasi-sdk 步骤就是）。这里的执行单位是整个 `run:` 块，按 GitHub 的方式交给 `bash -e`，不需要把块拆成单条命令。

## 替换表

`uses:` 步骤没法原样在本地执行，每一种都换成一个有名字的替换，名字写进证据包的 `substitutions`，读者看得见每条被换成了什么。表外的 `uses:`（包括已有条目的版本号变化，如 `actions/checkout@v5`）让 `run.sh` 在计划阶段失败，不静默跳过。

| `uses:` | 替换 id | 为什么合理 |
|---|---|---|
| `plan`（job） | `external-all` | 见上一节：外部运行就是全集，plan job 不执行，gate job 对它的条件视为成立。 |
| `actions/checkout@v4` | `tree-worktree` | CI 每个 job 都是新检出。`git worktree add --detach <sha>` 给每个 job 一棵独立的树，内容与检出相同。差别：worktree 带全部历史和 tag，CI 默认 depth 1 无 tag（`fetch-depth: 0` 的三个 job 除外）。依赖「没有历史」的脚本会表现不同；目前没有发现这样的脚本，`seedjar.sh` 在缺 tag 时会自己去取，有 tag 时直接用。 |
| `./.github/actions/dawn-toolchain` | `dawn-toolchain-local` | 这个复合 action 做四件事：装 GraalVM 21、恢复种子缓存、恢复 coursier 缓存、`./bin/dawn --version`。本地对应：`JAVA_HOME`/`PATH` 指向本机 JDK 21；把共享种子缓存里对应 tag 的两个目录拷进 worktree 的 `.dawn/seeds`（等价于 cache restore，`seedjar.sh` 每次命中都会按校验表重验，所以拷来的缓存不需要被信任）；`build: 'false'` 时不构建。整体替换只在复合 action 仍是这个形状时才诚实，所以同一提交上的 `action.yml` 会被取指纹（输入集合与默认值、两次 setup-graalvm、两个缓存的路径、重试提示与构建两个 run 步骤），形状变了就拒绝。 |
| `actions/cache@v4` | `noop` | 保存一侧不需要：本地缓存本来就在。恢复一侧由上一行的种子拷贝承担；coursier 缓存就是用户自己的 `~/.cache/coursier`，与 runner 上一样由同一个 HOME 共享。它在 `gates.yml` 里没有直接出现，只在复合 action 内部，表里仍列出，免得读者以为它被漏掉。 |
| `actions/upload-artifact@v4` | `artifact-store-local` | 拷到 `<out>/artifacts/<name>`。`if-no-files-found: error` 照样生效，重复的制品名照样报错（v4 的行为）。 |
| `actions/download-artifact@v4` | `artifact-fetch-local` | 按 `pattern` 把匹配的制品各自拷到 `<path>/<制品名>`，与 v4 不带 `merge-multiple` 时的布局一致。`mutant-shards-complete` 因此照跑，输入来自本地目录。 |
| `actions/setup-node@v4` | `node-host` | **任务单的表里没有这一行，偏离理由：** `docs` job 用它；按「表外一律失败」，没有这行 `docs` 永远跑不了。本地用 PATH 上的 `node`，版本号写进证据包的 `toolchain.node`，是不是 `lts/*` 由读者对照判断。`cache: npm` 当作无操作。 |
| `actions/setup-java@v4` | `jdk21-host` | **同样是任务单外的一行：** `compiler-weight-contract` 用它装 Temurin 21。本地用同一个 JDK 21（GraalVM CE，不是 Temurin），只接受 `java-version: '21'`，版本字符串写进 `toolchain.java`。 |

除 `uses:` 之外，本地后端还有几处改变了步骤看到的环境。它们不是替换，但同样影响「跑的是什么」，所以也以 `adjust:*` 为主语列进 `substitutions`：

| 调整 | 替换 id | 理由 |
|---|---|---|
| `adjust:runner-temp` | `per-job-directory` | `RUNNER_TEMP` 与 `${{ runner.temp }}` 指向每 job 一个的目录，放在 worktree 外，免得脏了树。 |
| `adjust:tmpdir` | `per-job-directory` | `TMPDIR` 每 job 一个，`mktemp` 类的临时文件互不相见。runner 上没有设它；设了只会更隔离。 |
| `adjust:literal-tmp-paths` | `machine-wide-lock` | 步骤里写死的 `/tmp/<名字>`（今天只有 `contracts-1` 的 `/tmp/gate-emit`）在同一台机器的所有检出之间共享。按字面路径取一把机器级文件锁，两个 `run.sh` 不会同时用它；挡不住别的程序。路径是从命令文本里扫出来的，不是手写的表。 |
| `adjust:playground-port` | `free-port-per-run` | `playground/test/contract.sh` 默认 8097，结束时 `fuser -k` 这个端口。WSL2 下 8097 可能落在 WinNAT 保留段里 bind 失败；共享机器上 `fuser -k 8097` 还会杀掉别人的进程。每次运行挑一个空闲端口经 `PLAY_TEST_PORT` 传入（该脚本本来就支持这个变量）。 |
| `adjust:github-env-files` | `per-step-files` | `GITHUB_ENV`、`GITHUB_PATH` 等是每步一个文件，`ENV` 与 `PATH` 按 runner 的规则带到后续步骤。`wasm-target` 靠它把 `DAWN_WASM_CC` 与 `DAWNC_BIN` 传给后面的步骤。 |

另外，宿主环境里的 `GITHUB_*`、`RUNNER_*`、`DAWN_*`、`JAVA_HOME` 等变量在交给步骤前被清掉，再设 `CI=true`。`DAWN_SEED` 之类的变量会悄悄改变工具链的来源，不能从开发者的 shell 漏进来。

## complete 的定义

`complete = true` 当且仅当：

- 该提交上 `gates.yml` 全部 job 的全部 `run:` 步骤，按 (job id, run 原文) 组成的多重集，等于证据包 `steps[]` 中 `executed = true` 的 (job, command) 多重集；
- 并且每个已执行步骤的退出码是 0。

缺一条、多一条、任一步骤未执行、任一非零（超时记 124），都是 `false`，`run.sh` 以 1 退出。跳过不是成功。

几点说明：

- 用多重集而不是集合：同一条命令合法地出现多次（`diagnostic-reads.py --self-test` 在三个 job 里各跑一次），集合会把「三次里只跑了两次」看成相等。
- 元素带 job id：命令从一个 job 挪到另一个 job 算一少一多。CI 上 job 是独立检出与独立环境，挪 job 不是无害的。
- `complete` 永远重算，不信任证据包里的值。`bundle.py verify` 从 git 读该提交的 `gates.yml`，核对 `gates_blob`，重算多重集与 `complete`，与包里的值不一致就判无效。
- 本地因环境跑不了的步骤照实记录：要么执行了且非零，要么（前面失败且没开 `--keep-going`）`executed = false`。两者都让 `complete = false`。没有任何路径能把跑不了的步骤记成成功。
- `--keep-going` 在某步失败后继续跑本 job 余下的步骤，这是 GitHub 不做的。它只用于本地摸清还有哪些步骤会失败，不改变 `complete` 的判定。

## 证据包 schema 与泄露规则

字段只有：`tree`、`gates_blob`、`substitutions[]`（`subject`、`replacement`）、`steps[]`（`job`、`name`、`command`、`exit_code`、`stdout_sha256`、`stderr_sha256`、`executed`）、`toolchain`（`seed_jar_sha256`、`java`、`cc`、`python`、`node`）、`complete`。任何层级出现其它字段都拒绝出包。

为什么是白名单：证据包是要交给别人的东西，它该证明的是「这棵树上这套门禁跑过、结果如何」，不该顺带公开跑它的机器。黑名单只能列出想到的泄露；白名单让时长、核数、内存、GPU、主机名、用户名、路径、镜像名、环境变量根本没有字段可放。自测对这些名字逐个证明被拒（在顶层、step、toolchain、substitution 四处各试一次）。

白名单挡不住「合法字段里装着路径」，所以每个字符串值还要过泄露过滤：含 `/` 或 `\`、含 `@`、形如主机名（点分标签、以字母结尾）、形如 IPv4 或 IPv6、含本机主机名或用户名，都拒绝，且生成器不写文件。

偏离任务单的一处：命令、步骤名、`uses:` 引用天然含 `/`（`./scripts/...`、`actions/checkout@v4`），按字面规则它们永远出不了包。所以豁免只给这三类字段，且只在值**逐字出现**在该提交的 `gates.yml` 里时成立。那是公开文本，验证者会重读；自由文本字段（`toolchain` 各项）没有豁免。替换 id 是 `gatesplan.py` 里的固定词表，不含 `/`。

`stdout`/`stderr` 只存 sha256。日志本身留在 `<out>/logs`，给跑的人排错用；哈希让事后可以把某份留存的日志对上某一步，但输出里有时间戳与临时路径，同一步两次运行的哈希一般不同，它不是可复现性声明。

## 后端契约

后端是一个 `backend_<name>.py` 模块，暴露 `create(ctx)`，返回的对象实现 `prepare()`、`run_job(job, artifacts)`、`toolchain()`、`cleanup()`。`run_job` 拿到的是 `gatesplan` 给的一个 job（有序的 run 步骤与带替换 id 的 use 步骤）和本次运行的制品目录，还回每个 run 步骤的 `executed`、`exit_code`、两个输出哈希。

分工：`gatesplan.py` 决定跑什么，后端决定在哪跑、每个替换 id 怎么实现，`bundle.py` 决定结果算不算完整，`runner.py` 只管调度（按 `gates.yml` 的定义顺序，即预期时长降序，最多 `--jobs` 个并行；有 `needs:` 的 job 等依赖结束后照跑，对应 `if: always()`）。crun 后端因此只需新增 `backend_crun.py`，通过 `--backend crun` 选中，不改现有文件。后端专属选项走 `--backend-opt KEY=VALUE`，也不需要改 `run.sh`。

## 签名、落盘与 GitHub 侧核验（第 2 刀）

证据包能被任何人重算，但说不出是谁跑的。任何人都能写一份 `bundle.json`，所以没有签名的证据包只是声明。第 2 刀补上这一半，协议的摘要写在 [bootstrap.md](bootstrap.md)「GitHub 之外执行门禁集的证据协议」，这里记取舍。

新增文件：

- `allowed_signers`：一行，identity `dawn-gates`，`namespaces="dawn-gates"`，后接维护者专用钥 `~/.ssh/dawn-gates-sign` 的公钥。
- `verify_note.py`：读 note、拆信封、验签、核对提交，`--selftest` 在临时仓库里用两把一次性钥演示每条负控先红后绿。
- `publish.py`：本地复核、拒绝不完整的包、签名、本地验签、写 note、推 `refs/notes/gates`、派发工作流。`--dry-run` 停在推之前，`--dry-run-dispatch` 停在派发之前，`--remote` 可以是本地 bare 仓库；`--remote` 不是 `origin` 时拒绝派发，免得把一次演练派发到真仓库。
- `.github/workflows/verify-external.yml`：`workflow_dispatch`，输入 `sha`，一个 job，写 `gates/maintainer` status。

### 签什么

签名对象是 bundle 的规范字节：`json.dumps(bundle, sort_keys=True, separators=(',', ':'))` 加换行。不签信封文本，所以 note 可以缩进排版、便于人读；核验方把解析出的 bundle 重新规范化再验，签名绑定的是内容而不是排版。信封的 JSON 解析拒绝重复键：否则同一段文本在不同解析器下可能得出两个不同的 bundle。

签名用 `ssh-keygen -Y sign/verify`，namespace 固定为 `dawn-gates`。选 SSH 签名而不是 GPG：runner 自带 OpenSSH，不需要装钥匙环；公钥一行即可入库，信任根在仓库里而不在某个钥匙服务器上。

### 核验清单与共用代码

`verify_note.py` 逐项核，每一项都执行并打印，不在第一项失败时停：note、信封、签名、`tree == sha`、`gates_blob == git rev-parse <sha>:.github/workflows/gates.yml`、`bundle.check`、`complete`。其中 `bundle.check` 是从 `bundle.py verify` 里抽出来的函数，`bundle.py verify`、`publish.py`、`verify_note.py` 三处都调它，不存在第二份核验逻辑。替换表的「只含已知行」由它覆盖：`validate` 拒绝未知主语与未知替换 id，另外整张表必须逐行等于该提交 `gates.yml` 推出的那张，已知 id 挂错行也红。

核验方给泄露过滤传空的身份集合。泄露过滤里「本机主机名、用户名」一条保护的是生产者的机器，核验方不知道生产者叫什么；传入 runner 自己的名字只会让结论随运行地点变化。路径、主机名形状、地址形状这些形状规则照常生效。

### 偏离任务单的一处：核验器不取自被测提交

任务单写的是「checkout 该 sha」。实际做法是 `actions/checkout` 取派发所在的 ref（默认分支），再 `git fetch --depth=1 origin <sha> refs/notes/gates` 把被测提交当作对象读进来。理由：如果核验器与 `allowed_signers` 取自被测提交，一个改了 `allowed_signers` 或 `verify_note.py` 的提交就能给自己作保。`gatesplan.py` 本来就只从 git 对象读 `gates.yml` 与复合 action，不需要工作树。工作流的第一步还要求 `GITHUB_REF` 是默认分支，挡住 `gh workflow run --ref <别的分支>` 的误用。

这挡不住有写权限的恶意者：他可以在任何分支上写一个直接打 `success` 的工作流。commit status 的可信度上限就是仓库写权限，这一点写进了 bootstrap.md 的诚实边界。能脱离 GitHub 复核的是 note 里的签名。

### 失败也要写 status

status 步骤 `if: always()`，verify 步骤的 outcome 不是 `success` 就写 `failure`，包括前面的 fetch 失败导致 verify 被跳过的情况。只有输入不是 40 位十六进制时不写，因为那时没有可写的提交。沉默的失败与没跑无法区分。

### 实测

本地 bare 仓库演练（2026-09-23，详细记录在任务报告里）：浅克隆 + 按 sha 取对象与 notes + `verify_note.py`，对一个两步的小 `gates.yml` 合计约 0.6s；对真实 `gates.yml`（173 个 run 步骤）`verify_note.py` 本身不到 1s。托管 runner 上加上排队、起机与 `actions/checkout`，预计整个 job 在 15s 到 30s 之间；这是估计，首次真实运行后以 run 的计时为准。预算按 floor 记，timeout 5 分钟。

## 与 #167 的关系

#167 要的是「分片之后各分片步骤的并集仍等于原 job 的步骤」的核对。本刀的多重集比较（`bundle.multiset_diff`）就是这个并集检查的核心：它逐条点名少了的和多出的命令。

但两者的比较对象不同，需要说清：本刀拿「证据包执行了的」去比「同一提交上 `gates.yml` 写着的」，所以能抓住「跑的时候漏了一条」，抓不住「`gates.yml` 本身删掉了一条」。#167 要抓的正是后者，它要求对照一份入库的期望或拆分时记录的并集。第 2 刀把这条核对接进 CI 时，比较对象换成入库期望（例如上一个提交的多重集，或随拆分一起提交的并集），就能关掉 #167。

## 实测

本节数字只作参考，不做性能声明；机器是共享的 16 核 / 15.6 GiB WSL2，运行期间还有其他进程。

2026-09-23，`run.sh --sha d9b10e62 --backend local --jobs 8 --keep-going`，GraalVM CE 21.0.2，Python 3.14.7，node v26.8.2，cc 13.3.0：

| 项 | 结果 |
|---|---|
| 墙钟 | 4969s（1:22:49） |
| 内存占用（MemTotal − MemAvailable） | 起始 2.19 GiB，峰值 10.6 GiB |
| job | 39 个里 37 个全绿，169 个 run 步骤全部执行，167 个退出码 0 |
| 非零 | `lsp-workspace` 的 `./scripts/lsp-liveness.py`（exit 1）；`docs` 的 `./playground/test/contract.sh`（exit 1） |
| 证据包 | `complete = false`，`run.sh` 退出 1；`bundle.py verify` 复算一致 |

两个红步骤都不是替换表的问题，逐个复跑确认了原因：

- `lsp-liveness.py` 的 hangup 检查要求客户端关闭 stdin 后 5s 内退出。全套运行时 load average 在 30 上下，它在 5.00s 时仍存活。机器空闲时单独复跑 `lsp-workspace`，8 步全绿。这是负载下的计时，不是环境缺失。
- `playground/test/contract.sh` 里的 `lsp_contract.py` 在「SIGTERM 优雅回收子进程」一项失败（socket 在帧中途关闭），空闲时复跑仍然失败，所以是确定性的。把 PATH 上的 `python3` 换成系统的 3.12.3（ubuntu-latest 的版本）后单独复跑 `docs`，6 步全绿。网关用 `sys.executable` 启动，在 Python 3.14 下 SIGTERM 路径的行为不同。这是网关对 3.14 的兼容问题，CI 看不到。

本机没有「因为缺工具或断网而根本跑不了」的步骤：wasi-sdk 下载、N−1 种子下载、ASan、clang 都可用。

## 接入前必改

这些是本刀在 `run.sh` 里绕开、但没有在仓库源码里改掉的债：

- `contracts-1` 的 `/tmp/gate-emit` 写死在 `gates.yml`。本地用机器级锁串行化，挡不住其他程序；应改成 `$RUNNER_TEMP/gate-emit`。
- `playground/test/contract.sh` 默认 8097 并在退出时 `fuser -k` 该端口。本地用 `PLAY_TEST_PORT` 绕开；默认值应改成向内核要空闲端口，`fuser -k` 应改成只杀自己起的进程。
- `scripts/spike-native/run.sh` 在编不出 ASan 时只打印一行 note 并把 asan 检查记为 blocked，job 仍然绿。在 CI 上无害（runner 有 ASan），在外部后端上会让「跑过了」少一个维度而证据包看不出来。应让缺 ASan 成为失败，或至少成为可机读的结果。
- 本地后端用宿主 PATH 上的 `python3`、`node`，只把版本写进证据包，不钉版本。上面的 3.14 实例说明这会改变结果；接入前应能钉住与 ubuntu-latest 一致的解释器版本，或者让不一致成为拒绝。
- `lsp-liveness.py` 的 5s 上限在高并行的共享机器上会误红；外部后端要么降低并行度，要么这个检查要按机器负载给出可解释的余量。

## 不做的（理由）

- **release 守卫接受外部证据。** `release.yml` 的 `verified` job 仍然只认 `ci.yml` 在该 sha 上的成功运行。让它也接受 `gates/maintainer`，要先回答维护者自证能不能替代托管 runner 的独立运行、以及 status 可被有写权限者伪造时守卫该读 status 还是自己验 note。这是第 4 刀。
- **自动触发。** `publish.py` 之后派发工作流是手动的一步（脚本替维护者执行 `gh workflow run`）。不做推 `refs/notes/gates` 时自动触发：Actions 的 `push` 触发器按分支与 tag 过滤，推 notes ref 能否可靠地触发工作流没有实测；更要紧的是，自动触发意味着任何能推 notes 的人都能让 runner 替他写 status，而派发是一个需要写权限、留在 Actions 记录里的显式动作。
- **发布红的证据。** `publish.py` 拒绝 `complete` 不为 true 的包。签名的「门禁没过」不能让任何人做任何事，没有绿 status 已经说明了这一点。
- **多钥与轮换过渡期。** `allowed_signers` 只有一行。换钥即改这一行，旧 note 从此核不过；要保留旧证据的可核验性，需要按时间段接受多把钥，等真的换钥时再说。
- **crun 后端。** 仍不做。契约为它留好了位置，但 GPU 集群的派发、同步和锁卡是另一套问题，而且 `gates.yml` 今天没有 GPU 门禁（tile 的 GPU 差分在 `tile.yml`，不在本刀范围）。
- **时长字段。** 证据包不记时长。时长是机器画像的一部分（核数、负载、邻居），不是树的性质；它也无法被验证者复核。本地计时写在 `summary.json`，只给跑的人看。
- **把 `/tmp/gate-emit`、8097 改掉。** 任务单明确本刀不改仓库源码，且 #168 正在改 `gates.yml`；这些列进上一节。
- **解析复合 action 并逐步替换其内部步骤。** 复合 action 的内部是 GraalVM 下载与缓存，没有门禁；整体替换加指纹更简单，也更早暴露变化。
- **覆盖 `tile.yml`、`editor-grammar.yml`、`nightly.yml`。** 任务单的范围是 `gates.yml`。前两个是按路径触发的门禁工作流，`tile.yml` 需要 GPU；把它们纳入是 crun 后端那一刀的事。
