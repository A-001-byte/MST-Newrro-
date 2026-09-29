'use strict';
/* SwarmShield dashboard: vanilla JS, no build step. State is built only from the observer's SSE stream (/events)
   plus /state, /config/mission.json, /deployments, /scenarios. Every panel renders inside its own try/catch, so a
   missing panel or missing data never blocks the others. Cross-node times come from ev.rx (observer clock). */
(() => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const COL = { 1: '#4aa3ff', 2: '#3ddc97' };
  const shortHash = (h) => (h ? String(h).replace(/^0x/, '').slice(0, 8) : '-');
  const safeUrl = (u) => (typeof u === 'string' && /^https:\/\//.test(u) ? u : null);

  const S = {
    members: {}, byAddr: {}, zones: {}, worldCfg: null,
    up: {}, nodeState: {}, master: {}, masterH: {}, killed: new Set(), offline: new Set(), revoked: new Set(),
    cont: {}, world: [], worldAt: 0, tracks: {}, custody: {}, scores: {}, chains: { 1: null, 2: null },
    timeline: [], metrics: null, mock: false, runId: '-', deployments: {}, mst: { enrollment: null, revocation: null, anchor: null, audit: null },
    missionId: '', rosterHash: '', xcFlash: 0, xcClusters: null, lastRx: -1, lastSeq: 0, t0: null, scenario: null, dropOn: false, connected: false,
  };
  const dirty = { map: true, chains: true, tl: true, mst: true, metrics: true, strip: true };
  let queue = [];
  let raf = 0;

  const nm = (addr) => (addr && S.byAddr[String(addr).toLowerCase()] ? S.byAddr[String(addr).toLowerCase()].name : (addr ? shortHash(addr) : '?'));
  const cname = (c) => 'C' + c;

  // ------------------------------------------------------------------ reducers ---
  function tlPush(ev, kind, cls, text, link) {
    S.timeline.push({ seq: ev.seq, rx: ev.rx, kind, cls, text, link: safeUrl(link) });
    if (S.timeline.length > 600) S.timeline.splice(0, S.timeline.length - 600);
    dirty.tl = true;
  }

  function apply(ev) {
    const d = ev.data || {};
    const cl = ev.cluster;
    if (S.t0 == null) S.t0 = ev.rx;
    if (d.mock === true) S.mock = true;
    switch (ev.event) {
      case 'node_up':
        S.up[ev.name] = d; if (d.engine === 'mock') S.mock = true; dirty.strip = true; break;
      case 'node_state':
        S.nodeState[ev.name] = d;
        if (d.cluster != null && d.master && (S.masterH[d.cluster] == null || d.height >= S.masterH[d.cluster])) { S.master[d.cluster] = d.master; S.masterH[d.cluster] = d.height; }
        if (d.revoked) Object.keys(d.revoked).forEach((a) => S.revoked.add(a.toLowerCase()));
        if (d.contingency && d.cluster != null && !d.light) S.cont[d.cluster] = Object.keys(d.contingency).length > 0;
        dirty.map = dirty.strip = true; break;
      case 'commit': {
        const ch = S.chains[cl] || (S.chains[cl] = { height: 0, blocks: [] });
        if (!ch.blocks.some((b) => b.height === d.height)) {
          ch.blocks.push({ height: d.height, hash: d.blockHash, proposer: d.proposer, txCount: d.txCount, txTypes: d.txTypes || {}, signers: d.qcSigners || [], sw: d.signedWeight, aw: d.activeWeight, round: d.round, lat: d.latencyMs });
          ch.blocks.sort((a, b) => b.height - a.height);
          ch.blocks.length = Math.min(ch.blocks.length, 10);
        }
        if (d.height > ch.height) ch.height = d.height;
        dirty.chains = true; break;
      }
      case 'world': S.world = d.objects || []; S.worldAt = performance.now(); dirty.map = true; break;
      case 'track': S.tracks[cl + '|' + d.obj] = { x: d.x_cm, y: d.y_cm, conf: d.conf_pct, members: d.members, height: d.height, at: performance.now() }; dirty.map = true; break;
      case 'ownership':
        S.custody[d.obj] = d.cluster; dirty.map = true;
        if (d.via !== 'initial') tlPush(ev, 'custody', 'xc', `${d.obj} custody is now ${cname(d.cluster)} (${d.via}) @h${d.height}`);
        break;
      case 'master_changed':
        S.master[cl] = d.master; S.masterH[cl] = d.height; dirty.map = dirty.chains = true;
        tlPush(ev, 'master', 'warn', `${cname(cl)} master ${nm(d.prev)} -> ${nm(d.master)} (${d.reason}) @h${d.height}`); break;
      case 'evidence':
        tlPush(ev, 'evidence', 'bad', `${nm(d.offender)} signed two conflicting votes (${d.kind}) at h${d.height} r${d.round}`); break;
      case 'revoked':
        S.revoked.add(String(d.member).toLowerCase()); dirty.map = dirty.strip = true;
        tlPush(ev, 'revoked', 'bad', `${nm(d.member)} REVOKED (${d.reason}) from h${d.effectiveHeight}, weight removed`); break;
      case 'contingency':
        S.cont[cl] = d.mode === 'ENTER'; dirty.map = true;
        tlPush(ev, 'contingency', 'warn', `${cname(cl)} CONTINGENCY ${d.mode} (peer ${cname(d.peer)}, ${d.reason || '-'}) @h${d.height}`); break;
      case 'history_merged':
        tlPush(ev, 'history', 'info', `${cname(cl)} merged ${d.blocks} blocks from ${cname(d.peer)} (${d.fromHeight}-${d.toHeight})`); break;
      case 'xc_sent':
        S.xcFlash = performance.now() + 3500; S.xcClusters = [cl, d.dst]; dirty.map = true;
        tlPush(ev, 'xc_sent', 'xc', `${cname(cl)} XC_SEND #${d.seq} to ${cname(d.dst)} for ${d.obj || '?'} committed @h${d.height}`); break;
      case 'xc_verified':
        tlPush(ev, 'xc_verified', 'xc', `${cname(cl)} verified ${cname(d.src)} certificate #${d.seq}: weight ${d.signedWeight}/${d.activeWeight} from ${(d.signers || []).length} signers (relayed by ${nm(d.relayer)})`); break;
      case 'xc_rejected':
        tlPush(ev, 'xc_rejected', 'bad', `${cname(cl)} rejected packet #${d.seq} from ${cname(d.src)}: ${d.reason}`); break;
      case 'xc_acked':
        tlPush(ev, 'xc_acked', 'ok', `${cname(cl)} ack #${d.seq} from ${cname(d.dst)} committed @h${d.height}, custody released`); break;
      case 'xc_timeout':
        tlPush(ev, 'xc_timeout', 'warn', `${cname(cl)} handoff #${d.seq} to ${cname(d.dst)} timed out, custody kept`); break;
      case 'topology':
        S.killed = new Set((d.nodes || []).filter((n) => n.killed).map((n) => n.node.toLowerCase()));
        S.offline = new Set((d.nodes || []).filter((n) => n.online === false).map((n) => n.node.toLowerCase()));
        S.partition = d.partition; S.dropOn = (d.globalDropRate || 0) > 0; dirty.map = dirty.strip = true; break;
      case 'fault_injected': {
        const who = d.name || (d.node === '*' ? 'all' : nm(d.node));
        tlPush(ev, 'fault', 'bad', `FAULT ${d.mode}${d.rate != null ? ' ' + Math.round(d.rate * 100) + '%' : ''}${d.node === '*' ? '' : ' on ' + who}`); break;
      }
      case 'scenario':
        S.scenario = d;
        if (d.state === 'step') { if (d.do === 'note') tlPush(ev, 'scenario', 'info', d.message || ''); }
        else tlPush(ev, 'scenario', 'info', `scenario ${d.name} ${d.state}`);
        break;
      case 'member_score': {
        const was = S.scores[d.member]; S.scores[d.member] = d;
        if (d.flagged && !(was && was.flagged)) tlPush(ev, 'score', 'warn', `${nm(d.member)} observations deviate ${d.deviation_cm} cm from the fused track (flagged, not provable, not revoked)`);
        dirty.strip = true; break;
      }
      case 'enrollment':
        S.mst.enrollment = d; dirty.mst = true; tlPush(ev, 'enrollment', 'mst', `mission enrolled on MST, rosterHash ${shortHash(d.rosterHash)}`, d.explorerUrl); break;
      case 'onchain_revocation':
        S.mst.revocation = d; dirty.mst = true; tlPush(ev, 'onchain_rev', 'mst', `on-chain revocation of ${nm(d.member)} (${d.reason || ''})`, d.explorerUrl); break;
      case 'anchor':
        S.mst.anchor = d; dirty.mst = true; tlPush(ev, 'anchor', 'mst', `${cname(d.cluster)} anchored h${d.height} ${shortHash(d.blockHash)}`, d.explorerUrl); break;
      case 'audit':
        S.mst.audit = d; dirty.mst = true; tlPush(ev, 'audit', d.ok ? 'ok' : 'bad', `audit cluster ${d.cluster}: ${d.ok ? 'OK' : 'FAILED'} ${d.detail || ''}`); break;
      case 'metrics': S.metrics = d; dirty.metrics = true; break;
      default: break;
    }
  }

  // -------------------------------------------------------------------- render ---
  function guard(name, fn) { try { fn(); } catch (e) { console.warn('panel ' + name + ' failed:', e); } }
  function frame() {
    raf = 0;
    const batch = queue; queue = [];
    for (const ev of batch) guard('reduce ' + ev.event, () => apply(ev));
    if (dirty.map || performance.now() < S.xcFlash + 200) guard('map', renderMap);
    if (dirty.strip) guard('strip', renderStrip);
    if (dirty.chains) guard('chains', renderChains);
    if (dirty.tl) guard('timeline', renderTimeline);
    if (dirty.mst) guard('mst', renderMst);
    if (dirty.metrics) guard('metrics', renderMetrics);
    $('mockBanner').hidden = !S.mock;
    if (performance.now() < S.xcFlash + 200) schedule();
  }
  function schedule() { if (!raf) raf = requestAnimationFrame(frame); }

  const isKilled = (m) => S.killed.has(m.address) || S.offline.has(m.address);
  function renderMap() {
    dirty.map = false;
    const svg = $('mapSvg');
    const W = 200, H = 100;
    let s = '';
    for (const c of [1, 2]) {
      const z = S.zones[c] || { xMin_cm: (c - 1) * 10000, xMax_cm: c * 10000 };
      s += `<rect x="${z.xMin_cm / 100}" y="0" width="${(z.xMax_cm - z.xMin_cm) / 100}" height="${H}" fill="${COL[c]}" fill-opacity="0.09" stroke="${COL[c]}" stroke-opacity="0.5" stroke-dasharray="2 1"/>`;
      s += `<text x="${(z.xMin_cm + z.xMax_cm) / 200}" y="-3" text-anchor="middle" font-size="4" fill="${COL[c]}" style="fill:${COL[c]}">Zone ${c === 1 ? 'A' : 'B'} / cluster ${c}${S.cont[c] ? '  CONTINGENCY' : ''}</text>`;
      if (S.cont[c]) s += `<rect x="${z.xMin_cm / 100}" y="0" width="${(z.xMax_cm - z.xMin_cm) / 100}" height="${H}" fill="#ffb454" fill-opacity="0.08"/>`;
    }
    s += `<line x1="100" y1="0" x2="100" y2="${H}" stroke="#8b9ab5" stroke-width="0.3"/>`;
    s += `<line x1="90" y1="0" x2="90" y2="${H}" stroke="#ffb454" stroke-width="0.3" stroke-dasharray="1.5 1.5"/><text x="90" y="${H + 4}" text-anchor="middle" font-size="2.6" style="fill:#ffb454">handoff line 90 m</text>`;
    const mem = Object.values(S.members);
    // certified packet flash between the two masters
    if (performance.now() < S.xcFlash) {
      const a = S.members[nm(S.master[1])], b = S.members[nm(S.master[2])];
      if (a && b) s += `<line x1="${a.pos[0] / 100}" y1="${a.pos[1] / 100}" x2="${b.pos[0] / 100}" y2="${b.pos[1] / 100}" stroke="#b18cff" stroke-width="0.8" stroke-dasharray="2 1.5"/>`;
    }
    for (const m of mem) {
      const x = m.pos[0] / 100, y = m.pos[1] / 100, col = COL[m.cluster] || '#999';
      const dead = isKilled(m), rev = S.revoked.has(m.address), isMaster = S.master[m.cluster] === m.address;
      const fill = dead ? '#6b7688' : col;
      if (m.light) s += `<rect x="${x - 1.9}" y="${y - 1.9}" width="3.8" height="3.8" transform="rotate(45 ${x} ${y})" fill="${dead ? '#6b7688' : '#ffb454'}" stroke="${col}" stroke-width="0.5"/>`;
      else s += `<circle cx="${x}" cy="${y}" r="2" fill="${fill}"/>`;
      if (isMaster && !rev) s += `<circle cx="${x}" cy="${y}" r="3.4" fill="none" stroke="#fff" stroke-width="0.6"/>`;
      if (rev) s += `<path d="M${x - 2.6} ${y - 2.6}L${x + 2.6} ${y + 2.6}M${x + 2.6} ${y - 2.6}L${x - 2.6} ${y + 2.6}" stroke="#ff5d6c" stroke-width="0.9"/>`;
      s += `<text x="${x}" y="${y + 5.6}" text-anchor="middle" font-size="2.4" style="fill:${dead ? '#8b9ab5' : ''}">${esc(m.name)}</text>`;
    }
    // true positions (hollow) and accepted tracks (filled)
    for (const o of S.world) {
      const x = o.x_cm / 100, y = o.y_cm / 100;
      s += `<circle cx="${x}" cy="${y}" r="2.6" fill="none" stroke="#fff" stroke-width="0.5" stroke-dasharray="1 0.6"/>`;
      s += `<text x="${x}" y="${y - 4}" text-anchor="middle" font-size="2.6" font-weight="700">${esc(o.id)}</text>`;
      const cust = S.custody[o.id];
      if (cust != null) s += `<g><rect x="${x - 5}" y="${y + 3.4}" width="10" height="3.6" rx="1" fill="${COL[cust] || '#999'}"/><text x="${x}" y="${y + 6}" text-anchor="middle" font-size="2.4" style="fill:#0e1420" font-weight="700">custody C${cust}</text></g>`;
    }
    const now = performance.now();
    for (const [k, t] of Object.entries(S.tracks)) {
      if (now - t.at > 6000) continue;
      const c = Number(k.split('|')[0]);
      const off = c === 1 ? -1.1 : 1.1;
      s += `<circle cx="${t.x / 100}" cy="${t.y / 100 + off}" r="1.5" fill="${COL[c]}" stroke="#fff" stroke-width="0.3"><title>${esc(k)} track h${t.height} conf ${t.conf}% from ${t.members} drones</title></circle>`;
    }
    svg.innerHTML = s;
  }

  function renderStrip() {
    dirty.strip = false;
    const names = Object.keys(S.members).sort();
    $('nodeStrip').innerHTML = names.map((n) => {
      const m = S.members[n], st = S.nodeState[n] || {};
      const dead = isKilled(m), rev = S.revoked.has(m.address), sc = S.scores[m.address];
      const flag = sc && sc.flagged ? ' lying?' : '';
      const fault = st.fault && st.fault !== 'honest' ? ' [' + st.fault + ']' : '';
      return `<span class="chip c${m.cluster}${dead ? ' dead' : ''}${rev ? ' rev' : ''}">${esc(n)}${m.light ? ' (board)' : ''} h${st.height != null ? st.height : '-'}${S.master[m.cluster] === m.address ? ' master' : ''}${rev ? ' REVOKED' : ''}${esc(fault)}${flag}</span>`;
    }).join('');
  }

  function renderChains() {
    dirty.chains = false;
    for (const c of [1, 2]) {
      const el = $('chain' + c), ch = S.chains[c];
      const members = Object.values(S.members).filter((m) => m.cluster === c);
      if (!ch || !ch.blocks.length) { el.className = 'chain c' + c; el.innerHTML = `<h3>Cluster ${c}</h3><div class="empty">waiting for the first block</div>`; continue; }
      const top = ch.blocks[0];
      const dots = members.map((m) => `<span class="qd w${m.weight >= 3 ? 3 : 1}${top.signers.map((a) => a.toLowerCase()).includes(m.address) ? ' f' : ''}" title="${esc(m.name)} weight ${m.weight}"></span>`).join('');
      const rows = ch.blocks.map((b) => `<li><span>#${b.height}</span><span>${shortHash(b.hash)}</span><span>${esc(nm(b.proposer))}</span><span>r${b.round}</span>${Object.entries(b.txTypes).map(([t, n]) => `<span class="tb ${esc(t)}">${esc(t)}${n > 1 ? 'x' + n : ''}</span>`).join('')}</li>`).join('');
      el.className = 'chain c' + c;
      el.innerHTML = `<h3>Cluster ${c}</h3>
        <div class="chainhead"><span>height <b>${top.height}</b></span><span>block <b class="mono">${shortHash(top.hash)}</b></span><span>proposer <b>${esc(nm(top.proposer))}</b></span><span>master <b>${esc(nm(S.master[c]))}</b></span><span>QC <b>${top.sw}/${top.aw}</b></span><span>round <b>${top.round}</b></span></div>
        <div class="qc">${dots}</div><ul class="blocks">${rows}</ul>`;
    }
  }

  function renderTimeline() {
    dirty.tl = false;
    const q = $('filter').value.trim().toLowerCase();
    const t0 = S.t0 == null ? 0 : S.t0;
    const rows = [];
    for (let i = S.timeline.length - 1; i >= 0 && rows.length < 250; i--) {
      const e = S.timeline[i];
      const hay = (e.kind + ' ' + e.text).toLowerCase();
      if (q && !hay.includes(q)) continue;
      const sec = Math.max(0, Math.round((e.rx - t0) / 1000));
      const mm = String(Math.floor(sec / 60)).padStart(2, '0') + ':' + String(sec % 60).padStart(2, '0');
      rows.push(`<li><span class="t">${mm}</span><span class="k ${e.cls}">${esc(e.kind)}</span><span>${esc(e.text)}${e.link ? ` <a href="${esc(e.link)}" target="_blank" rel="noopener">explorer</a>` : ''}</span></li>`);
    }
    $('tl').innerHTML = rows.join('') || '<li class="empty">no events yet</li>';
  }

  function renderMst() {
    dirty.mst = false;
    const dep = S.deployments || {};
    const contracts = dep.contracts || {};
    const explorer = dep.explorer || 'https://testnet.mstscan.com';
    let h = '';
    if (!Object.keys(contracts).length) h += '<div class="empty">Contracts not deployed yet (no deployment record). This panel fills in when contracts/deployments/mst-testnet.json exists.</div>';
    else {
      h += '<table>' + Object.entries(contracts).map(([n, c]) => `<tr><td>${esc(n)}</td><td class="mono"><a href="${esc(explorer)}/address/${esc(c.address)}" target="_blank" rel="noopener">${esc(c.address)}</a></td></tr>`).join('') + '</table>';
      if (dep.chainId) h += `<div class="hint">MST testnet chain ${esc(dep.chainId)}</div>`;
    }
    const mis = (dep.missions || {})[S.missionId];
    if (mis) {
      const tx = (h) => `<a class="mono" href="${esc(explorer)}/tx/${esc(h)}" target="_blank" rel="noopener">${esc(shortHash(h))}</a>`;
      const same = mis.rosterHash && S.rosterHash && String(mis.rosterHash).toLowerCase() === String(S.rosterHash).toLowerCase();
      let r = `<tr><td>rosterHash on-chain</td><td>${same ? '<b style="color:var(--ok)">matches mission.json</b>' : '<b style="color:var(--bad)">DIFFERS from mission.json</b>'} <span class="mono">${esc(shortHash(mis.rosterHash))}</span></td></tr>`;
      r += `<tr><td>create / finalize</td><td>${mis.createTx ? tx(mis.createTx) : '-'} / ${mis.finalizeTx ? tx(mis.finalizeTx) : '-'}</td></tr>`;
      r += `<tr><td>enroll</td><td>${(mis.enrollTxs || []).map(tx).join(' ') || '-'}</td></tr>`;
      r += `<tr><td>revocations</td><td>${(mis.revocations || []).map((v) => esc(nm(v.member)) + ' ' + esc(v.reason || '') + ' ' + tx(v.txHash)).join('<br>') || '-'}</td></tr>`;
      r += `<tr><td>anchors</td><td>${(mis.anchors || []).map((a) => 'C' + esc(a.cluster) + ' h' + esc(a.height) + ' ' + tx(a.txHash)).join('<br>') || '-'}</td></tr>`;
      r += `<tr><td>handoff receipts</td><td>${(mis.handoffs || []).map((x) => 'C' + esc(x.src) + '&rarr;C' + esc(x.dst) + ' #' + esc(x.xcSeq) + ' ' + tx(x.txHash)).join('<br>') || '-'}</td></tr>`;
      h += '<div class="hint">This mission, from the deployment record</div><table>' + r + '</table>';
    } else if (Object.keys(contracts).length) {
      h += '<div class="empty">Deployment record has no entry for this mission id yet.</div>';
    }
    const line = (label, e, text) => `<tr><td>${label}</td><td>${e ? esc(text(e)) + (safeUrl(e.explorerUrl) ? ` <a href="${esc(e.explorerUrl)}" target="_blank" rel="noopener">tx</a>` : '') : '<span class="empty">none yet</span>'}</td></tr>`;
    h += '<table>' + line('Latest enrollment', S.mst.enrollment, (e) => 'rosterHash ' + shortHash(e.rosterHash))
      + line('Latest revocation', S.mst.revocation, (e) => nm(e.member) + ' ' + (e.reason || ''))
      + line('Latest anchor', S.mst.anchor, (e) => 'C' + e.cluster + ' h' + e.height + ' ' + shortHash(e.blockHash))
      + line('Audit', S.mst.audit, (e) => (e.ok ? 'OK ' : 'FAILED ') + (e.detail || '')) + '</table>';
    $('mstBody').innerHTML = h;
  }

  function fmt(st, unit) {
    if (!st || st.n === 0 || st.p50 == null) return '<span class="empty">-</span>';
    const f = (v) => (v == null ? '-' : v + (unit || ' ms'));
    return `${f(st.p50)}${st.p95 != null && st.p95 !== st.p50 ? ' / ' + f(st.p95) : ''} <span class="hint">n=${st.n}</span>`;
  }
  function renderMetrics() {
    dirty.metrics = false;
    const m = S.metrics;
    if (!m) { $('metricsBody').innerHTML = '<div class="empty">no metrics yet</div>'; return; }
    const cl = m.clusters || {};
    let h = `<table><tr><th>metric</th><th>p50 / p95</th></tr>`;
    for (const c of Object.keys(cl).sort()) {
      h += `<tr><td>C${c} commit latency (engine)</td><td>${fmt(cl[c].commit_latency_ms)}</td></tr>`;
      h += `<tr><td>C${c} block interval</td><td>${fmt(cl[c].block_interval_ms)}</td></tr>`;
      h += `<tr><td>C${c} rounds per height</td><td>${cl[c].rounds && cl[c].rounds.avg_x100 != null ? (cl[c].rounds.avg_x100 / 100).toFixed(2) + ' avg, max ' + cl[c].rounds.max : '-'}</td></tr>`;
    }
    h += `<tr><td>Handoff: send to receive commit</td><td>${fmt(m.handoff_recv_ms)}</td></tr>
      <tr><td>Handoff: send to ack commit</td><td>${fmt(m.handoff_ack_ms)}</td></tr>
      <tr><td>Master failover (kill to ROLE)</td><td>${fmt(m.failover_ms)}</td></tr>
      <tr><td>Revocation (evidence to revoked)</td><td>${fmt(m.revocation_ms)}</td></tr>
      <tr><td>Contingency: cut to ENTER</td><td>${fmt(m.contingency_enter_ms)}</td></tr>
      <tr><td>Contingency: heal to EXIT</td><td>${fmt(m.contingency_exit_ms)}</td></tr>
      <tr><td>Messages per commit (hub)</td><td>${m.msgs_per_commit == null ? '-' : m.msgs_per_commit}</td></tr>
      <tr><td>Bytes per commit (hub)</td><td>${m.bytes_per_commit == null ? '-' : m.bytes_per_commit}</td></tr></table>`;
    if (m.mock) h += '<div class="hint">MOCK run: these numbers describe the simulator, not the real engine.</div>';
    $('metricsBody').innerHTML = h;
  }

  // ------------------------------------------------------------------ controls ---
  function toast(msg, ok) { const t = $('toast'); t.textContent = msg; t.className = 'toast ' + (ok ? 'ok' : 'bad'); }
  async function post(path, body, label) {
    try {
      const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
      const j = await r.json().catch(() => ({}));
      toast((label || path) + (j.ok ? ': ok' : ': ' + (j.error || 'failed')), !!j.ok);
      return j;
    } catch (e) { toast((label || path) + ': network error', false); return null; }
  }
  function wire() {
    const on = (id, fn) => { const el = $(id); if (el) el.addEventListener('click', fn); };
    on('btnScn', () => post('/control/scenario', { name: $('scnSel').value }, 'scenario ' + $('scnSel').value));
    on('btnScnStop', () => post('/control/scenario/stop', {}, 'scenario stop'));
    on('btnObj1', () => post('/control/object', { obj: 'OBJ-1', action: 'start' }, 'start OBJ-1'));
    on('btnObj2', () => post('/control/object', { obj: 'OBJ-2', action: 'start' }, 'start OBJ-2'));
    on('btnKill', () => { const a = S.master[1]; if (!a) return toast('no C1 master known yet', false); post('/control/kill', { node: a }, 'kill ' + nm(a)); });
    on('btnRevive', async () => { const dead = [...S.killed]; if (!dead.length) return toast('nothing is killed', false); for (const a of dead) await post('/control/revive', { node: a }, 'revive ' + nm(a)); });
    on('btnPart', () => post('/control/partition', { groups: [['cluster:1'], ['cluster:2']] }, 'partition'));
    on('btnHeal', () => post('/control/heal', {}, 'heal'));
    on('btnDrop', () => post('/control/drop', { node: '*', rate: S.dropOn ? 0 : 0.2 }, S.dropOn ? 'drop 0%' : 'drop 20%'));
    on('btnEquiv', () => post('/control/fault', { node: $('faultSel').value, mode: 'equivocate' }, 'equivocate ' + $('faultSel').value));
    on('btnFalse', () => post('/control/fault', { node: $('faultSel').value, mode: 'false_obs' }, 'false_obs ' + $('faultSel').value));
    on('btnExport', () => post('/control/export', {}, 'export run'));
    $('filter').addEventListener('input', () => { dirty.tl = true; schedule(); });
  }

  // ------------------------------------------------------------------- bootstrap ---
  async function getJson(url) { try { const r = await fetch(url); return r.ok ? await r.json() : null; } catch (e) { return null; } }
  async function loadStatic() {
    const m = await getJson('/config/mission.json');
    if (m && m.clusters) {
      for (const c of m.clusters) {
        S.zones[c.clusterId] = c.zone;
        for (const mm of c.members) {
          const rec = { name: mm.name, address: String(mm.address).toLowerCase(), cluster: c.clusterId, light: (mm.flags & 1) === 1, pos: mm.pos_cm, weight: mm.weight, host: mm.host };
          S.members[mm.name] = rec; S.byAddr[rec.address] = rec;
        }
        S.master[c.clusterId] = S.master[c.clusterId] || (S.members[c.initialMaster] || {}).address;
      }
      S.missionId = String(m.missionId || '').toLowerCase(); S.rosterHash = m.rosterHash;
      if (String(m._comment || '').includes('DEV/MOCK')) S.mock = true;
      const sel = $('faultSel');
      sel.innerHTML = Object.values(S.members).filter((x) => !x.light).map((x) => `<option value="${esc(x.name)}"${x.name === 'c1-d3' ? ' selected' : ''}>${esc(x.name)}</option>`).join('');
      dirty.map = dirty.strip = dirty.chains = true;
    }
    const sc = await getJson('/scenarios');
    if (sc && sc.scenarios) $('scnSel').innerHTML = sc.scenarios.map((n) => `<option value="${esc(n)}"${n === 'demo' ? ' selected' : ''}>${esc(n)}</option>`).join('');
    await refreshSlow();
    schedule();
  }
  async function refreshSlow() {
    const dep = await getJson('/deployments');
    S.deployments = dep || {}; dirty.mst = true;
    const st = await getJson('/state');
    if (st) {
      S.runId = st.runId || S.runId; $('runId').textContent = 'run: ' + S.runId;
      if (st.mock) S.mock = true;
      if (!S.metrics && st.metrics) { S.metrics = st.metrics; dirty.metrics = true; }
    }
    schedule();
  }
  function connect() {
    const es = new EventSource('/events');
    es.onopen = () => { S.connected = true; const c = $('conn'); c.textContent = 'live'; c.className = 'badge on'; };
    es.onerror = () => { S.connected = false; const c = $('conn'); c.textContent = 'reconnecting'; c.className = 'badge off'; };
    es.onmessage = (m) => {
      let ev; try { ev = JSON.parse(m.data); } catch (e) { return; }
      if (!ev || !ev.event) return;
      if (ev.rx < S.lastRx || (ev.rx === S.lastRx && ev.seq <= S.lastSeq)) return;   // reconnect replay: already applied
      S.lastRx = ev.rx; S.lastSeq = ev.seq;
      queue.push(ev); schedule();
    };
  }
  window.addEventListener('DOMContentLoaded', () => {
    wire(); loadStatic(); connect();
    setInterval(refreshSlow, 10000);
    setInterval(() => { dirty.map = true; schedule(); }, 1000);   // let stale tracks fade
  });
})();
