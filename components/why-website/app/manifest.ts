import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "WHY TRADE",
    short_name: "WHY",
    description: "Autonomous crypto alpha engine. 340+ live strategies.",
    start_url: "/",
    display: "standalone",
    background_color: "#0A0B0F",
    theme_color: "#F0852A",
    icons: [
      {
        src: "/apple-icon",
        sizes: "180x180",
        type: "image/png",
      },
    ],
  };
}
