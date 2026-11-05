import { Routes, Route } from 'react-router-dom';
import { Toaster } from '@/components/ui/toaster';
import { AppShell } from '@/components/layout/AppShell';
import { FixtureProvider } from '@/state/FixtureProvider';
import { PreviewMode } from '@/state/PreviewMode';
import NotFound from './pages/NotFound';
import Overview from './pages/Overview';
import VaultRewards from './pages/VaultRewards';
import BorrowRepay from './pages/BorrowRepay';
import Trading from './pages/Trading';
import RiskActivity from './pages/RiskActivity';

const App = ({ preview = false }: { preview?: boolean }) => (
    <PreviewMode.Provider value={preview}>
    <FixtureProvider>
        <Routes>
            <Route element={<AppShell />}>
                <Route path="/" element={<Overview />} />
                <Route path="/vault" element={<VaultRewards />} />
                <Route path="/borrow" element={<BorrowRepay />} />
                <Route path="/trading" element={<Trading />} />
                <Route path="/risk" element={<RiskActivity />} />
            </Route>
            <Route path="*" element={<NotFound />} />
        </Routes>
        <Toaster />
    </FixtureProvider>
    </PreviewMode.Provider>
);

export default App;
