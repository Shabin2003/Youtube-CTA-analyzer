/**
 * api.js — VideoRAG frontend API client
 *
 * Covers:
 *   ingestVideos(urlA, urlB)  → POST /api/ingest
 *   deleteSession(id)         → DELETE /api/session/:id
 *   streamChat(id, msg, cbs)  → POST /api/chat  (SSE)
 *
 * All network errors surface as plain Error objects so callers
 * can read .message without inspecting the raw Response.
 */

const BASE_URL = (
  process.env.REACT_APP_API_URL || 'http://localhost:8000'
).replace(/\/$/, '');

// ─── tiny fetch wrapper ───────────────────────────────────────────────────────

async function apiFetch(path, options = {}) {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  });

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body?.detail ?? body?.message ?? detail;
    } catch {
      // ignore parse errors — keep statusText
    }
    throw new Error(`[${res.status}] ${detail}`);
  }

  return res;
}

// ─── ingest ──────────────────────────────────────────────────────────────────

/**
 * Ingest two video URLs.
 *
 * @param {string} urlA
 * @param {string} urlB
 * @returns {Promise<{ session_id: string, video_a: object, video_b: object }>}
 */
export async function ingestVideos(urlA, urlB) {
  const res = await apiFetch('/api/ingest', {
    method: 'POST',
    body: JSON.stringify({ url_a: urlA, url_b: urlB }),
  });
  return res.json();
}

// ─── session ─────────────────────────────────────────────────────────────────

/**
 * Delete a session and its Pinecone namespace.
 *
 * @param {string} sessionId
 */
export async function deleteSession(sessionId) {
  await apiFetch(`/api/session/${encodeURIComponent(sessionId)}`, {
    method: 'DELETE',
  });
}

/**
 * Fetch session metadata (video cards + turn count).
 *
 * @param {string} sessionId
 */
export async function getSession(sessionId) {
  const res = await apiFetch(`/api/session/${encodeURIComponent(sessionId)}`);
  return res.json();
}

// ─── streaming chat ───────────────────────────────────────────────────────────

/**
 * Send a chat message and stream the response via SSE.
 *
 * The backend emits lines in the form:
 *   data: __SOURCES__[...json]   → citations array
 *   data: <token>                → text token
 *   data: [DONE]                 → stream end
 *   data: __ERROR__<message>     → server-side error
 *
 * @param {string}   sessionId
 * @param {string}   message
 * @param {{
 *   onSources?: (sources: object[]) => void,
 *   onToken?:   (token: string)    => void,
 *   onDone?:    ()                 => void,
 *   onError?:   (err: Error)       => void,
 * }} callbacks
 */
export async function streamChat(sessionId, message, callbacks = {}) {
  const { onSources, onToken, onDone, onError } = callbacks;

  let res;
  try {
    res = await apiFetch('/api/chat', {
      method: 'POST',
      body: JSON.stringify({ session_id: sessionId, message }),
    });
  } catch (err) {
    onError?.(err);
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      // Process every complete SSE line (lines end with \n\n or \n)
      const lines = buffer.split('\n');
      // Keep the last (potentially incomplete) fragment in the buffer
      buffer = lines.pop() ?? '';

      for (const line of lines) {
        // Skip non-data lines
        if (!line.includes('data:')) continue;

        // Strip only 'data:' prefix + one optional space after it.
        // Do NOT trimStart the rest — LLMs emit leading spaces as word separators.
        const raw = line.slice(line.indexOf('data:') + 5);
        const payload = raw.startsWith(' ') ? raw.slice(1) : raw;

        if (payload === '[DONE]') {
          onDone?.();
          return;
        }

        if (payload.startsWith('__ERROR__')) {
          onError?.(new Error(payload.slice('__ERROR__'.length)));
          return;
        }

        if (payload.startsWith('__SOURCES__')) {
          try {
            const sources = JSON.parse(payload.slice('__SOURCES__'.length));
            onSources?.(sources);
          } catch {
            // malformed sources JSON — skip silently
          }
          continue;
        }

        // Plain text token
        if (payload) {
          onToken?.(payload);
        }
      }
    }
  } catch (err) {
    onError?.(err instanceof Error ? err : new Error(String(err)));
  } finally {
    try { reader.cancel(); } catch { /* already closed */ }
  }
}