import { useEffect } from 'react';
import { toast } from 'sonner';
import { apiFetch } from './api';

// Polls the borsa alarm event feed and raises a browser notification (plus an
// in-app toast) for each newly triggered level alarm.
const STORAGE_KEY = 'oj-borsa-alarm-last-event';
const POLL_MS = 60_000;

interface AlarmEvent {
  id: string;
  symbol: string;
  message: string;
}

function readLast(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeLast(id: string) {
  try {
    localStorage.setItem(STORAGE_KEY, id);
  } catch {
    /* storage unavailable */
  }
}

function notify(ev: AlarmEvent) {
  toast(ev.message);
  if (typeof Notification === 'undefined') return;
  if (Notification.permission === 'granted') {
    new Notification('Borsa alarmı', { body: ev.message, tag: ev.id });
  }
}

export function useBorsaAlarms() {
  useEffect(() => {
    let cancelled = false;
    if (typeof Notification !== 'undefined' && Notification.permission === 'default') {
      void Notification.requestPermission().catch(() => {});
    }

    const poll = async () => {
      const last = readLast();
      try {
        const res = await apiFetch(
          `/api/borsa/alarm/olaylar?after=${encodeURIComponent(last ?? '')}`,
        );
        if (!res.ok || cancelled) return;
        const body = (await res.json()) as { events: AlarmEvent[] };
        // First run: only remember the newest event, don't replay history.
        if (last !== null) body.events.forEach(notify);
        const newest = body.events[body.events.length - 1]?.id ?? last ?? '';
        if (newest !== last) writeLast(newest);
      } catch {
        /* server offline — try again next tick */
      }
    };

    void poll();
    const interval = setInterval(poll, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);
}
