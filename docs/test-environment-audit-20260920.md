# 本地与云端测试环境审查（2026-09-20）

## 结论与范围

核心 App → Product Backend → Agent 链路具备配置，运行服务的 readiness 正常；**不能认定全部产品能力及下一次发布已完全就绪**。云端仍运行 9 月 9 日附近的旧版本，SMTP、Google 登录、FCM、报告生成和 Azure 尚未配齐或验证，最近三仓 CI 均有失败记录。

用户确认的测试范围：本地与云端测试环境暂不启用 Google 登录、FCM 和报告生成。这三项按有意禁用处理，不作为当前测试环境配置完整性或发布的阻塞项；相关缺口仅供将来启用时参考。Azure 等待提供配置，SMTP 状态未在此次范围确认中变更。

本次检查实际私有 env、Docker 运行参数、Compose、GitHub 配置名称、历史 Actions 耗时和本地工具链；修复配置与发布代码。没有提交、推送、构建新 APK、发布镜像、执行数据库迁移或重启云端服务。配置修复不等于运行服务已经采用新配置。

## App 构建配置

| 项目 | 本地 | 云端测试包 |
|---|---|---|
| Flavor / 环境 | `local` / local | `unified` / test |
| Product API | `http://127.0.0.1:8769` | `https://backend-test.lute-momcozylab.luteos.cloud:8443` |
| Agent API | `http://127.0.0.1:8010` | `https://agent-test.lute-momcozylab.luteos.cloud:8443` |
| 配置校验 | 通过 | 通过，两端地址必须同时提供 |
| 工具链 | Flutter 3.44.4、Java 17、Android 36 检查通过 | workflow 已固定相应版本 |
| 签名 | 本机分发签名未实际验证 | GitHub 四个签名 secret 名称齐全；未重新签包验证值 |
| 证书 | 本地 HTTP | 使用 App 内置 CA 访问两个 HTTPS readiness 均通过 |
| 发布依赖 | 本地 Docker 服务在运行 | SSH、发布仓 token、smoke 邀请码/设备 secret 名称齐全 |

`127.0.0.1` 是开发主机地址；Android 真机/模拟器仍需端口反向转发或明确配置可访问的主机地址。配置检查不替代真机安装、登录、通知及视频通话测试。

App 的 Google OAuth 和 Firebase 参数未在当前测试发布流程中配齐，不能据此声明 Google 登录和系统推送可用。

## 后端配置与运行状态

- 本地 Product / Agent API、Agent Worker、通知/邮件 Worker 及共享 PostgreSQL、Redis、MinIO 在运行。两端 Settings 校验、JWT issuer/audience 和服务间密钥对应关系检查通过。
- 本地私有 env 为 `0600`。初始化脚本现在补齐缺失的模板字段，迁移 `OPENAI_REASONING_EFFORT`、`OPENAI_TEXT_VERBOSITY`、`OPENAI_RESPONSES_STORE` 为统一 Provider 字段，删除废弃 `FACT_WORKER_*`，保留既有密钥和显式配置；重复执行幂等。
- 本地 Agent 配置为 `openai_responses`，压缩阈值为 200000；新增账户邮件/Google 字段已进入两套后端 env 模板，未填造凭证。
- 云端 Product 当前 commit 为 `ad5fe9f6a0ac2db815097c2b7326686784d3f738`，Agent 为 `f80a2667387e411ce2b1cc47fcb4800445b8adce`；云端运行镜像与本地未发布源码不是同一版本。
- 云端 API、Agent Worker、视频 Worker 以及共享基础设施在运行；通知、邮件和报告 Worker 当前未部署。现有云端认证配置 `AUTH_REQUIRE_ACTIVE_SESSION=false`，而当前测试 Compose 已要求 true，需下次版本发布后再验证登录/续聊。
- 云端 Agent 实际使用 OpenAI、模型 `gpt-5.6-terra`、单次输出上限 8000。**运行中压缩阈值仍为 100000；持久部署文件已改为 200000，下次部署才生效。**
- 修复后，本地两套 Compose 和云端当前版本两套 Compose 的 `config --quiet` 均通过。云端检查使用各自 manifest 的固定镜像和 Agent release ID。

### 已修复的云端配置损坏

`/opt/momcozy-lab/shared/backend/deploy.env` 的基础配置被写成了含 97 个字面量 `\n` 的一行注释，实际只能读取后加的少量视频字段。正在运行的旧服务使用另一份临时 env，因此健康检查不能发现下次标准部署将失败的问题。

已恢复真实换行、保留后加的视频配置，并在服务器内存中比对恢复后的关键凭证与运行容器一致；新文件通过部署脚本校验，原子写入且权限保持 `0600`。未输出凭证。

私有备份：

- `/opt/momcozy-lab/shared/backend/deploy.env.before-newline-repair-20260920T074857Z`
- `/opt/momcozy-lab/shared/agent/deploy.env.before-budget-update-20260920T074857Z`

### 尚未完整的能力

| 能力 | 缺口 / 验证边界 |
|---|---|
| 邮件注册、验证、密码重置 | 本地及云端缺 SMTP、发信地址、邮件 token 加密密钥；启动邮件 Worker 不等于邮件发送可用 |
| Google 登录 | 后端 client ID 和 App 对应构建参数未配齐 |
| 系统推送 | FCM 配置/凭证和 App Firebase 参数未配齐；站内通知与 FCM 推送分开判断 |
| 报告生成 | 本地及云端缺独立 Runtime 地址和专用服务密钥；报告 Worker 为条件启用 |
| 语音 / 视觉 | 当前默认 disabled，未做真实提供商请求 |
| 本地图片送入远端模型 | 资源公网基址仍为 `https://api.local.example.test` 示例值，需要可达公网入口；不能用本地健康检查证明模型可取图 |
| Azure 切换 | 云端无 Azure endpoint/key 配置，也未做 Entra 或 Azure 真实业务验收 |
| 视频咨询 | 云端配置为 LiveKit，Worker 在运行；本次未做真实双端通话验收 |

本机另有旧 `agent-worker-1` / `agent-fact-worker-1` 重启循环，不属于当前 `momcozy-lab-*` Compose 栈。本次未删除旧项目容器或其他云端产品栈。

## 耗时审查与流程修改

历史 Actions 证据：

| 运行 | 观察 |
|---|---|
| App release `33864030999`（9 月 4 日） | 总计约 25 分 5 秒；release gate 18 分 54 秒，依赖安装 3 分 16 秒，Flutter setup 67 秒，锁内发布约 25 秒 |
| Agent delivery `34343468771`（9 月 9 日） | 整体约 97 秒，实际部署 36 秒，原评论 gate 21 秒 |
| Backend delivery `34307434684`（9 月 9 日） | 整体约 79 秒，实际部署 26 秒，原评论 gate 11 秒 |
| Agent CI `34587019213`（9 月 11 日） | 完整镜像 tar 上传因 Actions artifact storage quota 耗尽而失败 |

已删除：

1. **手动发布后的第二次 issue 评论确认和最长 30 分钟轮询。** 改为发布前立即校验 `TEST_APPROVERS` 中的原始发起者及重跑者；保留 main 限制、test 环境记录、SSH host key 验证、固定 SHA/摘要和共享发布锁。旧 `TEST_APPROVAL_ISSUE` 变量不再被使用。
2. **App release gate 额外构建 local debug APK。** gate 只生成待分发的 unified release APK；独立本地 debug 命令和 CI 验证保留。
3. **Agent / Backend CI 完整镜像的 save → artifact 上传 → 下载 → load 中转。** 前置检查通过后，在同一 runner 构建、smoke、核对 OCI revision、推送同一镜像，只上传小型不可变摘要清单。

第 3 项减少制品存储和传输，但 container job 现在等待前置检查，可能改变 CI 关键路径；不能在未重跑前承诺总耗时下降多少。已耗尽的 GitHub 配额不会因改代码立即恢复，小型清单上传仍可能受限。本次未删除远端历史制品。

保留：

- 数据库版本有变化时的备份和迁移；无 schema 变化时原流程已自动跳过。
- Agent API 排空、未完成 Run / 压缩任务等待、失败回滚；补齐等待确认 Run 和 compacting/blocked context head 的检查。
- 后端通知/邮件及已启用报告/视频 Worker 与 API 同镜像部署、重启和回滚；关闭的 Worker 停止，历史快照只选择当时存在的服务。仅调整应用服务，不重建 PostgreSQL/Redis/MinIO。
- 本地/公网健康检查、签名、不可变发布清单、共享主机锁、App 与已部署服务的联调门禁。
- release 构建的 `flutter clean`：仓库已有旧 AOT 快照被复用的防护依据，本次不为提速删除。
- 格式、静态检查、单元测试及 macOS golden lane；这些检查当前确实能发现问题。

## 验证结果和发布阻塞

- Agent 部署相关测试：36 passed；Backend：46 passed；对应 Ruff 检查通过。
- App 脚本全量：33 passed、1 failed（另有 18 subtests passed）。失败为已有 `schedule_page_golden_test.dart` 缺 `@Tags(['golden'])`；保留该失败，没有放宽测试断言。排除该既有失败后的复查为 33 passed。
- 五份改动 workflow YAML 可解析，JS 语法及本次文件 diff whitespace 检查通过。
- 最近远端 CI 也并非全绿：App `35054948291` 有格式/golden 失败；Backend `35054598012` 有 MinIO、迁移就绪和 OpenAPI 发布步骤失败；Agent 如上为 artifact 配额失败。修改后的流程尚未推送或在 GitHub 重跑，不能宣称已经发布验收通过。
- 未执行新 APK 完整构建、真实模型请求、Azure 切换、SMTP/FCM/Google 联调或云端迁移/重启。上述缺口需在相应能力启用及下一次正式测试发布前补齐并验证。
