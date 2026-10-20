import { redirect } from "next/navigation";

export default function DiscoveryPage() {
  redirect("/profile?strategy=ai");
}
