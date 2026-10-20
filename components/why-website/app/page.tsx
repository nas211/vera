import { Nav }          from "@/components/Nav";
import { Footer }       from "@/components/Footer";
import { Hero }         from "@/components/sections/Hero";
import { Products }     from "@/components/sections/Products";
import { Exchanges }    from "@/components/sections/Exchanges";
import { Tunnel }       from "@/components/sections/Tunnel";
import { Truths }       from "@/components/sections/Truths";
import { Arrival }      from "@/components/sections/Arrival";
import { TalkToWhy }    from "@/components/sections/TalkToWhy";
import { HoldY }        from "@/components/sections/HoldY";
import { Pricing }      from "@/components/sections/Pricing";
import { Finale }       from "@/components/sections/Finale";
import { RevealObserver } from "@/components/RevealObserver";

export default function Home() {
  return (
    <>
      <RevealObserver />
      <Nav />
      <main>
        <Hero />
        <Products />
        <Exchanges />
        <Tunnel />
        <Truths />
        <Arrival />
        <TalkToWhy />
        <HoldY />
        <Pricing />
        <Finale />
      </main>
      <Footer />
    </>
  );
}
