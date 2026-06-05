import React, { useState } from 'react';
import './IngestForm.css';

export default function IngestForm({ onIngest, loading }) {
  const [urlA, setUrlA] = useState('');
  const [urlB, setUrlB] = useState('');

  const valid = urlA.trim() && urlB.trim();

  const handleSubmit = (e) => {
    e.preventDefault();
    if (valid && !loading) onIngest(urlA.trim(), urlB.trim());
  };

  return (
    <form className="ingest" onSubmit={handleSubmit}>
      <div className="ingest__header">
        <span className="ingest__logo">VideoRAG</span>
        <span className="ingest__sub">Creator Intelligence Platform</span>
      </div>

      <div className="ingest__fields">
        <Field
          label="VIDEO A"
          placeholder="YouTube or Instagram Reel URL"
          value={urlA}
          onChange={setUrlA}
          accent="a"
          disabled={loading}
        />
        <div className="ingest__vs">VS</div>
        <Field
          label="VIDEO B"
          placeholder="YouTube or Instagram Reel URL"
          value={urlB}
          onChange={setUrlB}
          accent="b"
          disabled={loading}
        />
      </div>

      <button className="ingest__btn" type="submit" disabled={!valid || loading}>
        {loading ? (
          <><span className="ingest__spinner" /> Extracting & Embedding…</>
        ) : (
          'Analyse Both Videos →'
        )}
      </button>

      {loading && (
        <div className="ingest__progress">
          Pulling transcripts → embedding chunks → indexing in Pinecone
        </div>
      )}
    </form>
  );
}

function Field({ label, placeholder, value, onChange, accent, disabled }) {
  return (
    <div className={`ingest__field ingest__field--${accent}`}>
      <label className="ingest__label">{label}</label>
      <input
        className="ingest__input"
        type="url"
        placeholder={placeholder}
        value={value}
        onChange={e => onChange(e.target.value)}
        disabled={disabled}
        required
      />
    </div>
  );
}
