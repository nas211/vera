import type { Metadata } from "next";
import { Nav } from "@/components/Nav";
import { Footer } from "@/components/Footer";
import { ProfileApp } from "@/components/profile/ProfileApp";

// Deep link to the Yellow Pro settings drawer on the profile page.
export const metadata: Metadata = {
  title: "Yellow Pro settings — WHY TRADE",
  robots: { index: false, follow: false },
};

export default function YellowSettings() {
  return (
    <>
      <Nav />
      <ProfileApp initialView="yellow" />
      <Footer />
    </>
  );
}
