(() => {
  if (window !== window.top) {
    if (window.__qscriptSubframeInit) return;
    window.__qscriptSubframeInit = true;
    window.addEventListener('keydown', (e) => {
      if (e.key === 'F5' || e.code === 'F5') {
        e.preventDefault();
        e.stopPropagation();
        if (e.repeat) return;
        try {
          window.top.postMessage({ type: '__qscript_f5' }, '*');
        } catch (err) {}
      }
    }, true);
    return;
  }

  if (window.__qscriptCursorInit) return;
  window.__qscriptCursorInit = true;

  function initCursor() {
    if (document.getElementById('cursor-layer')) return;
    const target = document.head || document.documentElement || document.body;
    if (!target) return;

    if (!document.getElementById('cursor-style')) {
      const style = document.createElement('style');
      style.id = 'cursor-style';
      style.textContent = `
        #cursor-layer {
          position: fixed;
          top: 0;
          left: 0;
          width: 0;
          height: 0;
          pointer-events: none;
          z-index: 2147483647;
          overflow: visible;
        }
        #cursor-pointer {
          position: absolute;
          top: 0;
          left: 0;
          width: 64px;
          height: 64px;
          margin: -16px 0 0 -23px;
          pointer-events: none;
          transform: translate(0, 0);
          will-change: transform;
          opacity: 0;
          transition: opacity 0.15s ease;
        }
        .click-ripple {
          position: absolute;
          border-radius: 50%;
          border: 3px solid rgba(255, 193, 7, 0.9);
          background: rgba(255, 235, 59, 0.35);
          pointer-events: none;
          transform: translate(-50%, -50%) scale(0.1);
          animation: ripple-effect 0.55s cubic-bezier(0.1, 0.8, 0.3, 1) forwards;
        }
        @keyframes ripple-effect {
          0% { transform: translate(-50%, -50%) scale(0.1); opacity: 0.9; }
          100% { transform: translate(-50%, -50%) scale(1.4); opacity: 0; }
        }
        .callout-bubble {
          position: fixed;
          z-index: 2147483646;
          font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
          font-size: 16pt;
          font-weight: 600;
          padding: 12px 24px;
          border-radius: 8px;
          box-shadow: 0 4px 14px rgba(0,0,0,0.25);
          pointer-events: none;
          opacity: 0;
          max-width: 80vw;
          width: max-content;
          left: 50%;
          bottom: 40px;
          transform: translateX(-50%);
        }
        .zoom-indicator-ring {
          position: absolute;
          width: 72px;
          height: 72px;
          border-radius: 50%;
          pointer-events: none;
          transform: translate(-50%, -50%) scale(0.45);
          animation: zoom-ring-pulse 0.55s cubic-bezier(0.16, 1, 0.3, 1) forwards;
        }
        .zoom-indicator-ring--in {
          border: 4px solid rgba(34, 197, 94, 0.95);
          background: rgba(34, 197, 94, 0.18);
          box-shadow: 0 0 18px rgba(34, 197, 94, 0.55);
        }
        .zoom-indicator-ring--out {
          border: 4px solid rgba(249, 115, 22, 0.95);
          background: rgba(249, 115, 22, 0.18);
          box-shadow: 0 0 18px rgba(249, 115, 22, 0.55);
        }
        @keyframes zoom-ring-pulse {
          0% { transform: translate(-50%, -50%) scale(0.45); opacity: 1; }
          65% { transform: translate(-50%, -50%) scale(1.15); opacity: 0.9; }
          100% { transform: translate(-50%, -50%) scale(1.45); opacity: 0; }
        }
      `;
      target.appendChild(style);
    }

    const container = document.createElement('div');
    container.id = 'cursor-layer';

    const cursor = document.createElement('div');
    cursor.id = 'cursor-pointer';
    cursor.innerHTML = `
      <svg width="64" height="64" viewBox="0 0 64 64" fill="none">
        <circle cx="32" cy="32" r="30" fill="rgba(255, 220, 40, 0.45)" />
        <g transform="translate(23, 16)">
          <path d="M0 0 L0 26 L6.5 19.5 L12 32 L15.5 30.5 L10 18 L18 18 Z" 
                fill="#ffffff" stroke="#000000" stroke-width="1.8" stroke-linejoin="round"/>
        </g>
      </svg>
    `;
    container.appendChild(cursor);

    const savedX = sessionStorage.getItem('cursor_x');
    const savedY = sessionStorage.getItem('cursor_y');
    if (savedX !== null && savedY !== null) {
      cursor.style.transform = `translate(${savedX}px, ${savedY}px)`;
      cursor.style.opacity = '1';
    }

    const bodyTarget = document.body || document.documentElement;
    if (bodyTarget) {
      bodyTarget.appendChild(container);
    }
  }

  const savedInitX = Number(sessionStorage.getItem('cursor_x'));
  const savedInitY = Number(sessionStorage.getItem('cursor_y'));
  let hasCursorPos = sessionStorage.getItem('cursor_x') !== null && sessionStorage.getItem('cursor_y') !== null;
  let lastMouseX = Number.isFinite(savedInitX) && hasCursorPos
    ? savedInitX
    : (window.innerWidth || 1280) / 2;
  let lastMouseY = Number.isFinite(savedInitY) && hasCursorPos
    ? savedInitY
    : (window.innerHeight || 720) / 2;

  window.__scriptMouseTarget = null;
  window.__getCursorPos = () => (hasCursorPos ? { x: lastMouseX, y: lastMouseY } : null);

  let lastActivityReportAt = -1000;
  let activityFlushTimer = null;
  function reportUserActivity(force) {
    if (typeof window.__pyUserActivity !== 'function') return;
    const now = performance.now();
    if (force || now - lastActivityReportAt >= 50) {
      lastActivityReportAt = now;
      if (activityFlushTimer) {
        clearTimeout(activityFlushTimer);
        activityFlushTimer = null;
      }
      window.__pyUserActivity();
      return;
    }
    if (!activityFlushTimer) {
      activityFlushTimer = setTimeout(() => {
        activityFlushTimer = null;
        lastActivityReportAt = performance.now();
        if (typeof window.__pyUserActivity === 'function') {
          window.__pyUserActivity();
        }
      }, 50);
    }
  }

  function isScriptPointerEvent(e) {
    const t = window.__scriptMouseTarget;
    if (!t) return false;
    return Math.abs(e.clientX - t.x) <= 2 && Math.abs(e.clientY - t.y) <= 2;
  }

  if (typeof window.__zoomT0 !== 'number') {
    window.__zoomT0 = performance.now();
  }

  function showZoomIndicator(x, y, eventType) {
    initCursor();
    const layer = document.getElementById('cursor-layer');
    if (!layer) return;
    const ring = document.createElement('div');
    ring.className = `zoom-indicator-ring zoom-indicator-ring--${eventType === 'in' ? 'in' : 'out'}`;
    ring.style.left = `${x}px`;
    ring.style.top = `${y}px`;
    ring.dataset.zoomType = eventType;
    layer.appendChild(ring);
    setTimeout(() => ring.remove(), 550);
  }

  window.__zoomAt = async (x, y, source, targetState) => {
    const cx = typeof x === 'number' && Number.isFinite(x) ? x : lastMouseX;
    const cy = typeof y === 'number' && Number.isFinite(y) ? y : lastMouseY;
    const clientMs = Date.now();
    let src = 'api';
    let desired = null;
    if (source === 'f5') {
      src = 'f5';
    } else if (source === 'in' || source === 'out') {
      desired = source;
    }
    if (targetState === 'in' || targetState === 'out') {
      desired = targetState;
    }
    let eventType = null;
    if (typeof window.__pyRecordZoom === 'function') {
      eventType = await window.__pyRecordZoom(cx, cy, clientMs, src, desired);
    } else {
      const current = Boolean(window.__zoomActive);
      if ((desired === 'in' && current) || (desired === 'out' && !current)) {
        return null;
      }
      window.__zoomActive = !current;
      eventType = window.__zoomActive ? 'in' : 'out';
      window.__zoomEvents = window.__zoomEvents || [];
      window.__zoomEvents.push({
        t: Number(((performance.now() - window.__zoomT0) / 1000).toFixed(3)),
        x: Math.round(cx),
        y: Math.round(cy),
        type: eventType,
      });
    }
    if (eventType) {
      showZoomIndicator(cx, cy, eventType);
    }
    return eventType;
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initCursor);
  } else {
    initCursor();
  }

  window.addEventListener('mousemove', (e) => {
    hasCursorPos = true;
    lastMouseX = e.clientX;
    lastMouseY = e.clientY;
    initCursor();
    const ptr = document.getElementById('cursor-pointer');
    if (ptr) {
      ptr.style.transform = `translate(${e.clientX}px, ${e.clientY}px)`;
      ptr.style.opacity = '1';
    }
    try {
      sessionStorage.setItem('cursor_x', e.clientX);
      sessionStorage.setItem('cursor_y', e.clientY);
    } catch (err) {}
    if (!isScriptPointerEvent(e)) {
      reportUserActivity(false);
    }
  }, true);

  let f5Held = false;
  let lastF5At = -1000;
  function triggerF5Zoom() {
    const now = performance.now();
    if (now - lastF5At < 100) return;
    lastF5At = now;
    window.__zoomAt(lastMouseX, lastMouseY, 'f5');
  }

  window.addEventListener('keydown', (e) => {
    if (e.key === 'F5' || e.code === 'F5') {
      e.preventDefault();
      e.stopPropagation();
      if (e.repeat || f5Held) return;
      f5Held = true;
      triggerF5Zoom();
    }
  }, true);

  window.addEventListener('keyup', (e) => {
    if (e.key === 'F5' || e.code === 'F5') {
      f5Held = false;
    }
  }, true);

  window.addEventListener('blur', () => {
    f5Held = false;
  });

  window.addEventListener('message', (e) => {
    if (e.data && e.data.type === '__qscript_f5') {
      triggerF5Zoom();
    }
  });

  window.addEventListener('mousedown', (e) => {
    hasCursorPos = true;
    lastMouseX = e.clientX;
    lastMouseY = e.clientY;
    initCursor();
    const layer = document.getElementById('cursor-layer');
    if (!layer) return;
    const ripple = document.createElement('div');
    ripple.className = 'click-ripple';
    ripple.style.left = `${e.clientX}px`;
    ripple.style.top = `${e.clientY}px`;
    ripple.style.width = '64px';
    ripple.style.height = '64px';
    layer.appendChild(ripple);
    setTimeout(() => ripple.remove(), 550);
    if (!isScriptPointerEvent(e)) {
      reportUserActivity(true);
    }
  }, true);

  window.showCallout = (text, bgColor, textColor, fade) => {
    initCursor();
    const el = document.createElement('div');
    el.className = 'callout-bubble';
    el.style.backgroundColor = bgColor || '#ffeb3b';
    el.style.color = textColor || '#000000';
    el.style.transition = `opacity ${fade || 2}s ease`;
    el.textContent = text;
    (document.body || document.documentElement).appendChild(el);
    requestAnimationFrame(() => { el.style.opacity = '1'; });
    return el;
  };
})();
