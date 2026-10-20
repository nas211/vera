import { SUPPORT_SYSTEM } from "./policy";
import type { SupportTurn } from "./shared";

// Text-only adapter for the shared-Core Customer Support persona. No agent tools,
// tenant credentials or trading APIs are exposed to this visitor-facing instance.
export async function supportReply(question: string, history: SupportTurn[]): Promise<string> {
  const key = process.env.WHY_SUPPORT_API_KEY?.trim();
  const endpoint = process.env.WHY_SUPPORT_ENDPOINT?.trim();
  const model = process.env.WHY_SUPPORT_MODEL?.trim();
  if (!key || !endpoint || !model) throw new Error("support_not_configured");
  const url = new URL(endpoint);
  if (url.protocol !== "https:" || url.username || url.password || url.search || url.hash) {
    throw new Error("support_invalid_endpoint");
  }
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${key}`, "User-Agent": "why-helpdesk/1.0" },
    body: JSON.stringify({
      model,
      messages: [{ role: "system", content: SUPPORT_SYSTEM }, ...history, { role: "user", content: question }],
      max_completion_tokens: 1200,
      reasoning_effort: process.env.WHY_SUPPORT_REASONING_EFFORT || "none",
      ...(url.hostname === "api.groq.com" && model.startsWith("openai/gpt-oss-") ? { include_reasoning: false } : {}),
    }),
    signal: AbortSignal.timeout(25_000),
    redirect: "error",
    cache: "no-store",
  });
  // Never return provider error bodies: they may contain prompts or credentials.
  if (!response.ok) throw new Error(`support_provider_${response.status}`);
  const data = await response.json();
  const content = data?.choices?.[0]?.message?.content;
  const reply = typeof content === "string" ? content.replace(/<think>[\s\S]*?<\/think>/gi, "").trim() : "";
  if (!reply || data?.choices?.[0]?.finish_reason === "length") throw new Error("support_incomplete_reply");
  return reply.slice(0, 5000);
}
