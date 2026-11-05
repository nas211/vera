import '../polyfills';
import '../index.css';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { HashRouter } from 'react-router-dom';
import App from '../App';

const root = document.getElementById('root');
if (!root) throw new Error('Missing preview root');

// No Web3Provider, wallet restoration, authentication, or service transport.
// Hash routes stay beneath the preview URL, including on refresh.
createRoot(root).render(<StrictMode><HashRouter><App preview /></HashRouter></StrictMode>);
