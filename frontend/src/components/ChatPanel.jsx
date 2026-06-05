import React, { useState, useRef, useEffect, useCallback } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { streamChat } from '../lib/api';
import './ChatPanel.css';

// Straight ASCII quotes — curly quotes break JS string literals
const SUGGESTED = [
  "Why did Video A get more engagement than Video B?",
  "What's the engagement rate of each video?",
  "Compare the hooks in the first 5 seconds.",
  "Who's the creator of Video B and what's their follower count?",
  "Suggest improvements for B based on what worked in A.",
];

export default function ChatPanel({ sessionId }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const bottomRef = useRef(null);
  const inputRef = useRef(null);
  // Track whether the component is still mounted so we skip state updates after unmount
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const sendMessage = useCallback(async (text) => {
    if (!text.trim() || streaming || !sessionId) return;

    const userMsg = text.trim();
    setInput('');
    setMessages(prev => [...prev, { role: 'user', content: userMsg }]);

    // Stable ID for the in-flight assistant bubble
    const assistantId = `assistant-${Date.now()}`;
    setMessages(prev => [
      ...prev,
      { role: 'assistant', content: '', sources: [], id: assistantId, streaming: true },
    ]);
    setStreaming(true);

    let tokenBuffer = '';

    await streamChat(sessionId, userMsg, {
      onSources: (sources) => {
        if (!mountedRef.current) return;
        setMessages(prev =>
          prev.map(m => m.id === assistantId ? { ...m, sources } : m)
        );
      },
      onToken: (token) => {
        if (!mountedRef.current) return;
        tokenBuffer += token;
        setMessages(prev =>
          prev.map(m => m.id === assistantId ? { ...m, content: tokenBuffer } : m)
        );
      },
      onDone: () => {
        if (!mountedRef.current) return;
        setMessages(prev =>
          prev.map(m => m.id === assistantId ? { ...m, streaming: false } : m)
        );
        setStreaming(false);
        inputRef.current?.focus();
      },
      onError: (err) => {
        if (!mountedRef.current) return;
        setMessages(prev =>
          prev.map(m =>
            m.id === assistantId
              ? { ...m, content: `⚠️ ${err.message}`, streaming: false, error: true }
              : m
          )
        );
        setStreaming(false);
      },
    });
  }, [sessionId, streaming]);

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage(input);
    }
  };

  return (
    <div className="chat">
      <div className="chat__header">
        <span className="chat__title">RAG Chat</span>
        <span className="chat__turns">{Math.floor(messages.length / 2)} turns</span>
      </div>

      <div className="chat__body">
        {messages.length === 0 && (
          <div className="chat__empty">
            <div className="chat__empty-title">Ask anything about both videos</div>
            <div className="chat__suggestions">
              {SUGGESTED.map((s, i) => (
                <button key={i} className="chat__suggestion" onClick={() => sendMessage(s)}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((msg, i) => (
          <Message key={msg.id ?? i} msg={msg} />
        ))}
        <div ref={bottomRef} />
      </div>

      <div className="chat__footer">
        <textarea
          ref={inputRef}
          className="chat__input"
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={streaming ? 'Streaming…' : 'Ask about the videos…  ⏎ to send'}
          disabled={streaming}
          rows={2}
        />
        <button
          className="chat__send"
          onClick={() => sendMessage(input)}
          disabled={streaming || !input.trim()}
          aria-label="Send message"
        >
          {streaming ? (
            <span className="chat__spinner" />
          ) : (
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
              <path
                d="M2 8h12M8 2l6 6-6 6"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          )}
        </button>
      </div>
    </div>
  );
}

/**
 * Normalise LLM output so react-markdown renders lists/paragraphs correctly.
 * LLMs often omit the blank line that CommonMark requires before a list.
 */
function normaliseMarkdown(text) {
  return text
    .replace(/([^\n])\n(\d+\.\s)/g, '$1\n\n$2')
    .replace(/([^\n])\n([-*]\s)/g, '$1\n\n$2')
    .replace(/\n{3,}/g, '\n\n');
}

function Message({ msg }) {
  const isUser = msg.role === 'user';
  return (
    <div className={`msg msg--${isUser ? 'user' : 'assistant'}${msg.error ? ' msg--error' : ''}`}>
      <div className="msg__role">{isUser ? 'YOU' : 'AI'}</div>
      <div className="msg__body">
        {isUser ? (
          <span>{msg.content}</span>
        ) : (
          <>
            <div className="msg__text">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {normaliseMarkdown(msg.content)}
              </ReactMarkdown>
              {msg.streaming && <span className="msg__cursor" />}
            </div>
            {msg.sources?.length > 0 && <SourceList sources={msg.sources} />}
          </>
        )}
      </div>
    </div>
  );
}

function SourceList({ sources }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="sources">
      <button className="sources__toggle" onClick={() => setOpen(v => !v)}>
        {open ? '▾' : '▸'} {sources.length} source{sources.length !== 1 ? 's' : ''} cited
      </button>
      {open && (
        <div className="sources__list">
          {sources.map((s, i) => (
            <div key={i} className={`source source--${s.label.toLowerCase()}`}>
              <span className="source__tag">Video {s.label} · Chunk {s.chunk_index}</span>
              <span className="source__score">{(s.score * 100).toFixed(0)}% match</span>
              <div className="source__text">{s.chunk_text}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}