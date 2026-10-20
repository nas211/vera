(() => {
  "use strict";
  const $ = (q) => document.querySelector(q);
  const $$ = (q) => [...document.querySelectorAll(q)];
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const completed = new Set();
  let side = "long";
  let selectedAgent = 0;
  let sequenceTimer = null;
  let toastTimer = null;
  const price = 0.18432;
  const feeRate = 0.0004;
  const tick = 0.00001;

  // Demo interactions never call exchanges or use a connected wallet.
  // The separate wallet module only connects accounts and reads testnet balances.
  document.body.classList.add("js-ready");
  $("#year").textContent = new Date().getFullYear();
  if ("IntersectionObserver" in window && !reducedMotion) {
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add("visible");
          observer.unobserve(entry.target);
        }
      });
    }, { threshold: 0.06 });
    $$(".reveal").forEach((el) => observer.observe(el));
  } else $$(".reveal").forEach((el) => el.classList.add("visible"));

  const menu = $(".menu-toggle");
  menu.addEventListener("click", () => {
    const open = menu.getAttribute("aria-expanded") !== "true";
    menu.setAttribute("aria-expanded", String(open));
    menu.setAttribute("aria-label", open ? "Close navigation" : "Open navigation");
    $("#nav-links").classList.toggle("open", open);
  });
  $$("#nav-links a").forEach((a) => a.addEventListener("click", () => {
    menu.setAttribute("aria-expanded", "false");
    menu.setAttribute("aria-label", "Open navigation");
    $("#nav-links").classList.remove("open");
  }));

  const featureText = {
    trade: ["YOU SET THE DIRECTION.", "Intelligence behind<br>every decision.", "Explore a trading flow that brings market research, strategy review, and risk controls into one clear view.", "Simulated trades. MetaMask connection available."],
    earn: ["A SCENARIO. NOT A PROMISE.", "See the possibilities.<br>Set your assumptions.", "What might an earning experience look like? Explore hypothetical DOGE rewards with a balance, an assumed yield, and a little time.", "DOGE earning products are still in development."],
    bonuses: ["A LITTLE EXTRA CURIOSITY.", "Good things start<br>with exploration.", "Get to know Vela through three preview missions. Discover the trading flow, explore a rewards scenario, and meet the agents.", "Demo milestones only. No rewards are claimable."]
  };
  function selectTab(name, focus = false) {
    $$(".feature-tab").forEach((button) => {
      const selected = button.dataset.tab === name;
      button.classList.toggle("active", selected);
      button.setAttribute("aria-selected", String(selected));
      button.tabIndex = selected ? 0 : -1;
      if (selected && focus) button.focus();
    });
    $$(".app-panel").forEach((p) => { p.hidden = p.id !== `panel-${name}`; });
    const [eyebrow, title, copy, note] = featureText[name];
    $("#feature-copy").innerHTML = `<span class="eyebrow">${eyebrow}</span><h3>${title}</h3><p>${copy}</p><div class="feature-footnote"><span>↳</span> ${note}</div>`;
  }
  $$(".feature-tab").forEach((tab, i, tabs) => {
    tab.addEventListener("click", () => selectTab(tab.dataset.tab));
    tab.addEventListener("keydown", (event) => {
      let index;
      if (event.key === "ArrowRight" || event.key === "ArrowDown") index = (i + 1) % tabs.length;
      if (event.key === "ArrowLeft" || event.key === "ArrowUp") index = (i + tabs.length - 1) % tabs.length;
      if (event.key === "Home") index = 0;
      if (event.key === "End") index = tabs.length - 1;
      if (index !== undefined) { event.preventDefault(); selectTab(tabs[index].dataset.tab, true); }
    });
  });

  function drawChart(range) {
    const ranges = { "1H": [3.7, 0.3, "10:00", "10:30", "11:00"], "1D": [2.3, 1.8, "00:00", "12:00", "23:59"], "1W": [4.9, 3.4, "MON", "THU", "SUN"], "1M": [1.5, 6.2, "DAY 1", "DAY 15", "DAY 30"] };
    const [frequency, phase, start, middle, end] = ranges[range];
    const samples = Array.from({ length: 85 }, (_, i) => {
      const progress = i / 84;
      const shape = 129 - progress * 55 + Math.sin(progress * 14 * frequency + phase) * 8 + Math.sin(progress * 37 + phase) * 9 - Math.exp(-((progress - 0.66) ** 2) / .009) * 29;
      return [progress * 640, shape];
    });
    const correction = 75 - samples[samples.length - 1][1];
    const points = samples.map(([x, y], i) => [x, y + correction * i / 84]);
    const path = points.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(2)},${y.toFixed(2)}`).join(" ");
    $("#chart-line").setAttribute("d", path);
    $("#chart-area").setAttribute("d", `${path} L640,190 L0,190 Z`);
    $("#chart-dot").setAttribute("cx", "640");
    $("#chart-dot").setAttribute("cy", "75");
    $("#market-chart").setAttribute("aria-label", `Illustrative DOGE price chart, ${range}. Generated demo data, not live market data.`);
    $("#chart-start").textContent = start; $("#chart-middle").textContent = middle; $("#chart-end").textContent = end;
    $$(".timeframes button").forEach((button) => {
      const active = button.dataset.range === range;
      button.classList.toggle("active", active); button.setAttribute("aria-pressed", String(active));
    });
  }
  $$(".timeframes button").forEach((button) => button.addEventListener("click", () => drawChart(button.dataset.range)));
  $("#chart-reset").addEventListener("click", () => drawChart("1D"));
  drawChart("1D");
  $$(".direction-toggle button").forEach((button) => button.addEventListener("click", () => {
    side = button.dataset.side;
    $$(".direction-toggle button").forEach((b) => {
      b.classList.toggle("active", b === button); b.setAttribute("aria-pressed", String(b === button));
    });
  }));

  function toast(text) {
    clearTimeout(toastTimer);
    $("#toast").textContent = text;
    $("#toast").classList.add("visible");
    toastTimer = setTimeout(() => $("#toast").classList.remove("visible"), 3200);
  }
  function markMission(name) {
    completed.add(name);
    const percent = Math.round(completed.size / 3 * 100);
    $("#mission-count").textContent = completed.size;
    $("#progress-percent").textContent = `${percent}%`;
    $("#progress-ring").style.background = `conic-gradient(var(--gold) ${percent}%, #343a2f ${percent}%)`;
    $$(".mission").forEach((button) => {
      const done = completed.has(button.dataset.mission);
      button.classList.toggle("done", done);
      button.querySelector(".mission-status").textContent = done ? "Explored ✓" : "Explore ↗";
    });
  }
  $("#trade-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const amount = Number($("#trade-amount").value);
    if (!Number.isFinite(amount) || amount < 5 || amount > 2000) { $("#trade-amount").reportValidity(); return; }
    const qty = amount / price;
    const entryFee = amount * feeRate;
    // Same fee-inclusive trigger algebra as the copied core; this illustrative
    // new position has no realized exits. No result is sent to any trading system.
    const available = 2 - entryFee;
    const bound = side === "long" ? (price - available / qty) / (1 - feeRate) : (price + available / qty) / (1 + feeRate);
    const stop = (side === "long" ? Math.ceil(bound / tick) : Math.floor(bound / tick)) * tick;
    const net = (side === "long" ? stop - price : price - stop) * qty - entryFee - stop * qty * feeRate;
    const rows = [["Direction", `${side === "long" ? "↗ Long" : "↘ Short"} · simulated`], ["Position notional", `$${amount.toFixed(2)}`], ["Illustrative entry", `$${price.toFixed(5)}`], ["Quantity", `${qty.toFixed(4)} DOGE`], ["Inward-rounded stop trigger", `$${stop.toFixed(5)}`], ["Trigger loss incl. assumed fees", `$${Math.max(0, -net).toFixed(2)} / $2.00 budget`]];
    $("#trade-summary").replaceChildren(...rows.map(([label, value]) => {
      const row = document.createElement("div"); row.className = "summary-row";
      const key = document.createElement("span"); key.textContent = label;
      const val = document.createElement("strong"); val.textContent = value;
      row.append(key, val); return row;
    }));
    $("#trade-dialog").showModal();
    markMission("trade");
  });
  function updateEarn(mark = true) {
    const deposit = Number($("#earn-deposit").value);
    const rate = Number($("#earn-rate").value);
    const months = Number($("#earn-months").value);
    $("#earn-deposit-label").textContent = deposit.toLocaleString("en-US");
    $("#earn-month-label").textContent = months;
    if (!Number.isFinite(rate) || rate < 0 || rate > 30 || $("#earn-rate").value === "") {
      $("#earn-output").textContent = "—";
      return;
    }
    const reward = deposit * (Math.pow(1 + rate / 100, months / 12) - 1);
    $("#earn-output").textContent = reward.toLocaleString("en-US", { maximumFractionDigits: 0 });
    if (mark) markMission("earn");
  }
  ["#earn-deposit", "#earn-rate", "#earn-months"].forEach((selector) => $(selector).addEventListener("input", () => updateEarn()));
  updateEarn(false);
  $$(".mission").forEach((button) => button.addEventListener("click", () => {
    const name = button.dataset.mission;
    if (name === "agents") {
      $("#intelligence").scrollIntoView({ behavior: reducedMotion ? "instant" : "smooth" });
      selectAgent(0);
      return;
    }
    selectTab(name, true);
    if (name === "trade") $("#trade-amount").focus({ preventScroll: true });
    if (name === "earn") { $("#earn-rate").focus({ preventScroll: true }); toast("Change an assumption to explore your scenario."); }
  }));

  const agents = [
    ["RESEARCH", "Look for the possibilities.", "Explore market context and propose testable ideas. Every idea starts as a hypothesis."],
    ["CRITIC", "Give every idea a worthy challenge.", "Question assumptions, look for weaknesses, and ask what the evidence may be missing."],
    ["DIRECTION", "Make sense of the wider picture.", "Interpret market context and offer a direction for further evaluation, without bypassing risk controls."],
    ["PAPER", "Let the idea meet a simulation.", "Evaluate strategies with simulated positions before considering any real execution path."],
    ["PROMOTION", "Let evidence earn the next step.", "Review paper results and qualification criteria. A promising hypothesis alone is not approval to trade."],
    ["FEEDBACK", "Close the loop. Open a new question.", "Review outcomes and share observations with the other roles to improve the next research cycle."]
  ];
  function selectAgent(index, mark = true) {
    selectedAgent = index;
    $$(".agent-card").forEach((card, i) => { card.classList.toggle("active", i === index); card.setAttribute("aria-pressed", String(i === index)); });
    $("#agent-step").textContent = `0${index + 1} / ${agents[index][0]}`;
    $("#agent-detail-title").textContent = agents[index][1]; $("#agent-detail-copy").textContent = agents[index][2];
    if (mark) markMission("agents");
  }
  $$(".agent-card").forEach((card) => card.addEventListener("click", () => selectAgent(Number(card.dataset.agent))));
  $("#sequence-toggle").addEventListener("click", () => {
    if (sequenceTimer) {
      clearInterval(sequenceTimer); sequenceTimer = null;
      $("#sequence-toggle").innerHTML = '<span class="play-icon">▷</span> Play the agent flow';
    } else {
      selectAgent(selectedAgent);
      sequenceTimer = setInterval(() => selectAgent((selectedAgent + 1) % agents.length), 2600);
      $("#sequence-toggle").innerHTML = '<span class="play-icon">Ⅱ</span> Pause the agent flow';
    }
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden && sequenceTimer) {
      clearInterval(sequenceTimer); sequenceTimer = null;
      $("#sequence-toggle").innerHTML = '<span class="play-icon">▷</span> Play the agent flow';
    }
  });
  $("#preview-info").addEventListener("click", () => $("#info-dialog").showModal());
  $$("dialog").forEach((dialog) => {
    dialog.querySelector(".dialog-close").addEventListener("click", () => dialog.close());
    dialog.querySelector(".dialog-done")?.addEventListener("click", () => dialog.close());
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) {
        const rect = dialog.getBoundingClientRect();
        if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
      }
    });
  });
})();
