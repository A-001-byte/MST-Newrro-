import { lazy, Suspense, useCallback, useEffect, useState } from 'react';
import { AnimatePresence, MotionConfig, motion } from 'framer-motion';
import { Landing } from './components/landing/Landing';
import { BridgeKeyProvider } from './hooks/useBridgeKey';

const Dashboard = lazy(() => import('./components/dashboard/Dashboard'));

type Route = 'landing' | 'live';
const readRoute = (): Route => (window.location.hash.startsWith('#/live') ? 'live' : 'landing');

export function App() {
  const [route, setRoute] = useState<Route>(readRoute);

  useEffect(() => {
    const onHash = () => setRoute(readRoute());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  const navigate = useCallback((to: Route, extra = '') => {
    const hash = to === 'live' ? `#/live${extra}` : '#/';
    if (window.location.hash !== hash) window.location.hash = hash;
    setRoute(to);
    window.scrollTo({ top: 0 });
  }, []);

  // Warm the dashboard chunk while the hero is on screen
  useEffect(() => {
    const t = window.setTimeout(() => void import('./components/dashboard/Dashboard'), 1200);
    return () => window.clearTimeout(t);
  }, []);

  return (
    <MotionConfig reducedMotion="user">
      <BridgeKeyProvider>
        <AnimatePresence mode="wait" initial={false}>
          {route === 'landing' ? (
            <motion.div
              key="landing"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0, scale: 1.02, filter: 'blur(6px)' }}
              transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
            >
              <Landing onLaunch={(extra) => navigate('live', extra)} />
            </motion.div>
          ) : (
            <motion.div
              key="live"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
            >
              <Suspense fallback={<div className="min-h-screen bg-black" />}>
                <Dashboard onExit={() => navigate('landing')} />
              </Suspense>
            </motion.div>
          )}
        </AnimatePresence>
      </BridgeKeyProvider>
    </MotionConfig>
  );
}
