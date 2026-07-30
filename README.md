# ai4s — 面向企业的 AI 接入及安全网关

**ai4s = AI for Security · AI for Smart · AI for Sustainability · AI for Success**

为企业内部员工提供统一、安全、可治理的 LLM API 出口。

## 定位

- **AI for Security（安全）**：敏感数据（密钥、PII、商业机密）在发给 LLM 前被检测、脱敏或阻断；全链路审计留痕。
- **AI for Smart（智能）**：多供应商/多渠道智能路由，按成本、延迟、余额、健康度选择最优通道，自动故障转移。
- **AI for Sustainability（可持续）**：员工 key 全生命周期管理（签发、额度、追踪、回收），成本可控、合规可持续运营。
- **AI for Success（成功）**：让员工顺畅用上 AI 而不被安全流程绊住，人机协同提升组织生产力。

## 当前状态

立项调研阶段。脚手架选型调研已完成（2026-07-30，由 claude / codex / kimi 三方独立调研并交叉核验）：

- 调研报告：[`docs/research/2026-07-30-gateway-scaffolding-research.md`](docs/research/2026-07-30-gateway-scaffolding-research.md)
- 待拍板决策点见报告第五节

## 需求清单（v1）

1. 上游聚合：多个 LLM 供应商/中转站 → 统一 OpenAI 兼容入口
2. OAuth 上游：Codex/Claude/Gemini CLI 订阅账号作为可选上游（隔离使用）
3. 智能路由：成本/延迟/余额/使用率选路 + 自动故障转移
4. 员工 key 管理：按人签发、额度预算、用量追踪、团队差异化策略；设备绑定（以 mTLS 客户端证书实现，MAC 绑定已论证不可行）
5. 内容级 DLP：运行时阻断/脱敏敏感数据，不只是日志可观测
