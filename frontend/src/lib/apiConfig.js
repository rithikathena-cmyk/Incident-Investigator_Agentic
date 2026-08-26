// In dev (Vite's dev server on :5173) the API is a separate origin
// (localhost:8787). In a production build served by this same FastAPI app
// (see app/server.py's StaticFiles mount at the bottom of that file), it's
// the same origin the page itself was loaded from - so a relative API_URL
// and a WS_URL derived from window.location just work regardless of
// whatever domain the app actually gets deployed to, with no build-time
// knowledge of that domain needed. VITE_API_URL/VITE_WS_URL override
// either way, for a split frontend/backend deployment instead.
export const API_URL = import.meta.env.VITE_API_URL || (import.meta.env.DEV ? 'http://localhost:8787' : '')

export const WS_URL =
  import.meta.env.VITE_WS_URL ||
  (import.meta.env.DEV
    ? 'ws://localhost:8787/ws/investigate'
    : `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}/ws/investigate`)
