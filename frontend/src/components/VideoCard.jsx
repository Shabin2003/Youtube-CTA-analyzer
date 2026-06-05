import React from 'react';
import './VideoCard.css';

function fmt(n) {
  if (!n && n !== 0) return '—';
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toLocaleString();
}

function fmtDate(s) {
  if (!s || s.length !== 8) return s || '—';
  return `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}`;
}

function fmtDuration(secs) {
  if (!secs) return '—';
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

export default function VideoCard({ video, label }) {
  if (!video) return null;
  const isA = label === 'A';

  return (
    <div className={`vcard vcard--${label.toLowerCase()}`}>
      <div className="vcard__label">{label}</div>

      {video.thumbnail && (
        <div className="vcard__thumb-wrap">
          <img
            className="vcard__thumb"
            src={video.thumbnail}
            alt={video.title}
            loading="lazy"
          />
        </div>
      )}

      <div className="vcard__body">
        <div className="vcard__title" title={video.title}>{video.title}</div>
        <div className="vcard__creator">
          <span className="vcard__creator-label">by</span> {video.creator}
          {video.follower_count > 0 && (
            <span className="vcard__followers"> · {fmt(video.follower_count)} followers</span>
          )}
        </div>

        <div className="vcard__stats">
          <Stat label="Views" value={fmt(video.views)} />
          <Stat label="Likes" value={fmt(video.likes)} />
          <Stat label="Comments" value={fmt(video.comments)} />
          <Stat label="Engagement" value={`${Number(video.engagement_rate).toFixed(2)}%`} accent />
          <Stat label="Duration" value={fmtDuration(video.duration)} />
          <Stat label="Uploaded" value={fmtDate(video.upload_date)} />
          <Stat label="Platform" value={video.platform} />
        </div>

        {video.hashtags && (
          <div className="vcard__hashtags">
            {(Array.isArray(video.hashtags)
              ? video.hashtags
              : video.hashtags.split(',')
            ).slice(0, 6).map((h, i) => (
              <span key={i} className="vcard__tag">{String(h).trim()}</span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function Stat({ label, value, accent }) {
  return (
    <div className={`vcard__stat${accent ? ' vcard__stat--accent' : ''}`}>
      <span className="vcard__stat-label">{label}</span>
      <span className="vcard__stat-value">{value}</span>
    </div>
  );
}