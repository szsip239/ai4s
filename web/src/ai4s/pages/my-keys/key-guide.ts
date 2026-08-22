/**
 * 「我的 Key」页配置指南的纯数据/片段构建（issue #82）——与渲染分离，便于 node --test 直测。
 * 入口地址不写死：调用方传 window.location.origin（员工实际访问的入口即正确入口，
 * tailnet 规范名/localhost 自适应）。URL 组合（origin→base）只在本层发生，组件不拼 URL。
 */

/** 指南示例里的 Key 占位符（语言中立；页面文案指引到「我的 Key」列表查看复制） */
export const KEY_PLACEHOLDER = '<API_KEY>';

/** 指南列出的常用模型（2026-08-22 线上实测 200；note 为 i18n 键后缀，见 ai4s.myKeys.guide.model.*） */
export const GUIDE_MODELS: { name: string; noteKey: string }[] = [
  { name: 'echo-test', noteKey: 'echo' },
  { name: 'kimi-for-coding', noteKey: 'kimiForCoding' },
  { name: 'k3', noteKey: 'k3' },
  { name: 'gpt-5.6-sol', noteKey: 'gpt56sol' },
];

export interface GuideSnippets {
  /** API base（origin + /v1），组件直接取用，不在 tsx 里拼 URL */
  base: string;
  curl: string;
  python: string;
  claudeCode: string;
}

export function buildGuideSnippets(origin: string): GuideSnippets {
  const base = `${origin}/v1`;
  return {
    base,
    curl: `curl ${base}/chat/completions \\
  -H "Authorization: Bearer ${KEY_PLACEHOLDER}" \\
  -H "Content-Type: application/json" \\
  -d '{"model":"echo-test","messages":[{"role":"user","content":"你好"}]}'`,
    python: `from openai import OpenAI

client = OpenAI(base_url="${base}", api_key="${KEY_PLACEHOLDER}")
resp = client.chat.completions.create(
    model="echo-test",
    messages=[{"role": "user", "content": "你好"}],
)
print(resp.choices[0].message.content)`,
    // Claude Code 类（Anthropic Messages 协议）：客户端会在 BASE_URL 后拼 /v1/messages，
    // 故 BASE_URL 只到入口根（2026-08-22 实测 /v1/messages 200）
    claudeCode: `export ANTHROPIC_BASE_URL=${origin}
export ANTHROPIC_AUTH_TOKEN=${KEY_PLACEHOLDER}`,
  };
}
