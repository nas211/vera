// The SDK ships compiled Tailwind 4 CSS. Keep it out of our Tailwind 3 compiler.
import css from '../../../../node_modules/@dogeos/dogeos-sdk/dist/dogeos-sdk.css?raw';
if (!document.getElementById('vela-dogeos-wallet-styles')) {
  const style=document.createElement('style');style.id='vela-dogeos-wallet-styles';style.textContent=css;document.head.append(style);
}
