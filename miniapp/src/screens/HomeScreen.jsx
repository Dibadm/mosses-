// src/screens/HomeScreen.jsx
import { useEffect, useState } from 'react';
import { useStore } from '../lib/store';
import { usePolling } from '../lib/usePolling';
import { api } from '../lib/api';
import { TopBar, fmt } from '../components/Chrome';
import { haptic } from '../lib/telegram';
import FadeIn from '../components/FadeIn';

export default function HomeScreen({ onEnterRoom, onOpenGame }) {
  const { user, runAction } = useStore();
  const [rooms, setRooms] = useState(null);
  const [activeGame, setActiveGame] = useState(null);
  const [jackpot, setJackpot] = useState(null);

  const fetchAll = async () => {
    const [roomsRes, activeRes, jackpotRes] = await Promise.all([
      api.getRooms(),
      api.getMyActiveGame(),
      api.getJackpot(),
    ]);
    setRooms(roomsRes.rooms);
    setActiveGame(activeRes.has_game ? activeRes : null);
    setJackpot(jackpotRes.jackpot?.current_amount > 0 ? jackpotRes.jackpot : null);
  };

  const { loading, error } = usePolling(fetchAll, { interval: 10000, backoffMax: 60000 });

  if (error) {
    return (
      <FadeIn>
        <div className="screen">
          <TopBar title="ሀበሻ ቤት" />
          <div className="empty-state">
            <div className="empty-state-icon">⚠️</div>
            <div className="empty-state-title">Could not load</div>
            <div className="empty-state-body">{error.message || 'Network error'}</div>
          </div>
        </div>
      </FadeIn>
    );
  }

  return (
    <FadeIn>
      <div className="screen">
        <TopBar title="ሀበሻ ቤት" />

        {user && (
          <div className="row mt-1">
            <div className="text-sm text-dim">@{user.username || user.user_id}</div>
            <div className="pill pill-gold">🔥 {user.daily_streak ?? 0}d streak</div>
          </div>
        )}

        {activeGame && (
          <button
            className="card game-card-active btn-block"
            onClick={() => onOpenGame(activeGame.game_id)}
          >
            <div className="row">
              <div className="text-lg text-bold">🎮 Open game · Bingo {fmt(activeGame.room_fee)} ETB</div>
              <span className={`pill ${activeGame.state === 'running' ? 'pill-green' : ''}`}>
                {activeGame.state === 'running' ? '🔴 Live' : '🟡 Waiting'}
              </span>
            </div>
            <div className="divider" />
            <div className="text-sm text-dim">
              You have {activeGame.my_cards.length} card{activeGame.my_cards.length === 1 ? '' : 's'} in this round — tap to rejoin
            </div>
          </button>
        )}

        <div className="section-title">Pick a room to join the next round</div>

        {!rooms && loading && (
          <div className="empty-state">
            <div className="spinner" />
            <div className="empty-state-title">Loading rooms…</div>
          </div>
        )}

        {!rooms && !loading && (
          <div className="empty-state">
            <div className="empty-state-icon">🎱</div>
            <div className="empty-state-title">No rooms available</div>
            <div className="empty-state-body">Check back soon for the next round.</div>
          </div>
        )}

        {rooms && rooms.map(room => (
          <RoomCard
            key={room.room_fee}
            room={room}
            jackpot={room.jackpot}
            onClick={() => {
              haptic.light();
              onEnterRoom(room.room_fee);
            }}
          />
        ))}
      </div>
    </FadeIn>
  );
}

function RoomCard({ room, onClick, jackpot }) {
  const busy = room.state === 'running';
  return (
    <button
      className="card btn-block"
      onClick={onClick}
      disabled={busy}
    >
      <div className="row">
        <div className="text-xl text-bold">🎱 Bingo {fmt(room.room_fee)} ETB</div>
        {busy ? (
          <span className="pill">🔴 In progress</span>
        ) : (
          <span className="pill pill-green">🟢 Open</span>
        )}
      </div>

      <div className="divider" />

      <div className="row">
        <span className="card-meta">Prize pool</span>
        <span className="text-gold text-bold">{fmt(room.prize_pool)} ETB</span>
      </div>
      <div className="row">
        <span className="card-meta">Cards sold</span>
        <span>{room.cards_sold}/{room.card_pool_size}</span>
      </div>
      <div className="row">
        <span className="card-meta">Players</span>
        <span>👥 {room.player_count}</span>
      </div>
      {jackpot && jackpot.current_amount > 0 && (
        <>
          <div className="jackpot-text">
            <span>💰 Jackpot</span>
            <span className="text-gold text-bold">{fmt(jackpot.current_amount)} / 1000 ETB</span>
          </div>
          <div className="jackpot-bar">
            <div className="jackpot-fill" style={{ width: `${Math.min(100, (jackpot.current_amount / 1000) * 100)}%` }} />
          </div>
        </>
      )}
    </button>
  );
}
