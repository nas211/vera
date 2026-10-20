(() => {
  "use strict";
  const $ = selector => document.querySelector(selector);
  $("#year").textContent = new Date().getFullYear();
  const menu = $(".menu-toggle");
  menu.addEventListener("click", () => {
    const open = menu.getAttribute("aria-expanded") !== "true";
    menu.setAttribute("aria-expanded", String(open));
    menu.setAttribute("aria-label", open ? "Close navigation" : "Open navigation");
    $("#nav-links").classList.toggle("open", open);
  });
  function calculate() {
    const balance = Number($("#earn-deposit").value);
    const apy = Number($("#earn-rate").value);
    const months = Number($("#earn-months").value);
    $("#earn-deposit-label").textContent = balance.toLocaleString("en-US");
    $("#earn-month-label").textContent = months;
    if (!Number.isFinite(balance) || balance < 1000 || balance > 50000 ||
        !Number.isFinite(apy) || apy < 0 || apy > 30 || $("#earn-rate").value === "" ||
        ![3, 6, 12, 24].includes(months)) {
      $("#earn-output").textContent = "—";
      return;
    }
    const rewards = balance * (Math.pow(1 + apy / 100, months / 12) - 1);
    $("#earn-output").textContent = rewards.toLocaleString("en-US", {maximumFractionDigits:0});
  }
  ["#earn-deposit", "#earn-rate", "#earn-months"].forEach(selector => $(selector).addEventListener("input", calculate));
  calculate();
})();
