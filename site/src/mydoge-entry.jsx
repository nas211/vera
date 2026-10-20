import React from "react";
import { createRoot } from "react-dom/client";

const root = createRoot(document.getElementById("mydoge-root"));
const clientId = window.VELA_WALLET_CONFIG?.dogeosClientId?.trim();

function Pending() {
  return <section className="wallet-stage" aria-labelledby="mydoge-title">
    <span className="stage-mark" aria-hidden="true">Ð</span>
    <p className="stage-eyebrow">VELA × THE DOGEOS EXPERIENCE</p>
    <h1 id="mydoge-title">Your MyDoge.<br /><em>Your next move.</em></h1>
    <p>One familiar wallet. A new world of possibilities.</p>
    <div className="stage-notice" role="status">
      <span className="stage-dot" /> MyDoge connection is awaiting activation
      <small>We’re preparing Vela’s connection to MyDoge. In the meantime, you can explore the preview or use MetaMask on DogeOS testnet.</small>
    </div>
    <a className="stage-primary" href="/?wallet=metamask">Connect with MetaMask <span>↗</span></a>
    <a className="stage-secondary" href="/">Back to Vela <span>↗</span></a>
  </section>;
}

class WalletBoundary extends React.Component {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    if (this.state.failed) return <section className="wallet-stage"><h1>Let’s try<br /><em>that again.</em></h1><p>The wallet could not open. Refresh this page to retry.</p><a className="stage-primary" href="/mydoge.html">Try again ↗</a><a className="stage-secondary" href="/">Back to Vela ↗</a></section>;
    return this.props.children;
  }
}

if (!clientId) {
  root.render(<Pending />);
} else {
  // Load DogeOS services only on this dedicated, user-selected wallet page and
  // only after the app has its own registered client ID.
  root.render(<section className="wallet-stage"><p role="status">Opening your MyDoge experience…</p></section>);
  import("./mydoge-app.jsx").then(({ MyDogeApp }) => {
    root.render(<WalletBoundary><MyDogeApp clientId={clientId} /></WalletBoundary>);
  }).catch(() => {
    root.render(<section className="wallet-stage"><h1>A moment<br /><em>to reconnect.</em></h1><p>The wallet could not load. Check your connection and try again.</p><a className="stage-primary" href="/mydoge.html">Try again ↗</a><a className="stage-secondary" href="/">Back to Vela ↗</a></section>);
  });
}
