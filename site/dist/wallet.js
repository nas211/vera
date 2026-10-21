(() => {
  "use strict";
  // All marketing wallet controls enter the authenticated demo portal.
  // Keep normal page history so Back returns to the original homepage.
  document.querySelectorAll(".wallet-open").forEach((button) => {
    button.addEventListener("click", () => location.assign("/trade"));
  });
  // Preserve old MetaMask return links without leaving a redirect loop in Back.
  if (new URLSearchParams(location.search).get("wallet") === "metamask") {
    location.replace("/trade");
  }
})();
