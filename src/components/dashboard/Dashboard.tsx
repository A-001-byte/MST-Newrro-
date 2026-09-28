import { useCallback, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { SwarmProvider, useSwarm } from '../../hooks/useSwarm';
import { TopNav } from './TopNav';
import { ConnectionPill } from './ConnectionPill';
import { StatCards } from './StatCards';
import { ThreatBanner } from './ThreatBanner';
import { SectorMap } from './SectorMap';
import { CorroborationPanel } from './CorroborationPanel';
import { BoardCard } from './BoardCard';
import { TxFeed } from './TxFeed';
import { Leaderboard } from './Leaderboard';
import { TaskMarket } from './TaskMarket';
import { DemoDock } from './DemoDock';
import { NodeDrawer } from './NodeDrawer';
import { ContractsModal } from './ContractsModal';
import { RegisterNodeModal } from './RegisterNodeModal';

export default function Dashboard({ onExit }: { onExit: () => void }) {
  return (
    <SwarmProvider>
      <DashboardView onExit={onExit} />
    </SwarmProvider>
  );
}

function DashboardView({ onExit }: { onExit: () => void }) {
  const { state } = useSwarm();
  const [selected, setSelected] = useState<string | null>(null);
  const [contractsOpen, setContractsOpen] = useState(() => window.location.hash.includes('/contracts'));
  const [registerOpen, setRegisterOpen] = useState(false);
  const threat = state.threat;

  const openContracts = useCallback(() => setContractsOpen(true), []);
  const closeContracts = useCallback(() => {
    setContractsOpen(false);
    if (window.location.hash.includes('/contracts')) history.replaceState(null, '', '#/live');
  }, []);
  const closeDrawer = useCallback(() => setSelected(null), []);
  const closeRegister = useCallback(() => setRegisterOpen(false), []);

  return (
    <div className="relative min-h-screen overflow-x-clip bg-black text-white" data-threat={!!threat}>
      {/* Backdrop: grid, cool glow, grain */}
      <div aria-hidden className="pointer-events-none fixed inset-0">
        <div className="absolute inset-0 bg-grid" />
        <div className="absolute inset-x-0 top-0 h-[520px] bg-[radial-gradient(ellipse_60%_100%_at_50%_0%,rgb(59_130_246/0.10),transparent_70%)]" />
        <div className="absolute inset-0 bg-noise" />
      </div>

      {/* Threat tone: red edge glow over everything */}
      <AnimatePresence>
        {threat && (
          <motion.div
            aria-hidden
            key="threat-glow"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.5 }}
            className="pointer-events-none fixed inset-0 z-40 shadow-[inset_0_0_0_1px_rgb(244_63_94/0.45),inset_0_0_120px_rgb(244_63_94/0.22)]"
          >
            <div className="absolute inset-x-0 top-0 h-48 bg-[radial-gradient(ellipse_60%_100%_at_50%_0%,rgb(244_63_94/0.14),transparent_70%)]" />
          </motion.div>
        )}
      </AnimatePresence>

      <TopNav onExit={onExit} onOpenContracts={openContracts} onOpenRegister={() => setRegisterOpen(true)} />
      <ConnectionPill />

      <main className="relative mx-auto w-full max-w-[1600px] space-y-3 px-3 pt-3 pb-40 sm:px-6 sm:pb-32">
        <AnimatePresence initial={false}>{threat && <ThreatBanner key={threat.confirmedAt} threat={threat} />}</AnimatePresence>

        <StatCards />

        <div className="grid grid-cols-1 gap-3 lg:grid-cols-12">
          <SectorMap
            onSelect={setSelected}
            className="lg:col-span-7 lg:row-span-2 xl:col-span-6 xl:col-start-1 xl:row-start-1"
          />
          <CorroborationPanel className="lg:col-span-5 xl:col-span-3 xl:col-start-7 xl:row-start-1" />
          <BoardCard onSelect={setSelected} className="lg:col-span-5 xl:col-span-3 xl:col-start-7 xl:row-start-2" />
          <TxFeed className="h-[520px] lg:col-span-5 lg:h-[640px] xl:col-span-3 xl:col-start-10 xl:row-span-3 xl:row-start-1 xl:h-auto" />
          <Leaderboard onSelect={setSelected} className="lg:col-span-7 xl:col-span-9 xl:col-start-1 xl:row-start-3" />
          <TaskMarket className="lg:col-span-12 xl:col-span-12" />
        </div>
      </main>

      <DemoDock />
      <NodeDrawer address={selected} onClose={closeDrawer} />
      <ContractsModal open={contractsOpen} onClose={closeContracts} />
      <RegisterNodeModal open={registerOpen} onClose={closeRegister} />
    </div>
  );
}
