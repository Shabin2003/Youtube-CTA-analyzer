import React, { useState, useCallback } from 'react';
import { ingestVideos, deleteSession } from './lib/api';
import IngestForm from './components/IngestForm';
import VideoCard from './components/VideoCard';
import ChatPanel from './components/ChatPanel';
import './App.css';

export default function App() {
  const [phase, setPhase] = useState('ingest'); // 'ingest' | 'loading' | 'chat'
  const [sessionId, setSessionId] = useState(null);
  const [videos, setVideos] = useState({ A: null, B: null });
  const [error, setError] = useState('');

  const handleIngest = useCallback(async (urlA, urlB) => {
    setPhase('loading');
    setError('');
    try {
      const data = await ingestVideos(urlA, urlB);
      setSessionId(data.session_id);
      setVideos({ A: data.video_a, B: data.video_b });
      setPhase('chat');
    } catch (err) {
      setError(err.message || 'Something went wrong during ingestion.');
      setPhase('ingest');
    }
  }, []);

  const handleReset = useCallback(async () => {
    if (sessionId) {
      try { await deleteSession(sessionId); } catch { /* best-effort cleanup */ }
    }
    setSessionId(null);
    setVideos({ A: null, B: null });
    setPhase('ingest');
    setError('');
  }, [sessionId]);

  if (phase === 'ingest' || phase === 'loading') {
    return (
      <div className="app app--center">
        <div className="app__noise" />
        <IngestForm onIngest={handleIngest} loading={phase === 'loading'} />
        {error && <div className="app__error">{error}</div>}
      </div>
    );
  }

  return (
    <div className="app app--chat">
      <div className="app__noise" />
      <header className="app__topbar">
        <span className="app__logo">VideoRAG</span>
        <button className="app__reset" onClick={handleReset}>← New Session</button>
      </header>

      <main className="app__main">
        <aside className="app__sidebar">
          <VideoCard video={videos.A} label="A" />
          <VideoCard video={videos.B} label="B" />
        </aside>
        <section className="app__chat">
          <ChatPanel sessionId={sessionId} />
        </section>
      </main>
    </div>
  );
}
