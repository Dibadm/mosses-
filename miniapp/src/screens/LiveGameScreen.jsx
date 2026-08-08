// src/screens/LiveGameScreen.jsx
import { useEffect, useState, useRef, useCallback } from 'react';
import { useStore } from '../lib/store';
import { usePolling } from '../lib/usePolling';
import { api } from '../lib/api';
import { fmt, Balance } from '../components/Chrome';
import { haptic, mainButton, showAlert } from '../lib/telegram';
import FadeIn from '../components/FadeIn';

const LETTERS = ['B', 'I', 'N', 'G', 'O'];

export default function LiveGameScreen({ gameId, onFinished }) {
  const { runAction, refreshUser } = useStore();
  const [state, setState] = useState(null);
  const [audioOn, setAudioOn] = useState(true);
  const lastSpokenRef = useRef(null);

  const speakAmharic = useCallback((text) => {
    try {
      const synth = window.speechSynthesis;
      if (!synth) return;
      const u = new SpeechSynthesisUtterance(text);
      u.lang = 'am-ET';
      u.rate = 0.95;
      synth.cancel();
      synth.speak(u);
    } catch { /* no TTS available */ }
  }, []);

  const playAnnouncement = useCallback((number, lastCall) => {
    if (!audioOn) return;
    let fellBack = false;
    const fallback = () => {
      if (!fellBack && lastCall) {
        fellBack = true;
        speakAmharic(`${lastCall.letter} ${lastCall.amharic}`);
      }
    };
    const audio = new Audio(`/audio/${number}.mp3`);
    audio.onerror = fallback;
    audio.play().catch(fallback);
  }, [audioOn, speakAmharic]);

  const fetchGameState = useCallback(async () => {
    const res = await api.getGameState(gameId);
    setState(res);
    if (res.state === 'running' && res.last_call) {
      const n = res.last_call.number;
      if (lastSpokenRef.current !== n) {
        lastSpokenRef.current = n;
        playAnnouncement(n, res.last_call);
      }
    }
    if (res.state === 'finished') {
      await refreshUser();
    }
    return res;
  }, [gameId, refreshUser, playAnnouncement]);

  const baseInterval = state === 'running' ? 3000 : 5000;
  const { data, error, loading, resetBackoff } = usePolling(fetchGameState, {
    interval: baseInterval,
    backoffMax: 30000,
    immediate: true,
  });

  useEffect(() => {
    resetBackoff();
  }, [gameId, resetBackoff]);

  const effectiveState = state || data;

  const toggleAuto = async () => {
    if (!effectiveState) return;
    const next = !effectiveState.auto_win;
    haptic.light();
    await runAction(() => api.toggleAutoWin(gameId, next));
    const fresh = await runAction(() => api.getGameState(gameId));
    setState(fresh);
  };

  const claimBingo = async () => {
    haptic.medium();
    try {
      await runAction(() => api.claimBingo(gameId));
    } catch {
      // already toasted
    }
  };

  const markNumber = async (cardIndex, number) => {
    if (!effectiveState) return;

    setState(prev => {
      if (!prev) return prev;
      const nextCards = prev.my_cards.map(card => {
        if (card.card_index !== cardIndex) return card;
        const isMarked = (card.marked || []).includes(number);
        const newMarked = isMarked
          ? card.marked
          : [...(card.marked || []), number];
        return { ...card, marked: newMarked };
      });
      return { ...prev, my_cards: nextCards };
    });

    haptic.light();
    try {
      const res = await runAction(() => api.markNumber(gameId, cardIndex, number));
      if (res.marked) {
        setState(prev => {
          if (!prev) return prev;
          const nextCards = prev.my_cards.map(card => {
            if (card.card_index !== cardIndex) return card;
            return { ...card, marked: res.marked };
          });
          return { ...prev, my_cards: nextCards };
        });
      }
    } catch {
      // fire-and-forget; next poll reconciles from server
    }
  };

  useEffect(() => {
    if (!effectiveState || effectiveState.state !== 'running') {
      mainButton.hide();
      return;
    }
    mainButton.show('🎯 BINGO!', claimBingo);
    return () => mainButton.offClick(claimBingo);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [effectiveState?.state]);

  if (!effectiveState) {
    return (
      <FadeIn>
        <div className="screen">
          <div className="empty-state">
            <div className="spinner" />
            <div className="empty-state-title">Loading game…</div>
          </div>
        </div>
      </FadeIn>
    );
  }

  if (effectiveState.state === 'waiting') {
    const remaining = effectiveState.countdown_seconds_remaining ?? null;
    const total = effectiveState.countdown_total_seconds || 60;
    return (
      <FadeIn>
        <div className="screen">
          <div className="card waiting-card">
            <div className="text-xl text-bold mb-1">⏳ Waiting for players…</div>
            {remaining !== null && remaining > 0 && (
              <>
                <div className="row mt-2 mb-1">
                  <span className="card-meta">Starting in</span>
                  <span className="text-gold text-bold countdown-text">{remaining}s</span>
                </div>
                <div className="waiting-timer">
                  <div className="waiting-timer-bar" style={{ width: `${(remaining / total) * 100}%` }} />
                </div>
              </>
            )}
            <div className="text-dim text-sm mt-2">Prize pool: {fmt(effectiveState.prize_pool)} ETB</div>
          </div>
        </div>
      </FadeIn>
    );
  }

  if (effectiveState.state === 'finished') {
    return <ResultScreen state={effectiveState} onDone={onFinished} />;
  }

  return (
    <FadeIn>
      <div className="screen">
        <div className="row">
          <div className="text-bold">Bingo {fmt(effectiveState.room_fee)} ETB</div>
          <button
            onClick={() => setAudioOn(v => !v)}
            title={audioOn ? 'Mute number call' : 'Unmute number call'}
            className={`btn-icon ${audioOn ? 'btn-icon-audio-on' : 'btn-icon-audio-off'}`}
          >
            {audioOn ? '🔊' : '🔇'}
          </button>
          <Balance />
        </div>

        <div className="card">
          <div className="text-xs text-dim mb-1">Call {effectiveState.call_count}/{effectiveState.max_calls}</div>
          {effectiveState.last_call && (
            <div className="live-call-number">{effectiveState.last_call.letter}-{effectiveState.last_call.number}</div>
          )}
          {effectiveState.last_call && (
            <div className="live-call-amharic">{effectiveState.last_call.amharic}</div>
          )}
          <div className="live-call-meta">
            🏆 Pool: {fmt(effectiveState.prize_pool)} ETB · 👥 {effectiveState.player_count} players
            {effectiveState.jackpot && effectiveState.jackpot.current_amount > 0 && (
              <>
                <div className="jackpot-text">
                  <span>💰 Jackpot</span>
                  <span className="text-gold">{fmt(effectiveState.jackpot.current_amount)} / 1000 ETB</span>
                </div>
                <div className="jackpot-bar">
                  <div className="jackpot-fill" style={{ width: `${Math.min(100, (effectiveState.jackpot.current_amount / 1000) * 100)}%` }} />
                </div>
              </>
            )}
          </div>
        </div>

        <NumberGrid called={effectiveState.called_numbers} />

        <button
          className={`btn ${effectiveState.auto_win ? 'btn-success' : 'btn-secondary'} btn-block`}
          onClick={toggleAuto}
        >
          🤖 Auto-win: {effectiveState.auto_win ? 'ON' : 'OFF'}
        </button>

        {effectiveState.my_cards.map(card => (
          <CardView key={card.card_index} card={card} gameId={gameId} autoWin={effectiveState.auto_win} onMark={markNumber} calledNumbers={effectiveState.called_numbers} />
        ))}
      </div>
    </FadeIn>
  );
}

function NumberGrid({ called }) {
  const calledSet = new Set(called);
  const cells = [];
  for (let n = 1; n <= 75; n++) {
    cells.push(
      <div
        key={n}
        className={`number-cell ${calledSet.has(n) ? 'number-cell-called' : ''}`}
      >
        {n}
      </div>
    );
  }
  return (
    <div className="card">
      <div className="number-grid">
        {cells}
      </div>
    </div>
  );
}

function CardView({ card, gameId, autoWin, onMark, calledNumbers = [] }) {
  const renderCalledSet = new Set(card.called || []);
  const allowedSet = new Set(calledNumbers);
  const markedSet = new Set(card.marked || []);

  const handleCellClick = async (value) => {
    if (autoWin || value === 0) return;
    if (!allowedSet.has(value)) return;
    await onMark(card.card_index, value);
  };

  const getCellClass = (isFree, isCalled, isMarked) => {
    if (isFree) return 'bingo-cell bingo-cell-free';
    if (isMarked) return 'bingo-cell bingo-cell-marked';
    if (isCalled) return 'bingo-cell bingo-cell-called';
    return 'bingo-cell';
  };

  return (
    <div className="card">
      <div className="text-xs text-dim mb-1">Cartela #{card.card_number}</div>
      <div className="bingo-grid">
        {LETTERS.map(l => (
          <div key={l} className="cell-letter">{l}</div>
        ))}
        {[0, 1, 2, 3, 4].map(row => (
          card.grid.map((col, colIdx) => {
            const value = col[row];
            const isFree = value === 0;
            const isCalled = !isFree && renderCalledSet.has(value);
            const isMarked = !isFree && markedSet.has(value);

            return (
              <div
                key={`${colIdx}-${row}`}
                onClick={() => handleCellClick(value)}
                className={`${getCellClass(isFree, isCalled, isMarked)} ${!isFree && autoWin ? 'bingo-cell-readonly' : ''}`}
              >
                {isFree ? '★' : value}
              </div>
            );
          })
        ))}
      </div>
    </div>
  );
}

function ResultScreen({ state, onDone }) {
  const won = state.i_won;
  const winners = state.winner_details && state.winner_details.length
    ? state.winner_details
    : (state.winners || []).map(uid => ({ user_id: uid, username_masked: `User ${uid}`, cards: [] }));

  return (
    <FadeIn>
      <div className="screen">
        <div
          className={`card ${won ? 'result-win' : 'result-lose'}`}
          style={{ padding: 28, textAlign: 'center' }}
        >
          <div className="text-3xl">{won ? '🏆' : '😮'}</div>
          <div className="text-xl text-bold" style={{ marginTop: 8 }}>
            {won ? 'You won!' : winners.length > 0 ? 'Round over' : 'No winner — refunded'}
          </div>
          {won && (
            <div className="text-gold text-bold" style={{ fontSize: 28, marginTop: 6 }}>
              +{fmt(state.per_winner_amount)} ETB
            </div>
          )}
          {!won && winners.length > 0 && (
            <div className="text-dim text-sm mt-1">
              {winners.length} winner{winners.length > 1 ? 's' : ''} took {fmt(state.per_winner_amount)} ETB each
            </div>
          )}
        </div>

        {winners.length > 0 && (
          <div className="card">
            <div className="text-dim text-sm mb-1" style={{ fontWeight: 700 }}>
              🏆 Winning card{winners.length > 1 || (winners[0] && winners[0].cards.length > 1) ? 's' : ''}
            </div>
            {winners.map(w => (
              <div key={w.user_id} style={{ paddingTop: 8, borderTop: '1px solid var(--border)' }}>
                <div className="row mb-1">
                  <div className="text-gold text-bold">{w.username_masked}</div>
                </div>
                {(w.cards || []).map(card => (
                  <div key={card.card_number} style={{ marginBottom: 8 }}>
                    <div className="text-dim text-xs mb-1">
                      Cartela #{card.card_number} · {card.pattern}
                    </div>
                    <WinnerCardPreview grid={card.grid} winning={card.winning_numbers} />
                  </div>
                ))}
              </div>
            ))}
          </div>
        )}

        <button className="btn btn-primary btn-block" onClick={onDone}>Back to Lobby</button>
      </div>
    </FadeIn>
  );
}

function WinnerCardPreview({ grid, winning = [] }) {
  const winSet = new Set(winning);
  return (
    <div style={{ border: '1px solid var(--border)', borderRadius: 6, padding: 6, background: 'var(--bg-card)' }}>
      <div className="mini-grid">
        {LETTERS.map(l => (
          <div key={l} className="mini-grid-letter">{l}</div>
        ))}
        {[0, 1, 2, 3, 4].map(row => (
          grid.map((col, colIdx) => {
            const value = col[row];
            const isFree = value === 0;
            const won = !isFree && winSet.has(value);
            const cellClass = isFree ? 'mini-grid-cell-free' : won ? 'mini-grid-cell-win' : 'mini-grid-cell';
            return (
              <div key={`${colIdx}-${row}`} className={cellClass}>
                {isFree ? '★' : value}
              </div>
            );
          })
        ))}
      </div>
    </div>
  );
}
