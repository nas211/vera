export type Message = {
  role: "why" | "user";
  type: "text" | "card";
  text?: string;
  card?: { label: string; value: string; color?: "green" | "red" | "gold" }[];
};

export const OPENING: Message[] = [
  {
    role: "why",
    type: "card",
    card: [
      { label: "SOL long", value: "cut — PF 0.71 over 20 trades" },
      { label: "recycled into", value: "TON" },
      { label: "reason", value: "edge gone, capital moves" },
    ],
  },
  {
    role: "why",
    type: "card",
    card: [
      { label: "WIF long", value: "+3.2% / +$640", color: "green" },
      { label: "booked", value: "confirmed" },
      { label: "status", value: "closed" },
    ],
  },
  {
    role: "why",
    type: "text",
    text: "Today so far: +$1,240 net · paper\n\nAsk me anything 👇",
  },
];

export const ANSWERS: Record<string, Message[]> = {
  "why short ETH?": [
    {
      role: "why",
      type: "card",
      card: [
        { label: "funding", value: "negative" },
        { label: "OI", value: "into resistance" },
        { label: "short PF", value: "1.4" },
      ],
    },
    {
      role: "why",
      type: "text",
      text: "I size what's working. Flip me the second the edge flips.",
    },
  ],
  "how's my week?": [
    {
      role: "why",
      type: "card",
      card: [
        { label: "7-day net", value: "+6.8%", color: "green" },
        { label: "profit factor", value: "1.31" },
        { label: "trades", value: "412" },
        { label: "win rate", value: "58%" },
        { label: "best", value: "funding arb +2.9%", color: "green" },
        { label: "worst", value: "breakout fade — cut early", color: "red" },
      ],
    },
    { role: "why", type: "text", text: "Nothing heroic. Just edge, compounded." },
  ],
  "/status": [
    {
      role: "why",
      type: "card",
      card: [
        { label: "live strategies", value: "340" },
        { label: "on probation", value: "11" },
        { label: "suspended", value: "2" },
        { label: "open positions", value: "6 bracketed" },
        { label: "today paper", value: "+$1,240", color: "green" },
        { label: "uptime", value: "99.98%" },
      ],
    },
    { role: "why", type: "text", text: "I don't sleep so you can." },
  ],
  "you down bad?": [
    {
      role: "why",
      type: "card",
      card: [
        { label: "last loss", value: "−0.4% / −$120", color: "red" },
        { label: "strategy", value: "retired" },
        { label: "capital", value: "redeployed" },
      ],
    },
    {
      role: "why",
      type: "text",
      text: "My miss, not the market's. Already retired the strategy.\n\nI'd rather show you the red than hide it.",
    },
  ],
  "moon soon?": [
    {
      role: "why",
      type: "text",
      text: "I don't do moonboy. I do profit factor.\n\nReceipts, not hopium.",
    },
  ],
  "/kill": [
    {
      role: "why",
      type: "card",
      card: [
        { label: "status", value: "kill switch armed 🛑" },
        { label: "confirm", value: "/kill confirm" },
      ],
    },
    {
      role: "why",
      type: "text",
      text: "You always have the last word — I just do the work.",
    },
  ],
};

export const DEFAULT_ANSWER: Message[] = [
  {
    role: "why",
    type: "text",
    text: "Ask me what I'm trading, why I'm in it, how your week looks, or tell me to /kill.\n\nI deal in positions and numbers — not vibes.",
  },
];

export const TICKER_PINGS = [
  { text: "Closed ARB long", detail: "+1.9% / +$380", delta: 380, color: "green" as const },
  { text: "Opened short DOGE", detail: "sizing in", delta: 0, color: "gold" as const },
  { text: "Scalped ETH", detail: "+0.6% / +$210", delta: 210, color: "green" as const },
  { text: "Closed short DOGE", detail: "+1.5% / +$300", delta: 300, color: "green" as const },
  { text: "Cut SUI long early", detail: "−0.4% / −$120 · my call", delta: -120, color: "red" as const },
  { text: "Funding arb TON", detail: "+2.4% / +$540", delta: 540, color: "green" as const },
  { text: "Booked WIF runner", detail: "+3.6% / +$720", delta: 720, color: "green" as const },
  { text: "Rotating into majors", detail: "rebalancing", delta: 0, color: "gold" as const },
  { text: "Closed long BTC", detail: "+0.9% / +$460", delta: 460, color: "green" as const },
];

export const MINT_ITEMS = [
  { sym: "ARB", action: "MINT", pct: "+1.9%", pos: "+$380" },
  { sym: "WIF", action: "MINT", pct: "+3.2%", pos: "+$640" },
  { sym: "TON", action: "ARBIT", pct: "+2.4%", pos: "+$540" },
  { sym: "SUI", action: "CUT", pct: "−0.4%", pos: "−$120" },
  { sym: "BTC", action: "MINT", pct: "+0.9%", pos: "+$460" },
  { sym: "ETH", action: "SCALP", pct: "+0.6%", pos: "+$210" },
  { sym: "DOGE", action: "MINT", pct: "+1.5%", pos: "+$300" },
  { sym: "SOL", action: "CUT", pct: "−0.3%", pos: "−$90" },
  { sym: "PEPE", action: "MINT", pct: "+4.1%", pos: "+$820" },
];
