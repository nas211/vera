import type { MetadataRoute } from "next";

export default function sitemap(): MetadataRoute.Sitemap {
  return [
    { url: "https://whytradingbot.com",         lastModified: new Date(), changeFrequency: "weekly",  priority: 1 },
    { url: "https://whytradingbot.com/trades",  lastModified: new Date(), changeFrequency: "hourly",  priority: 0.9 },
    { url: "https://whytradingbot.com/privacy", lastModified: new Date(), changeFrequency: "yearly",  priority: 0.3 },
    { url: "https://whytradingbot.com/terms",   lastModified: new Date(), changeFrequency: "yearly",  priority: 0.3 },
    { url: "https://whytradingbot.com/brand",   lastModified: new Date(), changeFrequency: "yearly",  priority: 0.3 },
  ];
}
