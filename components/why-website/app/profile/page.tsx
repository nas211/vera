import type { Metadata } from "next";
import { Nav } from "@/components/Nav";
import { Footer } from "@/components/Footer";
import { ProfileApp } from "@/components/profile/ProfileApp";

// The WHY profile / dashboard: account, plan, connected exchanges. Everything Yellow
// here goes through the existing Yellow customer backend at /yellow/api/ (same origin).
export const metadata: Metadata = {
  title: "Your profile — WHY TRADE",
  robots: { index: false, follow: false },
};

export default async function Profile({ searchParams }: { searchParams: Promise<{ strategy?: string }> }) {
  const { strategy } = await searchParams;
  return (
    <>
      <Nav />
      <ProfileApp initialDiscovery={strategy === "ai"} />
      <Footer />
    </>
  );
}
